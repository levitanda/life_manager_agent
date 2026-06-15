"""FastAPI sidecar — public-facing HTTPS endpoints for the bot.

Routes:
  GET  /health                           — liveness
  GET  /oauth/start?state=<signed_uid>   — redirect user to Google consent
  GET  /oauth/callback?code&state        — exchange code, store encrypted token
  POST /stripe/webhook                   — handle subscription lifecycle
  POST /alice/{user_id}/{secret}         — Yandex Alice skill webhook (per-user)

Runs as a separate systemd unit on port 8000, fronted by nginx at
https://dalev.click/. The Telegram bot itself runs in a different process
and shares the SQLite DB via `db.py`.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

# Load .env BEFORE any module reads env vars (MASTER_KEY etc).
# systemd's EnvironmentFile parser is too strict for keys with special chars
# like the trailing `=` of base64-encoded Fernet keys; python-dotenv is
# lenient and reliable.
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

logger = logging.getLogger(__name__)

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
OAUTH_REDIRECT_URI = f"{PUBLIC_BASE_URL}/oauth/callback"
STATE_MAX_AGE_SECONDS = 60 * 30  # 30 min

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def _serializer() -> URLSafeTimedSerializer:
    """State-token serializer. Master key is reused — single secret per env."""
    secret = os.environ.get("MASTER_KEY") or os.environ.get("STATE_SECRET") or "dev-secret"
    return URLSafeTimedSerializer(secret, salt="oauth-state-v1")


def sign_state(user_id: int, code_verifier: Optional[str] = None) -> str:
    """Sign a state token. `code_verifier` (PKCE) is optional — included by
    /oauth/start so /oauth/callback can complete the exchange with the same
    verifier Google saw at consent time."""
    payload: dict = {"user_id": int(user_id)}
    if code_verifier is not None:
        payload["cv"] = code_verifier
    return _serializer().dumps(payload)


def verify_state(token: str) -> dict:
    """Returns the decoded payload {user_id, cv?}."""
    try:
        data = _serializer().loads(token, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}")
    data["user_id"] = int(data["user_id"])
    return data


def _dashboard_serializer() -> URLSafeTimedSerializer:
    """Separate salt so dashboard tokens can't be replayed as OAuth state."""
    secret = os.environ.get("MASTER_KEY") or os.environ.get("STATE_SECRET") or "dev-secret"
    return URLSafeTimedSerializer(secret, salt="dashboard-token-v1")


# Dashboard tokens live longer than OAuth state (30 days) since users
# may bookmark the URL.
DASHBOARD_TOKEN_MAX_AGE = 60 * 60 * 24 * 30


def sign_dashboard_token(user_id: int) -> str:
    return _dashboard_serializer().dumps({"user_id": int(user_id), "purpose": "dashboard"})


def verify_dashboard_token(token: str) -> int:
    try:
        data = _dashboard_serializer().loads(token, max_age=DASHBOARD_TOKEN_MAX_AGE)
    except BadSignature as e:
        raise HTTPException(status_code=400, detail=f"Invalid dashboard token: {e}")
    if data.get("purpose") != "dashboard":
        raise HTTPException(status_code=400, detail="Wrong token purpose")
    return int(data["user_id"])


