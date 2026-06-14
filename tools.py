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
import json
import logging
from typing import Optional

import pytz

import a2a_client
import calendar_client
import config
import contacts_client
import diary
import scheduled_actions
import smart_home
import weather_client
import whatsapp_client
import whatsapp_summary

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
    task_number: Optional[int] = None,
    event_title: Optional[str] = None,
    date: Optional[str] = None,
    time: Optional[str] = None,
    duration_minutes: int = 60,
    _active_tasks=None,
    **_kwargs,
) -> dict:
    """Reschedule a task (by task_number from the active list) OR any calendar
    event (by event_title — fuzzy substring match across primary + task calendars).
    """
    if not time:
        return _err("Укажи новое время (например, «перенеси встречу с Леонелем на пятницу в 17:00»).")

    target = None  # dict with id, cal_id, title
    if task_number is not None:
        task = _resolve_task_by_number(task_number, _active_tasks or [])
        if task:
            target = {"id": task["id"], "cal_id": task["cal_id"], "title": task["title"]}

    if target is None and event_title:
        matches = calendar_client.find_event_by_title(event_title)
        if not matches:
            return _err(f"В календаре не нашлось события с «{event_title}». Уточни название.")
        if len(matches) > 1:
            lines = [f"Нашёл несколько событий с «{event_title}»:"]
            for m in matches[:5]:
                lines.append(f"  • {m['summary']} — {m['start'].strftime('%d.%m в %H:%M')}")
            lines.append("Уточни какое именно (по дате или времени).")
            return _err("\n".join(lines))
        m = matches[0]
        target = {"id": m["id"], "cal_id": m["cal_id"], "title": m["summary"]}

    if target is None:
        return _err("Не понял, что переносить — укажи task_number или event_title.")

    new_start = _resolve_start_dt(date, time)
    new_end = new_start + datetime.timedelta(minutes=duration_minutes)
    try:
        calendar_client.reschedule_task(target["id"], target["cal_id"], new_start, new_end)
        return _ok(f"📅 Перенесено: «{target['title']}» → {new_start.strftime('%d.%m.%Y в %H:%M')}")
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


# ─── WhatsApp (via Baileys bridge) ───────────────────────────────────────────

def whatsapp_send_group(*, group_name: str, message: str, **_kwargs) -> dict:
    """Send a message to a WhatsApp chat (group OR personal contact) by friendly name.

    The friendly name comes from whatsapp_groups.json, which may include
    aliases and a configured signature (appended automatically).
    """
    ok, msg = whatsapp_client.send_to_name(group_name, message)
    if ok:
        return _ok(f"💬 Отправлено в WhatsApp: «{group_name}»")
    return _err(f"WhatsApp: {msg}")


def whatsapp_list_groups(**_kwargs) -> dict:
    """List all WhatsApp groups visible to the bridge."""
    st = whatsapp_client.status()
    if not st.get("ready"):
        err = st.get("error") or "не авторизован"
        return _err(f"WhatsApp bridge не готов: {err}. Проверь сервис и QR.")
    groups = whatsapp_client.list_groups()
    if not groups:
        return _ok("Групп не найдено.")
    lines = ["💬 *WhatsApp группы:*"]
    for g in groups:
        lines.append(f"  • {g['name']} — `{g['id']}` ({g.get('size', 0)} участ.)")
    lines.append("\nЧтобы использовать по короткому имени — добавь id в `whatsapp_groups.json`.")
    return _ok("\n".join(lines))


def whatsapp_review_unread(**_kwargs) -> dict:
    """Return a prioritized Haiku-generated summary of unread WhatsApp chats.

    Does NOT return raw messages — the user reads those in WhatsApp itself.
    Output is a 🔴/🟡/⚪ priority breakdown so the agent (and user) sees who
    needs a reply and what each conversation is about.
    """
    st = whatsapp_client.status()
    if not st.get("ready"):
        return _err(f"WhatsApp bridge не готов: {st.get('error') or 'не авторизован'}")
    chats = whatsapp_client.unread_chats()
    if not chats:
        return _ok("В WhatsApp нет непрочитанных сообщений.")

    summary = whatsapp_summary.summarize_unread_chats(chats)
    if not summary:
        total = sum(c.get("unreadCount", 0) for c in chats)
        return _ok(f"Непрочитанных {total} в {len(chats)} чатах, но содержимого для сводки нет (медиа/стикеры).")

    header = f"💬 *WhatsApp — {len(chats)} чат(а) с непрочитанными:*\n\n"
    return _ok(header + summary)


