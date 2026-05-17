"""APScheduler setup for morning digest and evening check-in."""

import asyncio
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

import bot_handlers
import config
import conversation

logger = logging.getLogger(__name__)


def _parse_time(time_str: str) -> tuple[int, int]:
    h, m = time_str.split(":")
    return int(h), int(m)


def setup_scheduler(app: Application) -> AsyncIOScheduler:
    tz = pytz.timezone(config.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    mh, mm = _parse_time(config.MORNING_TIME)
    eh, em = _parse_time(config.EVENING_TIME)

    scheduler.add_job(
        bot_handlers._send_morning_digest,
        trigger="cron",
        hour=mh,
        minute=mm,
        args=[app],
        id="morning_digest",
        name="Morning digest",
        replace_existing=True,
    )
    logger.info("Scheduled morning digest at %s %s", config.MORNING_TIME, config.TIMEZONE)

    scheduler.add_job(
        bot_handlers._send_evening_checkin,
        trigger="cron",
        hour=eh,
        minute=em,
        args=[app],
        id="evening_checkin",
        name="Evening check-in",
        replace_existing=True,
    )
    logger.info("Scheduled evening check-in at %s %s", config.EVENING_TIME, config.TIMEZONE)

    async def _daily_summarize():
        saved = conversation.summarize_current_session()
        if saved:
            logger.info("Daily session summary saved.")

    scheduler.add_job(
        _daily_summarize,
        trigger="cron",
        hour=23,
        minute=30,
        id="daily_summary",
        name="Daily session summary",
        replace_existing=True,
    )
    logger.info("Scheduled daily summary at 23:30 %s", config.TIMEZONE)

    return scheduler
