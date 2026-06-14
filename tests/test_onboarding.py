"""Tests for onboarding.py — /start, /promo, /subscribe, /cancel handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_xxx")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_xxx")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_update(telegram_id: int = 42, args: list[str] | None = None):
    upd = MagicMock()
    upd.effective_user.id = telegram_id
    upd.effective_user.first_name = "Test"
    upd.effective_user.username = "tester"
    upd.effective_chat.id = telegram_id
    upd.effective_message.reply_text = AsyncMock()
    return upd


def _mk_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ─── /start ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_creates_user_and_shows_welcome():
    import onboarding, db
    upd = _mk_update(42)
    ctx = _mk_context()
    await onboarding.cmd_start(upd, ctx)
    # User created
    with db.session_scope() as s:
        assert db.get_user_by_telegram_id(s, 42) is not None
    # Welcome message sent
    upd.effective_message.reply_text.assert_awaited()
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "AI" in text or "ассистент" in text
    # Inline keyboard present
    kwargs = upd.effective_message.reply_text.call_args.kwargs
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_start_returning_active_user_skips_welcome():
    import onboarding, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42, display_name="X")
        u.subscription_status = "active"

    upd = _mk_update(42)
    await onboarding.cmd_start(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "возвращением" in text.lower() or "🙂" in text


@pytest.mark.asyncio
async def test_start_returning_promo_user_skips_welcome():
    import onboarding, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.subscription_status = "promo"
    upd = _mk_update(42)
    await onboarding.cmd_start(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "возвращением" in text.lower() or "🙂" in text


# ─── /promo ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promo_no_args():
    import onboarding
    upd = _mk_update(42)
    ctx = _mk_context(args=[])
    await onboarding.cmd_promo(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "КОД" in text


@pytest.mark.asyncio
async def test_promo_levitanonly_grants_access():
    import onboarding, db
    upd = _mk_update(42)
    ctx = _mk_context(args=["LEVITANONLY"])
    await onboarding.cmd_promo(upd, ctx)
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 42)
        assert user is not None
        assert user.subscription_status == "promo"
    # First reply is "промокод применён", then post-subscribe nudge
    assert upd.effective_message.reply_text.await_count >= 1


@pytest.mark.asyncio
async def test_promo_unknown_code():
    import onboarding, db
    upd = _mk_update(42)
    ctx = _mk_context(args=["FAKE"])
    await onboarding.cmd_promo(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "не найден" in text.lower()


@pytest.mark.asyncio
async def test_promo_case_insensitive():
    import onboarding, db
    upd = _mk_update(42)
    ctx = _mk_context(args=["levitanonly"])
    await onboarding.cmd_promo(upd, ctx)
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 42)
        assert user.subscription_status == "promo"


# ─── /subscribe ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_creates_checkout_link():
    import onboarding
    upd = _mk_update(42)
    ctx = _mk_context()
    with patch("stripe_client.create_checkout_session", return_value="https://stripe/abc"):
        await onboarding.cmd_subscribe(upd, ctx)
    # Reply includes inline keyboard with stripe URL
    kwargs = upd.effective_message.reply_text.call_args.kwargs
    kb = kwargs.get("reply_markup")
    assert kb is not None
    # Inspect button via .inline_keyboard
    button = kb.inline_keyboard[0][0]
    assert button.url == "https://stripe/abc"


@pytest.mark.asyncio
async def test_subscribe_handles_stripe_failure():
    import onboarding
    upd = _mk_update(42)
    ctx = _mk_context()
    with patch("stripe_client.create_checkout_session", side_effect=RuntimeError("api down")):
        await onboarding.cmd_subscribe(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "не получилось" in text.lower()


# ─── /cancel ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_no_subscription():
    import onboarding, db
    with db.session_scope() as s:
        db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
    upd = _mk_update(42)
    await onboarding.cmd_cancel(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "нет активной подписки" in text.lower()


@pytest.mark.asyncio
async def test_cancel_with_active_subscription():
    import onboarding, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.subscription_status = "active"
        u.stripe_subscription_id = "sub_X"
    upd = _mk_update(42)
    with patch("stripe_client.cancel_subscription", return_value=True) as mock_cancel:
        await onboarding.cmd_cancel(upd, _mk_context())
    mock_cancel.assert_called_once()
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "отменится" in text.lower()


# ─── Callback handler ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_onboard_promo_shows_instruction():
    import onboarding
    upd = MagicMock()
    upd.callback_query = MagicMock()
    upd.callback_query.answer = AsyncMock()
    upd.callback_query.data = "onboard:promo"
    upd.callback_query.message.reply_text = AsyncMock()
    await onboarding.cb_onboard(upd, _mk_context())
    text = upd.callback_query.message.reply_text.call_args[0][0]
    assert "/promo" in text


@pytest.mark.asyncio
async def test_cb_onboard_features_lists_capabilities():
    import onboarding
    upd = MagicMock()
    upd.callback_query = MagicMock()
    upd.callback_query.answer = AsyncMock()
    upd.callback_query.data = "onboard:features"
    upd.callback_query.message.reply_text = AsyncMock()
    await onboarding.cb_onboard(upd, _mk_context())
    text = upd.callback_query.message.reply_text.call_args[0][0]
    assert "календарь" in text.lower()
    assert "whatsapp" in text.lower()
