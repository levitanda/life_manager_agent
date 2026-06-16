"""Tests for record_completed_task — retroactive task logging."""

import datetime
from unittest.mock import MagicMock, patch

import pytest
import pytz

import calendar_client
import tools


TZ = pytz.timezone("Asia/Jerusalem")


# ─── calendar_client.record_completed_task ──────────────────────────────────

def test_calendar_record_completed_creates_done_event(mock_calendar_service):
    svc = mock_calendar_service
    svc.calendarList().list().execute.return_value = {
        "items": [{"id": "cal_short", "summary": "Задачи краткосрочные"}]
    }
    svc.events().insert().execute.return_value = {"id": "evt_done_123"}

    start_dt = TZ.localize(datetime.datetime(2026, 5, 17, 14, 0))
    event_id = calendar_client.record_completed_task("позвонила маме", start_dt, 30)

    assert event_id == "evt_done_123"
    # Verify the inserted event body has status=done and retroactive flag
    insert_call = svc.events().insert
    body = insert_call.call_args[1]["body"] if insert_call.call_args[1] else insert_call.call_args[0][1] if len(insert_call.call_args[0]) > 1 else None
    # try kwargs/args fallback
    if body is None:
        for call in insert_call.call_args_list:
            if "body" in call.kwargs:
                body = call.kwargs["body"]
                break
    assert body is not None
    props = body["extendedProperties"]["private"]
    assert props["status"] == "done"
    assert props["retroactive"] == "true"
    assert body["summary"] == "позвонила маме"


# ─── tool: record_completed_task ──────────────────────────────────────────────

# Freeze "now" inside tools.record_completed_task to noon — otherwise the
# test's slot-construction math becomes ambiguous in the late evening (when
# the "future" slot end clamps below current hour and both slots get
# classified as past).
class _FrozenNoon(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        base = datetime.datetime(2026, 6, 15, 12, 0)
        return tz.localize(base) if tz else base


def test_tool_record_picks_past_free_slot():
    """Tool should pick a past free slot, not a future one."""
    past_start_h, past_end_h = 9, 10        # before frozen noon
    future_start_h, future_end_h = 15, 16   # after frozen noon

    slots = [
        {"start": f"{past_start_h:02d}:00", "end": f"{past_end_h:02d}:00"},
        {"start": f"{future_start_h:02d}:00", "end": f"{future_end_h:02d}:00"},
    ]

    with patch("tools.datetime.datetime", _FrozenNoon), \
         patch("calendar_client.find_free_slots", return_value=slots), \
         patch("calendar_client.record_completed_task", return_value="evt_x") as mock_record:
        result = tools.record_completed_task(title="помыла окна", duration_minutes=30)

    assert result["status"] == "ok"
    call_args = mock_record.call_args
    placement_dt = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("start_dt")
    assert placement_dt.hour == past_start_h


def test_tool_record_fallback_when_no_past_slots():
    """When all free slots are in the future, fall back to placing before now."""
    slots = [{"start": "15:00", "end": "15:30"}]  # future of frozen noon

    with patch("tools.datetime.datetime", _FrozenNoon), \
         patch("calendar_client.find_free_slots", return_value=slots), \
         patch("calendar_client.record_completed_task", return_value="evt_y") as mock_record:
        result = tools.record_completed_task(title="thing", duration_minutes=30)

    assert result["status"] == "ok"
    call_args = mock_record.call_args
    placement_dt = call_args[0][1] if len(call_args[0]) > 1 else call_args.kwargs.get("start_dt")
    frozen_now = TZ.localize(datetime.datetime(2026, 6, 15, 12, 0))
    assert placement_dt <= frozen_now + datetime.timedelta(seconds=5)


def test_tool_record_handles_empty_slots():
    """When no slots at all, falls back to before-now."""
    with patch("calendar_client.find_free_slots", return_value=[]), \
         patch("calendar_client.record_completed_task", return_value="evt_z"):
        result = tools.record_completed_task(title="thing", duration_minutes=30)
    assert result["status"] == "ok"


def test_tool_record_handles_calendar_error():
    with patch("calendar_client.find_free_slots", return_value=[]), \
         patch("calendar_client.record_completed_task", side_effect=Exception("API down")):
        result = tools.record_completed_task(title="thing")
    assert result["status"] == "error"


def test_tool_record_schema_registered():
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    assert "record_completed_task" in schema_names
    assert "record_completed_task" in tools.TOOL_FUNCS
