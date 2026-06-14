"""One-off: migrate Daria's env-backed integrations into user_integrations.

For pushover/tuya/vesync the credentials currently live in .env as global
env vars (legacy single-user). After Phase 7 the multi-tenant code reads
from user_integrations instead, so the /settings menu shows ⚪ for these
even though they're configured.

Run once on the server:

    python3 infra/migrate_daria_integrations.py

Idempotent — re-running is safe (overwrites existing rows with current
env values).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

import config  # noqa: E402
import crypto  # noqa: E402
import db  # noqa: E402

logger = logging.getLogger("migrate_integrations")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def _upsert(session, user_id: int, integration: str, cfg: dict) -> None:
    row = (
        session.query(db.UserIntegration)
        .filter_by(user_id=user_id, integration=integration)
        .one_or_none()
    )
    enc = crypto.encrypt_json(cfg)
    if row is None:
        session.add(db.UserIntegration(
            user_id=user_id, integration=integration, enabled=1,
            config_json_encrypted=enc,
        ))
        logger.info("Inserted %s for user %s", integration, user_id)
    else:
        row.enabled = 1
        row.config_json_encrypted = enc
        logger.info("Updated %s for user %s", integration, user_id)


def main() -> int:
    telegram_id = int(config.TELEGRAM_CHAT_ID)
    db.init_db()
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, telegram_id)
        if user is None:
            logger.error("No DB user for TELEGRAM_CHAT_ID=%s — run migrate_daria_to_db.py first", telegram_id)
            return 1
        user_id = user.id

        # Pushover
        pkey = os.environ.get("PUSHOVER_USER_KEY")
        ptoken = os.environ.get("PUSHOVER_APP_TOKEN")
        if pkey and ptoken:
            _upsert(s, user_id, "pushover", {"user_key": pkey, "app_token": ptoken})
        else:
            logger.info("Pushover env not set — skipping")

        # Tuya
        tuya_required = ["TUYA_API_KEY", "TUYA_API_SECRET", "TUYA_API_REGION", "TUYA_USER_ID"]
        if all(os.environ.get(k) for k in tuya_required):
            _upsert(s, user_id, "tuya", {
                "api_key": os.environ["TUYA_API_KEY"],
                "api_secret": os.environ["TUYA_API_SECRET"],
                "region": os.environ["TUYA_API_REGION"],
                "user_id": os.environ["TUYA_USER_ID"],
            })
        else:
            logger.info("Tuya env not complete — skipping")

        # VeSync
        if all(os.environ.get(k) for k in ("VESYNC_EMAIL", "VESYNC_PASSWORD", "VESYNC_COUNTRY")):
            _upsert(s, user_id, "vesync", {
                "email": os.environ["VESYNC_EMAIL"],
                "password": os.environ["VESYNC_PASSWORD"],
                "country": os.environ["VESYNC_COUNTRY"],
            })
        else:
            logger.info("VeSync env not complete — skipping")

        # Diary Google Doc — default ON for legacy user (mirror was previously implicit)
        _upsert(s, user_id, "diary_doc", {"enabled": True})

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
