"""Tests for access.py — subscription gating decorator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


def _mk_update(telegram_user_id: int = 42):
    update = MagicMock()
    update.effective_user.id = telegram_user_id
    update.effective_message.reply_text = AsyncMock()
    return update


def _mk_user(telegram_id: int = 42, status: str = "inactive"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=telegram_id, telegram_chat_id=telegram_id)
        u.subscription_status = status
        return u.id


# ─── get_user_from_update ────────────────────────────────────────────────────


def test_get_user_returns_none_when_not_in_db():
    import access
    assert access.get_user_from_update(_mk_update(999)) is None


def test_get_user_returns_user_when_present():
    _mk_user(42)
    import access
    user = access.get_user_from_update(_mk_update(42))
    assert user is not None
    assert user.telegram_user_id == 42


# ─── has_access_telegram ─────────────────────────────────────────────────────


def test_has_access_inactive_is_false():
    _mk_user(42, status="inactive")
    import access
    assert access.has_access_telegram(_mk_update(42)) is False


def test_has_access_active_is_true():
    _mk_user(42, status="active")
    import access
    assert access.has_access_telegram(_mk_update(42)) is True


def test_has_access_promo_is_true():
    _mk_user(42, status="promo")
    import access
    assert access.has_access_telegram(_mk_update(42)) is True


def test_has_access_no_user_is_false():
    import access
    assert access.has_access_telegram(_mk_update(999)) is False


# ─── require_access decorator ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_require_access_blocks_unknown_user():
    import access
    called = False

    @access.require_access
    async def handler(update, context, *, user=None):
        nonlocal called
        called = True

    upd = _mk_update(999)
    await handler(upd, None)
    assert called is False
    upd.effective_message.reply_text.assert_awaited_once()
    args, _ = upd.effective_message.reply_text.call_args
    assert "не знакомы" in args[0].lower() or "/start" in args[0]


@pytest.mark.asyncio
async def test_require_access_blocks_inactive_user():
    _mk_user(42, status="inactive")
    import access
    called = False

    @access.require_access
    async def handler(update, context, *, user=None):
        nonlocal called
        called = True

    upd = _mk_update(42)
    await handler(upd, None)
    assert called is False
    upd.effective_message.reply_text.assert_awaited_once()
    msg = upd.effective_message.reply_text.call_args[0][0]
    assert "промокод" in msg.lower() or "$20" in msg


@pytest.mark.asyncio
async def test_require_access_allows_active_user_and_injects_user():
    _mk_user(42, status="active")
    import access
    seen_user = None

    @access.require_access
    async def handler(update, context, *, user=None):
        nonlocal seen_user
        seen_user = user
        return "ok"

    upd = _mk_update(42)
    result = await handler(upd, None)
    assert result == "ok"
    assert seen_user is not None
    assert seen_user.telegram_user_id == 42


@pytest.mark.asyncio
async def test_require_access_allows_promo_user():
    _mk_user(42, status="promo")
    import access
    called = False

    @access.require_access
    async def handler(update, context, *, user=None):
        nonlocal called
        called = True

    upd = _mk_update(42)
    await handler(upd, None)
    assert called is True


@pytest.mark.asyncio
async def test_require_access_blocks_cancelled_user():
    _mk_user(42, status="cancelled")
    import access
    called = False

    @access.require_access
    async def handler(update, context, *, user=None):
        nonlocal called
        called = True

    upd = _mk_update(42)
    await handler(upd, None)
    assert called is False
