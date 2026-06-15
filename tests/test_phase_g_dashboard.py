"""Tests for Phase G: dashboard.py + goal tools + web dashboard endpoint."""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_user(tg=222, *, name="Jony"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg, telegram_chat_id=tg, display_name=name)
        u.subscription_status = "promo"
        u.timezone = "Europe/Moscow"
        u.language = "ru"
        return u.id


# ─── Streak math ──────────────────────────────────────────────────────────────


def test_streak_no_diary_file_returns_zero():
    import dashboard
    fake = Path("/nonexistent/diary.md")
    current, longest = dashboard._compute_streaks(fake)
    assert current == 0 and longest == 0


def test_streak_computes_from_consecutive_day_headers(tmp_path):
    import dashboard
    today = datetime.date.today()
    diary = tmp_path / "diary.md"
    days = [today - datetime.timedelta(days=i) for i in range(5)]
    diary.write_text("\n".join(f"# {d.isoformat()}" for d in days), encoding="utf-8")
    current, longest = dashboard._compute_streaks(diary)
    assert current == 5
    assert longest == 5


def test_streak_breaks_on_gap(tmp_path):
    import dashboard
    today = datetime.date.today()
    diary = tmp_path / "diary.md"
    # 3 days, gap, then 2 days
    dates = [
        today,
        today - datetime.timedelta(days=1),
        today - datetime.timedelta(days=2),
        # gap
        today - datetime.timedelta(days=5),
        today - datetime.timedelta(days=6),
    ]
    diary.write_text("\n".join(f"# {d.isoformat()}" for d in dates), encoding="utf-8")
    current, longest = dashboard._compute_streaks(diary)
    assert current == 3
    assert longest == 3


# ─── User goals query ─────────────────────────────────────────────────────────


def test_user_goals_returns_own_active_goals():
    user_id = _mk_user(222)
    import db, dashboard
    with db.session_scope() as s:
        db.create_goal(s, user_id=user_id, title="Italian by Dec")
        db.create_goal(s, user_id=user_id, title="Run 5k")
    out = dashboard._user_goals(user_id)
    titles = {g["title"] for g in out}
    assert titles == {"Italian by Dec", "Run 5k"}


def test_user_goals_aggregates_latest_progress_pct():
    user_id = _mk_user(222)
    import db, dashboard
    with db.session_scope() as s:
        g = db.create_goal(s, user_id=user_id, title="Italian")
        s.add(db.GoalProgress(goal_id=g.id, user_id=user_id, pct=20))
        s.add(db.GoalProgress(goal_id=g.id, user_id=user_id, pct=50))
        s.add(db.GoalProgress(goal_id=g.id, user_id=user_id, pct=70))
    out = dashboard._user_goals(user_id)
    assert out[0]["latest_pct"] == 70
    assert out[0]["progress_count"] == 3


def test_user_goals_isolated_per_user():
    user_a = _mk_user(222, name="A")
    user_b = _mk_user(333, name="B")
    import db, dashboard
    with db.session_scope() as s:
        db.create_goal(s, user_id=user_a, title="Goal of A")
        db.create_goal(s, user_id=user_b, title="Goal of B")
    out_a = dashboard._user_goals(user_a)
    out_b = dashboard._user_goals(user_b)
    assert {g["title"] for g in out_a} == {"Goal of A"}
    assert {g["title"] for g in out_b} == {"Goal of B"}


# ─── Telegram dashboard render ────────────────────────────────────────────────


def test_dashboard_for_user_with_no_data():
    user_id = _mk_user(222, name="Jony")
    import dashboard
    with patch.object(dashboard, "calendar_client") as cc:
        cc.get_active_tasks.return_value = []
        cc.get_todays_calendar_events.return_value = []
        text, kb = dashboard.build_telegram_dashboard(user_id)
    assert "Jony" in text
    assert "Пока нет" in text or "Целей пока нет" in text


def test_dashboard_task_counts_correct():
    user_id = _mk_user(222)
    import dashboard
    with patch.object(dashboard, "calendar_client") as cc:
        cc.get_active_tasks.side_effect = lambda kind, **kw: [{}, {}, {}] if kind == "short" else [{}, {}]
        cc.get_todays_calendar_events.return_value = []
        text, kb = dashboard.build_telegram_dashboard(user_id)
    assert "3" in text  # short tasks count
    assert "2" in text  # long tasks count


