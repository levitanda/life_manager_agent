"""Tests for calendar_client.py — new functions."""

import datetime
from unittest.mock import MagicMock, patch, call

import pytz
import pytest

import calendar_client
import config


TZ = pytz.timezone("Asia/Jerusalem")


def _dt(h: int, m: int, date: datetime.date | None = None) -> datetime.datetime:
    d = date or datetime.date(2026, 5, 18)
    return TZ.localize(datetime.datetime(d.year, d.month, d.day, h, m))


# ─── reschedule_task ──────────────────────────────────────────────────────────

def test_reschedule_task_updates_event(mock_calendar_service):
    existing_event = {
        "id": "evt1",
        "summary": "Meeting",
        "start": {"dateTime": "2026-05-18T10:00:00+03:00"},
        "end": {"dateTime": "2026-05-18T11:00:00+03:00"},
    }
    svc = mock_calendar_service
    svc.events().get().execute.return_value = existing_event
    svc.events().update().execute.return_value = {}

    new_start = _dt(14, 0)
    new_end = _dt(15, 0)
    result = calendar_client.reschedule_task("evt1", "cal1", new_start, new_end)

    assert result is True
    svc.events().update.assert_called()


# ─── find_free_slots ──────────────────────────────────────────────────────────

def _patch_all_events(events):
    return patch.object(calendar_client, "get_all_events_for_date", return_value=events)


def test_find_free_slots_empty_day():
    target = datetime.date(2026, 5, 18)
    with _patch_all_events([]):
        slots = calendar_client.find_free_slots(target, duration_minutes=60)
    assert len(slots) >= 1
    assert slots[0]["start"] == "09:00"


def test_find_free_slots_no_room():
    target = datetime.date(2026, 5, 18)
    # Full day blocked
    events = [{"start": _dt(9, 0, target), "end": _dt(22, 0, target), "title": "Block"}]
    with _patch_all_events(events):
        slots = calendar_client.find_free_slots(target, duration_minutes=30)
    assert slots == []


def test_find_free_slots_gap_between_events():
    target = datetime.date(2026, 5, 18)
    events = [
        {"start": _dt(9, 0, target), "end": _dt(11, 0, target), "title": "A"},
        {"start": _dt(14, 0, target), "end": _dt(16, 0, target), "title": "B"},
    ]
    with _patch_all_events(events):
        slots = calendar_client.find_free_slots(target, duration_minutes=60)
    starts = [s["start"] for s in slots]
    assert "11:00" in starts  # gap 11:00–14:00
    assert "16:00" in starts  # gap 16:00–22:00


def test_find_free_slots_merges_overlapping():
    target = datetime.date(2026, 5, 18)
    events = [
        {"start": _dt(10, 0, target), "end": _dt(12, 0, target), "title": "A"},
        {"start": _dt(11, 0, target), "end": _dt(13, 0, target), "title": "B"},  # overlaps
    ]
    with _patch_all_events(events):
        slots = calendar_client.find_free_slots(target, duration_minutes=60)
    # Merged block 10:00–13:00; free slots: 09:00–10:00 (60 min) and 13:00–22:00
    starts = [s["start"] for s in slots]
    # 09:00–10:00 is exactly 60 min → should appear
    assert "09:00" in starts
    assert "13:00" in starts


# ─── get_conflicts ────────────────────────────────────────────────────────────

def test_get_conflicts_returns_overlapping():
    target = datetime.date(2026, 5, 18)
    events = [
        {"start": _dt(10, 0, target), "end": _dt(11, 0, target), "title": "Meeting"},
        {"start": _dt(15, 0, target), "end": _dt(16, 0, target), "title": "Call"},
    ]
    with _patch_all_events(events):
        conflicts = calendar_client.get_conflicts(_dt(10, 30, target), _dt(11, 30, target))
    assert "Meeting" in conflicts
    assert "Call" not in conflicts


def test_get_conflicts_no_overlap():
    target = datetime.date(2026, 5, 18)
    events = [{"start": _dt(10, 0, target), "end": _dt(11, 0, target), "title": "Meeting"}]
    with _patch_all_events(events):
        conflicts = calendar_client.get_conflicts(_dt(11, 0, target), _dt(12, 0, target))
    assert conflicts == []


def test_get_conflicts_empty():
    with _patch_all_events([]):
        conflicts = calendar_client.get_conflicts(_dt(10, 0), _dt(11, 0))
    assert conflicts == []


# ─── get_week_events ──────────────────────────────────────────────────────────

def test_find_event_by_title_matches_substring(mock_calendar_service):
    svc = mock_calendar_service
    svc.calendarList().list().execute.return_value = {
        "items": [{"id": "cal_short", "summary": "Задачи краткосрочные"}],
    }
    svc.events().list().execute.return_value = {
        "items": [
            {"id": "ev1", "summary": "Урок с Леонелем",
             "start": {"dateTime": "2026-05-22T19:00:00+03:00"},
             "end":   {"dateTime": "2026-05-22T20:00:00+03:00"}},
            {"id": "ev2", "summary": "Другое",
             "start": {"dateTime": "2026-05-21T10:00:00+03:00"},
             "end":   {"dateTime": "2026-05-21T11:00:00+03:00"}},
        ]
    }
    matches = calendar_client.find_event_by_title("Леонел")
    titles = [m["summary"] for m in matches]
    assert "Урок с Леонелем" in titles
    assert "Другое" not in titles


def test_find_event_by_title_no_match(mock_calendar_service):
    svc = mock_calendar_service
    svc.calendarList().list().execute.return_value = {"items": []}
    svc.events().list().execute.return_value = {"items": []}
    assert calendar_client.find_event_by_title("несуществующее") == []


def test_get_week_events_groups_by_date(mock_calendar_service):
    svc = mock_calendar_service
    svc.events().list().execute.return_value = {
        "items": [
            {"summary": "Standup", "start": {"dateTime": "2026-05-18T09:00:00+03:00"}, "end": {"dateTime": "2026-05-18T09:30:00+03:00"}},
            {"summary": "Retro", "start": {"dateTime": "2026-05-20T15:00:00+03:00"}, "end": {"dateTime": "2026-05-20T16:00:00+03:00"}},
        ]
    }
    result = calendar_client.get_week_events(datetime.date(2026, 5, 18), datetime.date(2026, 5, 24))
    assert "2026-05-18" in result
    assert "2026-05-20" in result
    assert result["2026-05-18"][0]["title"] == "Standup"
    assert result["2026-05-20"][0]["title"] == "Retro"


def test_get_week_events_all_day(mock_calendar_service):
    svc = mock_calendar_service
    svc.events().list().execute.return_value = {
        "items": [
            {"summary": "Holiday", "start": {"date": "2026-05-19"}, "end": {"date": "2026-05-20"}},
        ]
    }
    result = calendar_client.get_week_events(datetime.date(2026, 5, 18), datetime.date(2026, 5, 24))
    assert "2026-05-19" in result
    assert result["2026-05-19"][0]["time"] == "весь день"
