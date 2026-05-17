"""Tool implementations for the Anthropic tool-use agent.

Each tool is a pure business-logic function. It returns a dict:
  {"status": "ok" | "needs_confirmation" | "error",
   "summary": <user-facing string>,
   "data": <optional dict for confirmation flows>}

The agent loop aggregates summaries into the final reply.
Destructive tools (send_email, delete_task, reschedule_task) return
"needs_confirmation" — bot_handlers shows an inline keyboard.
"""

import datetime
import logging
from typing import Optional

import pytz

import calendar_client
import config
import contacts_client
import smart_home
import weather_client

logger = logging.getLogger(__name__)


def _ok(summary: str, **data) -> dict:
    return {"status": "ok", "summary": summary, "data": data}


def _err(summary: str) -> dict:
    return {"status": "error", "summary": summary}


def _confirm(summary: str, **data) -> dict:
    return {"status": "needs_confirmation", "summary": summary, "data": data}


def _resolve_start_dt(date: Optional[str], time: Optional[str]) -> Optional[datetime.datetime]:
    if not time:
        return None
    tz = pytz.timezone(config.TIMEZONE)
    base = datetime.date.fromisoformat(date) if date else datetime.datetime.now(tz).date()
    h, m = map(int, time.split(":"))
    return tz.localize(datetime.datetime(base.year, base.month, base.day, h, m))


# ─── add_task ────────────────────────────────────────────────────────────────

def add_task(
    *,
    title: str,
    task_type: str = "short",
    date: Optional[str] = None,
    end_date: Optional[str] = None,
    time: Optional[str] = None,
    duration_minutes: int = 60,
    attendees: Optional[list] = None,
    _context=None,
    **_kwargs,
) -> dict:
    start_dt = _resolve_start_dt(date, time)
    due_date = datetime.date.fromisoformat(date) if (date and not time) else None
    end_date_p = datetime.date.fromisoformat(end_date) if end_date else None

    attendee_emails, not_found = [], []
    for name in attendees or []:
        email = contacts_client.find_contact_email(name)
        (attendee_emails if email else not_found).append(email or name)

    # Conflict detection for timed events
    if start_dt:
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        try:
            conflicts = calendar_client.get_conflicts(start_dt, end_dt)
        except Exception:
            conflicts = []
        if conflicts:
            if _context is not None:
                _context.user_data["pending_task"] = {
                    "title": title, "task_type": task_type,
                    "due_date": due_date, "end_date": end_date_p,
                    "start_dt": start_dt, "duration_minutes": duration_minutes,
                    "attendee_emails": attendee_emails, "not_found": not_found,
                }
            conflict_list = ", ".join(conflicts[:3])
            return _confirm(
                f"⚠️ В это время уже есть: {conflict_list}\n"
                f"Добавить «{title}» на {start_dt.strftime('%d.%m.%Y в %H:%M')} всё равно?",
                kind="conflict_task",
            )

    try:
        calendar_client.add_task(
            title, task_type, due_date=due_date, end_date=end_date_p,
            start_dt=start_dt, duration_minutes=duration_minutes,
            attendees=attendee_emails or None,
        )
    except Exception as e:
        logger.error("add_task failed: %s", e)
        return _err(f"Не смог добавить задачу: {e}")

    emoji = "⚡" if task_type == "short" else "🎯"
    if start_dt:
        dur_str = f"{duration_minutes} мин." if duration_minutes != 60 else "1 час"
        text = f"{emoji} Добавил: {title}\n📅 {start_dt.strftime('%d.%m.%Y в %H:%M')} ({dur_str})"
    elif due_date and end_date_p:
        text = f"{emoji} Добавил: {title}\n📅 {due_date.strftime('%d.%m.%Y')} — {end_date_p.strftime('%d.%m.%Y')}"
    elif due_date:
        text = f"{emoji} Добавил: {title}\n📅 до {due_date.strftime('%d.%m.%Y')}"
    else:
        text = f"{emoji} Добавил: {title}"
    if attendee_emails:
        text += f"\n👥 Приглашены: {', '.join(attendee_emails)}"
    if not_found:
        text += f"\n⚠️ Не нашёл в контактах: {', '.join(not_found)}"
    return _ok(text)


# ─── complete_task / delete_task ─────────────────────────────────────────────

def _resolve_task_by_number(task_number, all_tasks):
    if isinstance(task_number, list):
        task_number = task_number[0] if task_number else None
    try:
        n = int(task_number) if task_number is not None else None
    except (ValueError, TypeError):
        return None
    if n is None or not (1 <= n <= len(all_tasks)):
        return None
    return all_tasks[n - 1]


