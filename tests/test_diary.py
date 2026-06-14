"""Tests for the personal diary module."""

import datetime
import os
from unittest.mock import MagicMock, patch

import pytest
import pytz

import config


@pytest.fixture
def tmp_diary(tmp_path, monkeypatch):
    """Redirect diary file paths to a temp dir."""
    diary_file = tmp_path / "diary.md"
    doc_cache = tmp_path / "diary_doc.json"
    monkeypatch.setattr(config, "DIARY_FILE", str(diary_file))
    monkeypatch.setattr(config, "DIARY_DOC_CACHE", str(doc_cache))
    return {"diary_file": diary_file, "doc_cache": doc_cache}


@pytest.fixture
def mock_google(monkeypatch):
    """Stub Google services so diary.append doesn't hit the network."""
    import diary as diary_mod
    docs_svc = MagicMock()
    drive_svc = MagicMock()
    # Drive.files().create() returns a Doc with id and link
    drive_svc.files.return_value.create.return_value.execute.return_value = {
        "id": "fake-doc-id",
        "webViewLink": "https://docs.google.com/document/d/fake-doc-id/edit",
    }
    # By default doc is empty (no day section)
    docs_svc.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": []}
    }
    docs_svc.documents.return_value.batchUpdate.return_value.execute.return_value = {}
    monkeypatch.setattr(diary_mod, "_docs_service", lambda: docs_svc)
    monkeypatch.setattr(diary_mod, "_drive_service", lambda: drive_svc)
    return {"docs": docs_svc, "drive": drive_svc}


def _now_at(hour: int, minute: int = 0, day_offset: int = 0):
    tz = pytz.timezone("Asia/Jerusalem")
    base = datetime.datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base + datetime.timedelta(days=day_offset)


# ─── Local file behavior ──────────────────────────────────────────────────────

def test_append_creates_first_entry(tmp_diary, mock_google):
    import diary
    r = diary.append("первая запись", when=_now_at(9, 30))
    assert r["ok"] is True
    content = tmp_diary["diary_file"].read_text(encoding="utf-8")
    assert "# " in content                     # day header
    assert "[09:30] первая запись" in content


def test_append_two_entries_same_day_keep_one_section(tmp_diary, mock_google):
    import diary
    diary.append("утром", when=_now_at(8, 0))
    diary.append("днём", when=_now_at(14, 30))
    content = tmp_diary["diary_file"].read_text(encoding="utf-8")
    headers = [l for l in content.splitlines() if l.startswith("# ")]
    assert len(headers) == 1
    assert "[08:00] утром" in content
    assert "[14:30] днём" in content
    # Time order preserved
    assert content.index("утром") < content.index("днём")


def test_newer_day_goes_on_top(tmp_diary, mock_google):
    import diary
    # yesterday first
    diary.append("вчерашняя", when=_now_at(20, 0, day_offset=-1))
    # then today
    diary.append("сегодняшняя", when=_now_at(9, 0))
    content = tmp_diary["diary_file"].read_text(encoding="utf-8")
    headers = [i for i, l in enumerate(content.splitlines()) if l.startswith("# ")]
    assert len(headers) == 2
    # Today's section appears before yesterday's
    assert content.index("сегодняшняя") < content.index("вчерашняя")


def test_append_empty_text_rejected(tmp_diary, mock_google):
    import diary
    r = diary.append("   ")
    assert r["ok"] is False


# ─── Read ─────────────────────────────────────────────────────────────────────

def test_read_today_returns_only_today(tmp_diary, mock_google):
    import diary
    diary.append("вчерашняя", when=_now_at(20, 0, day_offset=-1))
    diary.append("сегодняшняя", when=_now_at(9, 0))
    today_text = diary.read("today")
    assert "сегодняшняя" in today_text
    assert "вчерашняя" not in today_text


def test_read_empty(tmp_diary, mock_google):
    import diary
    assert "пуст" in diary.read("today").lower()


def test_read_yesterday(tmp_diary, mock_google):
    import diary
    diary.append("былое", when=_now_at(18, 0, day_offset=-1))
    diary.append("новое", when=_now_at(10, 0))
    y = diary.read("yesterday")
    assert "былое" in y
    assert "новое" not in y


