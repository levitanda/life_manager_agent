"""Tests for google_auth.py per-user mode (DB-backed)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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


def _make_user_with_token(payload: dict):
    import db, crypto
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.add(db.GoogleToken(
            user_id=u.id,
            token_json_encrypted=crypto.encrypt_json(payload),
            scopes=" ".join(payload.get("scopes", [])),
        ))
        return u.id


def test_get_credentials_per_user_loads_from_db():
    payload = {
        "token": "tok-1",
        "refresh_token": "ref-1",
        "client_id": "cid",
        "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }
    user_id = _make_user_with_token(payload)
    import google_auth
    fake = MagicMock(valid=True)
    with patch.object(google_auth.Credentials, "from_authorized_user_info", return_value=fake) as ff:
        got = google_auth.get_credentials(user_id=user_id)
    assert got is fake
    args, _ = ff.call_args
    assert args[0]["token"] == "tok-1"


def test_get_credentials_missing_user_token_raises():
    import google_auth
    with pytest.raises(RuntimeError, match="No Google token"):
        google_auth.get_credentials(user_id=999)


def test_per_user_refresh_persists_back():
    payload = {
        "token": "old",
        "refresh_token": "ref-1",
        "client_id": "cid",
        "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }
    user_id = _make_user_with_token(payload)

    fake = MagicMock(valid=False, expired=True, refresh_token="ref-1")
    fake.to_json.return_value = json.dumps({**payload, "token": "new"})
    # After refresh() the credentials become valid in real life
    def _refresh(_req):
        fake.valid = True
    fake.refresh.side_effect = _refresh

    import google_auth, crypto, db
    with patch.object(google_auth.Credentials, "from_authorized_user_info", return_value=fake):
        google_auth.get_credentials(user_id=user_id)

    fake.refresh.assert_called_once()
    # New token persisted in DB (encrypted)
    with db.session_scope() as s:
        row = s.get(db.GoogleToken, user_id)
        decrypted = crypto.decrypt_json(row.token_json_encrypted)
        assert decrypted["token"] == "new"
        assert row.refreshed_at is not None


def test_per_user_expired_no_refresh_token_raises():
    payload = {
        "token": "old",
        "client_id": "cid",
        "client_secret": "csec",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }
    user_id = _make_user_with_token(payload)

    fake = MagicMock(valid=False, expired=True, refresh_token=None)
    import google_auth
    with patch.object(google_auth.Credentials, "from_authorized_user_info", return_value=fake):
        with pytest.raises(RuntimeError, match="re-authorize"):
            google_auth.get_credentials(user_id=user_id)


def test_legacy_mode_unchanged(monkeypatch, tmp_path):
    """Without user_id, the legacy file-based flow still runs."""
    import google_auth
    token_file = tmp_path / "tok.json"
    token_file.write_text(json.dumps({
        "token": "x", "refresh_token": "y", "client_id": "c", "client_secret": "s",
        "token_uri": "u", "scopes": ["https://www.googleapis.com/auth/calendar"],
    }))
    monkeypatch.setattr("config.GOOGLE_TOKEN_FILE", str(token_file))
    fake = MagicMock(valid=True)
    with patch.object(google_auth.Credentials, "from_authorized_user_file", return_value=fake):
        got = google_auth.get_credentials()  # no user_id
    assert got is fake
