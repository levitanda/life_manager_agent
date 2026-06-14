"""Tests for the heartbeat scheduler — per-user dispatch by local time."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _add_active(tg=100, status="active", tz="Asia/Jerusalem", morning="06:30", evening="21:30"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg, telegram_chat_id=tg)
        u.subscription_status = status
        u.timezone = tz
        u.morning_time = morning
        u.evening_time = evening
        return u.id


# ─── _active_users ───────────────────────────────────────────────────────────


def test_active_users_skips_inactive():
    _add_active(100, status="active")
    _add_active(200, status="inactive")
    _add_active(300, status="promo")
    import scheduler
    users = scheduler._active_users()
    ids = {u["id"] for u in users}
    assert len(users) == 2
    # inactive user excluded
    assert 200 not in {u["telegram_chat_id"] for u in users}


# ─── _hhmm_in_tz ─────────────────────────────────────────────────────────────


def test_hhmm_in_tz_jerusalem():
    import scheduler
    user = {"timezone": "Asia/Jerusalem"}
    # UTC 03:30 = Jerusalem 06:30 (winter) or 06:30 (summer offset different but APScheduler handles DST)
    utc = datetime.datetime(2026, 6, 14, 3, 30, tzinfo=datetime.timezone.utc)
    # Jerusalem is UTC+3 in summer
    out = scheduler._hhmm_in_tz(user, utc)
    assert out == "06:30"


def test_hhmm_in_tz_moscow():
    import scheduler
    user = {"timezone": "Europe/Moscow"}
    utc = datetime.datetime(2026, 6, 14, 3, 30, tzinfo=datetime.timezone.utc)
    out = scheduler._hhmm_in_tz(user, utc)
    assert out == "06:30"  # Moscow is UTC+3


# ─── _heartbeat dispatch ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heartbeat_fires_morning_at_user_local_time():
    user_id = _add_active(100, tz="Asia/Jerusalem", morning="06:30")
    import scheduler

    app = MagicMock()
    morning_called = []
    async def fake_morning(a, target_user_id=None):
        morning_called.append(target_user_id)
    evening_called = []
    async def fake_evening(a, target_user_id=None):
        evening_called.append(target_user_id)

    with patch("bot_handlers._send_morning_digest", side_effect=fake_morning), \
         patch("bot_handlers._send_evening_checkin", side_effect=fake_evening), \
         patch.object(scheduler, "datetime", wraps=datetime) as dt_mock:
        # Force "now" to UTC 03:30 (Jerusalem 06:30)
        fake_now = datetime.datetime(2026, 6, 14, 3, 30, tzinfo=datetime.timezone.utc)
        with patch("scheduler.datetime") as fake_dt:
            fake_dt.datetime.now.return_value = fake_now
            fake_dt.timezone = datetime.timezone
            await scheduler._heartbeat(app)

    assert morning_called == [user_id]
    assert evening_called == []


@pytest.mark.asyncio
async def test_heartbeat_fires_evening_at_user_local_time():
    user_id = _add_active(100, tz="Europe/Moscow", evening="21:30")
    import scheduler
    app = MagicMock()
    e_called = []
    async def fake_e(a, target_user_id=None):
        e_called.append(target_user_id)
    m_called = []
    async def fake_m(a, target_user_id=None):
        m_called.append(target_user_id)

    fake_now = datetime.datetime(2026, 6, 14, 18, 30, tzinfo=datetime.timezone.utc)  # Moscow 21:30
    with patch("bot_handlers._send_morning_digest", side_effect=fake_m), \
         patch("bot_handlers._send_evening_checkin", side_effect=fake_e), \
         patch("scheduler.datetime") as fake_dt:
        fake_dt.datetime.now.return_value = fake_now
        fake_dt.timezone = datetime.timezone
        await scheduler._heartbeat(app)

    assert e_called == [user_id]
    assert m_called == []


@pytest.mark.asyncio
async def test_heartbeat_skips_users_not_matching_time():
    # User in Tokyo with morning 06:30 — at UTC 03:30 it's Tokyo 12:30, no fire
    _add_active(100, tz="Asia/Tokyo", morning="06:30")
    import scheduler
    fired = []
    async def fake(a, target_user_id=None):
        fired.append(target_user_id)

    fake_now = datetime.datetime(2026, 6, 14, 3, 30, tzinfo=datetime.timezone.utc)
    with patch("bot_handlers._send_morning_digest", side_effect=fake), \
         patch("bot_handlers._send_evening_checkin", side_effect=fake), \
         patch("scheduler.datetime") as fake_dt:
        fake_dt.datetime.now.return_value = fake_now
        fake_dt.timezone = datetime.timezone
        await scheduler._heartbeat(MagicMock())

    assert fired == []


@pytest.mark.asyncio
async def test_heartbeat_falls_back_when_legacy_signature():
    """If _send_morning_digest still has only (app) signature, heartbeat should
    catch the TypeError and call without target_user_id."""
    _add_active(100, tz="Asia/Jerusalem", morning="06:30")
    import scheduler
    legacy_calls = []
    async def legacy(a):  # no target_user_id kwarg accepted
        legacy_calls.append("called")
    async def evening_legacy(a):
        pass

    fake_now = datetime.datetime(2026, 6, 14, 3, 30, tzinfo=datetime.timezone.utc)
    with patch("bot_handlers._send_morning_digest", side_effect=legacy), \
         patch("bot_handlers._send_evening_checkin", side_effect=evening_legacy), \
         patch("scheduler.datetime") as fake_dt:
        fake_dt.datetime.now.return_value = fake_now
        fake_dt.timezone = datetime.timezone
        await scheduler._heartbeat(MagicMock())

    assert legacy_calls == ["called"]


# ─── setup_scheduler chooses correct mode ────────────────────────────────────


def test_setup_uses_heartbeat_when_users_exist():
    _add_active(100)
    import scheduler
    app = MagicMock()
    sched = scheduler.setup_scheduler(app)
    job_ids = {j.id for j in sched.get_jobs()}
    assert "heartbeat" in job_ids
    assert "morning_digest" not in job_ids


def test_setup_uses_legacy_when_no_users():
    import scheduler
    app = MagicMock()
    sched = scheduler.setup_scheduler(app)
    job_ids = {j.id for j in sched.get_jobs()}
    assert "morning_digest" in job_ids
    assert "evening_checkin" in job_ids
    assert "daily_summary" in job_ids
    assert "heartbeat" not in job_ids
