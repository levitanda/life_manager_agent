"""Google Calendar storage for tasks and progress notes."""

import datetime
from typing import Optional

import pytz
from googleapiclient.discovery import build

import config
import google_auth

_calendar_ids: dict[str, str] = {}


def _get_service():
    return build("calendar", "v3", credentials=google_auth.get_credentials())


def _get_or_create_calendar(name: str) -> str:
    if name in _calendar_ids:
        return _calendar_ids[name]

    svc = _get_service()
    calendars = svc.calendarList().list().execute().get("items", [])
    for cal in calendars:
        if cal["summary"] == name:
            _calendar_ids[name] = cal["id"]
            return cal["id"]

    new_cal = svc.calendars().insert(body={"summary": name}).execute()
    _calendar_ids[name] = new_cal["id"]
    return new_cal["id"]


def _today(tz: pytz.BaseTzInfo) -> datetime.date:
    return datetime.datetime.now(tz).date()


def add_task(
    title: str,
    task_type: str,
    due_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    start_dt: Optional[datetime.datetime] = None,
    duration_minutes: int = 60,
    attendees: Optional[list] = None,
) -> str:
    """Add a task. task_type: 'short' or 'long'.
    - start_dt set → timed event (start_dt to start_dt + duration_minutes)
    - end_date set → multi-day all-day event (due_date to end_date, inclusive)
    - neither     → single all-day event on due_date
    """
    tz = pytz.timezone(config.TIMEZONE)
    cal_name = config.SHORT_TASK_CALENDAR if task_type == "short" else config.LONG_TASK_CALENDAR
    cal_id = _get_or_create_calendar(cal_name)
    props = {"agent_task": "true", "task_type": task_type, "status": "active"}

    if start_dt is not None:
        if start_dt.tzinfo is None:
            start_dt = tz.localize(start_dt)
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        event = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": config.TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": config.TIMEZONE},
            "extendedProperties": {"private": props},
        }
    else:
        if due_date is None:
            due_date = _today(tz) + datetime.timedelta(days=1 if task_type == "short" else 7)
        # Google Calendar end date for all-day events is exclusive, so add 1 day
        gc_end = (end_date if end_date is not None else due_date) + datetime.timedelta(days=1)
        event = {
            "summary": title,
            "start": {"date": due_date.isoformat()},
            "end": {"date": gc_end.isoformat()},
            "extendedProperties": {"private": props},
        }

    if attendees:
        event["attendees"] = [{"email": email} for email in attendees]
    result = (
        _get_service()
        .events()
        .insert(calendarId=cal_id, body=event, sendUpdates="all" if attendees else "none")
        .execute()
    )
    return result["id"]


