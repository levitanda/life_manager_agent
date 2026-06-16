"""Tests for the access-loss cleanup path: deactivate_and_cleanup +
handle_bot_blocked global error handler.

When a user blocks/deletes the bot or cancels access, we must:
- Mark their subscription_status (unless they're on promo)
- Stop their WhatsApp bridge (unless it's the legacy external one)
- Never raise — these paths run from error handlers and webhooks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_user(tg=222, *, status="active"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg, telegram_chat_id=tg, display_name="J")
        u.subscription_status = status
        return u.id


# ─── deactivate_and_cleanup ────────────────────────────────────────────────────


def test_deactivate_flips_active_to_inactive_and_calls_wa_cleanup():
    import bot_handlers, db
    uid = _mk_user(status="active")
    with patch("whatsapp_supervisor.disable_for_user") as mock_dis:
        bot_handlers.deactivate_and_cleanup(uid, reason="test")
    mock_dis.assert_called_once_with(uid)
    with db.session_scope() as s:
        assert s.get(db.User, uid).subscription_status == "inactive"


def test_deactivate_preserves_promo_status():
    """Promo grants are permanent — only WA bridge should be touched, never the status."""
    import bot_handlers, db
    uid = _mk_user(status="promo")
    with patch("whatsapp_supervisor.disable_for_user") as mock_dis:
        bot_handlers.deactivate_and_cleanup(uid, reason="test")
    mock_dis.assert_called_once_with(uid)
    with db.session_scope() as s:
        assert s.get(db.User, uid).subscription_status == "promo"


def test_deactivate_idempotent_on_already_inactive():
    import bot_handlers, db
    uid = _mk_user(status="inactive")
    with patch("whatsapp_supervisor.disable_for_user") as mock_dis:
        bot_handlers.deactivate_and_cleanup(uid, reason="test")
    mock_dis.assert_called_once_with(uid)
    with db.session_scope() as s:
        assert s.get(db.User, uid).subscription_status == "inactive"


def test_deactivate_swallows_wa_cleanup_errors():
    """Must not raise even if WA cleanup blows up."""
    import bot_handlers
    uid = _mk_user(status="active")
    with patch("whatsapp_supervisor.disable_for_user", side_effect=RuntimeError("boom")):
        # Should not raise
        bot_handlers.deactivate_and_cleanup(uid, reason="test")


def test_deactivate_unknown_user_is_silent():
    """No user row → bail early; WA cleanup is unnecessary because cascade
    would have already removed any bridge row."""
    import bot_handlers
    with patch("whatsapp_supervisor.disable_for_user") as mock_dis:
        bot_handlers.deactivate_and_cleanup(99999, reason="test")
    mock_dis.assert_not_called()


def test_deactivate_zero_user_id_is_noop():
    import bot_handlers
    with patch("whatsapp_supervisor.disable_for_user") as mock_dis:
        bot_handlers.deactivate_and_cleanup(0, reason="test")
    mock_dis.assert_not_called()


# ─── handle_bot_blocked global error handler ─────────────────────────────────


def _mk_update_with_user(tg_id: int):
    from telegram import Update
    upd = MagicMock(spec=Update)
    upd.effective_user = MagicMock()
    upd.effective_user.id = tg_id
    return upd


def _mk_ctx_with_error(err):
    ctx = MagicMock()
    ctx.error = err
    return ctx


@pytest.mark.asyncio
async def test_forbidden_triggers_cleanup_for_known_user():
    """When Telegram returns Forbidden for a known user, that user is deactivated."""
    import bot_handlers, db
    from telegram.error import Forbidden
    uid = _mk_user(tg=42, status="active")
    upd = _mk_update_with_user(42)
    ctx = _mk_ctx_with_error(Forbidden("bot was blocked by the user"))
    with patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await bot_handlers.handle_bot_blocked(upd, ctx)
    mock_cleanup.assert_called_once()
    assert mock_cleanup.call_args[0][0] == uid
    assert mock_cleanup.call_args[1]["reason"] == "bot_blocked"


@pytest.mark.asyncio
async def test_forbidden_for_unknown_user_is_silent():
    """If we can't resolve the user, log but don't crash."""
    import bot_handlers
    from telegram.error import Forbidden
    upd = _mk_update_with_user(99999)  # not in DB
    ctx = _mk_ctx_with_error(Forbidden("user is deactivated"))
    with patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await bot_handlers.handle_bot_blocked(upd, ctx)
    mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_non_forbidden_error_does_not_deactivate():
    """Generic exceptions must NOT mark users inactive — that would be too
    aggressive (e.g. ANTHROPIC API down shouldn't kill a paying customer)."""
    import bot_handlers
    uid = _mk_user(tg=42, status="active")
    upd = _mk_update_with_user(42)
    ctx = _mk_ctx_with_error(RuntimeError("anthropic timeout"))
    with patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await bot_handlers.handle_bot_blocked(upd, ctx)
    mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_forbidden_without_update_is_silent():
    """Scheduled job sends don't pass an Update — gracefully skip."""
    import bot_handlers
    from telegram.error import Forbidden
    ctx = _mk_ctx_with_error(Forbidden("bot was blocked"))
    with patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        # update is None / non-Update sentinel
        await bot_handlers.handle_bot_blocked(MagicMock(), ctx)
    mock_cleanup.assert_not_called()
