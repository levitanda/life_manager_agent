"""Schedule one-off and recurring actions to be executed by the agent later.

Each action is a free-form natural-language command (same as if the user
typed it). When it fires, we run it through agent.run_agent and send the
result to that user's Telegram chat.

Storage: per-user scheduled_actions.json at data/users/{user_id}/ in
multi-tenant mode; project-root scheduled_actions.json in legacy mode.
Actions carry their owning user_id so the runtime can route results back.
"""

from __future__ import annotations

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

LEGACY_STORE_PATH = Path(__file__).parent / "scheduled_actions.json"

_scheduler = None
_bot_app = None


# ─── Path resolution ──────────────────────────────────────────────────────────


def _store_path(user_id: Optional[int]) -> Path:
    if user_id is None:
        return LEGACY_STORE_PATH
    import db
    p = Path(db.data_dir(), "users", str(user_id))
    p.mkdir(parents=True, exist_ok=True)
    return p / "scheduled_actions.json"


def _all_user_ids_with_actions() -> list[Optional[int]]:
    """Return every user_id that has an actions file (plus None for legacy)."""
    out: list[Optional[int]] = []
    if LEGACY_STORE_PATH.exists():
        out.append(None)
    import db
    users_root = Path(db.data_dir(), "users")
    if users_root.exists():
        for d in users_root.iterdir():
            if d.is_dir() and (d / "scheduled_actions.json").exists():
                try:
                    out.append(int(d.name))
                except ValueError:
                    pass
    return out


def _telegram_chat_id_for(user_id: Optional[int]) -> int:
    if user_id is None:
        return int(config.TELEGRAM_CHAT_ID)
    import db
    with db.session_scope() as s:
        u = s.get(db.User, user_id)
        if u is None:
            raise RuntimeError(f"User {user_id} not found")
        return int(u.telegram_chat_id)


# ─── Init / restore ──────────────────────────────────────────────────────────


def init(scheduler, bot_app) -> int:
    """Wire references; load and re-register persisted actions across all users."""
    global _scheduler, _bot_app
    _scheduler = scheduler
    _bot_app = bot_app
    total = 0
    for uid in _all_user_ids_with_actions():
        total += _restore_persisted_jobs(uid)
    return total


def _load_store(user_id: Optional[int] = None) -> list[dict]:
    path = _store_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return []


def _save_store(actions: list[dict], user_id: Optional[int] = None) -> None:
    _store_path(user_id).write_text(
        json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _restore_persisted_jobs(user_id: Optional[int] = None) -> int:
    actions = _load_store(user_id)
    if not actions:
        return 0
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    restored = 0
    expired_ids = []
    for action in actions:
        # Stamp legacy actions with their user_id for routing.
        action.setdefault("user_id", user_id)
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
        _save_store([a for a in actions if a["id"] not in expired_ids], user_id)
    return restored


def _add_date_job(action: dict) -> None:
    run_at = datetime.datetime.fromisoformat(action["run_at"])
    _scheduler.add_job(
        _fire_action,
        trigger=DateTrigger(run_date=run_at),
        args=[action["id"], action.get("user_id")],
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
    _scheduler.add_job(
        _fire_action,
        trigger=CronTrigger(**kwargs),
        args=[action["id"], action.get("user_id")],
        id=action["id"],
        replace_existing=True,
    )


async def _fire_action(action_id: str, user_id: Optional[int] = None) -> None:
    actions = _load_store(user_id)
    action = next((a for a in actions if a["id"] == action_id), None)
    if not action:
        logger.warning("Scheduled action %s not found in store (user=%s)", action_id, user_id)
        return

    text = action["action_text"]
    logger.info("Firing scheduled action %s for user %s: %s", action_id, user_id, text)

    import agent
    try:
        result = agent.run_agent(
            text, history=[], summaries=[], active_tasks=[], context=None,
            user_id=user_id,
        )
        reply = result.get("text") or "(пусто)"
    except Exception as e:
        logger.exception("Scheduled action execution failed: %s", e)
        reply = f"⚠️ Не удалось выполнить: {e}"

    if _bot_app:
        msg = f"⏰ Запланированное: «{text}»\n\n{reply}"
        try:
            chat_id = _telegram_chat_id_for(user_id)
            await _bot_app.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            logger.error("Failed to send scheduled reply to Telegram: %s", e)

    if not action.get("repeat"):
        _save_store([a for a in actions if a["id"] != action_id], user_id)


def schedule_action(
    action_text: str,
    run_at: datetime.datetime,
    repeat: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """Schedule a new action. Returns the created action dict."""
    tz = pytz.timezone(config.TIMEZONE)
    if run_at.tzinfo is None:
        run_at = tz.localize(run_at)

    action_id = uuid.uuid4().hex[:8]
    action = {
        "id": action_id,
        "user_id": user_id,
        "action_text": action_text,
        "run_at": run_at.isoformat(),
        "repeat": repeat,
        "at_time": run_at.strftime("%H:%M") if repeat else None,
        "created_at": datetime.datetime.now(tz).isoformat(),
    }

    actions = _load_store(user_id)
    actions.append(action)
    _save_store(actions, user_id)

    if _scheduler:
        if repeat:
            _add_cron_job(action)
        else:
            _add_date_job(action)
    return action


def list_actions(user_id: Optional[int] = None) -> list[dict]:
    return _load_store(user_id)


def cancel_action(action_id: str, user_id: Optional[int] = None) -> bool:
    actions = _load_store(user_id)
    remaining = [a for a in actions if a["id"] != action_id]
    if len(remaining) == len(actions):
        return False
    _save_store(remaining, user_id)
    if _scheduler:
        try:
            _scheduler.remove_job(action_id)
        except Exception:
            pass
    return True
