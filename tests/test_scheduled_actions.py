"""Tests for scheduled_actions module + scheduling tools."""

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytz

import scheduled_actions
import tools


TZ = pytz.timezone("Asia/Jerusalem")


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    p = tmp_path / "actions.json"
    monkeypatch.setattr(scheduled_actions, "LEGACY_STORE_PATH", p)
    yield p


@pytest.fixture
def fake_scheduler():
    sched = MagicMock()
    scheduled_actions._scheduler = sched
    scheduled_actions._bot_app = MagicMock()
    yield sched
    scheduled_actions._scheduler = None
    scheduled_actions._bot_app = None


# ─── store ────────────────────────────────────────────────────────────────────

def test_schedule_action_persists(tmp_store, fake_scheduler):
    run_at = TZ.localize(datetime.datetime(2030, 1, 1, 12, 0))
    action = scheduled_actions.schedule_action("включи свет", run_at)
    assert "id" in action
    data = json.loads(tmp_store.read_text())
    assert len(data) == 1
    assert data[0]["action_text"] == "включи свет"


def test_schedule_action_registers_with_scheduler(tmp_store, fake_scheduler):
    run_at = TZ.localize(datetime.datetime(2030, 1, 1, 12, 0))
    scheduled_actions.schedule_action("включи свет", run_at)
    fake_scheduler.add_job.assert_called_once()


def test_schedule_repeating_action(tmp_store, fake_scheduler):
    run_at = TZ.localize(datetime.datetime(2030, 1, 1, 22, 0))
    action = scheduled_actions.schedule_action("выключи свет", run_at, repeat="daily")
    assert action["repeat"] == "daily"
    assert action["at_time"] == "22:00"


def test_list_actions_returns_persisted(tmp_store, fake_scheduler):
    run_at = TZ.localize(datetime.datetime(2030, 1, 1, 12, 0))
    scheduled_actions.schedule_action("a1", run_at)
    scheduled_actions.schedule_action("a2", run_at)
    assert len(scheduled_actions.list_actions()) == 2


def test_cancel_action_removes(tmp_store, fake_scheduler):
    run_at = TZ.localize(datetime.datetime(2030, 1, 1, 12, 0))
    action = scheduled_actions.schedule_action("a1", run_at)
    ok = scheduled_actions.cancel_action(action["id"])
    assert ok is True
    assert scheduled_actions.list_actions() == []


def test_cancel_nonexistent_returns_false(tmp_store, fake_scheduler):
    assert scheduled_actions.cancel_action("nonexistent") is False


# ─── restore on startup ──────────────────────────────────────────────────────

def test_restore_drops_expired(tmp_store, fake_scheduler):
    past = (datetime.datetime.now(TZ) - datetime.timedelta(hours=1)).isoformat()
    future = (datetime.datetime.now(TZ) + datetime.timedelta(hours=1)).isoformat()
    tmp_store.write_text(json.dumps([
        {"id": "old", "action_text": "expired", "run_at": past, "repeat": None, "at_time": None, "created_at": past},
        {"id": "new", "action_text": "future", "run_at": future, "repeat": None, "at_time": None, "created_at": past},
    ]))
    restored = scheduled_actions._restore_persisted_jobs()
    assert restored == 1
    remaining = scheduled_actions.list_actions()
    assert len(remaining) == 1
    assert remaining[0]["id"] == "new"


def test_restore_recurring(tmp_store, fake_scheduler):
    tmp_store.write_text(json.dumps([
        {"id": "x", "action_text": "выключи свет", "run_at": "2030-01-01T22:00:00+02:00",
         "repeat": "daily", "at_time": "22:00", "created_at": "2025-01-01T00:00:00+02:00"},
    ]))
    restored = scheduled_actions._restore_persisted_jobs()
    assert restored == 1
    fake_scheduler.add_job.assert_called()


# ─── tool layer ──────────────────────────────────────────────────────────────

def test_tool_schedule_with_delay(tmp_store, fake_scheduler):
    result = tools.schedule_action(action_text="включи лампу", delay_minutes=2)
    assert result["status"] == "ok"
    assert "Запланировано" in result["summary"]


def test_tool_schedule_with_time(tmp_store, fake_scheduler):
    result = tools.schedule_action(action_text="выключи бойлер", at_time="23:00")
    assert result["status"] == "ok"


def test_tool_schedule_recurring_daily(tmp_store, fake_scheduler):
    result = tools.schedule_action(action_text="включи увлажнитель", at_time="22:00", repeat="daily")
    assert result["status"] == "ok"
    assert "каждый день" in result["summary"].lower()


def test_tool_schedule_missing_when(tmp_store, fake_scheduler):
    result = tools.schedule_action(action_text="something")
    assert result["status"] == "error"


def test_tool_list_empty(tmp_store, fake_scheduler):
    result = tools.list_scheduled_actions()
    assert result["status"] == "ok"
    assert "нет" in result["summary"].lower()


def test_tool_list_with_actions(tmp_store, fake_scheduler):
    tools.schedule_action(action_text="включи свет", delay_minutes=5)
    result = tools.list_scheduled_actions()
    assert "включи свет" in result["summary"]


def test_tool_cancel(tmp_store, fake_scheduler):
    created = tools.schedule_action(action_text="thing", delay_minutes=1)
    # extract id from summary or use store
    action_id = scheduled_actions.list_actions()[0]["id"]
    result = tools.cancel_scheduled_action(action_id=action_id)
    assert result["status"] == "ok"


def test_tool_cancel_nonexistent(tmp_store, fake_scheduler):
    result = tools.cancel_scheduled_action(action_id="bogus")
    assert result["status"] == "error"


def test_tool_schemas_registered():
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    for name in ("schedule_action", "list_scheduled_actions", "cancel_scheduled_action"):
        assert name in schema_names
        assert name in tools.TOOL_FUNCS
