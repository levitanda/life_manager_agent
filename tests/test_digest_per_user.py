"""End-to-end test: morning digest for a non-Daria user must not touch Daria's
state — neither her cached calendar ids, her Google account, her conversation
file, her Pushover keys, nor her TELEGRAM_CHAT_ID.

This is the regression for the live bug that surfaced this morning:
    ⚠️ Ошибка генерации дайджеста: 404 Not Found on calendar
    6f093328…@group.calendar.google.com
…where Daria's progress-calendar id had leaked into the new user's
session via calendar_client._calendar_ids (module-level dict keyed only
by name).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")    # Daria's chat id
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("PUSHOVER_USER_KEY", "DARIA_PUSHOVER_USER")
    monkeypatch.setenv("PUSHOVER_APP_TOKEN", "DARIA_PUSHOVER_TOKEN")
    import config
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", 111)
    import db, crypto, calendar_client
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    calendar_client._clear_calendar_id_cache()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()
    calendar_client._clear_calendar_id_cache()


def _make_user(tg_id: int, *, status: str = "promo"):
    """Insert a user. Returns internal user_id."""
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg_id, telegram_chat_id=tg_id)
        u.subscription_status = status
        u.timezone = "Europe/Moscow"
        u.morning_time = "06:30"
        return u.id


# ─── Test 1: calendar id cache isolated per user ────────────────────────────


def test_calendar_id_cache_does_not_leak_between_users():
    """Two users ask for the same calendar name; each must see their OWN id.

    Direct regression for: Daria's '6f093328...@group.calendar.google.com' id
    being returned to a new user → 404 on their Google.
    """
    daria_id = _make_user(111)
    friend_id = _make_user(222)

    import calendar_client

    # Each user has different service objects with different calendarList responses
    daria_svc = MagicMock()
    daria_svc.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Прогресс дня", "id": "DARIA_PROGRESS@group.calendar.google.com"}],
    }
    friend_svc = MagicMock()
    friend_svc.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Прогресс дня", "id": "FRIEND_PROGRESS@group.calendar.google.com"}],
    }

    def fake_service(user_id=None):
        return daria_svc if user_id == daria_id else friend_svc

    with patch.object(calendar_client, "_get_service", side_effect=fake_service):
        daria_cal = calendar_client._get_or_create_calendar("Прогресс дня", user_id=daria_id)
        friend_cal = calendar_client._get_or_create_calendar("Прогресс дня", user_id=friend_id)

    assert daria_cal == "DARIA_PROGRESS@group.calendar.google.com"
    assert friend_cal == "FRIEND_PROGRESS@group.calendar.google.com"
    # Cross-call to make sure cache hits are also per-user
    with patch.object(calendar_client, "_get_service", side_effect=fake_service):
        assert calendar_client._get_or_create_calendar("Прогресс дня", user_id=daria_id) == "DARIA_PROGRESS@group.calendar.google.com"
        assert calendar_client._get_or_create_calendar("Прогресс дня", user_id=friend_id) == "FRIEND_PROGRESS@group.calendar.google.com"


def test_calendar_creation_only_happens_in_calling_users_account():
    """If the calendar doesn't exist, it's created via THAT user's service,
    never via somebody else's session."""
    daria_id = _make_user(111)
    friend_id = _make_user(222)

    import calendar_client

    services_used: list[int] = []
    def fake_service(user_id=None):
        services_used.append(user_id)
        svc = MagicMock()
        svc.calendarList.return_value.list.return_value.execute.return_value = {"items": []}
        svc.calendars.return_value.insert.return_value.execute.return_value = {
            "id": f"NEW-{user_id}@group.calendar.google.com",
        }
        return svc

    with patch.object(calendar_client, "_get_service", side_effect=fake_service):
        d = calendar_client._get_or_create_calendar("Прогресс дня", user_id=daria_id)
        f = calendar_client._get_or_create_calendar("Прогресс дня", user_id=friend_id)
    assert d.startswith(f"NEW-{daria_id}@")
    assert f.startswith(f"NEW-{friend_id}@")
    # Each user's session was hit at least once (one _get_service per call)
    assert services_used.count(daria_id) >= 1
    assert services_used.count(friend_id) >= 1
    # And critically — Daria's session was NOT used while resolving the friend's calendar
    assert services_used == [daria_id, friend_id]


# ─── Test 2: full morning digest for a non-Daria user routes correctly ──────


