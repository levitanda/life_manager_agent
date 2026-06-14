"""Tests for web.py — FastAPI sidecar."""

from __future__ import annotations

import datetime
import json
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    import db, crypto, web
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    app = web.create_app()
    yield app
    db.reset_for_tests()
    crypto.reset_for_tests()


@pytest.fixture
def client(app):
    return TestClient(app)


# ─── /health ──────────────────────────────────────────────────────────────────


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ─── State token signing ─────────────────────────────────────────────────────


def test_sign_and_verify_state_roundtrip(app):
    import web
    token = web.sign_state(42)
    assert web.verify_state(token) == 42


def test_verify_state_rejects_garbage(app):
    import web
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        web.verify_state("not-a-real-token")
    assert exc.value.status_code == 400


# ─── OAuth start ──────────────────────────────────────────────────────────────


def test_oauth_start_redirects_to_google(client, app):
    import web
    state = web.sign_state(1)

    fake_flow = MagicMock()
    fake_flow.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?xyz", state)
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=fake_flow):
        r = client.get(f"/oauth/start?state={state}", follow_redirects=False)
    assert r.status_code == 302
    assert "accounts.google.com" in r.headers["location"]


def test_oauth_start_bad_state(client):
    r = client.get("/oauth/start?state=garbage")
    assert r.status_code == 400


# ─── OAuth callback ──────────────────────────────────────────────────────────


def _make_user(uid: int = 1, tg_id: int = 100):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg_id, telegram_chat_id=tg_id)
        return u.id


def test_oauth_callback_writes_token(client, app):
    import web, db, crypto
    user_id = _make_user()
    state = web.sign_state(user_id)

    fake_creds = MagicMock()
    fake_creds.to_json.return_value = json.dumps({
        "token": "ya29.test",
        "refresh_token": "1//ref",
        "client_id": "c", "client_secret": "s",
        "scopes": web.GOOGLE_SCOPES,
    })
    fake_flow = MagicMock(credentials=fake_creds)
    fake_flow.fetch_token.return_value = None
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=fake_flow):
        r = client.get(f"/oauth/callback?code=AUTH_CODE&state={state}")
    assert r.status_code == 200
    assert "Готово" in r.text

    # Token row persisted, encrypted
    with db.session_scope() as s:
        row = s.get(db.GoogleToken, user_id)
        assert row is not None
        decrypted = crypto.decrypt_json(row.token_json_encrypted)
        assert decrypted["token"] == "ya29.test"
        assert decrypted["refresh_token"] == "1//ref"


def test_oauth_callback_unknown_user_404(client, app):
    import web
    state = web.sign_state(9999)  # no user exists with this id

    fake_creds = MagicMock()
    fake_creds.to_json.return_value = json.dumps({"token": "x", "scopes": []})
    fake_flow = MagicMock(credentials=fake_creds)
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=fake_flow):
        r = client.get(f"/oauth/callback?code=x&state={state}")
    assert r.status_code == 404


def test_oauth_callback_bad_state(client):
    r = client.get("/oauth/callback?code=x&state=garbage")
    assert r.status_code == 400


# ─── Stripe webhook ──────────────────────────────────────────────────────────


def test_stripe_webhook_no_handler_returns_200(client):
    """When stripe_client isn't yet wired, the webhook still 200s to avoid retry storms."""
    r = client.post("/stripe/webhook", json={"type": "checkout.session.completed", "data": {}})
    assert r.status_code == 200
    assert r.json() == {"received": True}


def test_stripe_webhook_invalid_json_400(client):
    r = client.post("/stripe/webhook", content=b"not-json", headers={"content-type": "application/json"})
    assert r.status_code == 400


# ─── Alice webhook ────────────────────────────────────────────────────────────


def _enable_alice(user_id: int, secret: str):
    import db, crypto
    with db.session_scope() as s:
        s.add(db.UserIntegration(
            user_id=user_id,
            integration="alice",
            enabled=1,
            config_json_encrypted=crypto.encrypt_json({"secret": secret}),
        ))


def test_alice_webhook_unknown_user_404(client):
    r = client.post("/alice/999/abc", json={})
    assert r.status_code == 404


def test_alice_webhook_disabled_403(client, app):
    user_id = _make_user()
    # No alice integration row → 403
    r = client.post(f"/alice/{user_id}/whatever", json={})
    assert r.status_code == 403


def test_alice_webhook_bad_secret_403(client, app):
    user_id = _make_user()
    _enable_alice(user_id, "correct-secret")
    r = client.post(f"/alice/{user_id}/wrong-secret", json={})
    assert r.status_code == 403


def test_alice_webhook_authorized_200(client, app):
    user_id = _make_user()
    _enable_alice(user_id, "tok123")
    r = client.post(
        f"/alice/{user_id}/tok123",
        json={"version": "1.0", "session": {"session_id": "s1"}, "request": {}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "1.0"
    assert "response" in body