def test_read_specific_date(tmp_diary, mock_google):
    import diary
    diary.append("давнее", when=_now_at(12, 0, day_offset=-3))
    target = (_now_at(12, 0, day_offset=-3)).date().isoformat()
    assert "давнее" in diary.read(target)


def test_read_unknown_period(tmp_diary, mock_google):
    import diary
    diary.append("x", when=_now_at(9, 0))
    out = diary.read("flarble")
    assert "Не понимаю" in out


# ─── Google Doc mirror ────────────────────────────────────────────────────────

def test_creates_doc_on_first_append(tmp_diary, mock_google):
    import diary
    diary.append("первая", when=_now_at(9, 0))
    mock_google["drive"].files.return_value.create.assert_called_once()
    assert tmp_diary["doc_cache"].exists()


def test_doc_id_reused_on_subsequent_writes(tmp_diary, mock_google):
    import diary
    diary.append("a", when=_now_at(9, 0))
    diary.append("b", when=_now_at(10, 0))
    # create called only once
    assert mock_google["drive"].files.return_value.create.call_count == 1


def test_doc_failure_keeps_local_write_ok(tmp_diary, mock_google):
    import diary
    mock_google["drive"].files.return_value.create.side_effect = RuntimeError("api down")
    r = diary.append("важное", when=_now_at(9, 0))
    assert r["ok"] is True
    assert r["doc_synced"] is False
    assert "важное" in tmp_diary["diary_file"].read_text(encoding="utf-8")


# ─── Tools wrapping ───────────────────────────────────────────────────────────

def test_diary_write_tool(tmp_diary, mock_google):
    import tools
    r = tools.diary_write(text="что-то важное")
    assert r["status"] == "ok"


def test_diary_write_tool_empty(tmp_diary, mock_google):
    import tools
    r = tools.diary_write(text="")
    assert r["status"] == "error"


def test_diary_read_tool(tmp_diary, mock_google):
    import diary
    import tools
    diary.append("сегодня было хорошо", when=_now_at(9, 0))
    r = tools.diary_read(period="today")
    assert r["status"] == "ok"
    assert "сегодня было хорошо" in r["summary"]


# ─── Backfill from session summaries ──────────────────────────────────────────

def test_backfill_adds_past_days(tmp_diary, mock_google, tmp_path):
    import diary
    summaries = tmp_path / "summaries.jsonl"
    summaries.write_text(
        '{"date": "2026-05-17 22:29", "summary": "# Резюме беседы\\n\\nГоворили про умный дом."}\n'
        '{"date": "2026-05-18 16:07", "summary": "Отправила сообщения через WhatsApp."}\n',
        encoding="utf-8",
    )
    r = diary.backfill_from_summaries(str(summaries))
    assert r["ok"] is True
    assert r["days_added"] == 2
    content = tmp_diary["diary_file"].read_text(encoding="utf-8")
    assert "2026-05-17" in content
    assert "Говорили про умный дом" in content
    assert "Резюме беседы" not in content  # markdown header stripped


def test_backfill_skips_existing_days(tmp_diary, mock_google, tmp_path):
    import diary
    diary.append("сегодняшний пост", when=_now_at(9, 0))
    today_iso = _now_at(9, 0).date().isoformat()
    summaries = tmp_path / "summaries.jsonl"
    summaries.write_text(
        f'{{"date": "{today_iso} 23:30", "summary": "сегодняшнее резюме"}}\n'
        '{"date": "2026-05-17 22:29", "summary": "старое резюме"}\n',
        encoding="utf-8",
    )
    r = diary.backfill_from_summaries(str(summaries))
    assert r["days_added"] == 1
    assert r["days_skipped"] == 1
    content = tmp_diary["diary_file"].read_text(encoding="utf-8")
    assert "старое резюме" in content
    assert "сегодняшнее резюме" not in content  # today's section preserved as-is


def test_backfill_missing_file(tmp_diary, mock_google):
    import diary
    r = diary.backfill_from_summaries("/nonexistent/path.jsonl")
    assert r["ok"] is False
