"""Send push notifications via Pushover.

Two modes:
- Legacy single-user (user_id=None): credentials from .env PUSHOVER_USER_KEY +
  PUSHOVER_APP_TOKEN.
- Multi-tenant (user_id given): credentials from user_integrations row
  (Fernet-encrypted). If the user hasn't configured Pushover, the call is
  a silent no-op.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


def _creds_for(user_id: Optional[int]) -> Optional[tuple[str, str]]:
    """Return (app_token, user_key) for the user, or None if not configured."""
    if user_id is None:
        if config.PUSHOVER_USER_KEY and config.PUSHOVER_APP_TOKEN:
            return config.PUSHOVER_APP_TOKEN, config.PUSHOVER_USER_KEY
        return None
    try:
        import db
        import crypto
        with db.session_scope() as s:
            row = (
                s.query(db.UserIntegration)
                .filter_by(user_id=user_id, integration="pushover")
                .one_or_none()
            )
            if not row or not row.enabled or not row.config_json_encrypted:
                return None
            cfg = crypto.decrypt_json(row.config_json_encrypted)
            tok = cfg.get("app_token")
            usr = cfg.get("user_key")
            return (tok, usr) if (tok and usr) else None
    except Exception as e:
        logger.warning("pushover creds lookup failed for user %s: %s", user_id, e)
        return None


def send_push(message: str, title: str = "Дайджест дня", *, user_id: Optional[int] = None) -> bool:
    creds = _creds_for(user_id)
    if creds is None:
        return False
    token, user_key = creds
    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": token,
                "user": user_key,
                "title": title,
                "message": message[:1024],
                "priority": 0,
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning("pushover send failed for user %s: %s", user_id, e)
        return False
