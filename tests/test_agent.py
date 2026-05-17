"""Tests for agent.py — tool-use loop dispatch."""

from unittest.mock import MagicMock, patch

import pytest

import agent
import tools


def _text_block(text: str):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_use_block(tool_use_id: str, name: str, input_dict: dict):
    b = MagicMock()
    b.type = "tool_use"
    b.id = tool_use_id
    b.name = name
    b.input = input_dict
    return b


def _claude_response(stop_reason: str, content_blocks: list):
    resp = MagicMock()
    resp.stop_reason = stop_reason
    resp.content = content_blocks
    return resp


# ─── Single tool, end_turn ───────────────────────────────────────────────────

def test_single_tool_call_then_end_turn():
    """User asks for weather → tool_use → tool_result → end_turn with text."""
    fake_weather = MagicMock(return_value={"status": "ok", "summary": "🌤 Нешер: ясно, 20°C"})
    with patch("anthropic.Anthropic") as mock_anthropic, \
         patch.dict(tools.TOOL_FUNCS, {"get_weather": fake_weather}):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [_tool_use_block("u1", "get_weather", {})]),
            _claude_response("end_turn", [_text_block("Хорошая погода сегодня!")]),
        ]
        result = agent.run_agent("какая погода?")

    assert "Нешер: ясно" in result["text"]
    assert "Хорошая погода" in result["text"]
    assert result["actions"] == []


# ─── Chained tool calls ──────────────────────────────────────────────────────

def test_chained_tools():
    """Sequential tool calls: find_free_time → add_task → end_turn."""
    fake_find = MagicMock(return_value={"status": "ok", "summary": "🕐 Свободно 10:00–12:00"})
    fake_add = MagicMock(return_value={"status": "ok", "summary": "⚡ Добавил: йога"})
    with patch("anthropic.Anthropic") as mock_anthropic, \
         patch.dict(tools.TOOL_FUNCS, {"find_free_time": fake_find, "add_task": fake_add}):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [_tool_use_block("u1", "find_free_time", {"duration_minutes": 60})]),
            _claude_response("tool_use", [_tool_use_block("u2", "add_task", {"title": "йога", "time": "10:00"})]),
            _claude_response("end_turn", [_text_block("Готово!")]),
        ]
        result = agent.run_agent("найди час и запиши туда йогу")

    assert "Свободно 10:00–12:00" in result["text"]
    assert "Добавил: йога" in result["text"]


# ─── Parallel tool calls in one block ────────────────────────────────────────

def test_parallel_tools_one_response():
    """Claude returns multiple tool_use blocks in a single response."""
    fake_w = MagicMock(return_value={"status": "ok", "summary": "🌤 ясно"})
    fake_t = MagicMock(return_value={"status": "ok", "summary": "📋 1 задача"})
    with patch("anthropic.Anthropic") as mock_anthropic, \
         patch.dict(tools.TOOL_FUNCS, {"get_weather": fake_w, "show_tasks": fake_t}):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [
                _tool_use_block("u1", "get_weather", {}),
                _tool_use_block("u2", "show_tasks", {}),
            ]),
            _claude_response("end_turn", [_text_block("Вот сводка.")]),
        ]
        result = agent.run_agent("погода и задачи")

    assert "ясно" in result["text"]
    assert "1 задача" in result["text"]


# ─── Needs confirmation flow ─────────────────────────────────────────────────

def test_needs_confirmation_stops_loop():
    """Email tool returns needs_confirmation → loop stops, action emitted."""
    confirm_result = {
        "status": "needs_confirmation",
        "summary": "✉️ Готово к отправке. Отправить?",
        "data": {"kind": "email_preview"},
    }
    with patch("anthropic.Anthropic") as mock_anthropic, \
         patch.dict(tools.TOOL_FUNCS, {"send_email": MagicMock(return_value=confirm_result)}):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [_tool_use_block("u1", "send_email",
                {"to_email": "a@b.com", "subject": "Hi", "body": "Hello"})]),
        ]
        result = agent.run_agent("напиши письмо")

    assert any(a.get("action") == "needs_confirmation" for a in result["actions"])
    assert "Отправить?" in result["text"]


