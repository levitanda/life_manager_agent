"""APScheduler setup — heartbeat fires per-user digests in each user's local tz.

Strategy:
- Legacy single-user mode (no users in DB): keep the old fixed-time cron jobs
  for Daria so her behavior is unchanged until migration.
- Multi-user mode (users exist in DB): one minute-heartbeat job loops over
  every active user and fires their morning/evening/daily-summary jobs if
  their *local* time matches.

This avoids the 'one cron per user' explosion in APScheduler and means new
users start receiving digests as soon as they finish onboarding — no
restart needed.
"""

from __future__ import annotations

import datetime
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

import bot_handlers
import config
import conversation
import scheduled_actions

logger = logging.getLogger(__name__)


def _parse_time(time_str: str) -> tuple[int, int]:
    h, m = time_str.split(":")
    return int(h), int(m)


# ─── Heartbeat loop (multi-user) ─────────────────────────────────────────────


def _active_users() -> list[dict]:
    """Snapshot of users with access — returns plain dicts so callers don't
    have to hold a DB session."""
    import db
    out = []
    try:
        with db.session_scope() as s:
            for u in s.query(db.User).all():
                if u.has_access():
                    out.append({
                        "id": u.id,
                        "telegram_chat_id": int(u.telegram_chat_id),
                        "timezone": u.timezone,
                        "morning_time": u.morning_time,
                        "evening_time": u.evening_time,
                    })
    except Exception as e:
        logger.warning("scheduler heartbeat: DB read failed: %s", e)
    return out


def _hhmm_in_tz(user: dict, now_utc: datetime.datetime) -> str:
    """Return the user's current local HH:MM (5-char string)."""
    tz = pytz.timezone(user["timezone"])
    return now_utc.astimezone(tz).strftime("%H:%M")


async def _heartbeat(app: Application) -> None:
    """Fires every minute. Dispatches per-user jobs that match the current local time."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    for user in _active_users():
        try:
            now_local = _hhmm_in_tz(user, now_utc)
        except Exception:
            continue
        if now_local == user["morning_time"]:
            try:
                await bot_handlers._send_morning_digest(app, target_user_id=user["id"])
            except Exception as e:
                logger.exception("morning digest failed for user=%s: %s", user["id"], e)
        if now_local == user["evening_time"]:
            try:
                await bot_handlers._send_evening_checkin(app, target_user_id=user["id"])
            except Exception as e:
                logger.exception("evening check-in failed for user=%s: %s", user["id"], e)
        if now_local == "23:30":
            try:
                conversation.summarize_current_session(user_id=user["id"])
            except Exception as e:
                logger.warning("daily summary failed for user=%s: %s", user["id"], e)


# ─── Public entry ───────────────────────────────────────────────────────────


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    tz = pytz.timezone(config.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    if _has_any_users():
        # Multi-user heartbeat — one job, fires every minute, fans out to users
        scheduler.add_job(
            _heartbeat,
            trigger="cron",
            minute="*",
            args=[app],
            id="heartbeat",
            name="Per-user heartbeat",
            replace_existing=True,
        )
        logger.info("Multi-user heartbeat scheduled (every minute, per-user dispatch)")
    else:
        # Legacy single-user crons (Daria pre-migration)
        mh, mm = _parse_time(config.MORNING_TIME)
        eh, em = _parse_time(config.EVENING_TIME)

        scheduler.add_job(
            bot_handlers._send_morning_digest,
            trigger="cron", hour=mh, minute=mm, args=[app],
            id="morning_digest", name="Morning digest", replace_existing=True,
        )
        logger.info("Scheduled morning digest at %s %s", config.MORNING_TIME, config.TIMEZONE)

        scheduler.add_job(
            bot_handlers._send_evening_checkin,
            trigger="cron", hour=eh, minute=em, args=[app],
            id="evening_checkin", name="Evening check-in", replace_existing=True,
        )
        logger.info("Scheduled evening check-in at %s %s", config.EVENING_TIME, config.TIMEZONE)

        async def _daily_summarize():
            saved = conversation.summarize_current_session()
            if saved:
                logger.info("Daily session summary saved.")

        scheduler.add_job(
            _daily_summarize,
            trigger="cron", hour=23, minute=30,
            id="daily_summary", name="Daily session summary", replace_existing=True,
        )
        logger.info("Scheduled daily summary at 23:30 %s", config.TIMEZONE)

    restored = scheduled_actions.init(scheduler, app)
    if restored:
        logger.info("Restored %d scheduled actions from disk", restored)

    return scheduler


def _has_any_users() -> bool:
    """Returns True once at least one user is registered in the DB."""
    import db
    try:
        with db.session_scope() as s:
            return s.query(db.User).count() > 0
    except Exception:
        return False
