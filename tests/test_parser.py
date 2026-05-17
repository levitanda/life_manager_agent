"""Tests for parser.py — intent detection for new intents."""

from unittest.mock import patch, MagicMock

import pytest

import parser


def _mock_call(response: dict):
    mock = MagicMock()
    mock.content = [MagicMock(text=str(response).replace("'", '"'))]
    return mock


def _parse(text: str, response_dict: dict) -> dict:
    import json
    json_str = json.dumps(response_dict)
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=json_str)]

    with patch("anthropic.Anthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create.return_value = mock_msg
        return parser.parse_message(text)


# ─── reschedule_task ──────────────────────────────────────────────────────────

def test_reschedule_intent_returned():
    response = {"actions": [{"intent": "reschedule_task", "task_number": 2, "date": "2026-05-20", "time": "15:00", "duration_minutes": 60}]}
    result = _parse("перенеси задачу 2 на 20 мая в 15:00", response)
    actions = result.get("actions", [])
    assert len(actions) == 1
    assert actions[0]["intent"] == "reschedule_task"
    assert actions[0]["task_number"] == 2


# ─── find_free_time ───────────────────────────────────────────────────────────

def test_find_free_time_intent_returned():
    response = {"actions": [{"intent": "find_free_time", "date": "2026-05-18", "duration_minutes": 60}]}
    result = _parse("когда у меня есть свободный час завтра?", response)
    actions = result.get("actions", [])
    assert actions[0]["intent"] == "find_free_time"
    assert actions[0]["duration_minutes"] == 60


# ─── get_weekly_digest ────────────────────────────────────────────────────────

def test_weekly_digest_intent_returned():
    response = {"actions": [{"intent": "get_weekly_digest"}]}
    result = _parse("покажи план на неделю", response)
    actions = result.get("actions", [])
    assert actions[0]["intent"] == "get_weekly_digest"


# ─── multi-action ─────────────────────────────────────────────────────────────

def test_multi_action_parse():
    response = {
        "actions": [
            {"intent": "reschedule_task", "task_number": 1, "date": "2026-05-19", "time": "10:00"},
            {"intent": "get_weekly_digest"},
        ]
    }
    result = _parse("перенеси первую задачу и покажи план на неделю", response)
    actions = result.get("actions", [])
    assert len(actions) == 2
    intents = [a["intent"] for a in actions]
    assert "reschedule_task" in intents
    assert "get_weekly_digest" in intents


# ─── fallback on parse error ──────────────────────────────────────────────────

def test_parse_message_fallback_on_error():
    with patch("anthropic.Anthropic") as mock_client_cls:
        mock_client_cls.return_value.messages.create.side_effect = Exception("network error")
        result = parser.parse_message("hello")
    assert "intent" in result or "actions" in result
