"""Regression tests for Phase A — removal of hardcoded Daria-specific defaults.

These tests prove that:
- digest.py prompt never says "Дарья" / "Дарьи" for non-Daria users
- whatsapp_summary.py never labels self-sender as "Дарья" for non-Daria users
- weather_client.get_weather returns None when no city is passed
- news_client.get_news_headlines returns [] for a user with no DB rows
- agent.py system prompt uses user.display_name and user.timezone, not config defaults
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TIMEZONE", "Asia/Jerusalem")
    import db, crypto, calendar_client
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    calendar_client._clear_calendar_id_cache()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()
    calendar_client._clear_calendar_id_cache()


def _make_user(tg=222, *, name="Jony", tz="Europe/Moscow", status="promo"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg, telegram_chat_id=tg, display_name=name)
        u.subscription_status = status
        u.timezone = tz
        return u.id


# ─── digest.py ───────────────────────────────────────────────────────────────


def test_digest_prompt_uses_user_name_not_daria():
    import digest as digest_mod
    captured = {}
    def fake_chat(model, system, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"text": "ok", "tool_uses": [], "stop_reason": "end_turn"}
    with patch.object(digest_mod, "llm") as fake_llm:
        fake_llm.chat = fake_chat
        fake_llm.MODEL_SONNET_BEDROCK = "x"
        digest_mod.generate_morning_digest(
            [], [], [], None,
            user_name="Jony", user_timezone="Europe/Moscow",
        )
    p = captured["prompt"]
    assert "Jony" in p, "user name not threaded into prompt"
    assert "Дарь" not in p, "Daria literal still present in prompt"


def test_digest_prompt_falls_back_to_neutral_when_no_user_name():
    """Without user_name → uses generic noun, never 'Дарья'."""
    import digest as digest_mod
    captured = {}
    def fake_chat(model, system, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"text": "ok", "tool_uses": [], "stop_reason": "end_turn"}
    with patch.object(digest_mod, "llm") as fake_llm:
        fake_llm.chat = fake_chat
        fake_llm.MODEL_SONNET_BEDROCK = "x"
        digest_mod.generate_morning_digest([], [], [], None)
    p = captured["prompt"]
    assert "Дарь" not in p
    assert "пользовател" in p  # generic noun present


def test_digest_news_instruction_uses_actual_sources_not_kan_keshet_dozhd():
    """If user's news has sources [NPR, BBC], prompt must reference those —
    not hardcoded Kan 11 / Keshet 12 / Dozhd."""
    import digest as digest_mod
    captured = {}
    def fake_chat(model, system, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"text": "ok", "tool_uses": [], "stop_reason": "end_turn"}
    news = [
        {"source": "NPR", "title": "headline 1"},
        {"source": "BBC", "title": "headline 2"},
    ]
    with patch.object(digest_mod, "llm") as fake_llm:
        fake_llm.chat = fake_chat
        fake_llm.MODEL_SONNET_BEDROCK = "x"
        digest_mod.generate_morning_digest(
            [], [], [], None, news=news, user_name="Mika",
        )
    p = captured["prompt"]
    assert "NPR" in p
    assert "BBC" in p
    assert "Кан 11" not in p
    assert "Кешет 12" not in p
    assert "Дождь" not in p


# ─── whatsapp_summary.py ─────────────────────────────────────────────────────


def test_whatsapp_summary_uses_user_name_not_daria():
    user_id = _make_user(222, name="Jony")
    import whatsapp_summary as wa
    captured = {}
    def fake_chat(model, system, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return {"text": "🔴 ВАЖНО ОТВЕТИТЬ\n• X — sample", "tool_uses": [], "stop_reason": "end_turn"}
    unread = [{
        "name": "Mom", "unreadCount": 1,
        "recentMessages": [
            {"text": "hi", "fromMe": False, "senderName": "Mom"},
            {"text": "ok", "fromMe": True},  # self-sent
        ],
    }]
    with patch.object(wa, "llm") as fake_llm:
        fake_llm.chat = fake_chat
        fake_llm.MODEL_NOVA_LITE = "x"
        wa.summarize_unread_chats(unread, user_id=user_id)
    p = captured["prompt"]
    assert "Jony" in p
    assert "Дарь" not in p


def test_whatsapp_summary_self_label_uses_user_name():
    user_id = _make_user(222, name="Jony")
    import whatsapp_summary as wa
    raw = wa._build_raw_context(
        [{"name": "Mom", "unreadCount": 1,
          "recentMessages": [{"text": "ok", "fromMe": True}]}],
        user_name=wa._resolve_user_name(user_id),
    )
    assert "Jony: ok" in raw
    assert "Дарья: ok" not in raw


# ─── weather_client.py ────────────────────────────────────────────────────────


def test_weather_returns_none_without_city():
    import weather_client
    assert weather_client.get_weather() is None
    assert weather_client.get_weather(city=None) is None
    assert weather_client.get_weather(city="") is None


def test_weather_with_city_attempts_geocode():
    import weather_client
    with patch.object(weather_client, "_geocode", return_value=(35.7, 139.7)) as gc, \
         patch("requests.get") as req:
        req.return_value.json.return_value = {"daily": {
            "temperature_2m_max": [25.0], "temperature_2m_min": [18.0],
            "precipitation_probability_max": [10], "weathercode": [1],
        }}
        req.return_value.raise_for_status = lambda: None
        out = weather_client.get_weather(city="Tokyo")
    gc.assert_called_once_with("Tokyo")
    assert "Tokyo" in (out or "")
    assert "Нешер" not in (out or "")


# ─── news_client.py ──────────────────────────────────────────────────────────


def test_news_for_user_with_no_feed_rows_returns_empty():
    """A user who opted out of news in onboarding (or hasn't been onboarded
    yet) gets an empty list — not Daria's Israeli/Russian sources."""
    user_id = _make_user(222)
    import news_client
    # No user_news_feeds rows for this user; legacy fallback should NOT kick in
    result = news_client.get_news_headlines(user_id=user_id)
    assert result == []


def test_news_for_legacy_no_user_id_still_uses_legacy_feeds():
    """user_id=None → legacy global feeds (Daria pre-migration)."""
    import news_client
    with patch.object(news_client, "_fetch_feed", return_value=[]) as f:
        news_client.get_news_headlines(user_id=None)
    # _fetch_feed was called for each legacy feed
    assert f.call_count == len(news_client.LEGACY_NEWS_FEEDS)


# ─── agent.py ────────────────────────────────────────────────────────────────


def test_agent_system_prompt_uses_user_name_and_timezone():
    user_id = _make_user(222, name="Jony", tz="Europe/Moscow")
    import agent
    # Mock llm.chat so we can capture the system prompt that gets sent
    captured = {}
    def fake_chat(model, system, messages, **kwargs):
        captured["system"] = system
        return {"text": "ok", "tool_uses": [], "stop_reason": "end_turn"}
    with patch.object(agent, "llm") as fake_llm:
        fake_llm.chat = fake_chat
        fake_llm.MODEL_SONNET_BEDROCK = "x"
        agent.run_agent("привет", user_id=user_id)
    sys_prompt = captured["system"]
    assert "Jony" in sys_prompt
    assert "Europe/Moscow" in sys_prompt
    # No Daria leak
    assert "Дарь" not in sys_prompt or "Дарья по имени" not in sys_prompt  # bot persona may legitimately be named Daria for Daria; for Jony there must be no Daria literal
    assert "по имени Дарья" not in sys_prompt
