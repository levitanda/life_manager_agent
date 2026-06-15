"""
Conversation memory: current session + long-term session summaries.

Two modes:
- Legacy single-user (user_id=None): files at project root.
- Multi-tenant (user_id given): files under data/users/{user_id}/.

Flow:
  - Active session: last _MAX_MESSAGES messages in conversation_history.json
  - On session end (8h inactivity): Claude generates a summary → appended to session_summaries.jsonl
  - New session start: loads last _SUMMARIES_IN_CONTEXT summaries as long-term memory
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Optional

import pytz

import config
import llm

logger = logging.getLogger(__name__)

_HISTORY_BASENAME = "conversation_history.json"
_SUMMARIES_BASENAME = "session_summaries.jsonl"

_MAX_MESSAGES = 20        # raw messages kept per active session
_STALE_HOURS = 8          # inactivity threshold → session ends
_SUMMARIES_IN_CONTEXT = 15  # how many past summaries to include in each request


# ─── Path resolution ──────────────────────────────────────────────────────────


def _user_dir(user_id: Optional[int]) -> Path:
    if user_id is None:
        return Path(".")
    import db
    p = Path(db.data_dir(), "users", str(user_id))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _history_path(user_id: Optional[int]) -> str:
    return str(_user_dir(user_id) / _HISTORY_BASENAME)


def _summaries_path(user_id: Optional[int]) -> str:
    return str(_user_dir(user_id) / _SUMMARIES_BASENAME)


def _user_tz(user_id: Optional[int]):
    """Resolve the timezone for a user. Falls back to config.TIMEZONE when
    user_id is None (legacy single-user path) or the DB lookup fails."""
    if user_id is None:
        return pytz.timezone(config.TIMEZONE)
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u and u.timezone:
                return pytz.timezone(u.timezone)
    except Exception:
        pass
    return pytz.timezone(config.TIMEZONE)


# ── Raw session history ────────────────────────────────────────────────────────


def _load(user_id: Optional[int] = None) -> dict:
    path = _history_path(user_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict, user_id: Optional[int] = None) -> None:
    with open(_history_path(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_stale(last_ts: str, user_id: Optional[int] = None) -> bool:
    tz = _user_tz(user_id)
    last = datetime.datetime.fromisoformat(last_ts)
    if not last.tzinfo:
        last = tz.localize(last)
    return datetime.datetime.now(tz) - last > datetime.timedelta(hours=_STALE_HOURS)


def get_history(user_id: Optional[int] = None) -> list[dict]:
    """Return current session messages. If session is stale, summarize first and return []."""
    data = _load(user_id)
    entry = data.get("session", {})
    msgs = entry.get("messages", [])
    last_ts = entry.get("last_ts")

    if last_ts and msgs and _is_stale(last_ts, user_id=user_id):
        _summarize_and_save(msgs, last_ts, user_id=user_id)
        _save({}, user_id)
        return []

    return msgs


def add(user_msg: str, assistant_msg: str, user_id: Optional[int] = None) -> None:
    data = _load(user_id)
    entry = data.get("session", {"messages": []})
    msgs = entry["messages"]
    msgs.append({"role": "user", "content": user_msg})
    msgs.append({"role": "assistant", "content": assistant_msg[:600]})
    entry["messages"] = msgs[-_MAX_MESSAGES:]
    tz = _user_tz(user_id)
    entry["last_ts"] = datetime.datetime.now(tz).isoformat()
    data["session"] = entry
    _save(data, user_id)


def summarize_current_session(user_id: Optional[int] = None) -> bool:
    """Summarize and clear the active session. Returns True if there was something to summarize."""
    data = _load(user_id)
    entry = data.get("session", {})
    msgs = entry.get("messages", [])
    last_ts = entry.get("last_ts")
    if not msgs:
        return False
    _summarize_and_save(msgs, last_ts or datetime.datetime.now().isoformat(), user_id=user_id)
    _save({}, user_id)
    return True


# ── Summaries ──────────────────────────────────────────────────────────────────


def _summarize_and_save(
    messages: list[dict],
    session_ts: str,
    user_id: Optional[int] = None,
) -> None:
    if not messages:
        return

    lines = []
    for msg in messages:
        role = "Пользователь" if msg["role"] == "user" else "Ассистент"
        lines.append(f"{role}: {msg['content'][:400]}")

    prompt = (
        "Составь краткое резюме этой беседы (5-7 предложений на русском).\n"
        "Включи:\n"
        "- Что обсуждалось и что было сделано (задачи, письма, планы)\n"
        "- Важные факты о пользователе (настроение, события, упомянутые люди)\n"
        "- Что полезно помнить в будущих разговорах\n\n"
        "Беседа:\n" + "\n".join(lines)
    )
    try:
        result = llm.chat(
            llm.MODEL_NOVA_LITE,
            "",
            [{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        summary_text = result["text"].strip()
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)
        summary_text = f"Сессия без резюме (ошибка: {e})"

    try:
        tz = _user_tz(user_id)
        dt = datetime.datetime.fromisoformat(session_ts)
        if not dt.tzinfo:
            dt = tz.localize(dt)
        else:
            dt = dt.astimezone(tz)
        date_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        date_str = session_ts[:16]

    entry = {"date": date_str, "summary": summary_text}
    with open(_summaries_path(user_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("Session summary saved for %s (user=%s)", date_str, user_id)

    # Mirror the session summary into the personal diary as today's wrap-up.
    try:
        import diary
        diary.append(f"Резюме сессии: {summary_text}", user_id=user_id)
    except Exception as e:
        logger.warning("Diary mirror of session summary failed: %s", e)


def get_recent_summaries(
    n: int = _SUMMARIES_IN_CONTEXT,
    user_id: Optional[int] = None,
) -> list[dict]:
    path = _summaries_path(user_id)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    result = []
    for line in lines[-n:]:
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def clear_all(user_id: Optional[int] = None) -> None:
    """Wipe everything for a user — use only for debugging."""
    for f in (_history_path(user_id), _summaries_path(user_id)):
        if os.path.exists(f):
            os.remove(f)