def complete_task(*, task_number: int, _active_tasks=None, **_kwargs) -> dict:
    task = _resolve_task_by_number(task_number, _active_tasks or [])
    if not task:
        return _err("Не нашёл такую задачу. Напиши /tasks чтобы увидеть список.")
    try:
        calendar_client.complete_task(task["id"], task["cal_id"])
        return _ok(f"✅ Выполнено: {task['title']}")
    except Exception as e:
        logger.error("complete_task failed: %s", e)
        return _err(f"Не получилось отметить выполненной: {e}")


def delete_task(*, task_number: int, _active_tasks=None, **_kwargs) -> dict:
    task = _resolve_task_by_number(task_number, _active_tasks or [])
    if not task:
        return _err("Не нашёл такую задачу. Напиши /tasks чтобы увидеть список.")
    try:
        calendar_client.delete_task(task["id"], task["cal_id"])
        return _ok(f"🗑 Удалено: {task['title']}")
    except Exception as e:
        logger.error("delete_task failed: %s", e)
        return _err(f"Не получилось удалить: {e}")


# ─── reschedule_task ─────────────────────────────────────────────────────────

def reschedule_task(
    *,
    task_number: int,
    date: Optional[str] = None,
    time: Optional[str] = None,
    duration_minutes: int = 60,
    _active_tasks=None,
    **_kwargs,
) -> dict:
    task = _resolve_task_by_number(task_number, _active_tasks or [])
    if not task:
        return _err("Не нашёл такую задачу.")
    if not time:
        return _err("Укажи новое время (например, «перенеси задачу 2 на завтра в 15:00»).")
    new_start = _resolve_start_dt(date, time)
    new_end = new_start + datetime.timedelta(minutes=duration_minutes)
    try:
        calendar_client.reschedule_task(task["id"], task["cal_id"], new_start, new_end)
        return _ok(f"📅 Перенесено: «{task['title']}» → {new_start.strftime('%d.%m.%Y в %H:%M')}")
    except Exception as e:
        logger.error("reschedule_task failed: %s", e)
        return _err(f"Не удалось перенести: {e}")


# ─── get_weather ─────────────────────────────────────────────────────────────

def get_weather(*, city: Optional[str] = None, date: Optional[str] = None, **_kwargs) -> dict:
    target_date = datetime.date.fromisoformat(date) if date else None
    result = weather_client.get_weather(target_date, city)
    return _ok(f"🌤 {result}") if result else _err("Не удалось получить погоду.")


# ─── find_free_time ──────────────────────────────────────────────────────────

def find_free_time(*, date: Optional[str] = None, duration_minutes: int = 60, **_kwargs) -> dict:
    tz = pytz.timezone(config.TIMEZONE)
    target_date = datetime.date.fromisoformat(date) if date else datetime.datetime.now(tz).date()
    try:
        slots = calendar_client.find_free_slots(target_date, duration_minutes)
    except Exception as e:
        logger.error("find_free_time failed: %s", e)
        return _err("Не удалось проверить расписание.")
    if not slots:
        return _ok(f"Свободных окон от {duration_minutes} мин. на {target_date.strftime('%d.%m')} не нашлось.")
    lines = "\n".join(f"• {s['start']}–{s['end']}" for s in slots)
    return _ok(f"🕐 Свободное время {target_date.strftime('%d.%m')}:\n{lines}")


# ─── show_tasks ──────────────────────────────────────────────────────────────

def show_tasks(*, _active_tasks=None, **_kwargs) -> dict:
    if not _active_tasks:
        return _ok("📋 Активных задач нет.")
    lines = ["📋 *Активные задачи*\n"]
    short = [t for t in _active_tasks if t.get("type") == "short"]
    long_ = [t for t in _active_tasks if t.get("type") == "long"]

    def fmt(t):
        if t.get("time"):
            return f"{t['title']} — {t['due']} {t['time']}–{t['end_time']}"
        return f"{t['title']} — до {t['due']}"

    if short:
        lines.append("*⚡ Краткосрочные:*")
        for i, t in enumerate(short, 1):
            lines.append(f"{i}. {fmt(t)}")
    if long_:
        lines.append("\n*🎯 Долгосрочные:*")
        offset = len(short)
        for i, t in enumerate(long_, offset + 1):
            lines.append(f"{i}. {fmt(t)}")
    return _ok("\n".join(lines))


