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


def _mock_claude(text: str = "Digest text"):
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text=text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg
    return mock_client


def test_generate_morning_digest_basic():
    with patch("anthropic.Anthropic", return_value=_mock_claude("Good morning!")):
        result = digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None
        )
    assert result == "Good morning!"


def test_generate_morning_digest_with_news():
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = _mock_claude()
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, news=SAMPLE_NEWS
        )
        prompt = mock_cls.return_value.messages.create.call_args[1]["messages"][0]["content"]
    assert "Кан 11" in prompt
    assert "News A" in prompt


def test_generate_morning_digest_with_birthdays():
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = _mock_claude()
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, birthdays=SAMPLE_BIRTHDAYS
        )
        prompt = mock_cls.return_value.messages.create.call_args[1]["messages"][0]["content"]
    assert "Alice" in prompt


def test_generate_morning_digest_no_news_no_birthdays():
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = _mock_claude()
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, news=None, birthdays=None
        )
        prompt = mock_cls.return_value.messages.create.call_args[1]["messages"][0]["content"]
    assert "НОВОСТИ" not in prompt


def test_generate_morning_digest_with_weather():
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = _mock_claude()
        digest_module.generate_morning_digest(
            SAMPLE_EVENTS, SAMPLE_SHORT, SAMPLE_LONG, None, weather="ясно, 25°C"
        )
        prompt = mock_cls.return_value.messages.create.call_args[1]["messages"][0]["content"]
    assert "25°C" in prompt


def test_generate_weekly_digest():
    week_events = {
        "2026-05-18": [{"title": "Standup", "time": "09:00"}],
        "2026-05-20": [{"title": "Retro", "time": "15:00"}],
    }
    with patch("anthropic.Anthropic", return_value=_mock_claude("Weekly overview")):
        result = digest_module.generate_weekly_digest(week_events, SAMPLE_SHORT, SAMPLE_LONG)
    assert result == "Weekly overview"


def test_generate_weekly_digest_empty_week():
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value = _mock_claude()
        digest_module.generate_weekly_digest({}, [], [])
        prompt = mock_cls.return_value.messages.create.call_args[1]["messages"][0]["content"]
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