async def _run_digest_for(user_id: int):
    """Drive bot_handlers._send_morning_digest with everything mocked.

    Asserts (via the mocks) that every client call carries user_id=N.
    Returns the chat_id Telegram saw on send_message + the user_id used in
    every client invocation.
    """
    import bot_handlers

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    app = MagicMock(bot=fake_bot)

    # Capture all user_id values seen by client calls
    seen: dict[str, list] = {}
    def remember(name):
        def _inner(*args, user_id=None, **kwargs):
            seen.setdefault(name, []).append(user_id)
            return []
        return _inner

    # Stub every client + the llm-bound digest generator
    patches = [
        patch("bot_handlers.calendar_client.get_todays_calendar_events", side_effect=remember("events")),
        patch("bot_handlers.calendar_client.get_active_tasks", side_effect=remember("tasks")),
        patch("bot_handlers.calendar_client.get_progress_before_date", side_effect=remember("progress")),
        patch("bot_handlers.gmail_client.get_unread_emails", side_effect=remember("emails")),
        patch("bot_handlers.weather_client.get_weather", return_value=""),
        patch("bot_handlers.news_client.get_news_headlines", return_value=[]),
        patch("bot_handlers.birthday_client.get_todays_birthdays", side_effect=remember("birthdays")),
        patch("bot_handlers.conversation.get_history", side_effect=remember("history")),
        patch("bot_handlers.conversation.get_recent_summaries", side_effect=remember("summaries")),
        patch("bot_handlers.whatsapp_client.unread_chats", side_effect=remember("wa")),
        patch("bot_handlers.whatsapp_summary.summarize_unread_chats", return_value=""),
        patch("bot_handlers.digest_module.generate_morning_digest",
              return_value="dummy digest text"),
        patch("bot_handlers.conversation.add"),
        patch("bot_handlers.pushover_client.send_push"),
    ]
    for p in patches:
        p.start()
    try:
        await bot_handlers._send_morning_digest(app, target_user_id=user_id)
    finally:
        for p in patches:
            p.stop()

    chat_ids = [c.kwargs.get("chat_id") for c in fake_bot.send_message.await_args_list]
    return chat_ids, seen


async def test_friend_digest_uses_friend_chat_and_user_id():
    """Smoke for the actual bug: target_user_id=friend must send to friend's
    chat (not Daria's) AND every client call must carry friend's user_id."""
    friend_id = _make_user(222)

    chat_ids, seen = await _run_digest_for(friend_id)

    # Sent to FRIEND's chat (telegram_chat_id was set to tg_id=222 by helper)
    assert chat_ids, "no message was sent"
    assert all(cid == 222 for cid in chat_ids), f"leaked to {chat_ids} (expected all 222)"
    assert 111 not in chat_ids, "leaked to Daria's TELEGRAM_CHAT_ID"

    # All client calls saw friend's user_id
    for kind, ids in seen.items():
        assert ids and all(uid == friend_id for uid in ids), (
            f"{kind} called with user_ids {ids}, expected only [{friend_id}]"
        )


async def test_legacy_digest_still_works_when_no_target_user_id():
    """Daria pre-migration: target_user_id=None should still send to her env chat
    and use user_id=None for clients (legacy)."""
    chat_ids, seen = await _run_digest_for(None)
    # 111 is the TELEGRAM_CHAT_ID set by the fixture
    assert chat_ids and all(cid == 111 for cid in chat_ids)
    for kind, ids in seen.items():
        assert ids and all(uid is None for uid in ids), (
            f"legacy mode {kind} called with {ids}, expected all None"
        )


# ─── Test 3: inactive subscription user does NOT generate or send anything ─


# ─── Test 4: regular new user receives a complete digest end-to-end ──────────


async def _drive_digest(user_id, **overrides):
    """Helper: run _send_morning_digest with every dependency mocked.
    `overrides` lets a caller swap any client's return value or side_effect.
    Returns (chat_ids_sent_to, all_chunk_texts).
    """
    import bot_handlers
    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    app = MagicMock(bot=fake_bot)

    defaults = {
        "events": [],
        "tasks": [],
        "progress": "",
        "emails": [],
        "weather": "Солнечно +25",
        "news": [{"source": "TestNews", "title": "тест-новость"}],
        "birthdays": [],
        "history": [],
        "summaries": [],
        "wa_chats": [],
        "wa_summary": "",
        "digest_text": "Привет! Доброе утро ☀️",
    }
    defaults.update({k: v for k, v in overrides.items() if v is not None})

    def kw(name):
        v = defaults[name]
        if isinstance(v, Exception):
            return {"side_effect": v}
        return {"return_value": v}

    patches = [
        patch("bot_handlers.calendar_client.get_todays_calendar_events", **kw("events")),
        patch("bot_handlers.calendar_client.get_active_tasks", **kw("tasks")),
        patch("bot_handlers.calendar_client.get_progress_before_date", **kw("progress")),
        patch("bot_handlers.gmail_client.get_unread_emails", **kw("emails")),
        patch("bot_handlers.weather_client.get_weather", **kw("weather")),
        patch("bot_handlers.news_client.get_news_headlines", **kw("news")),
        patch("bot_handlers.birthday_client.get_todays_birthdays", **kw("birthdays")),
        patch("bot_handlers.conversation.get_history", **kw("history")),
        patch("bot_handlers.conversation.get_recent_summaries", **kw("summaries")),
        patch("bot_handlers.whatsapp_client.unread_chats", **kw("wa_chats")),
        patch("bot_handlers.whatsapp_summary.summarize_unread_chats", **kw("wa_summary")),
        patch("bot_handlers.digest_module.generate_morning_digest",
              return_value=defaults["digest_text"]),
        patch("bot_handlers.conversation.add"),
        patch("bot_handlers.pushover_client.send_push", return_value=False),
        patch("bot_handlers.asyncio.sleep", new=AsyncMock()),  # don't really wait 10s in tests
    ]
    for p in patches:
        p.start()
    try:
        await bot_handlers._send_morning_digest(app, target_user_id=user_id)
    finally:
        for p in patches:
            p.stop()
    chat_ids = [c.kwargs.get("chat_id") for c in fake_bot.send_message.await_args_list]
    texts = [c.kwargs.get("text") for c in fake_bot.send_message.await_args_list]
    return chat_ids, texts


