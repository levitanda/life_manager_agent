"""
Conversation memory: current session + long-term session summaries.

Flow:
  - Active session: last _MAX_MESSAGES messages in conversation_history.json
  - On session end (8h inactivity): Claude generates a summary → appended to session_summaries.jsonl
  - New session start: loads last _SUMMARIES_IN_CONTEXT summaries as long-term memory
"""

import datetime
import json
import logging
import os

import anthropic
import pytz

import config

logger = logging.getLogger(__name__)

_HISTORY_FILE = "conversation_history.json"
_SUMMARIES_FILE = "session_summaries.jsonl"

_MAX_MESSAGES = 20        # raw messages kept per active session
_STALE_HOURS = 8          # inactivity threshold → session ends
_SUMMARIES_IN_CONTEXT = 15  # how many past summaries to include in each request


# ── Raw session history ────────────────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(_HISTORY_FILE):
        with open(_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_stale(last_ts: str) -> bool:
    tz = pytz.timezone(config.TIMEZONE)
    last = datetime.datetime.fromisoformat(last_ts)
    if not last.tzinfo:
        last = tz.localize(last)
    return datetime.datetime.now(tz) - last > datetime.timedelta(hours=_STALE_HOURS)


def get_history() -> list[dict]:
    """
    Return current session messages.
    If session is stale, summarize it first and return [] to start fresh.
    """
    data = _load()
    entry = data.get("session", {})
    msgs = entry.get("messages", [])
    last_ts = entry.get("last_ts")

    if last_ts and msgs and _is_stale(last_ts):
        _summarize_and_save(msgs, last_ts)
        _save({})
        return []

    return msgs


def add(user_msg: str, assistant_msg: str) -> None:
    data = _load()
    entry = data.get("session", {"messages": []})
    msgs = entry["messages"]
    msgs.append({"role": "user", "content": user_msg})
    msgs.append({"role": "assistant", "content": assistant_msg[:600]})
    entry["messages"] = msgs[-_MAX_MESSAGES:]
    tz = pytz.timezone(config.TIMEZONE)
    entry["last_ts"] = datetime.datetime.now(tz).isoformat()
    data["session"] = entry
    _save(data)


def summarize_current_session() -> bool:
    """Summarize and clear the active session. Returns True if there was something to summarize."""
    data = _load()
    entry = data.get("session", {})
    msgs = entry.get("messages", [])
    last_ts = entry.get("last_ts")
    if not msgs:
        return False
    _summarize_and_save(msgs, last_ts or datetime.datetime.now().isoformat())
    _save({})
    return True


# ── Summaries ──────────────────────────────────────────────────────────────────

def _summarize_and_save(messages: list[dict], session_ts: str) -> None:
    if not messages:
        return

    lines = []
    for msg in messages:
        role = "Пользователь" if msg["role"] == "user" else "Ассистент"
        lines.append(f"{role}: {msg['content'][:400]}")

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        result = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": (
                    "Составь краткое резюме этой беседы (5-7 предложений на русском).\n"
                    "Включи:\n"
                    "- Что обсуждалось и что было сделано (задачи, письма, планы)\n"
                    "- Важные факты о пользователе (настроение, события, упомянутые люди)\n"
                    "- Что полезно помнить в будущих разговорах\n\n"
                    "Беседа:\n" + "\n".join(lines)
                ),
            }],
        )
        summary_text = result.content[0].text.strip()
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)
        summary_text = f"Сессия без резюме (ошибка: {e})"

    try:
        tz = pytz.timezone(config.TIMEZONE)
        dt = datetime.datetime.fromisoformat(session_ts)
        if not dt.tzinfo:
            dt = tz.localize(dt)
        date_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        date_str = session_ts[:16]

    entry = {"date": date_str, "summary": summary_text}
    with open(_SUMMARIES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info("Session summary saved for %s", date_str)

    # Mirror the session summary into the personal diary as today's wrap-up.
    # Lazy import to avoid pulling Google API libs at module load.
    try:
        import diary
        diary.append(f"Резюме сессии: {summary_text}")
    except Exception as e:
        logger.warning("Diary mirror of session summary failed: %s", e)


def get_recent_summaries(n: int = _SUMMARIES_IN_CONTEXT) -> list[dict]:
    if not os.path.exists(_SUMMARIES_FILE):
        return []
    with open(_SUMMARIES_FILE, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    result = []
    for line in lines[-n:]:
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def clear_all() -> None:
    """Wipe everything — use only for debugging."""
    for f in (_HISTORY_FILE, _SUMMARIES_FILE):
        if os.path.exists(f):
            os.remove(f)
