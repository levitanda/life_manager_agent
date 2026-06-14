"""Tests for digest.py."""

import datetime
from unittest.mock import patch, MagicMock

import pytest

import digest as digest_module


SAMPLE_EVENTS = [{"title": "Standup", "time": "09:00"}]
SAMPLE_SHORT = [{"title": "Fix bug", "due": "2026-05-18"}]
SAMPLE_LONG = [{"title": "Write book", "due": "2026-12-31"}]
SAMPLE_NEWS = [
    {"source": "Кан 11", "title": "News A"},
    {"source": "Дождь", "title": "News B"},
]
SAMPLE_BIRTHDAYS = [{"name": "Alice", "email": "alice@example.com"}]


def _llm_result(text: str = "Digest text") -> dict:
    return {"text": text, "tool_uses": [], "stop_reason": "end_turn", "raw": {}}


def _prompt_from_mock(mock_chat) -> str:
    """Extract the user-message content from the latest llm.chat call."""
    args, kwargs = mock_chat.call_args
    # signature: chat(model, system, messages, ...)
    if "messages" in kwargs:
        messages = kwargs["messages"]
    else:
        messages = args[2]
    return messages[0]["content"]


def test_generate_morning_digest_basic():
    with patch("llm.chat", return_value=_llm_result("Good morning!")):
        result = digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None
        )
    assert result == "Good morning!"


def test_generate_morning_digest_with_news():
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, news=SAMPLE_NEWS
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "Кан 11" in prompt
    assert "News A" in prompt


def test_generate_morning_digest_with_birthdays():
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, birthdays=SAMPLE_BIRTHDAYS
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "Alice" in prompt


def test_generate_morning_digest_no_news_no_birthdays():
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, news=None, birthdays=None
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "НОВОСТИ" not in prompt


def test_generate_morning_digest_with_recent_messages():
    """Recent conversation history should be injected into the prompt."""
    messages = [
        {"role": "user", "content": "сегодня плохо себя чувствую, голова болит"},
        {"role": "assistant", "content": "понимаю, постарайся отдохнуть"},
    ]
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None,
            recent_messages=messages,
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "голова болит" in prompt
    assert "НЕДАВНИЙ РАЗГОВОР" in prompt


def test_generate_morning_digest_with_whatsapp_summary():
    """Pre-built WhatsApp summary should be inserted verbatim into the prompt."""
    summary = "🔴 ВАЖНО ОТВЕТИТЬ\n• Семья — мама спрашивает про планы на выходные"
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None,
            whatsapp_summary=summary,
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "WHATSAPP" in prompt
    assert "мама спрашивает про планы" in prompt
    assert "ВАЖНО ОТВЕТИТЬ" in prompt


def test_generate_morning_digest_without_whatsapp_summary():
    """Empty/None summary should not add a WhatsApp section to the prompt."""
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None,
            whatsapp_summary=None,
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "WHATSAPP (готовая сводка" not in prompt


def test_generate_morning_digest_with_session_summaries():
    """Long-term session summaries should be in the prompt."""
    sums = [{"date": "2026-05-15 21:00", "summary": "Обсуждали проект Х, переживала из-за дедлайна"}]
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None,
            summaries=sums,
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "проект Х" in prompt
    assert "ДОЛГОСРОЧНАЯ ПАМЯТЬ" in prompt


def test_format_history_truncates_long_messages():
    long_msg = "x" * 1000
    result = digest_module._format_history([{"role": "user", "content": long_msg}])
    assert len(result) < 400  # truncated to 300 chars + role prefix


def test_format_history_empty():
    assert digest_module._format_history([]) == ""


def test_generate_morning_digest_with_weather():
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, weather="ясно, 25°C"
        )
        prompt = _prompt_from_mock(mock_chat)
    assert "25°C" in prompt


def test_generate_weekly_digest():
    week_events = {
        "2026-05-18": [{"title": "Standup", "time": "09:00"}],
        "2026-05-20": [{"title": "Retro", "time": "15:00"}],
    }
    with patch("llm.chat", return_value=_llm_result("Weekly overview")):
        result = digest_module.generate_weekly_digest(week_events, SAMPLE_SHORT, SAMPLE_LONG)
    assert result == "Weekly overview"


def test_generate_weekly_digest_empty_week():
    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_weekly_digest({}, [], [])
        prompt = _prompt_from_mock(mock_chat)
    assert "Событий не запланировано" in prompt


def test_format_news_helper():
    news = [
        {"source": "Кан 11", "title": "A"},
        {"source": "Кан 11", "title": "B"},
        {"source": "Дождь", "title": "C"},
    ]
    result = digest_module._format_news(news)
    assert "Кан 11" in result
    assert "A" in result
    assert "C" in result


def test_format_news_empty():
    assert digest_module._format_news([]) == "Новости недоступны."


def test_format_birthdays_helper():
    birthdays = [{"name": "Alice"}, {"name": "Bob"}]
    result = digest_module._format_birthdays(birthdays)
    assert "Alice" in result
    assert "Bob" in result


def test_format_birthdays_empty():
    assert digest_module._format_birthdays([]) == ""