def test_dashboard_includes_today_events_when_present():
    user_id = _mk_user(222)
    import dashboard
    with patch.object(dashboard, "calendar_client") as cc:
        cc.get_active_tasks.return_value = []
        cc.get_todays_calendar_events.return_value = [
            {"title": "Doctor", "time": "14:00"},
        ]
        text, kb = dashboard.build_telegram_dashboard(user_id)
    assert "Doctor" in text


# ─── Tools ────────────────────────────────────────────────────────────────────


def test_add_goal_tool_persists():
    user_id = _mk_user(222)
    import tools
    r = tools.add_goal(title="Learn Italian", _user_id=user_id)
    assert r["status"] == "ok"
    import db
    with db.session_scope() as s:
        titles = [g.title for g in s.query(db.Goal).filter_by(user_id=user_id).all()]
    assert titles == ["Learn Italian"]


def test_add_goal_requires_user_id():
    import tools
    r = tools.add_goal(title="x", _user_id=None)
    assert r["status"] == "error"


def test_record_goal_progress_finds_by_title_substring():
    user_id = _mk_user(222)
    import db, tools
    with db.session_scope() as s:
        db.create_goal(s, user_id=user_id, title="Learn Italian by December")
    r = tools.record_goal_progress(goal="Italian", note="watched a movie", pct=30, _user_id=user_id)
    assert r["status"] == "ok"
    with db.session_scope() as s:
        rows = [(p.pct, p.note) for p in s.query(db.GoalProgress).all()]
    assert rows == [(30, "watched a movie")]


def test_record_goal_progress_unknown_goal():
    user_id = _mk_user(222)
    import tools
    r = tools.record_goal_progress(goal="nonexistent", _user_id=user_id)
    assert r["status"] == "error"


def test_list_goals_returns_active_only():
    user_id = _mk_user(222)
    import db, tools
    with db.session_scope() as s:
        g1 = db.create_goal(s, user_id=user_id, title="Active goal")
        g2 = db.create_goal(s, user_id=user_id, title="Done goal")
        g2.status = "completed"
    r = tools.list_goals(_user_id=user_id)
    assert r["status"] == "ok"
    assert "Active goal" in r["summary"]
    assert "Done goal" not in r["summary"]


# ─── Web dashboard endpoint ───────────────────────────────────────────────────


def test_web_dashboard_signs_and_verifies_token():
    import web
    token = web.sign_dashboard_token(42)
    assert web.verify_dashboard_token(token) == 42


def test_web_dashboard_rejects_garbage_token():
    import web
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        web.verify_dashboard_token("garbage")


def test_web_dashboard_rejects_oauth_state_replay():
    """OAuth state and dashboard tokens use different salts — can't
    cross-replay."""
    import web
    from fastapi import HTTPException
    oauth_token = web.sign_state(42)
    with pytest.raises(HTTPException):
        web.verify_dashboard_token(oauth_token)


def test_web_dashboard_html_endpoint():
    """GET /dashboard/{user_id}?token=...&purpose=dashboard returns HTML."""
    user_id = _mk_user(222, name="Mika")
    import web
    from fastapi.testclient import TestClient
    app = web.create_app()
    token = web.sign_dashboard_token(user_id)
    client = TestClient(app)
    import dashboard
    with patch.object(dashboard, "calendar_client") as cc:
        cc.get_active_tasks.return_value = []
        cc.get_todays_calendar_events.return_value = []
        r = client.get(f"/dashboard/{user_id}?token={token}")
    assert r.status_code == 200
    assert "Mika" in r.text
    assert "tailwindcss" in r.text  # CDN loaded


def test_web_dashboard_user_mismatch_403():
    user_id = _mk_user(222)
    import web
    from fastapi.testclient import TestClient
    app = web.create_app()
    token = web.sign_dashboard_token(user_id)
    client = TestClient(app)
    r = client.get(f"/dashboard/9999?token={token}")
    assert r.status_code == 403
