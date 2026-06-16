"""Phase H migration: add per-user telegram_username + group invite plumbing.

Idempotent — safe to re-run.

ALTERs:
- users.telegram_username TEXT (for /group_invite @handle lookup)
- group_members.accepted_at DATETIME (NULL == pending invite)
- group_members.invited_by INTEGER

Run on the server:

    cd /home/ubuntu/life-agent
    sudo systemctl stop life-agent
    source venv/bin/activate
    python3 infra/migrate_add_group_pending.py
    sudo systemctl start life-agent
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

import db  # noqa: E402

logger = logging.getLogger("migrate_phase_h")
logging.basicConfig(level=logging.INFO, format="%(message)s")


COLUMNS = [
    ("users", "telegram_username", "TEXT"),
    ("group_members", "accepted_at", "DATETIME"),
    ("group_members", "invited_by", "INTEGER"),
]


def _alter_add(conn, table: str, col: str, col_type: str) -> bool:
    from sqlalchemy import text
    ddl = f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
    try:
        conn.execute(text(ddl))
        logger.info("  + %s.%s added", table, col)
        return True
    except Exception as e:
        msg = str(e).lower()
        if "duplicate column" in msg or "already exists" in msg:
            logger.info("  = %s.%s already present", table, col)
            return False
        raise


def main() -> int:
    logger.info("=== Phase H migration: group invites + telegram_username ===")
    engine = db.get_engine()
    added = 0
    for table, col, ctype in COLUMNS:
        with engine.begin() as conn:
            if _alter_add(conn, table, col, ctype):
                added += 1
    db.init_db()  # create_all for any not-yet-present tables (idempotent)
    logger.info("Phase H migration complete — %d new column(s) added", added)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
