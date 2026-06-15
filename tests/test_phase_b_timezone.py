"""Phase B regression tests — every per-user code path must resolve timezone
from User.timezone (DB), not the global config.TIMEZONE default.

The single-user legacy path (user_id=None) must still honor config.TIMEZONE.
"""

from __future__ import annotations

import datetime
import json
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
    import config
    monkeypatch.setattr(config, "TIMEZONE", "Asia/Jerusalem")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _make_user(tg=222, *, tz="Europe/Moscow", name="Jony"):
    import db
    with db.session_scope() as s:
        u = db.create_user(
            s,
            telegram_user_id=tg,
            telegram_chat_id=tg,
            display_name=name,
            timezone=tz,
        )
        u.subscription_status = "promo"
        return u.id


# ─── conversation.py ─────────────────────────────────────────────────────────


def test_conversation_summary_uses_user_timezone():
    """When a user in Asia/Tokyo triggers a session summary, the saved
    `date` field must be rendered in Tokyo TZ — not the global default."""
    user_id = _make_user(tz="Asia/Tokyo")
    import conversation

    # Build a session_ts at exactly 2026-06-14 00:30 UTC.
    # In Tokyo that's 09:30 the same day; in Asia/Jerusalem (config default)
    # it would be 03:30. The strftime output disambiguates.
    session_ts = "2026-06-14T00:30:00+00:00"

    # Stub the LLM call so we don't hit Bedrock.
    fake_result = {"text": "summary text"}
    with patch.object(conversation.llm, "chat", return_value=fake_result):
        conversation._summarize_and_save(
            [{"role": "user", "content": "hi"}],
            session_ts,
            user_id=user_id,
        )

    # Read the saved summary file
    summaries = conversation.get_recent_summaries(user_id=user_id)
    assert len(summaries) == 1
    saved_date = summaries[0]["date"]
    # 00:30 UTC → 09:30 Tokyo (UTC+9). NOT 03:30 (Asia/Jerusalem UTC+3).
    assert saved_date == "2026-06-14 09:30", (
        f"Expected Tokyo time '2026-06-14 09:30', got {saved_date!r}"
    )


def test_conversation_add_timestamp_uses_user_timezone():
    """conversation.add() stamps last_ts with the user's local timezone."""
    user_id = _make_user(tz="Asia/Tokyo")
    import conversation
    conversation.add("hello", "world", user_id=user_id)

    # Read raw history file
    import os
    path = conversation._history_path(user_id)
    assert os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    last_ts = data["session"]["last_ts"]

    # Parse the offset suffix — Tokyo is +09:00
    assert last_ts.endswith("+09:00"), f"Expected Tokyo offset, got {last_ts!r}"


# ─── scheduled_actions.py ────────────────────────────────────────────────────


def test_scheduled_action_cron_uses_user_timezone():
    """schedule_action with repeat='daily' for a Moscow user → the cron job
    is registered with timezone='Europe/Moscow' (not Asia/Jerusalem)."""
    user_id = _make_user(tz="Europe/Moscow")
    import scheduled_actions

    fake_sched = MagicMock()
    scheduled_actions._scheduler = fake_sched
    scheduled_actions._bot_app = MagicMock()
    try:
        run_at = datetime.datetime(2030, 1, 1, 22, 0)  # naive
        scheduled_actions.schedule_action(
            "выключи свет",
            run_at,
            repeat="daily",
            user_id=user_id,
        )
        fake_sched.add_job.assert_called_once()
        # Pull the trigger out of the call and inspect its timezone
        _, kwargs = fake_sched.add_job.call_args
        trigger = kwargs.get("trigger")
        # CronTrigger stores its timezone in trigger.timezone
        tz_repr = str(trigger.timezone)
        assert "Europe/Moscow" in tz_repr, (
            f"Cron trigger timezone leaked: {tz_repr!r}"
        )
    finally:
        scheduled_actions._scheduler = None
        scheduled_actions._bot_app = None


def test_scheduled_action_run_at_uses_user_timezone():
    """A naive run_at passed for a Moscow user must be localized to Moscow,
    not Asia/Jerusalem."""
    user_id = _make_user(tz="Europe/Moscow")
    import scheduled_actions

    fake_sched = MagicMock()
    scheduled_actions._scheduler = fake_sched
    scheduled_actions._bot_app = MagicMock()
    try:
        run_at = datetime.datetime(2030, 1, 1, 12, 0)  # naive
        action = scheduled_actions.schedule_action(
            "включи свет",
            run_at,
            user_id=user_id,
        )
        # Persisted run_at should now carry the Moscow offset, not Jerusalem.
        # Moscow is UTC+3 (no DST since 2014); Jerusalem is +02:00/+03:00.
        # In Jan 2030 Jerusalem = +02:00, Moscow = +03:00 → distinguishable.
        assert action["run_at"].endswith("+03:00"), (
            f"Expected Moscow offset +03:00, got {action['run_at']!r}"
        )
    finally:
        scheduled_actions._scheduler = None
        scheduled_actions._bot_app = None


def test_legacy_path_still_uses_config_timezone(tmp_path, monkeypatch):
    """user_id=None → falls back to config.TIMEZONE for the cron timezone."""
    import scheduled_actions
    import config

    monkeypatch.setattr(
        scheduled_actions, "LEGACY_STORE_PATH", tmp_path / "actions.json"
    )
    fake_sched = MagicMock()
    scheduled_actions._scheduler = fake_sched
    scheduled_actions._bot_app = MagicMock()
    try:
        run_at = datetime.datetime(2030, 1, 1, 22, 0)
        scheduled_actions.schedule_action(
            "legacy", run_at, repeat="daily", user_id=None,
        )
        _, kwargs = fake_sched.add_job.call_args
        trigger = kwargs.get("trigger")
        tz_repr = str(trigger.timezone)
        assert config.TIMEZONE in tz_repr, (
            f"Legacy path lost config.TIMEZONE: trigger tz={tz_repr!r}, "
            f"config.TIMEZONE={config.TIMEZONE!r}"
        )
    finally:
        scheduled_actions._scheduler = None
        scheduled_actions._bot_app = None


def test_legacy_conversation_uses_config_timezone(monkeypatch, tmp_path):
    """conversation.add(user_id=None) stamps last_ts with config.TIMEZONE offset."""
    monkeypatch.chdir(tmp_path)
    import conversation
    conversation.add("hi", "there", user_id=None)
    import os
    path = conversation._history_path(None)
    assert os.path.exists(path)
    data = json.loads(open(path, encoding="utf-8").read())
    last_ts = data["session"]["last_ts"]
    # Asia/Jerusalem offset: +02:00 or +03:00 depending on DST.
    assert last_ts.endswith("+02:00") or last_ts.endswith("+03:00"), (
        f"Expected Jerusalem offset, got {last_ts!r}"
    )


# ─── bot_handlers helper ─────────────────────────────────────────────────────


def test_bot_handlers_user_tz_resolves_from_db():
    user_id = _make_user(tz="Pacific/Auckland")
    import bot_handlers
    tz = bot_handlers._user_tz(user_id)
    assert str(tz) == "Pacific/Auckland"


def test_bot_handlers_user_tz_legacy_falls_back():
    import bot_handlers
    import config
    tz = bot_handlers._user_tz(None)
    assert str(tz) == config.TIMEZONE
