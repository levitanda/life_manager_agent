"""Access control: gate Telegram handlers behind active subscription / promo.

Usage in a handler:

    from access import require_access

    @require_access
    async def cmd_tasks(update, context, *, user):
        # user is the DB-backed User row with has_access() == True
        ...

The decorator looks up the user by telegram_user_id; if not found OR no
access, it nudges the user toward onboarding and returns silently.

The user row is passed as a `user` keyword arg so the handler avoids a
second DB lookup.
"""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# Friendly text shown when access is denied.
NO_SUBSCRIPTION_TEXT = (
    "🔒 Для использования бота нужна активная подписка ($20/мес) "
    "или промокод.\n\nНажми /start чтобы начать настройку, "
    "или введи /promo <CODE> если у тебя уже есть код."
)
NEEDS_ONBOARDING_TEXT = (
    "👋 Привет! Похоже мы ещё не знакомы. Нажми /start чтобы начать."
)


def get_or_create_user_from_update(update) -> "tuple[Optional[object], bool]":
    """Return (User or None, was_created).

    Looks up the Telegram user in the DB. Does NOT create a row by default —
    onboarding does that explicitly. Returns (None, False) if no row exists.
    """
    import db
    with db.session_scope() as session:
        u = db.get_user_by_telegram_id(session, update.effective_user.id)
        if u is None:
            return None, False
        # Detach so the caller can use the object after session closes.
        session.expunge(u)
        return u, False


def get_user_from_update(update) -> Optional[object]:
    """Just the lookup, no creation. Returns the (detached) User row or None."""
    u, _ = get_or_create_user_from_update(update)
    return u


def has_access_telegram(update) -> bool:
    """Quick boolean check used by simple handlers / scheduler loops."""
    u = get_user_from_update(update)
    return u is not None and u.has_access()


def require_access(handler: Callable[..., Awaitable]) -> Callable[..., Awaitable]:
    """Decorator: gate a Telegram handler behind active subscription / promo.

    Calls the wrapped handler with `user=<User>` kwarg added. If access is
    denied, sends a polite message and returns without invoking the handler.
    """

    @functools.wraps(handler)
    async def wrapper(update, context, *args, **kwargs):
        user = get_user_from_update(update)
        if user is None:
            try:
                await update.effective_message.reply_text(NEEDS_ONBOARDING_TEXT)
            except Exception:
                pass
            return
        if not user.has_access():
            try:
                await update.effective_message.reply_text(NO_SUBSCRIPTION_TEXT)
            except Exception:
                pass
            return
        kwargs["user"] = user
        return await handler(update, context, *args, **kwargs)

    return wrapper
