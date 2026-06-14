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


def sign_state(user_id: int) -> str:
    return _serializer().dumps({"user_id": int(user_id)})


def verify_state(token: str) -> int:
    try:
        data = _serializer().loads(token, max_age=STATE_MAX_AGE_SECONDS)
    except BadSignature as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}")
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

        `state` is a signed token that encodes the user_id (created by the
        bot when it sent the user the OAuth link).
        """
        user_id = verify_state(state)
        # Lazy import: keeps test-mode startup snappy
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
        logger.info("OAuth start for user_id=%s", user_id)
        return RedirectResponse(url, status_code=302)

    @app.get("/oauth/callback")
    async def oauth_callback(code: str, state: str) -> HTMLResponse:
        """Exchange the auth code for tokens and persist encrypted in DB."""
        user_id = verify_state(state)
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

        Implementation note: signature verification (stripe.Webhook.construct_event)
        is wired in Phase 5 once stripe_client.py is in. For now this endpoint
        accepts JSON and updates subscription_status if the event clearly maps
        to a known case — fail-safe default is to log and return 200 to avoid
        Stripe retry storms during early rollout.
        """
        try:
            body = await request.body()
            event = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        try:
            from stripe_client import apply_event_to_db
            apply_event_to_db(event)
        except ImportError:
            logger.info("Stripe event received (handler not yet wired): %s", event.get("type"))
        except Exception as e:
            logger.exception("Stripe webhook failed: %s", e)
            # Still 200: Stripe will retry on 5xx — for unknown events we'd rather log and move on.
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

    return app


# ─── Module-level singleton (uvicorn entry point) ─────────────────────────────

app = create_app()