def whatsapp_send_to_any(*, chat_query: str, message: str, **_kwargs) -> dict:
    """Multi-source recipient resolution:
      1. whatsapp_groups.json registry (with signature support)
      2. Baileys fuzzy chat search (known WhatsApp chats and groups)
      3. Google Contacts → phone number → WhatsApp JID
    """
    st = whatsapp_client.status()
    if not st.get("ready"):
        return _err(f"WhatsApp bridge не готов: {st.get('error') or 'не авторизован'}")

    needle = chat_query.lower().strip()

    # 1. Registry first — preserves configured signatures
    registry = whatsapp_client._load_registry()
    if needle in registry:
        ok, msg = whatsapp_client.send_to_name(chat_query, message)
        if ok:
            return _ok(f"💬 Отправлено в WhatsApp: «{chat_query}»")
        return _err(f"WhatsApp: {msg}")

    # 2. Fuzzy search across Baileys-known chats
    matches = whatsapp_client.find_chats(chat_query)
    if len(matches) == 1:
        chat = matches[0]
        ok, msg = whatsapp_client.send_to_chat(chat["id"], message)
        if ok:
            return _ok(f"💬 Отправлено в WhatsApp: «{chat['name']}»")
        return _err(f"WhatsApp: {msg}")
    if len(matches) > 1:
        lines = [f"Несколько совпадений с «{chat_query}» среди WhatsApp-чатов:"]
        for m in matches[:5]:
            lines.append(f"  • {m['name']}")
        lines.append("Уточни какой именно (по полному названию).")
        return _err("\n".join(lines))

    # 3. Google Contacts → phone → WA JID
    contact = contacts_client.find_contact(chat_query)
    if contact and contact.get("phone"):
        jid = whatsapp_client.phone_to_jid(contact["phone"])
        display = contact.get("name") or chat_query
        ok, msg = whatsapp_client.send_to_chat(jid, message)
        if ok:
            return _ok(f"💬 Отправлено в WhatsApp: «{display}» (+{contact['phone']})")
        return _err(f"WhatsApp ({display}): {msg}")

    return _err(
        f"Не нашёл «{chat_query}» ни в WhatsApp-чатах, ни в Google Contacts. "
        f"Уточни имя точнее или дай номер телефона."
    )


# ─── record_completed_task (retroactive logging) ─────────────────────────────

def record_completed_task(
    *,
    title: str,
    duration_minutes: int = 30,
    **_kwargs,
) -> dict:
    """Log a task that was done today but wasn't in the plan.

    Finds a past free slot today and creates the task already marked as done.
    Falls back to placing it `duration_minutes` before now if no past gap exists.
    """
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    today = now.date()

    # Search for free slots throughout the active day
    try:
        slots = calendar_client.find_free_slots(today, duration_minutes, "07:00", "23:00")
    except Exception:
        slots = []

    # Pick the latest slot whose end is already in the past
    placement_dt = None
    for slot in slots:
        eh, em = map(int, slot["end"].split(":"))
        slot_end = tz.localize(datetime.datetime(today.year, today.month, today.day, eh, em))
        if slot_end <= now:
            sh, sm = map(int, slot["start"].split(":"))
            placement_dt = tz.localize(datetime.datetime(today.year, today.month, today.day, sh, sm))
            # don't break — keep walking to find the LATEST past slot

    # Fallback: place ending right before now
    if placement_dt is None:
        placement_dt = now - datetime.timedelta(minutes=duration_minutes)

    try:
        calendar_client.record_completed_task(title, placement_dt, duration_minutes)
        end = placement_dt + datetime.timedelta(minutes=duration_minutes)
        return _ok(
            f"✅ Записала задним числом: «{title}»\n"
            f"📅 {placement_dt.strftime('%H:%M')}–{end.strftime('%H:%M')} — отмечено выполненным"
        )
    except Exception as e:
        logger.error("record_completed_task failed: %s", e)
        return _err(f"Не удалось записать: {e}")


# ─── A2A (Agent2Agent) — calling external agents ─────────────────────────────

def a2a_list_known_agents(**_kwargs) -> dict:
    """List external A2A agents that have been registered locally."""
    agents = a2a_client.list_known_agents()
    if not agents:
        return _ok(
            "Внешних A2A-агентов пока не зарегистрировано. "
            "Используй a2a_discover чтобы добавить — например по URL из каталога "
            "a2a.dev, smithery.ai или agentcommerce.io."
        )
    lines = ["🤖 *Зарегистрированные A2A-агенты:*"]
    for a in agents:
        desc = a.get("description") or "—"
        skills = a.get("skill_ids") or []
        lines.append(f"  • *{a['name']}* — {desc[:120]}")
        if skills:
            lines.append(f"    умеет: {', '.join(skills[:6])}")
    return _ok("\n".join(lines))


