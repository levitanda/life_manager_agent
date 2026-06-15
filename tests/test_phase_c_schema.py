"""Tests for Phase C: personalization columns + goals/groups schema +
infra/migrate_add_personalization_fields.py."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text


# ─── Schema-level fixtures ────────────────────────────────────────────────────


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Fresh SQLite + data dir + master key per test."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    import db
    import crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield tmp_path
    db.reset_for_tests()
    crypto.reset_for_tests()


# ─── Personalization columns ──────────────────────────────────────────────────


def test_new_columns_present_after_init_db(isolated_db):
    """Fresh init_db() must yield users.{city,language,news_country,onboarding_state}."""
    import db
    with db.session_scope() as s:
        rows = s.execute(text("PRAGMA table_info(users)")).all()
    col_names = {r[1] for r in rows}
    assert {"city", "language", "news_country", "onboarding_state"}.issubset(col_names)

    # Defaults applied for new users
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.flush()
        s.refresh(u)
        assert u.language == "ru"
        assert u.onboarding_state == "pending"
        assert u.city is None
        assert u.news_country is None


# ─── user_news_feeds ──────────────────────────────────────────────────────────


def test_user_news_feeds_isolated_per_user(isolated_db):
    import db
    with db.session_scope() as s:
        u1 = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        u2 = db.create_user(s, telegram_user_id=2, telegram_chat_id=2)
        s.add(db.UserNewsFeed(user_id=u1.id, source_name="A", url="https://a"))
        s.add(db.UserNewsFeed(user_id=u1.id, source_name="B", url="https://b"))
        s.add(db.UserNewsFeed(user_id=u2.id, source_name="C", url="https://c"))

    with db.session_scope() as s:
        u1_feeds = s.query(db.UserNewsFeed).filter_by(user_id=1).all()
        u2_feeds = s.query(db.UserNewsFeed).filter_by(user_id=2).all()
        assert len(u1_feeds) == 2
        assert len(u2_feeds) == 1
        assert {f.source_name for f in u1_feeds} == {"A", "B"}
        assert u2_feeds[0].source_name == "C"


# ─── Goals + collaborators ────────────────────────────────────────────────────


def test_create_goal_with_owner(isolated_db):
    """db.create_goal() inserts the goal and the owner row in goal_collaborators."""
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        goal = db.create_goal(
            s, user_id=u.id, title="Run a marathon",
            description="42.2km", category="health",
        )
        assert goal.id is not None
        assert goal.status == "active"

    with db.session_scope() as s:
        goal = s.query(db.Goal).one()
        assert goal.title == "Run a marathon"
        collabs = s.query(db.GoalCollaborator).filter_by(goal_id=goal.id).all()
        assert len(collabs) == 1
        assert collabs[0].role == "owner"
        assert collabs[0].user_id == goal.user_id
        assert collabs[0].accepted_at is not None


# ─── Groups ───────────────────────────────────────────────────────────────────


def test_create_group_and_join(isolated_db):
    """db.create_group() creates the admin row in group_members."""
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        group = db.create_group(s, name="Levitan family", creator_id=u.id)
        assert group.id is not None

    with db.session_scope() as s:
        members = s.query(db.GroupMember).filter_by(group_id=1).all()
        assert len(members) == 1
        assert members[0].user_id == 1
        assert members[0].role == "admin"


def test_shared_goal_group_visibility(isolated_db):
    """A goal with a group_id is visible (via plain SQL join) to all group members."""
    import db
    with db.session_scope() as s:
        owner = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        partner = db.create_user(s, telegram_user_id=2, telegram_chat_id=2)
        group = db.create_group(s, name="Family", creator_id=owner.id)
        # Add partner to the group
        s.add(db.GroupMember(group_id=group.id, user_id=partner.id, role="member"))
        # Owner creates a shared goal
        goal = db.create_goal(
            s, user_id=owner.id, title="Save 10k", group_id=group.id,
        )
        goal_id = goal.id
        partner_id = partner.id

    # Plain SQL: goals visible to partner via group membership
    with db.session_scope() as s:
        rows = s.execute(
            text(
                "SELECT g.id, g.title FROM goals g "
                "JOIN group_members gm ON gm.group_id = g.group_id "
                "WHERE gm.user_id = :uid"
            ),
            {"uid": partner_id},
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == goal_id
        assert rows[0][1] == "Save 10k"


# ─── Migration script ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Set up env + a fake project root for the migration script."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TIMEZONE", "Asia/Jerusalem")

    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    import db
    import crypto
    db.reset_for_tests()
    crypto.reset_for_tests()

    fake_root = tmp_path / "project"
    fake_root.mkdir()
    (fake_root / ".env").write_text("", encoding="utf-8")

    # Bootstrap Daria so the backfill has a row to operate on.
    db.init_db()
    with db.session_scope() as s:
        db.create_user(
            s, telegram_user_id=12345, telegram_chat_id=12345,
            display_name="Daria", timezone="Asia/Jerusalem",
        )

    yield fake_root


def _import_migration(fake_root):
    mod_name = "infra.migrate_add_personalization_fields"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    infra_init = Path(__file__).resolve().parent.parent / "infra" / "__init__.py"
    if not infra_init.exists():
        infra_init.touch()
    mod = importlib.import_module(mod_name)
    mod.PROJECT_ROOT = fake_root
    return mod


def test_migration_idempotent(fake_project):
    """Running the migration twice must not error and must not duplicate rows."""
    mig = _import_migration(fake_project)
    rc1 = mig.main()
    assert rc1 == 0
    rc2 = mig.main()
    assert rc2 == 0

    import db
    with db.session_scope() as s:
        feeds = s.query(db.UserNewsFeed).filter_by(user_id=1).all()
        assert len(feeds) == 3  # not 6


def test_migration_backfills_daria(fake_project):
    """After migration, Daria has the expected personalization values and 3 feeds."""
    mig = _import_migration(fake_project)
    rc = mig.main()
    assert rc == 0

    import db
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 12345)
        assert user is not None
        assert user.city == "Нешер"
        assert user.language == "ru"
        assert user.news_country == "IL,RU"
        assert user.onboarding_state == "completed"

        feeds = s.query(db.UserNewsFeed).filter_by(user_id=user.id).all()
        assert len(feeds) == 3
        urls = {f.url for f in feeds}
        # Sanity-check that they match the legacy list.
        import news_client
        assert urls == set(news_client.LEGACY_NEWS_FEEDS.values())
        for f in feeds:
            assert f.enabled == 1
