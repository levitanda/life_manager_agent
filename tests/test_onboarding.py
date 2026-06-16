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
    # The wizard handler probes callback_query — keep it None unless the test
    # is exercising a callback path.
    upd.callback_query = None
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
    """User with onboarding_state='completed' gets the brief return greeting.
    (Phase E: pre-completed users with access drop into the wizard, not the
    greeting — see test_phase_ef_wizard.py.)"""
    import onboarding, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42, display_name="X")
        u.subscription_status = "active"
        u.onboarding_state = "completed"

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
        u.onboarding_state = "completed"
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
async def test_subscribe_shows_coming_soon():
    """Stripe doesn't work in Israel — /subscribe responds with a placeholder
    until payments are wired up via a region-friendly provider."""
    import onboarding
    upd = _mk_update(42)
    ctx = _mk_context()
    await onboarding.cmd_subscribe(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "скоро" in text.lower() or "следи" in text.lower()
    assert "/promo" in text.lower()


# ─── /cancel ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_unknown_user_replies_nothing_to_cancel():
    import onboarding
    upd = _mk_update(42)
    await onboarding.cmd_cancel(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "не зарегистрирован" in text.lower()


@pytest.mark.asyncio
async def test_cancel_promo_user_flips_inactive_and_stops_bridge():
    """Promo users with no Stripe sub should still get a clean cancel:
    status → inactive AND WA bridge stopped."""
    import onboarding, db, bot_handlers
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.subscription_status = "promo"
    upd = _mk_update(42)
    with patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await onboarding.cmd_cancel(upd, _mk_context())
    mock_cleanup.assert_called_once()
    args, kwargs = mock_cleanup.call_args
    assert args[0] > 0  # the user_id
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "отключён" in text.lower() or "отключен" in text.lower()


@pytest.mark.asyncio
async def test_cancel_with_active_subscription_calls_stripe_and_cleanup():
    """Stripe-subscribed user → Stripe cancel + immediate WA cleanup."""
    import onboarding, db, bot_handlers
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.subscription_status = "active"
        u.stripe_subscription_id = "sub_X"
    upd = _mk_update(42)
    with patch("stripe_client.cancel_subscription", return_value=True) as mock_cancel, \
         patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await onboarding.cmd_cancel(upd, _mk_context())
    mock_cancel.assert_called_once()
    mock_cleanup.assert_called_once()
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "отменится" in text.lower() and "whatsapp" in text.lower()


@pytest.mark.asyncio
async def test_cancel_stripe_error_does_not_call_cleanup():
    """If Stripe rejects, don't tear down — give user a chance to retry."""
    import onboarding, db, bot_handlers
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.subscription_status = "active"
        u.stripe_subscription_id = "sub_X"
    upd = _mk_update(42)
    with patch("stripe_client.cancel_subscription", side_effect=RuntimeError("API down")), \
         patch.object(bot_handlers, "deactivate_and_cleanup") as mock_cleanup:
        await onboarding.cmd_cancel(upd, _mk_context())
    mock_cleanup.assert_not_called()
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "не получилось" in text.lower()


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