# ─── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(title="life-agent-web", version="0.1")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/oauth/start")
    async def oauth_start(state: str) -> RedirectResponse:
        """Redirect the user to Google's consent screen.

        Decodes user_id from `state`, builds an authorization URL with PKCE,
        and re-signs a fresh state that bakes in the code_verifier so the
        callback can complete the exchange.
        """
        data = verify_state(state)
        user_id = data["user_id"]
        from google_auth_oauthlib.flow import Flow
        import config

        flow = Flow.from_client_secrets_file(
            config.GOOGLE_CREDENTIALS_FILE,
            scopes=GOOGLE_SCOPES,
            redirect_uri=OAUTH_REDIRECT_URI,
        )
        url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state,
        )
        # If the Flow generated a PKCE verifier, re-sign state with it and
        # patch the URL so the verifier survives the round-trip.
        verifier = getattr(flow, "code_verifier", None)
        if verifier:
            import urllib.parse as up
            new_state = sign_state(user_id, code_verifier=verifier)
            parts = up.urlsplit(url)
            q = dict(up.parse_qsl(parts.query, keep_blank_values=True))
            q["state"] = new_state
            url = up.urlunsplit((
                parts.scheme, parts.netloc, parts.path,
                up.urlencode(q), parts.fragment,
            ))
        logger.info("OAuth start for user_id=%s (pkce=%s)", user_id, bool(verifier))
        return RedirectResponse(url, status_code=302)

    @app.get("/oauth/callback")
    async def oauth_callback(code: str, state: str) -> HTMLResponse:
        """Exchange the auth code for tokens and persist encrypted in DB."""
        data = verify_state(state)
        user_id = data["user_id"]
        code_verifier = data.get("cv")
        from google_auth_oauthlib.flow import Flow
        import config
        import crypto
        import db
        import datetime

        flow = Flow.from_client_secrets_file(
            config.GOOGLE_CREDENTIALS_FILE,
            scopes=GOOGLE_SCOPES,
            redirect_uri=OAUTH_REDIRECT_URI,
            state=state,
        )
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        creds = flow.credentials
        payload = json.loads(creds.to_json())
        scopes_str = " ".join(payload.get("scopes") or GOOGLE_SCOPES)

        with db.session_scope() as session:
            user = session.get(db.User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail=f"Unknown user_id={user_id}")
            row = session.get(db.GoogleToken, user_id)
            enc = crypto.encrypt_json(payload)
            if row is None:
                session.add(db.GoogleToken(
                    user_id=user_id,
                    token_json_encrypted=enc,
                    scopes=scopes_str,
                    refreshed_at=datetime.datetime.utcnow(),
                ))
            else:
                row.token_json_encrypted = enc
                row.scopes = scopes_str
                row.refreshed_at = datetime.datetime.utcnow()

        logger.info("OAuth callback success for user_id=%s", user_id)
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:48px;text-align:center'>"
            "<h1>✅ Готово!</h1>"
            "<p>Google аккаунт подключён. Возвращайся в Telegram-бот.</p>"
            "</body></html>"
        )

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request) -> JSONResponse:
        """Handle subscription lifecycle events from Stripe.

        When STRIPE_WEBHOOK_SECRET is set, the Stripe-Signature header is
        verified via stripe.Webhook.construct_event. If verification fails,
        return 400. Without a secret, fall back to plain JSON parse (dev mode).
        """
        body = await request.body()
        sig = request.headers.get("stripe-signature")
        secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

        if secret and sig:
            try:
                import stripe_client
                event = stripe_client.construct_event(body, sig)
            except Exception as e:
                logger.warning("Stripe signature verification failed: %s", e)
                raise HTTPException(status_code=400, detail="bad signature")
        else:
            try:
                event = json.loads(body)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")
            if secret and not sig:
                logger.warning("Stripe webhook hit without Stripe-Signature header")

        try:
            import stripe_client
            stripe_client.apply_event_to_db(event)
        except Exception as e:
            logger.exception("Stripe webhook handler failed: %s", e)
            # Return 200 anyway — Stripe retries on 5xx, we'd rather log and move on.
        return JSONResponse({"received": True})

    @app.post("/alice/{user_id}/{secret}")
    async def alice_webhook(user_id: int, secret: str, request: Request) -> JSONResponse:
        """Per-user Yandex Alice skill webhook. The {secret} path segment is
        a per-user token stored in user_integrations to prevent random hits."""
        import db
        import crypto

        with db.session_scope() as s:
            user = s.get(db.User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            row = (
                s.query(db.UserIntegration)
                .filter_by(user_id=user_id, integration="alice")
                .one_or_none()
            )
            if not row or not row.enabled or not row.config_json_encrypted:
                raise HTTPException(status_code=403, detail="alice disabled")
            cfg = crypto.decrypt_json(row.config_json_encrypted)
            if cfg.get("secret") != secret:
                raise HTTPException(status_code=403, detail="bad secret")

        # Full Alice protocol handler comes in Phase 7. Stub returns acknowledged.
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        logger.info("Alice webhook for user=%s session=%s", user_id, payload.get("session", {}).get("session_id"))
        return JSONResponse({
            "response": {"text": "Готово.", "end_session": False},
            "version": payload.get("version", "1.0"),
        })

    @app.get("/dashboard/{user_id}")
    async def dashboard_html(user_id: int, token: str) -> HTMLResponse:
        """Per-user web dashboard. Token is signed via sign_dashboard_token
        from inside the bot; URL is sent to the user as a button in their
        /dashboard Telegram reply."""
        verified_id = verify_dashboard_token(token)
        if verified_id != user_id:
            raise HTTPException(status_code=403, detail="token user mismatch")
        try:
            import dashboard as dash
            return HTMLResponse(dash.render_html_dashboard(user_id))
        except Exception as e:
            logger.exception("dashboard render failed for user=%s: %s", user_id, e)
            return HTMLResponse(
                f"<html><body><h1>Dashboard error</h1><pre>{e}</pre></body></html>",
                status_code=500,
            )

    return app


# ─── Module-level singleton (uvicorn entry point) ─────────────────────────────

app = create_app()
