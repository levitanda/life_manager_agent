"""One-off migration: bootstrap Daria as user_id=1 in the new multi-tenant DB.

Run on the server BEFORE switching the bot to multi-tenant mode:

    cd /home/ubuntu/life-agent
    sudo systemctl stop life-agent           # avoid races with the running bot
    source venv/bin/activate
    python3 infra/migrate_daria_to_db.py
    sudo systemctl start life-agent

What it does (idempotent — safe to re-run):

1. init_db() — create schema + seed LEVITANONLY.
2. Create or fetch the user row keyed by TELEGRAM_CHAT_ID from .env.
   Mark subscription_status='promo' (Daria gets lifetime free via promo).
3. Read google_token.json from project root, encrypt with MASTER_KEY,
   upsert into google_tokens table.
4. Move per-user flat files from project root → data/users/1/.
   Already-moved files are left alone.

Files moved:
  - conversation_history.json
  - session_summaries.jsonl
  - diary.md
  - diary_doc.json
  - scheduled_actions.json
  - a2a_agents.json
  - whatsapp_groups.json
  - last_digest.txt
  - pending_alice_message.txt
  - personality.json (if present)

Files NOT moved (still global, will be migrated in later phases):
  - google_credentials.json (OAuth app credentials — single per project)
  - .env (deployment secrets — single per process)
  - a2a_clients.json (A2A server auth — single per process)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

# Ensure project root is importable when the script is run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import config  # noqa: E402  — provides TELEGRAM_CHAT_ID after .env load
import crypto  # noqa: E402
import db  # noqa: E402

logger = logging.getLogger("migrate_daria")
logging.basicConfig(level=logging.INFO, format="%(message)s")


PER_USER_FILES = [
    "conversation_history.json",
    "session_summaries.jsonl",
    "diary.md",
    "diary_doc.json",
    "scheduled_actions.json",
    "a2a_agents.json",
    "whatsapp_groups.json",
    "last_digest.txt",
    "pending_alice_message.txt",
    "personality.json",
]


def _ensure_master_key() -> None:
    if not os.environ.get("MASTER_KEY"):
        # Generate and persist a key in .env so subsequent boots see it.
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key().decode()
        env_path = PROJECT_ROOT / ".env"
        with env_path.open("a", encoding="utf-8") as f:
            f.write(f"\nMASTER_KEY={new_key}\n")
        os.environ["MASTER_KEY"] = new_key
        logger.info("Generated MASTER_KEY and appended it to .env")


def _upsert_daria(session) -> "db.User":
    telegram_id = int(config.TELEGRAM_CHAT_ID)
    existing = db.get_user_by_telegram_id(session, telegram_id)
    if existing:
        logger.info("User already exists: id=%s telegram=%s", existing.id, telegram_id)
        return existing
    user = db.create_user(
        session,
        telegram_user_id=telegram_id,
        telegram_chat_id=telegram_id,
        display_name="Daria",
        timezone=os.environ.get("TIMEZONE", "Asia/Jerusalem"),
    )
    logger.info("Created user id=%s for Daria (telegram=%s)", user.id, telegram_id)
    return user


def _grant_promo(session, user: "db.User") -> None:
    if user.subscription_status == "promo":
        logger.info("Promo already active — skipping")
        return
    ok, msg = db.redeem_promo(session, user, "LEVITANONLY")
    logger.info("Promo redemption: ok=%s msg=%s", ok, msg)


def _store_google_token(session, user: "db.User") -> None:
    token_path = PROJECT_ROOT / "google_token.json"
    if not token_path.exists():
        logger.warning("google_token.json not found — skipping token upsert")
        return
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    scopes = " ".join(payload.get("scopes") or [])
    existing = session.get(db.GoogleToken, user.id)
    if existing:
        existing.token_json_encrypted = crypto.encrypt_json(payload)
        existing.scopes = scopes
        logger.info("Updated google_token row for user %s", user.id)
    else:
        session.add(db.GoogleToken(
            user_id=user.id,
            token_json_encrypted=crypto.encrypt_json(payload),
            scopes=scopes,
        ))
        logger.info("Inserted google_token row for user %s (%d scopes)", user.id, len(payload.get("scopes") or []))


def _move_flat_files(user: "db.User") -> None:
    target_dir = user.data_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    skipped_present = 0
    skipped_missing = 0
    for name in PER_USER_FILES:
        src = PROJECT_ROOT / name
        dst = target_dir / name
        if not src.exists():
            skipped_missing += 1
            continue
        if dst.exists():
            logger.info("  %s — already in %s, skipping (source kept as backup)", name, dst)
            skipped_present += 1
            continue
        shutil.move(str(src), str(dst))
        logger.info("  %s → %s", name, dst)
        moved += 1
    logger.info(
        "Files: moved=%d, already-present=%d, source-missing=%d",
        moved, skipped_present, skipped_missing,
    )


def main() -> int:
    logger.info("=== Daria → multi-tenant migration ===")
    _ensure_master_key()
    db.init_db()

    with db.session_scope() as session:
        user = _upsert_daria(session)
        _grant_promo(session, user)
        _store_google_token(session, user)
        _move_flat_files(user)
        # Capture before session closes so we can log post-commit
        user_id = user.id
        user_dir = str(user.data_dir)

    logger.info("=== Done ===")
    logger.info("Daria is user_id=%s with subscription_status=promo.", user_id)
    logger.info("Per-user data dir: %s", user_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
