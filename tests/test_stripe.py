"""Tests for stripe_client.py with mocked stripe SDK."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_xxx")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_xxx")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _make_user():
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=100, telegram_chat_id=100)
        return u.id


# ─── create_checkout_session ─────────────────────────────────────────────────


def test_create_checkout_session_returns_url():
    user_id = _make_user()
    import stripe_client
    fake = MagicMock()
    fake.checkout.Session.create.return_value = MagicMock(url="https://checkout.stripe.com/abc")
    with patch.object(stripe_client, "_stripe", return_value=fake):
        url = stripe_client.create_checkout_session(user_id)
    assert url == "https://checkout.stripe.com/abc"
    fake.checkout.Session.create.assert_called_once()
    kwargs = fake.checkout.Session.create.call_args.kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"][0]["price"] == "price_xxx"
    assert kwargs["client_reference_id"] == str(user_id)
    assert kwargs["metadata"] == {"user_id": str(user_id)}


def test_create_checkout_unknown_user_raises():
    import stripe_client
    fake = MagicMock()
    with patch.object(stripe_client, "_stripe", return_value=fake):
        with pytest.raises(RuntimeError, match="not found"):
            stripe_client.create_checkout_session(99999)


def test_create_checkout_reuses_existing_customer_id():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        u = s.get(db.User, user_id)
        u.stripe_customer_id = "cus_existing"
    fake = MagicMock()
    fake.checkout.Session.create.return_value = MagicMock(url="https://x")
    with patch.object(stripe_client, "_stripe", return_value=fake):
        stripe_client.create_checkout_session(user_id)
    kwargs = fake.checkout.Session.create.call_args.kwargs
    assert kwargs.get("customer") == "cus_existing"


# ─── apply_event_to_db ────────────────────────────────────────────────────────


def _event(etype: str, *, user_id=None, **obj_extra) -> dict:
    obj = {"client_reference_id": str(user_id)} if user_id else {}
    obj.update(obj_extra)
    return {"type": etype, "data": {"object": obj}}


def test_event_checkout_completed_activates_user():
    import db, stripe_client
    user_id = _make_user()
    event = _event(
        "checkout.session.completed",
        user_id=user_id,
        customer="cus_X",
        subscription="sub_Y",
    )
    stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        u = s.get(db.User, user_id)
        assert u.subscription_status == "active"
        assert u.stripe_customer_id == "cus_X"
        assert u.stripe_subscription_id == "sub_Y"


def test_event_subscription_deleted_marks_cancelled():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event("customer.subscription.deleted", user_id=user_id)
    stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        assert s.get(db.User, user_id).subscription_status == "cancelled"


def test_event_payment_failed_marks_past_due():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event("invoice.payment_failed", user_id=user_id)
    stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        assert s.get(db.User, user_id).subscription_status == "past_due"


def test_event_no_user_id_is_silent():
    import db, stripe_client
    # event missing client_reference_id and metadata
    event = {"type": "checkout.session.completed", "data": {"object": {}}}
    stripe_client.apply_event_to_db(event)  # should not raise
    with db.session_scope() as s:
        assert s.query(db.User).count() == 0


def test_event_unknown_user_is_silent():
    import stripe_client
    event = _event("checkout.session.completed", user_id=99999)
    stripe_client.apply_event_to_db(event)  # no raise, just logs


def test_event_subscription_updated_maps_status():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event(
        "customer.subscription.updated",
        user_id=user_id,
        status="past_due",
    )
    stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        assert s.get(db.User, user_id).subscription_status == "past_due"


def test_event_subscription_updated_does_not_override_promo():
    """A user on promo must not lose their grant if Stripe sends an update."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "promo"
    event = _event(
        "customer.subscription.updated",
        user_id=user_id,
        status="canceled",
    )
    stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        assert s.get(db.User, user_id).subscription_status == "promo"


# ─── WhatsApp bridge cleanup on access loss ──────────────────────────────────


def test_event_subscription_deleted_disables_whatsapp_bridge():
    """When a paying user is cancelled, their WA bridge must be stopped."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
        s.add(db.WhatsAppBridge(user_id=user_id, port=3035, auth_dir="x", status="running"))
    event = _event("customer.subscription.deleted", user_id=user_id)
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user", return_value=True) as mock_dis:
        stripe_client.apply_event_to_db(event)
    mock_dis.assert_called_once_with(user_id)


def test_event_payment_failed_disables_whatsapp_bridge():
    """past_due also costs access → bridge must stop."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event("invoice.payment_failed", user_id=user_id)
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user") as mock_dis:
        stripe_client.apply_event_to_db(event)
    mock_dis.assert_called_once_with(user_id)


def test_event_subscription_updated_to_inactive_disables_bridge():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event("customer.subscription.updated", user_id=user_id, status="incomplete_expired")
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user") as mock_dis:
        stripe_client.apply_event_to_db(event)
    mock_dis.assert_called_once_with(user_id)


def test_event_checkout_completed_does_not_disable_bridge():
    """Going inactive → active mustn't trigger cleanup."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "inactive"
    event = _event("checkout.session.completed", user_id=user_id)
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user") as mock_dis:
        stripe_client.apply_event_to_db(event)
    mock_dis.assert_not_called()


def test_event_for_promo_user_does_not_touch_bridge():
    """Stripe cannot revoke promo access — bridge stays up."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "promo"
    event = _event("customer.subscription.updated", user_id=user_id, status="canceled")
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user") as mock_dis:
        stripe_client.apply_event_to_db(event)
    mock_dis.assert_not_called()


def test_disable_for_user_failure_does_not_break_webhook():
    """If WA cleanup blows up, the Stripe webhook handler still succeeds."""
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).subscription_status = "active"
    event = _event("customer.subscription.deleted", user_id=user_id)
    import whatsapp_supervisor
    with patch.object(whatsapp_supervisor, "disable_for_user", side_effect=RuntimeError("oops")):
        # must not raise
        stripe_client.apply_event_to_db(event)
    with db.session_scope() as s:
        # status mutation still committed
        assert s.get(db.User, user_id).subscription_status == "cancelled"


# ─── cancel_subscription ─────────────────────────────────────────────────────


def test_cancel_subscription_modifies_stripe():
    import db, stripe_client
    user_id = _make_user()
    with db.session_scope() as s:
        s.get(db.User, user_id).stripe_subscription_id = "sub_Z"
    fake = MagicMock()
    with patch.object(stripe_client, "_stripe", return_value=fake):
        ok = stripe_client.cancel_subscription(user_id)
    assert ok is True
    fake.Subscription.modify.assert_called_once_with("sub_Z", cancel_at_period_end=True)


def test_cancel_subscription_no_sub_returns_false():
    import stripe_client
    user_id = _make_user()
    assert stripe_client.cancel_subscription(user_id) is False
