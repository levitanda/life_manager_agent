"""Tests for the 4 A2A tool wrappers in tools.py."""

from unittest.mock import patch

import tools


# ─── list ────────────────────────────────────────────────────────────────────

def test_tool_list_empty():
    with patch("a2a_client.list_known_agents", return_value=[]):
        r = tools.a2a_list_known_agents()
    assert r["status"] == "ok"
    assert "не зарегистрировано" in r["summary"].lower()


def test_tool_list_with_agents():
    agents = [{"name": "research", "url": "x", "description": "deep research", "skill_ids": ["search"]}]
    with patch("a2a_client.list_known_agents", return_value=agents):
        r = tools.a2a_list_known_agents()
    assert "research" in r["summary"]
    assert "search" in r["summary"]


# ─── discover ────────────────────────────────────────────────────────────────

def test_tool_discover_success():
    payload = {"registered_as": "research", "agent_name": "X", "skills": ["s1"]}
    with patch("a2a_client.discover_agent", return_value=(True, payload)):
        r = tools.a2a_discover(url="https://x.com", friendly_name="research")
    assert r["status"] == "ok"
    assert "research" in r["summary"]


def test_tool_discover_failure():
    with patch("a2a_client.discover_agent", return_value=(False, "network error")):
        r = tools.a2a_discover(url="https://x.com")
    assert r["status"] == "error"


# ─── call_agent ──────────────────────────────────────────────────────────────

def test_tool_call_agent_success():
    payload = {"status": "completed", "result": {"status": "ok", "summary": "Found 3 results"}}
    with patch("a2a_client.call_agent", return_value=(True, payload)):
        r = tools.a2a_call_agent(agent_name="research", tool="search", params={"q": "x"})
    assert r["status"] == "ok"
    assert "Found 3 results" in r["summary"]


def test_tool_call_agent_failure():
    with patch("a2a_client.call_agent", return_value=(False, "forbidden")):
        r = tools.a2a_call_agent(agent_name="research", tool="search")
    assert r["status"] == "error"


def test_tool_call_agent_handles_non_dict_result():
    payload = {"status": "completed", "result": "plain text response"}
    with patch("a2a_client.call_agent", return_value=(True, payload)):
        r = tools.a2a_call_agent(agent_name="x", tool="t")
    assert r["status"] == "ok"
    assert "plain text response" in r["summary"]


# ─── remove ──────────────────────────────────────────────────────────────────

def test_tool_remove_existing():
    with patch("a2a_client.remove_agent", return_value=(True, "Removed")):
        r = tools.a2a_remove_agent(name="research")
    assert r["status"] == "ok"


def test_tool_remove_nonexistent():
    with patch("a2a_client.remove_agent", return_value=(False, "No agent")):
        r = tools.a2a_remove_agent(name="ghost")
    assert r["status"] == "error"


# ─── schemas registered ──────────────────────────────────────────────────────

def test_a2a_tools_in_schemas():
    names = {s["name"] for s in tools.TOOL_SCHEMAS}
    for n in ("a2a_list_known_agents", "a2a_discover", "a2a_call_agent", "a2a_remove_agent"):
        assert n in names
        assert n in tools.TOOL_FUNCS
