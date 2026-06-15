"""Phase C migration: add personalization, goals, and groups schema.

Run on the server (idempotent — safe to re-run):

    cd /home/ubuntu/life-agent
    sudo systemctl stop life-agent
    source venv/bin/activate
    python3 infra/migrate_add_personalization_fields.py
    sudo systemctl start life-agent

What it does:

1. ALTER TABLE users to add four personalization columns (city, language,
   news_country, onboarding_state). Each ALTER is wrapped in try/except so
   re-running on an already-migrated DB is safe.
2. db.init_db() — auto-creates the new tables (user_news_feeds, goals,
   goal_progress, groups, group_members, goal_collaborators) via
   Base.metadata.create_all (idempotent).
3. Backfill Daria (looked up by TELEGRAM_CHAT_ID from .env):
   - users.city = 'Нешер'
   - users.language = 'ru'
   - users.news_country = 'IL,RU'
   - users.onboarding_state = 'completed'
   - Three user_news_feeds rows mirroring news_client.LEGACY_NEWS_FEEDS.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import config  # noqa: E402  — provides TELEGRAM_CHAT_ID after .env load
import crypto  # noqa: E402  — kept for parity with other migration scripts
import db  # noqa: E402
import news_client  # noqa: E402

logger = logging.getLogger("migrate_personalization")
logging.basicConfig(level=logging.INFO, format="%(message)s")


# (column_name, sqlite_type, default_sql_literal_or_None)
NEW_USER_COLUMNS = [
    ("city", "TEXT", None),
    ("language", "TEXT", "'ru'"),
    ("news_country", "TEXT", None),
    ("onboarding_state", "TEXT", "'pending'"),
]


def _alter_users_add_columns() -> int:
    """ALTER TABLE users for each new personalization column.

    Returns the number of columns actually added (already-present ones are
    silently skipped via SQLite's "duplicate column name" error).
    """
    from sqlalchemy import text

    engine = db.get_engine()
    added = 0
    for col_name, col_type, default_literal in NEW_USER_COLUMNS:
        ddl = f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
        if default_literal is not None:
            ddl += f" DEFAULT {default_literal}"
        try:
            with engine.begin() as conn:
                conn.execute(text(ddl))
            logger.info("  + users.%s added", col_name)
            added += 1
        except Exception as e:
            msg = str(e).lower()
            if "duplicate column" in msg or "already exists" in msg:
                logger.info("  = users.%s already present", col_name)
            else:
                # Unknown error — re-raise so we don't silently corrupt state.
                raise
    return added


def _backfill_daria(session) -> None:
    telegram_id = int(config.TELEGRAM_CHAT_ID)
    user = db.get_user_by_telegram_id(session, telegram_id)
    if user is None:
        logger.warning(
            "No DB user for TELEGRAM_CHAT_ID=%s — skipping backfill "
            "(run migrate_daria_to_db.py first)", telegram_id,
        )
        return

    desired = {
        "city": "Нешер",
        "language": "ru",
        "news_country": "IL,RU",
        "onboarding_state": "completed",
    }
    changed = []
    for field, value in desired.items():
        if getattr(user, field, None) != value:
            setattr(user, field, value)
            changed.append(field)
    if changed:
        logger.info("Backfilled user %s fields: %s", user.id, ", ".join(changed))
    else:
        logger.info("User %s personalization fields already set — no change", user.id)

    # Backfill user_news_feeds from legacy global feed list.
    existing_urls = {
        row.url
        for row in session.query(db.UserNewsFeed)
        .filter(db.UserNewsFeed.user_id == user.id)
        .all()
    }
    inserted = 0
    for source_name, url in news_client.LEGACY_NEWS_FEEDS.items():
        if url in existing_urls:
            continue
        session.add(db.UserNewsFeed(
            user_id=user.id,
            source_name=source_name,
            url=url,
            enabled=1,
        ))
        inserted += 1
    if inserted:
        logger.info("Inserted %d user_news_feeds row(s) for user %s", inserted, user.id)
    else:
        logger.info("user_news_feeds already populated for user %s", user.id)


def main() -> int:
    logger.info("=== Phase C migration: personalization + goals + groups ===")

    # Step 1: ALTER TABLE users (must run before init_db's create_all, since
    # init_db's metadata only matters for *new* tables; columns on existing
    # tables need explicit ALTER on SQLite).
    db.get_engine()  # ensures DB file/dirs exist
    added = _alter_users_add_columns()
    logger.info("ALTER TABLE users: %d column(s) added", added)

    # Step 2: create_all() for new tables.
    db.init_db()
    logger.info("init_db() — new tables ensured")

    # Step 3: backfill Daria.
    with db.session_scope() as session:
        _backfill_daria(session)

    logger.info("=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
