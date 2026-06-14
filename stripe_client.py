"""Stripe billing client — Checkout sessions + webhook event handling.

Two main entry points:

    create_checkout_session(user_id) -> str
        Returns a Stripe-hosted Checkout URL. Bot DMs this link to the user.

    apply_event_to_db(event: dict) -> None
        Called by web.py /stripe/webhook. Updates user.subscription_status
        based on the event type. Recognized:
          - checkout.session.completed → 'active' + store sub/customer ids
          - customer.subscription.deleted → 'cancelled'
          - invoice.payment_failed → 'past_due'
          - customer.subscription.updated → may flip status
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


STRIPE_API_KEY_ENV = "STRIPE_API_KEY"
STRIPE_PRICE_ID_ENV = "STRIPE_PRICE_ID"     # the recurring price ($20/mo)
PUBLIC_BASE_URL_ENV = "PUBLIC_BASE_URL"


def _stripe():
    """Lazy stripe import + key configuration."""
    import stripe
    key = os.environ.get(STRIPE_API_KEY_ENV)
    if not key:
        raise RuntimeError("STRIPE_API_KEY not set in environment")
    stripe.api_key = key
    return stripe


# ─── Customer / Checkout ──────────────────────────────────────────────────────


def create_checkout_session(user_id: int) -> str:
    """Create a Checkout session for the given user_id. Returns the hosted URL."""
    stripe = _stripe()
    price_id = os.environ.get(STRIPE_PRICE_ID_ENV)
    if not price_id:
        raise RuntimeError("STRIPE_PRICE_ID not set in environment")

    base = os.environ.get(PUBLIC_BASE_URL_ENV, "https://dalev.click")
    success_url = f"{base}/health"  # We'll route to a thank-you page later
    cancel_url = f"{base}/health"

    import db
    with db.session_scope() as s:
        user = s.get(db.User, user_id)
        if user is None:
            raise RuntimeError(f"User {user_id} not found")
        customer_id = user.stripe_customer_id

    # Reuse existing customer or create on-the-fly via customer_email
    kwargs = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(user_id),
        "subscription_data": {"metadata": {"user_id": str(user_id)}},
        "metadata": {"user_id": str(user_id)},
    }
    if customer_id:
        kwargs["customer"] = customer_id

    session = stripe.checkout.Session.create(**kwargs)
    return session.url


# ─── Webhook events ──────────────────────────────────────────────────────────


def _user_id_from_event(event: dict) -> Optional[int]:
    """Best-effort extraction of our user_id from a Stripe event payload."""
    obj = event.get("data", {}).get("object", {}) or {}
    # checkout.session: client_reference_id or metadata.user_id
    for key in ("client_reference_id",):
        v = obj.get(key)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    for path in (obj.get("metadata"), obj.get("subscription_data") and obj["subscription_data"].get("metadata")):
        if path and path.get("user_id"):
            try:
                return int(path["user_id"])
            except (TypeError, ValueError):
                pass
    return None


def apply_event_to_db(event: dict) -> None:
    """Mutate the user row based on the Stripe event type. Idempotent."""
    etype = event.get("type", "")
    obj = event.get("data", {}).get("object", {}) or {}
    user_id = _user_id_from_event(event)
    if user_id is None:
        logger.info("Stripe event %s: no user_id in payload, skipping", etype)
        return

    import db
    with db.session_scope() as s:
        user = s.get(db.User, user_id)
        if user is None:
            logger.warning("Stripe event %s for unknown user_id=%s", etype, user_id)
            return

        if etype == "checkout.session.completed":
            user.subscription_status = "active"
            cust_id = obj.get("customer")
            sub_id = obj.get("subscription")
            if cust_id:
                user.stripe_customer_id = cust_id
            if sub_id:
                user.stripe_subscription_id = sub_id
            logger.info("Stripe: user %s activated (sub=%s)", user_id, sub_id)

        elif etype == "customer.subscription.deleted":
            user.subscription_status = "cancelled"
            logger.info("Stripe: user %s cancelled", user_id)

        elif etype == "invoice.payment_failed":
            user.subscription_status = "past_due"
            logger.info("Stripe: user %s past_due", user_id)

        elif etype == "customer.subscription.updated":
            status = obj.get("status")
            mapping = {
                "active": "active", "trialing": "active",
                "canceled": "cancelled",
                "past_due": "past_due", "unpaid": "past_due",
                "incomplete": "inactive", "incomplete_expired": "inactive",
            }
            if status in mapping and user.subscription_status != "promo":
                user.subscription_status = mapping[status]
                logger.info("Stripe: user %s status → %s", user_id, mapping[status])

        else:
            logger.debug("Stripe event %s ignored", etype)


def cancel_subscription(user_id: int) -> bool:
    """Cancel the user's Stripe subscription at period end. Returns True if cancellation queued."""
    import db
    with db.session_scope() as s:
        user = s.get(db.User, user_id)
        if user is None or not user.stripe_subscription_id:
            return False
        sub_id = user.stripe_subscription_id

    stripe = _stripe()
    stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    return True


# ─── Webhook signature verification (called by web.py in Phase 5 hardening) ──


def construct_event(payload: bytes, sig_header: str) -> dict:
    """Verify a webhook signature and return the parsed event."""
    stripe = _stripe()
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not set")
    return stripe.Webhook.construct_event(payload, sig_header, secret)