# ─── send_to_alice ───────────────────────────────────────────────────────────

def send_to_alice(*, message: str, **_kwargs) -> dict:
    if not message:
        return _err("Не понял, что передать Алисе.")
    with open(config.ALICE_MESSAGE_FILE, "w", encoding="utf-8") as f:
        f.write(message)
    return _ok("📢 Сообщение поставлено в очередь для Алисы.")


# ─── save_progress ───────────────────────────────────────────────────────────

def save_progress(*, text: str, **_kwargs) -> dict:
    try:
        calendar_client.save_progress(text)
        return _ok("✍️ Прогресс сохранён. Учту завтра утром!")
    except Exception as e:
        logger.error("save_progress failed: %s", e)
        return _err(f"Не получилось сохранить: {e}")


# ─── send_email (needs confirmation) ─────────────────────────────────────────

def send_email(
    *,
    to_email: Optional[str] = None,
    to_name: Optional[str] = None,
    subject: str = "Без темы",
    body: str = "",
    _context=None,
    **_kwargs,
) -> dict:
    if _context is not None:
        _context.user_data["pending_email"] = {
            "to_email": to_email, "to_name": to_name,
            "subject": subject, "body": body,
        }
    if not to_email:
        if _context is not None:
            _context.user_data["waiting_for"] = "email"
        name = to_name or "получателя"
        return _confirm(f"На какой email отправить письмо для {name}?", kind="email_address")
    return _confirm(
        f"✉️ Письмо для {to_name or to_email} «{subject}» готово. Отправить?",
        kind="email_preview",
    )


# ─── get_digest / get_weekly_digest (delegate-to-background flow) ────────────

def get_digest(*, date: Optional[str] = None, **_kwargs) -> dict:
    """Signals bot_handlers to run _send_morning_digest; the digest itself
    arrives as a separate message stream."""
    label = date or "сегодня"
    return _ok(
        f"⏳ Готовлю дайджест на {label}...",
        action="send_digest",
        target_date=date,
    )


def get_weekly_digest(**_kwargs) -> dict:
    return _ok("⏳ Составляю недельный обзор...", action="send_weekly_digest")


# ─── Smart Home (Tuya + VeSync) ──────────────────────────────────────────────

def smart_home_list(**_kwargs) -> dict:
    devices = smart_home.list_all_devices()
    if not devices:
        return _ok("🏠 Устройств не найдено. Проверь подключение Tuya / VeSync.")
    lines = ["🏠 *Устройства умного дома:*"]
    for d in devices:
        state = d.get("state", "?")
        icon = "🟢" if state == "on" else "⚪" if state == "off" else "❓"
        lines.append(f"{icon} {d['name']} ({d.get('backend', '?')})")
    return _ok("\n".join(lines))


def smart_home_turn_on(*, device_name: str, **_kwargs) -> dict:
    ok, msg = smart_home.turn_on(device_name)
    icon = "💡" if ok else "⚠️"
    return _ok(f"{icon} {msg}") if ok else _err(msg)


def smart_home_turn_off(*, device_name: str, **_kwargs) -> dict:
    ok, msg = smart_home.turn_off(device_name)
    icon = "🌑" if ok else "⚠️"
    return _ok(f"{icon} {msg}") if ok else _err(msg)


def smart_home_set_brightness(*, device_name: str, percent: int, **_kwargs) -> dict:
    ok, msg = smart_home.set_brightness(device_name, percent)
    return _ok(f"💡 {msg}") if ok else _err(msg)


def smart_home_set_color_temp(*, device_name: str, percent: int, **_kwargs) -> dict:
    ok, msg = smart_home.set_color_temp(device_name, percent)
    return _ok(f"🌡 {msg}") if ok else _err(msg)


def smart_home_set_fan_speed(*, device_name: str, speed: int, **_kwargs) -> dict:
    ok, msg = smart_home.set_fan_speed(device_name, speed)
    return _ok(f"💨 {msg}") if ok else _err(msg)


def smart_home_set_mode(*, device_name: str, mode: str, **_kwargs) -> dict:
    ok, msg = smart_home.set_mode(device_name, mode)
    return _ok(f"⚙️ {msg}") if ok else _err(msg)


