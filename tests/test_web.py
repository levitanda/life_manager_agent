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
    assert web.verify_state(token)["user_id"] == 42


def test_sign_and_verify_state_with_pkce_verifier(app):
    import web
    token = web.sign_state(42, code_verifier="abc123")
    decoded = web.verify_state(token)
    assert decoded["user_id"] == 42
    assert decoded["cv"] == "abc123"


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
    fake_flow.code_verifier = None  # no PKCE → URL passes through unchanged
    fake_flow.authorization_url.return_value = (
        f"https://accounts.google.com/o/oauth2/auth?state={state}&scope=x", state,
    )
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=fake_flow):
        r = client.get(f"/oauth/start?state={state}", follow_redirects=False)
    assert r.status_code == 302
    assert "accounts.google.com" in r.headers["location"]


def test_oauth_start_rewrites_state_when_pkce_present(client, app):
    """If the Flow generated a code_verifier, the redirect URL must contain a
    freshly-signed state that bakes in that verifier."""
    import web
    state = web.sign_state(1)

    fake_flow = MagicMock()
    fake_flow.code_verifier = "pkce-verifier-xyz"
    fake_flow.authorization_url.return_value = (
        f"https://accounts.google.com/o/oauth2/auth?state={state}&scope=x", state,
    )
    with patch("google_auth_oauthlib.flow.Flow.from_client_secrets_file", return_value=fake_flow):
        r = client.get(f"/oauth/start?state={state}", follow_redirects=False)
    assert r.status_code == 302
    import urllib.parse as up
    qs = dict(up.parse_qsl(up.urlsplit(r.headers["location"]).query))
    new_state = qs["state"]
    assert new_state != state
    decoded = web.verify_state(new_state)
    assert decoded["user_id"] == 1
    assert decoded["cv"] == "pkce-verifier-xyz"


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


def test_stripe_webhook_verifies_signature_when_secret_set(client, monkeypatch):
    """When STRIPE_WEBHOOK_SECRET is set and a Stripe-Signature header is sent,
    construct_event is called and bad signatures yield 400."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    with patch("stripe_client.construct_event", side_effect=ValueError("bad sig")):
        r = client.post(
            "/stripe/webhook",
            content=b'{"type":"x"}',
            headers={"stripe-signature": "t=1,v1=abc"},
        )
    assert r.status_code == 400


def test_stripe_webhook_signature_ok_routes_to_handler(client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    event = {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "1"}}}
    with patch("stripe_client.construct_event", return_value=event), \
         patch("stripe_client.apply_event_to_db") as apply:
        r = client.post(
            "/stripe/webhook",
            content=b'{"type":"x"}',
            headers={"stripe-signature": "t=1,v1=abc"},
        )
    assert r.status_code == 200
    apply.assert_called_once_with(event)


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