def get_active_tasks(task_type: str, target_date: Optional[datetime.date] = None) -> list[dict]:
    """Return active (non-completed) tasks of the given type.

    target_date=None  → all tasks not yet ended (for /tasks command)
    target_date=date  → only tasks that overlap with that specific date (for digest)
    """
    tz = pytz.timezone(config.TIMEZONE)
    today = _today(tz)
    ref = target_date or today

    # Look back 90 days so ongoing multi-day events that started earlier are included
    look_back = ref - datetime.timedelta(days=90)

    cal_name = config.SHORT_TASK_CALENDAR if task_type == "short" else config.LONG_TASK_CALENDAR
    try:
        cal_id = _get_or_create_calendar(cal_name)
    except Exception:
        return []

    result = (
        _get_service()
        .events()
        .list(
            calendarId=cal_id,
            timeMin=datetime.datetime.combine(look_back, datetime.time.min, tzinfo=tz).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    tasks = []
    for ev in result.get("items", []):
        props = ev.get("extendedProperties", {}).get("private", {})
        if props.get("agent_task") != "true" or props.get("status") == "done":
            continue

        start_info = ev["start"]
        end_info = ev.get("end", {})

        if "dateTime" in start_info:
            start_local = datetime.datetime.fromisoformat(start_info["dateTime"]).astimezone(tz)
            end_local = datetime.datetime.fromisoformat(end_info["dateTime"]).astimezone(tz)
            if target_date is not None:
                if start_local.date() != target_date:
                    continue
            else:
                if start_local.date() < today:
                    continue
            due = start_local.strftime("%Y-%m-%d")
            time_str = start_local.strftime("%H:%M")
            end_time_str = end_local.strftime("%H:%M")
        else:
            ev_start = datetime.date.fromisoformat(start_info["date"])
            ev_end_exclusive = datetime.date.fromisoformat(end_info["date"])
            ev_end_inclusive = ev_end_exclusive - datetime.timedelta(days=1)
            if target_date is not None:
                if not (ev_start <= target_date <= ev_end_inclusive):
                    continue
            else:
                if ev_end_exclusive <= today:
                    continue
            due = ev_start.isoformat()
            time_str = None
            end_time_str = ev_end_inclusive.isoformat() if ev_start != ev_end_inclusive else None

        tasks.append({
            "id": ev["id"],
            "title": ev["summary"],
            "due": due,
            "time": time_str,
            "end_time": end_time_str,
            "type": task_type,
            "cal_id": cal_id,
        })
    return tasks


def delete_task(task_id: str, cal_id: str) -> bool:
    _get_service().events().delete(calendarId=cal_id, eventId=task_id).execute()
    return True


def complete_task(task_id: str, cal_id: str) -> bool:
    svc = _get_service()
    event = svc.events().get(calendarId=cal_id, eventId=task_id).execute()
    props = event.setdefault("extendedProperties", {}).setdefault("private", {})
    props["status"] = "done"
    svc.events().update(calendarId=cal_id, eventId=task_id, body=event).execute()
    return True


def save_progress(text: str) -> None:
    """Save user's evening progress note."""
    tz = pytz.timezone(config.TIMEZONE)
    today = _today(tz)
    cal_id = _get_or_create_calendar(config.PROGRESS_CALENDAR)
    event = {
        "summary": f"Прогресс: {today.isoformat()}",
        "description": text,
        "start": {"date": today.isoformat()},
        "end": {"date": today.isoformat()},
        "extendedProperties": {"private": {"agent_progress": "true"}},
    }
    _get_service().events().insert(calendarId=cal_id, body=event).execute()



def get_todays_calendar_events(target_date: Optional[datetime.date] = None) -> list[dict]:
    """Return events from the primary Google Calendar for the given date (default: today)."""
    tz = pytz.timezone(config.TIMEZONE)
    date = target_date or _today(tz)
    start = datetime.datetime.combine(date, datetime.time.min, tzinfo=tz).isoformat()
    end = datetime.datetime.combine(date, datetime.time.max, tzinfo=tz).isoformat()

    result = (
        _get_service()
        .events()
        .list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for ev in result.get("items", []):
        start_info = ev["start"]
        time_str = start_info.get("dateTime", start_info.get("date", ""))
        events.append({"title": ev.get("summary", "Без названия"), "time": time_str})
    return events


def record_completed_task(
    title: str,
    start_dt: datetime.datetime,
    duration_minutes: int = 30,
    task_type: str = "short",
) -> str:
    """Create a timed event already marked as done (for retroactive logging)."""
    tz = pytz.timezone(config.TIMEZONE)
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    cal_name = config.SHORT_TASK_CALENDAR if task_type == "short" else config.LONG_TASK_CALENDAR
    cal_id = _get_or_create_calendar(cal_name)
    event = {
        "summary": title,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": config.TIMEZONE},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": config.TIMEZONE},
        "extendedProperties": {"private": {
            "agent_task": "true", "task_type": task_type, "status": "done",
            "retroactive": "true",
        }},
    }
    result = _get_service().events().insert(calendarId=cal_id, body=event).execute()
    return result["id"]


def reschedule_task(
    task_id: str,
    cal_id: str,
    new_start_dt: datetime.datetime,
    new_end_dt: datetime.datetime,
) -> bool:
    """Move a timed event to a new start/end datetime."""
    tz = pytz.timezone(config.TIMEZONE)
    if new_start_dt.tzinfo is None:
        new_start_dt = tz.localize(new_start_dt)
    if new_end_dt.tzinfo is None:
        new_end_dt = tz.localize(new_end_dt)
    svc = _get_service()
    event = svc.events().get(calendarId=cal_id, eventId=task_id).execute()
    event["start"] = {"dateTime": new_start_dt.isoformat(), "timeZone": config.TIMEZONE}
    event["end"] = {"dateTime": new_end_dt.isoformat(), "timeZone": config.TIMEZONE}
    svc.events().update(calendarId=cal_id, eventId=task_id, body=event).execute()
    return True


def get_all_events_for_date(target_date: datetime.date) -> list[dict]:
    """Return all timed events from primary + task calendars for a date."""
    tz = pytz.timezone(config.TIMEZONE)
    start = datetime.datetime.combine(target_date, datetime.time.min, tzinfo=tz).isoformat()
    end = datetime.datetime.combine(target_date, datetime.time.max, tzinfo=tz).isoformat()

    cal_ids = ["primary"]
    try:
        cal_ids.append(_get_or_create_calendar(config.SHORT_TASK_CALENDAR))
        cal_ids.append(_get_or_create_calendar(config.LONG_TASK_CALENDAR))
    except Exception:
        pass

    events = []
    svc = _get_service()
    for cal_id in cal_ids:
        try:
            result = svc.events().list(
                calendarId=cal_id,
                timeMin=start,
                timeMax=end,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            for ev in result.get("items", []):
                s_info = ev["start"]
                e_info = ev.get("end", {})
                if "dateTime" in s_info:
                    ev_start = datetime.datetime.fromisoformat(s_info["dateTime"]).astimezone(tz)
                    ev_end = datetime.datetime.fromisoformat(e_info["dateTime"]).astimezone(tz)
                    events.append({
                        "title": ev.get("summary", ""),
                        "start": ev_start,
                        "end": ev_end,
                    })
        except Exception:
            pass

    return sorted(events, key=lambda e: e["start"])


def _slot_minutes(slot: dict) -> int:
    sh, sm = map(int, slot["start"].split(":"))
    eh, em = map(int, slot["end"].split(":"))
    return (eh * 60 + em) - (sh * 60 + sm)


def find_free_slots(
    target_date: datetime.date,
    duration_minutes: int = 60,
    work_start: str = "09:00",
    work_end: str = "22:00",
) -> list[dict]:
    """Return free time slots on target_date within work hours."""
    tz = pytz.timezone(config.TIMEZONE)
    ws_h, ws_m = map(int, work_start.split(":"))
    we_h, we_m = map(int, work_end.split(":"))
    day_start = tz.localize(datetime.datetime(target_date.year, target_date.month, target_date.day, ws_h, ws_m))
    day_end = tz.localize(datetime.datetime(target_date.year, target_date.month, target_date.day, we_h, we_m))

    events = get_all_events_for_date(target_date)
    busy = sorted([(ev["start"], ev["end"]) for ev in events])

    # Merge overlapping busy intervals
    merged: list[list] = []
    for b_start, b_end in busy:
        if merged and b_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b_end)
        else:
            merged.append([b_start, b_end])

    slots = []
    cursor = day_start
    for b_start, b_end in merged:
        if cursor + datetime.timedelta(minutes=duration_minutes) <= b_start:
            slot = {"start": cursor.strftime("%H:%M"), "end": b_start.strftime("%H:%M")}
            if _slot_minutes(slot) >= duration_minutes:
                slots.append(slot)
        cursor = max(cursor, b_end)

    if cursor + datetime.timedelta(minutes=duration_minutes) <= day_end:
        slot = {"start": cursor.strftime("%H:%M"), "end": day_end.strftime("%H:%M")}
        if _slot_minutes(slot) >= duration_minutes:
            slots.append(slot)

    return slots


def get_conflicts(start_dt: datetime.datetime, end_dt: datetime.datetime) -> list[str]:
    """Return titles of timed events that overlap the given window."""
    tz = pytz.timezone(config.TIMEZONE)
    if start_dt.tzinfo is None:
        start_dt = tz.localize(start_dt)
    if end_dt.tzinfo is None:
        end_dt = tz.localize(end_dt)
    events = get_all_events_for_date(start_dt.date())
    return [ev["title"] for ev in events if ev["start"] < end_dt and ev["end"] > start_dt]


def get_week_events(start_date: datetime.date, end_date: datetime.date) -> dict[str, list[dict]]:
    """Return primary calendar events for a date range, grouped by ISO date string."""
    tz = pytz.timezone(config.TIMEZONE)
    start = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=tz).isoformat()
    end = datetime.datetime.combine(end_date, datetime.time.max, tzinfo=tz).isoformat()

    result = (
        _get_service()
        .events()
        .list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    week: dict[str, list[dict]] = {}
    for ev in result.get("items", []):
        s_info = ev["start"]
        if "dateTime" in s_info:
            ev_dt = datetime.datetime.fromisoformat(s_info["dateTime"]).astimezone(tz)
            date_str = ev_dt.date().isoformat()
            time_str = ev_dt.strftime("%H:%M")
        else:
            date_str = s_info["date"]
            time_str = "весь день"
        week.setdefault(date_str, []).append({"title": ev.get("summary", "Без названия"), "time": time_str})
    return week


def get_progress_before_date(target_date: Optional[datetime.date] = None) -> Optional[str]:
    """Return the progress note for the day before target_date (default: yesterday)."""
    tz = pytz.timezone(config.TIMEZONE)
    ref = (target_date or _today(tz)) - datetime.timedelta(days=1)
    try:
        cal_id = _get_or_create_calendar(config.PROGRESS_CALENDAR)
    except Exception:
        return None

    result = (
        _get_service()
        .events()
        .list(
            calendarId=cal_id,
            timeMin=datetime.datetime.combine(ref, datetime.time.min, tzinfo=tz).isoformat(),
            timeMax=datetime.datetime.combine(ref, datetime.time.max, tzinfo=tz).isoformat(),
            singleEvents=True,
        )
        .execute()
    )

    for ev in result.get("items", []):
        if ev.get("extendedProperties", {}).get("private", {}).get("agent_progress") == "true":
            return ev.get("description", "")
    return None
