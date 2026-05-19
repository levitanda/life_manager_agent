"""Tests for a2a_server.py — auth, allow-list, dispatch."""

import json
from unittest.mock import patch

import pytest

import a2a_server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(a2a_server, "CLIENTS_FILE", tmp_path / "clients.json")
    a2a_server.app.config["TESTING"] = True
    return a2a_server.app.test_client()


def _register_test_client(monkeypatch, tmp_path, allowed: list[str]):
    monkeypatch.setattr(a2a_server, "CLIENTS_FILE", tmp_path / "clients.json")
    raw_key = "A2A_test_secret"
    data = {
        "abc123": {
            "name": "test",
            "api_key_hash": a2a_server._hash_token(raw_key),
            "allowed_tools": allowed,
        }
    }
    (tmp_path / "clients.json").write_text(json.dumps(data))
    return raw_key


# ─── public endpoints ────────────────────────────────────────────────────────

def test_agent_card_is_public(client):
    r = client.get("/.well-known/agent.json")
    assert r.status_code == 200
    data = r.get_json()
    assert "name" in data
    assert "skills" in data
    # Only exposed tools listed
    skill_ids = {s["id"] for s in data["skills"]}
    assert skill_ids.issubset(a2a_server.A2A_EXPOSED_TOOLS)


def test_health_is_public(client):
    r = client.get("/a2a/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


# ─── auth ────────────────────────────────────────────────────────────────────

def test_missing_token_returns_401(client):
    r = client.post("/a2a/tasks/send", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "missing_token"


def test_invalid_token_returns_403(client):
    r = client.post(
        "/a2a/tasks/send",
        json={"metadata": {"tool": "get_weather", "params": {}}},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "invalid_token"


def test_valid_token_accepted(tmp_path, monkeypatch):
    key = _register_test_client(monkeypatch, tmp_path, ["get_weather"])
    a2a_server.app.config["TESTING"] = True
    client = a2a_server.app.test_client()
    with patch("tools.TOOL_FUNCS", new={"get_weather": lambda **_: {"status": "ok", "summary": "🌤"}}):
        r = client.post(
            "/a2a/tasks/send",
            json={"metadata": {"tool": "get_weather", "params": {}}},
            headers={"Authorization": f"Bearer {key}"},
        )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "completed"
    assert body["result"]["summary"] == "🌤"


# ─── allow-list enforcement ──────────────────────────────────────────────────

def test_tool_not_exposed_rejected(tmp_path, monkeypatch):
    key = _register_test_client(monkeypatch, tmp_path, ["send_email"])
    a2a_server.app.config["TESTING"] = True
    client = a2a_server.app.test_client()
    r = client.post(
        "/a2a/tasks/send",
        json={"metadata": {"tool": "send_email", "params": {}}},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "tool_not_exposed"


def test_tool_exposed_but_not_in_client_allowlist(tmp_path, monkeypatch):
    key = _register_test_client(monkeypatch, tmp_path, ["show_tasks"])
    a2a_server.app.config["TESTING"] = True
    client = a2a_server.app.test_client()
    r = client.post(
        "/a2a/tasks/send",
        json={"metadata": {"tool": "get_weather", "params": {}}},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "tool_not_allowed_for_client"


def test_missing_tool_param_returns_400(tmp_path, monkeypatch):
    key = _register_test_client(monkeypatch, tmp_path, ["get_weather"])
    a2a_server.app.config["TESTING"] = True
    client = a2a_server.app.test_client()
    r = client.post(
        "/a2a/tasks/send",
        json={"metadata": {}},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 400


# ─── CLI helpers ─────────────────────────────────────────────────────────────

def test_hash_token_stable():
    assert a2a_server._hash_token("a") == a2a_server._hash_token("a")
    assert a2a_server._hash_token("a") != a2a_server._hash_token("b")


def test_save_and_load_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(a2a_server, "CLIENTS_FILE", tmp_path / "c.json")
    a2a_server._save_clients({"id1": {"name": "x", "api_key_hash": "h", "allowed_tools": ["a"]}})
    assert a2a_server._load_clients()["id1"]["name"] == "x"


def test_find_client_by_token(tmp_path, monkeypatch):
    monkeypatch.setattr(a2a_server, "CLIENTS_FILE", tmp_path / "c.json")
    a2a_server._save_clients({"id1": {
        "name": "x",
        "api_key_hash": a2a_server._hash_token("rawkey"),
        "allowed_tools": ["show_tasks"],
    }})
    found = a2a_server._find_client("rawkey")
    assert found is not None
    assert found["id"] == "id1"
    assert a2a_server._find_client("wrong") is None