def a2a_discover(*, url: str, friendly_name: Optional[str] = None,
                 api_key: Optional[str] = None, **_kwargs) -> dict:
    """Fetch Agent Card from a remote A2A server and save under friendly_name."""
    ok, payload = a2a_client.discover_agent(url, friendly_name, api_key)
    if not ok:
        return _err(f"A2A discovery: {payload}")
    return _ok(
        f"🤖 Зарегистрировал агента «{payload['registered_as']}» — "
        f"{payload.get('agent_name')}. Умеет: {', '.join(payload.get('skills') or []) or '?'}"
    )


def a2a_call_agent(*, agent_name: str, tool: str,
                   params: Optional[dict] = None, **_kwargs) -> dict:
    """Send a task to a registered A2A agent."""
    ok, payload = a2a_client.call_agent(agent_name, tool, params or {})
    if not ok:
        return _err(f"A2A call: {payload}")
    # Extract textual representation from result
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        summary = result.get("summary") or json.dumps(result, ensure_ascii=False)[:600]
    else:
        summary = str(result)[:600]
    return _ok(f"🤖 [{agent_name}] {summary}")


def a2a_remove_agent(*, name: str, **_kwargs) -> dict:
    """Delete a registered A2A agent from the local registry."""
    ok, msg = a2a_client.remove_agent(name)
    return _ok(f"🗑 {msg}") if ok else _err(msg)


# ─── Scheduled actions (delayed / recurring flows) ───────────────────────────

def schedule_action(
    *,
    action_text: str,
    delay_minutes: Optional[int] = None,
    at_time: Optional[str] = None,
    at_date: Optional[str] = None,
    repeat: Optional[str] = None,
    **_kwargs,
) -> dict:
    """Schedule a natural-language action to fire later or repeat.

    Examples:
      action_text='включи лампу в зале', delay_minutes=2
      action_text='выключи бойлер', at_time='23:00'
      action_text='включи увлажнитель на ночь', at_time='22:00', repeat='daily'
    """
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)

    if delay_minutes:
        run_at = now + datetime.timedelta(minutes=delay_minutes)
    elif at_time:
        h, m = map(int, at_time.split(":"))
        base_date = datetime.date.fromisoformat(at_date) if at_date else now.date()
        run_at = tz.localize(datetime.datetime(base_date.year, base_date.month, base_date.day, h, m))
        # If time has passed today and no date specified and not repeating — push to tomorrow
        if run_at <= now and not at_date and not repeat:
            run_at += datetime.timedelta(days=1)
    else:
        return _err("Уточни когда — через сколько минут или в какое время.")

    action = scheduled_actions.schedule_action(action_text, run_at, repeat=repeat)

    if repeat == "daily":
        return _ok(f"⏰ Каждый день в {action['at_time']} буду делать: «{action_text}»")
    if repeat == "weekdays":
        return _ok(f"⏰ По будням в {action['at_time']} буду делать: «{action_text}»")
    if repeat == "weekend":
        return _ok(f"⏰ По выходным в {action['at_time']} буду делать: «{action_text}»")
    return _ok(f"⏰ Запланировано на {run_at.strftime('%d.%m в %H:%M')}: «{action_text}»")


def list_scheduled_actions(**_kwargs) -> dict:
    actions = scheduled_actions.list_actions()
    if not actions:
        return _ok("Запланированных действий нет.")
    tz = pytz.timezone(config.TIMEZONE)
    lines = ["⏰ *Запланированные действия:*"]
    for a in actions:
        run_at = datetime.datetime.fromisoformat(a["run_at"])
        if a.get("repeat"):
            when = f"каждый {a['repeat']} в {a['at_time']}"
        else:
            when = run_at.strftime("%d.%m в %H:%M")
        lines.append(f"  `{a['id']}` — «{a['action_text']}» → {when}")
    return _ok("\n".join(lines))


def cancel_scheduled_action(*, action_id: str, **_kwargs) -> dict:
    if scheduled_actions.cancel_action(action_id):
        return _ok(f"❌ Отменено: {action_id}")
    return _err(f"Не нашёл запланированное действие с id `{action_id}`.")


