"""Tests for bot_handlers.py — new intent routing."""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

import bot_handlers
import calendar_client


def _make_update(text: str = "") -> MagicMock:
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user.id = 123456
    return update


def _make_context() -> MagicMock:
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.application = MagicMock()
    return ctx


def _run(coro):
    return asyncio.run(coro)


ALL_TASKS = [
    {"id": "t1", "cal_id": "c1", "title": "Meeting", "due": "2026-05-18", "time": "10:00", "end_time": "11:00", "type": "short"},
    {"id": "t2", "cal_id": "c2", "title": "Report", "due": "2026-05-20", "time": None, "end_time": None, "type": "long"},
]


# ─── reschedule_task ──────────────────────────────────────────────────────────

def test_execute_reschedule_task_success():
    action = {
        "intent": "reschedule_task",
        "task_number": 1,
        "date": "2026-05-19",
        "time": "15:00",
        "duration_minutes": 60,
    }
    update = _make_update()
    ctx = _make_context()

    with patch.object(calendar_client, "reschedule_task", return_value=True) as mock_rs:
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    mock_rs.assert_called_once()
    assert "Перенесено" in result
    assert "Meeting" in result


def test_execute_reschedule_task_invalid_number():
    action = {"intent": "reschedule_task", "task_number": 99, "date": "2026-05-19", "time": "15:00"}
    update = _make_update()
    ctx = _make_context()

    result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))
    assert "Не нашёл" in result


def test_execute_reschedule_task_no_time():
    action = {"intent": "reschedule_task", "task_number": 1, "date": "2026-05-19", "time": None}
    update = _make_update()
    ctx = _make_context()

    result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))
    assert "Укажи" in result or "время" in result.lower()


def test_tool_reschedule_by_event_title():
    """Tool layer: reschedule by event_title looks up in calendar."""
    import tools
    import pytz, datetime
    tz = pytz.timezone("Asia/Jerusalem")
    fake_match = {
        "id": "ev_x",
        "cal_id": "primary",
        "summary": "Урок с Леонелем",
        "start": tz.localize(datetime.datetime(2026, 5, 22, 19, 0)),
        "end": tz.localize(datetime.datetime(2026, 5, 22, 20, 0)),
    }
    with patch("calendar_client.find_event_by_title", return_value=[fake_match]), \
         patch("calendar_client.reschedule_task", return_value=True) as mock_resched:
        result = tools.reschedule_task(event_title="Леонел", date="2026-05-22", time="17:00")
    assert result["status"] == "ok"
    assert "Леонел" in result["summary"]
    mock_resched.assert_called_once()


def test_tool_reschedule_by_event_title_no_match():
    import tools
    with patch("calendar_client.find_event_by_title", return_value=[]):
        result = tools.reschedule_task(event_title="несуществующее", time="17:00")
    assert result["status"] == "error"


def test_tool_reschedule_by_event_title_ambiguous():
    import tools
    import pytz, datetime
    tz = pytz.timezone("Asia/Jerusalem")
    matches = [
        {"id": f"ev{i}", "cal_id": "primary", "summary": "встреча",
         "start": tz.localize(datetime.datetime(2026, 5, 20+i, 10, 0)),
         "end": tz.localize(datetime.datetime(2026, 5, 20+i, 11, 0))}
        for i in range(3)
    ]
    with patch("calendar_client.find_event_by_title", return_value=matches):
        result = tools.reschedule_task(event_title="встреча", time="17:00")
    assert result["status"] == "error"
    assert "несколько" in result["summary"].lower()


# ─── find_free_time ───────────────────────────────────────────────────────────

def test_execute_find_free_time_with_slots():
    action = {"intent": "find_free_time", "date": "2026-05-18", "duration_minutes": 60}
    update = _make_update()
    ctx = _make_context()
    slots = [{"start": "11:00", "end": "14:00"}]

    with patch.object(calendar_client, "find_free_slots", return_value=slots):
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    assert "11:00" in result
    assert "14:00" in result


def test_execute_find_free_time_no_slots():
    action = {"intent": "find_free_time", "date": "2026-05-18", "duration_minutes": 60}
    update = _make_update()
    ctx = _make_context()

    with patch.object(calendar_client, "find_free_slots", return_value=[]):
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    assert "не нашлось" in result.lower() or "свободных" in result.lower()


# ─── get_weekly_digest ────────────────────────────────────────────────────────

def test_execute_get_weekly_digest():
    import digest as digest_module

    action = {"intent": "get_weekly_digest"}
    update = _make_update()
    ctx = _make_context()
    week_data = {"2026-05-18": [{"title": "Standup", "time": "09:00"}]}

    with patch.object(calendar_client, "get_week_events", return_value=week_data), \
         patch.object(calendar_client, "get_active_tasks", return_value=[]), \
         patch.object(digest_module, "generate_weekly_digest", return_value="Отличная неделя!"):
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    # The digest text is sent via reply_text; result is sentinel string
    assert "недельный обзор" in result
    calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Отличная неделя" in c for c in calls)


# ─── conflict detection in add_task ──────────────────────────────────────────

def test_add_task_shows_conflict_warning():
    action = {
        "intent": "add_task",
        "task_type": "short",
        "title": "New meeting",
        "date": "2026-05-18",
        "time": "10:30",
        "duration_minutes": 60,
        "attendees": [],
    }
    update = _make_update()
    ctx = _make_context()

    with patch.object(calendar_client, "get_conflicts", return_value=["Existing meeting"]):
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    assert "Existing meeting" in result
    assert ctx.user_data.get("pending_task") is not None


def test_add_task_no_conflict_adds_directly():
    action = {
        "intent": "add_task",
        "task_type": "short",
        "title": "Yoga",
        "date": "2026-05-18",
        "time": "07:00",
        "duration_minutes": 60,
        "attendees": [],
    }
    update = _make_update()
    ctx = _make_context()

    with patch.object(calendar_client, "get_conflicts", return_value=[]), \
         patch.object(calendar_client, "add_task", return_value="new_id"):
        result = _run(bot_handlers._execute_action(action, ALL_TASKS, "", update, ctx))

    assert "Добавил" in result
    assert "pending_task" not in ctx.user_data