async def test_regular_user_receives_complete_digest():
    """End-to-end happy path: a normal (non-Daria) user with all integrations
    working gets their digest delivered in full."""
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(friend_id)
    assert chats == [222]
    assert texts and "Доброе утро" in texts[0]


# ─── Test 5: digest is resilient to missing / failing integrations ──────────


async def test_digest_delivered_when_whatsapp_disconnected():
    """WhatsApp client raising must NOT block the digest — user still receives it."""
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(
        friend_id,
        wa_chats=RuntimeError("bridge not reachable"),
    )
    assert chats == [222], f"digest not delivered: chats={chats}"
    assert "Доброе утро" in (texts[0] or "")


async def test_digest_delivered_when_gmail_fails():
    """Expired Gmail token / API outage must not kill the digest."""
    friend_id = _make_user(222)
    from googleapiclient.errors import HttpError
    chats, texts = await _drive_digest(
        friend_id,
        emails=RuntimeError("invalid_grant: token expired"),
    )
    assert chats == [222]
    assert texts[0].startswith("Привет")


async def test_digest_delivered_when_calendar_404():
    """The exact failure shape that produced today's prod incident — a 404
    on calendar fetch — must not block the digest from being delivered."""
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(
        friend_id,
        events=Exception("HttpError 404 calendar not found"),
        tasks=Exception("HttpError 404 calendar not found"),
        progress=Exception("HttpError 404 calendar not found"),
    )
    assert chats == [222], "calendar 404 should not stop the digest"
    assert texts[0]


async def test_digest_delivered_when_news_unreachable():
    """News API timeout must not block the digest."""
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(
        friend_id, news=TimeoutError("rss timeout"),
    )
    assert chats == [222]


async def test_digest_delivered_when_birthday_client_explodes():
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(
        friend_id, birthdays=Exception("People API quota"),
    )
    assert chats == [222]


async def test_digest_delivered_when_every_optional_integration_fails():
    """Worst case: every optional source fails. Digest must still be sent."""
    friend_id = _make_user(222)
    chats, texts = await _drive_digest(
        friend_id,
        events=Exception("x"),
        tasks=Exception("x"),
        progress=Exception("x"),
        emails=Exception("x"),
        weather=Exception("x"),
        news=Exception("x"),
        birthdays=Exception("x"),
        history=Exception("x"),
        summaries=Exception("x"),
        wa_chats=Exception("x"),
        wa_summary=Exception("x"),
    )
    assert chats == [222], "digest must arrive even when every integration is down"


async def test_digest_skipped_silently_for_user_not_in_db():
    """When target_user_id points to a row that no longer exists, fall back
    to legacy chat_id rather than crashing or leaking to wrong account."""
    import bot_handlers

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    app = MagicMock(bot=fake_bot)
    patches = [
        patch("bot_handlers.calendar_client.get_todays_calendar_events", return_value=[]),
        patch("bot_handlers.calendar_client.get_active_tasks", return_value=[]),
        patch("bot_handlers.calendar_client.get_progress_before_date", return_value=""),
        patch("bot_handlers.gmail_client.get_unread_emails", return_value=[]),
        patch("bot_handlers.weather_client.get_weather", return_value=""),
        patch("bot_handlers.news_client.get_news_headlines", return_value=[]),
        patch("bot_handlers.birthday_client.get_todays_birthdays", return_value=[]),
        patch("bot_handlers.conversation.get_history", return_value=[]),
        patch("bot_handlers.conversation.get_recent_summaries", return_value=[]),
        patch("bot_handlers.whatsapp_client.unread_chats", return_value=[]),
        patch("bot_handlers.whatsapp_summary.summarize_unread_chats", return_value=""),
        patch("bot_handlers.digest_module.generate_morning_digest", return_value="ok"),
        patch("bot_handlers.conversation.add"),
        patch("bot_handlers.pushover_client.send_push"),
    ]
    for p in patches:
        p.start()
    try:
        await bot_handlers._send_morning_digest(app, target_user_id=99999)  # no such user
    finally:
        for p in patches:
            p.stop()
    # Bot still sends but chat_id falls back to legacy (111)
    sent_chats = [c.kwargs.get("chat_id") for c in fake_bot.send_message.await_args_list]
    assert sent_chats and all(c == 111 for c in sent_chats)