def diary_write(*, text: str, **_kwargs) -> dict:
    """Append an entry to the personal diary (local file + Google Doc mirror)."""
    if not text or not text.strip():
        return _err("Пустая запись — нечего сохранять.")
    result = diary.append(text.strip())
    if not result.get("ok"):
        return _err(f"Не удалось записать: {result.get('error')}")
    if result.get("doc_synced"):
        return _ok("📓 Записала в дневник.")
    return _ok(f"📓 Записала в локальный файл (Google Doc не доступен: {result.get('error')}).")


def diary_read(*, period: str = "today", **_kwargs) -> dict:
    """Read diary entries for a period: today, yesterday, week, month, all, YYYY-MM, YYYY-MM-DD."""
    text = diary.read(period=period)
    url = diary.doc_url()
    suffix = f"\n\n🔗 [Открыть дневник в Google Docs]({url})" if url else ""
    return _ok(text + suffix)


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
        "description": (
            "Reschedule ANY task or calendar event to a new date/time. "
            "Use task_number for items in the active task list, OR event_title "
            "(a keyword/name like 'Леонель') to find an event in the primary "
            "Google Calendar by fuzzy match. ALWAYS use event_title when the user "
            "refers to a meeting/event by name rather than a list number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_number": {"type": "integer", "description": "Index in active task list (1-based)"},
                "event_title": {"type": "string", "description": "Name/keyword of the event to find in calendar"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM"},
                "duration_minutes": {"type": "integer", "default": 60},
            },
            "required": ["time"],
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
        "name": "whatsapp_send_group",
        "description": (
            "Send a WhatsApp message to a configured chat — can be either a group "
            "OR a personal contact. Use the friendly name from whatsapp_groups.json "
            "(e.g. 'покупки', 'женя', 'муж'). Signatures are applied automatically "
            "where configured (no need to add them in the message text). "
            "Match user phrasing flexibly: «напиши Жене» / «отправь мужу» / «в группу покупки»."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "group_name": {"type": "string", "description": "Friendly name or alias from whatsapp_groups.json"},
                "message": {"type": "string"},
            },
            "required": ["group_name", "message"],
        },
    },
    {
        "name": "whatsapp_list_groups",
        "description": "List all WhatsApp groups the bridge can see — returns names and chatIds.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "whatsapp_review_unread",
        "description": (
            "Fetch all unread WhatsApp chats with their recent message history. "
            "Use this when the user asks 'что в вотсапе?', 'проверь сообщения', "
            "'что мне ответить', 'есть ли что важное в чатах'. The result includes "
            "sender names + message texts; the agent should analyze them and "
            "recommend reply priorities + draft replies."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "whatsapp_send_to_any",
        "description": (
            "Send a WhatsApp message to ANY recipient by name. Multi-source resolution: "
            "(1) checks whatsapp_groups.json registry, (2) fuzzy-searches known WhatsApp "
            "chats/groups, (3) looks up the name in Google Contacts and uses their phone "
            "number to construct a WhatsApp JID. Use this for 'напиши маме', 'отправь "
            "Ивану', 'передай в группу X' — works even for people the bot has never "
            "messaged before, as long as they're in Google Contacts with a phone number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_query": {"type": "string", "description": "Recipient name (person or group)"},
                "message": {"type": "string"},
            },
            "required": ["chat_query", "message"],
        },
    },
    {
        "name": "record_completed_task",
        "description": (
            "Log an unplanned task that the user already completed today. Use this "
            "when the user reports doing something that was NOT in their active "
            "task list (e.g., 'я сегодня помыла окна', 'позвонила маме'). "
            "Call this ONCE per individual completed item. The task is placed in "
            "a past free slot today and marked done immediately — perfect for "
            "history tracking and weekly review."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Что было сделано — короткая формулировка."},
                "duration_minutes": {"type": "integer", "default": 30},
            },
            "required": ["title"],
        },
    },
    {
        "name": "a2a_list_known_agents",
        "description": (
            "List external A2A-compatible agents the user has registered locally. "
            "Each entry has a friendly name, URL, what it does, and which skills it offers."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "a2a_discover",
        "description": (
            "Register a new external A2A agent by fetching its public Agent Card "
            "from /.well-known/agent.json. Use when the user gives a URL of an agent "
            "they want to use (from catalogs like a2a.dev, smithery.ai, "
            "agentcommerce.io, or directly from another person). If the remote "
            "requires authentication, pass api_key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Base URL of the remote A2A agent"},
                "friendly_name": {"type": "string", "description": "Short local alias"},
                "api_key": {"type": "string", "description": "Bearer token if the remote requires it"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "a2a_call_agent",
        "description": (
            "Delegate a specific task to a registered external A2A agent. "
            "Use this whenever the task is outside this bot's own tools — "
            "examples: deep web research → research agent; restaurant booking → "
            "booking agent; spouse's schedule → spouse's agent; specialized "
            "translation/medical/legal questions → relevant vertical agent. "
            "If no suitable agent is registered, call a2a_list_known_agents first "
            "to see what's available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string", "description": "Friendly name from local registry"},
                "tool": {"type": "string", "description": "Skill id on the remote agent"},
                "params": {"type": "object", "description": "Params per the remote skill's schema"},
            },
            "required": ["agent_name", "tool"],
        },
    },
    {
        "name": "a2a_remove_agent",
        "description": "Delete a previously registered external A2A agent.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "schedule_action",
        "description": (
            "Schedule a natural-language action to execute later. The action_text "
            "is exactly what the user would say (e.g., 'включи лампу в зале'). "
            "Use this for ANY delayed or recurring request: smart home, reminders, "
            "task additions, weather checks, anything. Specify ONE of: "
            "delay_minutes (fire after N minutes), or at_time (HH:MM today, "
            "automatically tomorrow if time has passed). Optionally combine at_time "
            "with at_date (YYYY-MM-DD) for specific dates. Use repeat='daily' / "
            "'weekdays' / 'weekend' for recurring schedules — they always need at_time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_text": {"type": "string", "description": "The action to execute, in natural language."},
                "delay_minutes": {"type": "integer", "description": "Fire after this many minutes from now."},
                "at_time": {"type": "string", "description": "HH:MM time to fire at."},
                "at_date": {"type": "string", "description": "YYYY-MM-DD date (combined with at_time)."},
                "repeat": {"type": "string", "enum": ["daily", "weekdays", "weekend"]},
            },
            "required": ["action_text"],
        },
    },
    {
        "name": "list_scheduled_actions",
        "description": "Show all currently scheduled and recurring actions.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_scheduled_action",
        "description": "Cancel a scheduled action by its short id (from list_scheduled_actions).",
        "input_schema": {
            "type": "object",
            "properties": {"action_id": {"type": "string"}},
            "required": ["action_id"],
        },
    },
    {
        "name": "diary_write",
        "description": (
            "Append an entry to the user's personal diary (local file + Google Doc mirror). "
            "Call this PROACTIVELY whenever the user shares something meaningful about her day: "
            "events, encounters, feelings, observations, small wins, struggles, plans, thoughts. "
            "Examples: 'сегодня встретила Аню', 'устала на работе', 'была интересная мысль про X', "
            "'утром пробежала 5км', 'мама звонила, обсуждали поездку'. Also handles explicit "
            "'запиши в дневник: …' commands. Write in third person past tense as Daria would "
            "look back at it, e.g. 'Встретилась с Аней — обсудили проект Х, она расстроена из-за…'. "
            "Skip trivial conversational filler ('привет', 'спасибо', 'ок'). One call per distinct "
            "moment — don't batch unrelated topics into a single entry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The diary entry text in Russian. Concise (1-3 sentences), reflective.",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "diary_read",
        "description": (
            "Read entries from the user's diary. Use for 'прочитай дневник', "
            "'что я писала вчера', 'покажи дневник за июнь', 'что было на прошлой неделе'. "
            "Period: 'today', 'yesterday', 'week', 'month', 'all', or specific 'YYYY-MM' / 'YYYY-MM-DD'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "today | yesterday | week | month | all | YYYY-MM | YYYY-MM-DD",
                    "default": "today",
                },
            },
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
    "record_completed_task": record_completed_task,
    "a2a_list_known_agents": a2a_list_known_agents,
    "a2a_discover": a2a_discover,
    "a2a_call_agent": a2a_call_agent,
    "a2a_remove_agent": a2a_remove_agent,
    "whatsapp_send_group": whatsapp_send_group,
    "whatsapp_list_groups": whatsapp_list_groups,
    "whatsapp_review_unread": whatsapp_review_unread,
    "whatsapp_send_to_any": whatsapp_send_to_any,
    "schedule_action": schedule_action,
    "list_scheduled_actions": list_scheduled_actions,
    "cancel_scheduled_action": cancel_scheduled_action,
    "diary_write": diary_write,
    "diary_read": diary_read,
}
