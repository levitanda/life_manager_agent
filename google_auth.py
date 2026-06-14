"""Shared Google OAuth credentials for all Google services.

Two modes:

1. **Legacy single-user (default)** — reads/writes a JSON file at
   `config.GOOGLE_TOKEN_FILE`. Used by the still-single-user bot today.

2. **Multi-tenant** — when `user_id` is passed, reads the encrypted token
   from the `google_tokens` table in the SQLite DB and writes refreshes
   back there. Used by post-migration code.

Both modes share the same Credentials object semantics: the caller gets
a refreshable Credentials and can build any Google service from it.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def get_credentials(user_id: Optional[int] = None) -> Credentials:
    """Return refreshable Google Credentials.

    If `user_id` is given, the token is loaded from the multi-tenant DB
    (decrypted) and any refresh is written back there. If omitted, the
    legacy single-user flow is used (file at config.GOOGLE_TOKEN_FILE).
    """
    if user_id is None:
        return _get_credentials_legacy()
    return _get_credentials_for_user(user_id)


# ─── Legacy single-user flow ─────────────────────────────────────────────────

def _get_credentials_legacy() -> Credentials:
    creds: Optional[Credentials] = None
    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GOOGLE_CREDENTIALS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(config.GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


# ─── Multi-tenant per-user flow ──────────────────────────────────────────────

def _get_credentials_for_user(user_id: int) -> Credentials:
    """Load token from DB, refresh if expired, write back if refreshed."""
    # Lazy import — keeps single-user mode free of db/crypto dep on its hot path.
    import db
    import crypto

    with db.session_scope() as session:
        row = session.get(db.GoogleToken, user_id)
        if row is None:
            raise RuntimeError(
                f"No Google token stored for user_id={user_id}. "
                "User must complete OAuth onboarding."
            )
        payload = crypto.decrypt_json(row.token_json_encrypted)

    creds = Credentials.from_authorized_user_info(payload, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist refreshed token
            new_payload = json.loads(creds.to_json())
            with db.session_scope() as session:
                row = session.get(db.GoogleToken, user_id)
                if row is not None:
                    row.token_json_encrypted = crypto.encrypt_json(new_payload)
                    row.refreshed_at = datetime.datetime.utcnow()
        else:
            raise RuntimeError(
                f"Google token for user_id={user_id} is invalid and cannot be refreshed. "
                "User must re-authorize."
            )

    return creds
