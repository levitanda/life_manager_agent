"""Tests for a2a_client.py — registry + HTTP wrappers."""

import json
from unittest.mock import MagicMock, patch

import pytest

import a2a_client


def _mock_resp(status=200, payload=None, raise_for_status=False):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.raise_for_status = MagicMock()
    if raise_for_status:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(a2a_client, "REGISTRY_FILE", tmp_path / "registry.json")
    yield


# ─── registry ────────────────────────────────────────────────────────────────

def test_list_known_agents_empty():
    assert a2a_client.list_known_agents() == []


def test_save_and_load():
    a2a_client._save_registry({"research": {"url": "http://x", "skills": []}})
    assert "research" in a2a_client._load_registry()


def test_list_known_agents_after_save():
    a2a_client._save_registry({"research": {
        "url": "http://x", "description": "deep", "skills": [{"id": "search"}]
    }})
    agents = a2a_client.list_known_agents()
    assert len(agents) == 1
    assert agents[0]["name"] == "research"
    assert agents[0]["skill_ids"] == ["search"]


def test_remove_agent_existing():
    a2a_client._save_registry({"x": {"url": "http://x"}})
    ok, _ = a2a_client.remove_agent("X")  # case insensitive
    assert ok is True
    assert a2a_client._load_registry() == {}


def test_remove_agent_nonexistent():
    ok, msg = a2a_client.remove_agent("ghost")
    assert ok is False
    assert "No agent" in msg


# ─── discover ────────────────────────────────────────────────────────────────

def test_discover_agent_success():
    card = {
        "name": "Research Agent",
        "url": "https://research.example.com/a2a",
        "description": "deep web research",
        "skills": [{"id": "search"}, {"id": "summarize"}],
        "version": "1.0",
    }
    with patch("requests.get", return_value=_mock_resp(200, card)):
        ok, payload = a2a_client.discover_agent(
            "https://research.example.com", "research", api_key="abc"
        )
    assert ok is True
    assert payload["agent_name"] == "Research Agent"
    assert payload["skills"] == ["search", "summarize"]
    reg = a2a_client._load_registry()
    assert reg["research"]["api_key"] == "abc"


def test_discover_agent_network_failure():
    with patch("requests.get", side_effect=Exception("connection refused")):
        ok, payload = a2a_client.discover_agent("https://offline.example.com")
    assert ok is False
    assert "Could not fetch" in payload


def test_discover_agent_uses_card_name_when_friendly_omitted():
    card = {"name": "Auto Name", "url": "x", "skills": []}
    with patch("requests.get", return_value=_mock_resp(200, card)):
        ok, payload = a2a_client.discover_agent("https://x.com")
    assert payload["registered_as"] == "auto name"


# ─── call_agent ──────────────────────────────────────────────────────────────

def test_call_agent_no_such_name():
    ok, msg = a2a_client.call_agent("ghost", "search")
    assert ok is False
    assert "No agent" in msg


def test_call_agent_sends_bearer_token():
    a2a_client._save_registry({
        "research": {"url": "https://research.example.com/a2a", "api_key": "tk_xyz"}
    })
    with patch("requests.post", return_value=_mock_resp(200, {"status": "completed", "result": {}})) as mp:
        ok, payload = a2a_client.call_agent("research", "search", {"q": "x"})
    assert ok is True
    sent_headers = mp.call_args.kwargs["headers"]
    assert sent_headers["Authorization"] == "Bearer tk_xyz"
    body = mp.call_args.kwargs["json"]
    assert body["metadata"]["tool"] == "search"
    assert body["metadata"]["params"] == {"q": "x"}


def test_call_agent_http_error():
    a2a_client._save_registry({"x": {"url": "https://x.com/a2a"}})
    with patch("requests.post", return_value=_mock_resp(403, {"error": "forbidden"})):
        ok, msg = a2a_client.call_agent("x", "search")
    assert ok is False
    assert "403" in msg


def test_call_agent_endpoint_normalization():
    """If url already ends with /a2a, do not double-append."""
    a2a_client._save_registry({"x": {"url": "https://x.com/a2a", "api_key": "k"}})
    with patch("requests.post", return_value=_mock_resp(200, {})) as mp:
        a2a_client.call_agent("x", "search")
    assert mp.call_args.args[0].endswith("/a2a/tasks/send")
    assert "/a2a/a2a/" not in mp.call_args.args[0]


def test_call_agent_url_without_a2a_suffix():
    a2a_client._save_registry({"x": {"url": "https://x.com", "api_key": "k"}})
    with patch("requests.post", return_value=_mock_resp(200, {})) as mp:
        a2a_client.call_agent("x", "search")
    assert mp.call_args.args[0].endswith("/a2a/tasks/send")