# ─── Action signal: send_digest ──────────────────────────────────────────────

def test_get_digest_emits_action():
    """get_digest tool result includes action=send_digest signal."""
    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [_tool_use_block("u1", "get_digest", {})]),
            _claude_response("end_turn", []),
        ]
        result = agent.run_agent("дай дайджест")

    assert any(a.get("action") == "send_digest" for a in result["actions"])


# ─── Safety cap ──────────────────────────────────────────────────────────────

def test_max_iterations_cap():
    """If Claude never returns end_turn, loop stops at MAX_ITERATIONS."""
    fake = MagicMock(return_value={"status": "ok", "summary": "🌤"})
    with patch("anthropic.Anthropic") as mock_anthropic, \
         patch.dict(tools.TOOL_FUNCS, {"get_weather": fake}):
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.return_value = _claude_response(
            "tool_use", [_tool_use_block("u1", "get_weather", {})]
        )
        result = agent.run_agent("loop forever")

    assert mock_client.messages.create.call_count == agent.MAX_ITERATIONS


# ─── Unknown tool handled ────────────────────────────────────────────────────

def test_unknown_tool_recorded_as_error():
    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = [
            _claude_response("tool_use", [_tool_use_block("u1", "does_not_exist", {})]),
            _claude_response("end_turn", [_text_block("Не нашёл инструмент.")]),
        ]
        result = agent.run_agent("???")

    # No crash; loop completes
    assert "Не нашёл" in result["text"]


# ─── API error handled gracefully ────────────────────────────────────────────

def test_api_failure_returns_fallback():
    with patch("anthropic.Anthropic") as mock_anthropic:
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("network error")
        result = agent.run_agent("hi")

    assert "не получилось" in result["text"].lower() or "попробуй" in result["text"].lower()
    assert result["actions"] == []


# ─── is_enabled feature flag ─────────────────────────────────────────────────

def test_is_enabled_env_var(monkeypatch):
    monkeypatch.delenv("USE_AGENT", raising=False)
    assert agent.is_enabled() is False
    monkeypatch.setenv("USE_AGENT", "true")
    assert agent.is_enabled() is True
    monkeypatch.setenv("USE_AGENT", "false")
    assert agent.is_enabled() is False


# ─── Personality loaded ──────────────────────────────────────────────────────

def test_personality_in_system_prompt():
    persona = agent._load_personality()
    assert "warmth" in persona
    prompt = agent._build_system_prompt(persona, [], [])
    assert "warmth=" in prompt
    assert "humor=" in prompt


# ─── Tool schemas valid ──────────────────────────────────────────────────────

def test_all_tool_schemas_have_required_fields():
    for schema in tools.TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"
        assert schema["name"] in tools.TOOL_FUNCS


# ─── Individual tool wrappers smoke ──────────────────────────────────────────

def test_show_tasks_with_data():
    result = tools.show_tasks(_active_tasks=[
        {"type": "short", "title": "Test", "due": "2026-05-18", "time": "10:00", "end_time": "11:00"},
    ])
    assert result["status"] == "ok"
    assert "Test" in result["summary"]


def test_show_tasks_empty():
    result = tools.show_tasks(_active_tasks=[])
    assert result["status"] == "ok"
    assert "нет" in result["summary"].lower()


def test_complete_task_invalid_number():
    result = tools.complete_task(task_number=99, _active_tasks=[])
    assert result["status"] == "error"


def test_send_to_alice_empty():
    result = tools.send_to_alice(message="")
    assert result["status"] == "error"


def test_get_weather_success():
    with patch("weather_client.get_weather", return_value="Нешер: ясно"):
        result = tools.get_weather()
    assert result["status"] == "ok"
    assert "ясно" in result["summary"]