# ─── Tool schema definitions for Anthropic API ───────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "add_task",
        "description": (
            "Add a task or event to Google Calendar. Use task_type='short' for "
            "1-3 day tasks, 'long' for projects spanning weeks/months. "
            "If time is set, creates a timed event with conflict detection. "
            "If only date is set, creates an all-day event. "
            "attendees lists names to find in Google Contacts and invite by email."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task or event title, no dates/times."},
                "task_type": {"type": "string", "enum": ["short", "long"]},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD for date ranges"},
                "time": {"type": "string", "description": "HH:MM 24-hour"},
                "duration_minutes": {"type": "integer", "default": 60},
                "attendees": {"type": "array", "items": {"type": "string"}, "description": "Names to invite"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a task as done. task_number is the 1-based index from the current active task list.",
        "input_schema": {
            "type": "object",
            "properties": {"task_number": {"type": "integer"}},
            "required": ["task_number"],
        },
    },
    {
        "name": "delete_task",
        "description": "Delete a task or event by its number in the active task list.",
        "input_schema": {
            "type": "object",
            "properties": {"task_number": {"type": "integer"}},
            "required": ["task_number"],
        },
    },
    {
        "name": "reschedule_task",
        "description": "Move a task/event to a new date and time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_number": {"type": "integer"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM"},
                "duration_minutes": {"type": "integer", "default": 60},
            },
            "required": ["task_number", "time"],
        },
    },
    {
        "name": "find_free_time",
        "description": "Find free time slots on a given date in work hours (09:00-22:00).",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
                "duration_minutes": {"type": "integer", "default": 60},
            },
        },
    },
    {
        "name": "get_weather",
        "description": "Get weather forecast for a city (default Nesher).",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD (default today)"},
            },
        },
    },
    {
        "name": "show_tasks",
        "description": "Show the list of all active tasks (short + long term).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_digest",
        "description": "Generate and send the daily digest with calendar, tasks, weather, news, emails.",
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD (default today)"}},
        },
    },
    {
        "name": "get_weekly_digest",
        "description": "Generate the weekly overview digest (current Mon-Sun).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "send_to_alice",
        "description": "Queue a message for Yandex Alice to read aloud when the user opens the skill.",
        "input_schema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "save_progress",
        "description": "Save the user's daily progress note for tomorrow's digest reference.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "smart_home_list",
        "description": "List all smart home devices (Tuya + VeSync) with their on/off state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "smart_home_turn_on",
        "description": "Turn on a smart home device by its name (как в приложениях Tuya/VeSync). Works for lights, outlets, purifiers, humidifiers, AC units.",
        "input_schema": {
            "type": "object",
            "properties": {"device_name": {"type": "string"}},
            "required": ["device_name"],
        },
    },
    {
        "name": "smart_home_turn_off",
        "description": "Turn off a smart home device by name.",
        "input_schema": {
            "type": "object",
            "properties": {"device_name": {"type": "string"}},
            "required": ["device_name"],
        },
    },
    {
        "name": "smart_home_set_brightness",
        "description": "Set Tuya bulb brightness, 1-100 percent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "percent": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["device_name", "percent"],
        },
    },
    {
        "name": "smart_home_set_color_temp",
        "description": "Set Tuya bulb color temperature, 0 (warm) to 100 (cool).",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "percent": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["device_name", "percent"],
        },
    },
    {
        "name": "smart_home_set_fan_speed",
        "description": "Set VeSync purifier/humidifier/fan speed (1-3 typically).",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "speed": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["device_name", "speed"],
        },
    },
    {
        "name": "smart_home_set_mode",
        "description": "Set VeSync device mode: 'auto', 'manual', 'sleep'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string"},
                "mode": {"type": "string", "enum": ["auto", "manual", "sleep"]},
            },
            "required": ["device_name", "mode"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Compose and prepare to send an email via Gmail. If to_email is missing, "
            "the bot will ask the user for it. Always requires user confirmation before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_email": {"type": "string"},
                "to_name": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["subject", "body"],
        },
    },
]


TOOL_FUNCS = {
    "add_task": add_task,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "reschedule_task": reschedule_task,
    "find_free_time": find_free_time,
    "get_weather": get_weather,
    "show_tasks": show_tasks,
    "get_digest": get_digest,
    "get_weekly_digest": get_weekly_digest,
    "send_to_alice": send_to_alice,
    "save_progress": save_progress,
    "send_email": send_email,
    "smart_home_list": smart_home_list,
    "smart_home_turn_on": smart_home_turn_on,
    "smart_home_turn_off": smart_home_turn_off,
    "smart_home_set_brightness": smart_home_set_brightness,
    "smart_home_set_color_temp": smart_home_set_color_temp,
    "smart_home_set_fan_speed": smart_home_set_fan_speed,
    "smart_home_set_mode": smart_home_set_mode,
}
