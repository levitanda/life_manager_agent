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

    result = _get_service().events().insert(calendarId=cal_id, body=event).execute()
    return result["id"]


def get_active_tasks(task_type: str) -> list[dict]:
    """Return all active (non-completed) tasks of the given type."""
    tz = pytz.timezone(config.TIMEZONE)
    today = _today(tz)

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
            timeMin=datetime.datetime.combine(today, datetime.time.min, tzinfo=tz).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    tasks = []
    for ev in result.get("items", []):
        props = ev.get("extendedProperties", {}).get("private", {})
        if props.get("agent_task") == "true" and props.get("status") != "done":
            start_info = ev["start"]
            end_info = ev.get("end", {})
            if "dateTime" in start_info:
                start_local = datetime.datetime.fromisoformat(start_info["dateTime"]).astimezone(tz)
                end_local = datetime.datetime.fromisoformat(end_info["dateTime"]).astimezone(tz)
                due = start_local.strftime("%Y-%m-%d")
                time_str = start_local.strftime("%H:%M")
                end_time_str = end_local.strftime("%H:%M")
            else:
                due = start_info.get("date", "")
                time_str = None
                end_time_str = None

            tasks.append(
                {
                    "id": ev["id"],
                    "title": ev["summary"],
                    "due": due,
                    "time": time_str,
                    "end_time": end_time_str,
                    "type": task_type,
                    "cal_id": cal_id,
                }
            )
    return tasks


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


def get_yesterday_progress() -> Optional[str]:
    """Return yesterday's progress note if it exists."""
    tz = pytz.timezone(config.TIMEZONE)
    yesterday = _today(tz) - datetime.timedelta(days=1)
    try:
        cal_id = _get_or_create_calendar(config.PROGRESS_CALENDAR)
    except Exception:
        return None

    result = (
        _get_service()
        .events()
        .list(
            calendarId=cal_id,
            timeMin=datetime.datetime.combine(yesterday, datetime.time.min, tzinfo=tz).isoformat(),
            timeMax=datetime.datetime.combine(yesterday, datetime.time.max, tzinfo=tz).isoformat(),
            singleEvents=True,
        )
        .execute()
    )

    for ev in result.get("items", []):
        if ev.get("extendedProperties", {}).get("private", {}).get("agent_progress") == "true":
            return ev.get("description", "")
    return None


def get_todays_calendar_events() -> list[dict]:
    """Return today's events from the primary Google Calendar."""
    tz = pytz.timezone(config.TIMEZONE)
    today = _today(tz)
    start = datetime.datetime.combine(today, datetime.time.min, tzinfo=tz).isoformat()
    end = datetime.datetime.combine(today, datetime.time.max, tzinfo=tz).isoformat()

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
