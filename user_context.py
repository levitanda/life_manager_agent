"""Small helper to bundle "who is this user" facts in one DB hit.

Used by digest builders and assorted handlers that need the user's name,
timezone, city, and language together. Falls back to config defaults so
legacy callers (Daria pre-migration) keep working when user_id is None
or the row is missing.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_user_context(user_id: Optional[int]) -> dict:
    """Return {display_name, timezone, city, language} for a user.

    Always returns the same set of keys; missing fields come back as None
    (or the config default for timezone/language).
    """
    import config
    out = {
        "display_name": None,
        "timezone": getattr(config, "TIMEZONE", "Europe/Moscow"),
        "city": None,
        "language": "ru",
    }
    if user_id is None:
        return out
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u is None:
                return out
            if u.display_name:
                out["display_name"] = u.display_name
            if u.timezone:
                out["timezone"] = u.timezone
            if getattr(u, "city", None):
                out["city"] = u.city
            if getattr(u, "language", None):
                out["language"] = u.language
    except Exception as e:
        logger.warning("get_user_context failed for %s: %s", user_id, e)
    return out
