"""Schedule one-off and recurring actions to be executed by the agent later.

Each action is a free-form natural-language command (same as if the user
typed it). When it fires, we run it through agent.run_agent and send the
result to Telegram.

Storage: scheduled_actions.json — list of action dicts. Reloaded on startup
so jobs survive restarts.
"""

import datetime
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

import pytz
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

import config

logger = logging.getLogger(__name__)

STORE_PATH = Path(__file__).parent / "scheduled_actions.json"

_scheduler = None
_bot_app = None


def init(scheduler, bot_app) -> int:
    """Wire references; load and re-register persisted actions. Returns count loaded."""
    global _scheduler, _bot_app
    _scheduler = scheduler
    _bot_app = bot_app
    return _restore_persisted_jobs()


def _load_store() -> list[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load scheduled_actions.json: %s", e)
        return []


def _save_store(actions: list[dict]) -> None:
    STORE_PATH.write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")


def _restore_persisted_jobs() -> int:
    actions = _load_store()
    if not actions:
        return 0
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    restored = 0
    expired_ids = []
    for action in actions:
        try:
            if action.get("repeat"):
                _add_cron_job(action)
                restored += 1
            else:
                run_at = datetime.datetime.fromisoformat(action["run_at"])
                if run_at <= now:
                    expired_ids.append(action["id"])
                    continue
                _add_date_job(action)
                restored += 1
        except Exception as e:
            logger.warning("Failed to restore action %s: %s", action.get("id"), e)
    if expired_ids:
        _save_store([a for a in actions if a["id"] not in expired_ids])
    return restored


def _add_date_job(action: dict) -> None:
    run_at = datetime.datetime.fromisoformat(action["run_at"])
    _scheduler.add_job(
        _fire_action,
        trigger=DateTrigger(run_date=run_at),
        args=[action["id"]],
        id=action["id"],
        replace_existing=True,
    )


def _add_cron_job(action: dict) -> None:
    repeat = action["repeat"]
    h, m = map(int, action["at_time"].split(":"))
    kwargs = {"hour": h, "minute": m, "timezone": config.TIMEZONE}
    if repeat == "weekdays":
        kwargs["day_of_week"] = "mon-fri"
    elif repeat == "weekend":
        kwargs["day_of_week"] = "sat,sun"
    # "daily" passes only hour/minute
    _scheduler.add_job(
        _fire_action,
        trigger=CronTrigger(**kwargs),
        args=[action["id"]],
        id=action["id"],
        replace_existing=True,
    )


async def _fire_action(action_id: str) -> None:
    actions = _load_store()
    action = next((a for a in actions if a["id"] == action_id), None)
    if not action:
        logger.warning("Scheduled action %s not found in store", action_id)
        return

    text = action["action_text"]
    logger.info("Firing scheduled action %s: %s", action_id, text)

    # Run via agent (imported lazily to avoid circular imports)
    import agent
    try:
        result = agent.run_agent(text, history=[], summaries=[], active_tasks=[], context=None)
        reply = result.get("text") or "(пусто)"
    except Exception as e:
        logger.exception("Scheduled action execution failed: %s", e)
        reply = f"⚠️ Не удалось выполнить: {e}"

    if _bot_app:
        msg = f"⏰ Запланированное: «{text}»\n\n{reply}"
        try:
            await _bot_app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=msg)
        except Exception as e:
            logger.error("Failed to send scheduled reply to Telegram: %s", e)

    # Remove one-shot actions from store after firing
    if not action.get("repeat"):
        _save_store([a for a in actions if a["id"] != action_id])


def schedule_action(
    action_text: str,
    run_at: datetime.datetime,
    repeat: Optional[str] = None,
) -> dict:
    """Schedule a new action. Returns the created action dict."""
    tz = pytz.timezone(config.TIMEZONE)
    if run_at.tzinfo is None:
        run_at = tz.localize(run_at)

    action_id = uuid.uuid4().hex[:8]
    action = {
        "id": action_id,
        "action_text": action_text,
        "run_at": run_at.isoformat(),
        "repeat": repeat,
        "at_time": run_at.strftime("%H:%M") if repeat else None,
        "created_at": datetime.datetime.now(tz).isoformat(),
    }

    actions = _load_store()
    actions.append(action)
    _save_store(actions)

    if _scheduler:
        if repeat:
            _add_cron_job(action)
        else:
            _add_date_job(action)
    return action


def list_actions() -> list[dict]:
    return _load_store()


def cancel_action(action_id: str) -> bool:
    actions = _load_store()
    remaining = [a for a in actions if a["id"] != action_id]
    if len(remaining) == len(actions):
        return False
    _save_store(remaining)
    if _scheduler:
        try:
            _scheduler.remove_job(action_id)
        except Exception:
            pass
    return True
