"""Tests for infra/migrate_daria_to_db.py — idempotent flat-file migration."""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Build a fake project root with flat files + token, point the migration script at it."""
    # Working dir for DB + per-user data
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TIMEZONE", "Asia/Jerusalem")

    # Force-reload config so it picks up new TELEGRAM_CHAT_ID
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    import db
    import crypto
    db.reset_for_tests()
    crypto.reset_for_tests()

    # Build flat files the migration expects to find
    fake_root = tmp_path / "project"
    fake_root.mkdir()
    (fake_root / "conversation_history.json").write_text('{"session": {"messages": []}}', encoding="utf-8")
    (fake_root / "session_summaries.jsonl").write_text('{"date":"2026-05-17","summary":"x"}\n', encoding="utf-8")
    (fake_root / "diary.md").write_text("# 2026-06-14\n[09:00] утро\n", encoding="utf-8")
    (fake_root / "diary_doc.json").write_text('{"doc_id":"abc"}', encoding="utf-8")
    (fake_root / "scheduled_actions.json").write_text('[]', encoding="utf-8")
    (fake_root / "a2a_agents.json").write_text('{}', encoding="utf-8")
    (fake_root / "whatsapp_groups.json").write_text('{}', encoding="utf-8")
    (fake_root / "last_digest.txt").write_text("вчерашний дайджест", encoding="utf-8")
    (fake_root / "google_token.json").write_text(json.dumps({
        "token": "tok",
        "refresh_token": "ref",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }), encoding="utf-8")

    # Empty .env (the script may append MASTER_KEY)
    (fake_root / ".env").write_text("", encoding="utf-8")

    # Make sure the script's PROJECT_ROOT points to our fake one for both
    # file reads and DB initialisation.
    yield fake_root


def _import_migration(fake_root):
    """Import infra/migrate_daria_to_db.py with its PROJECT_ROOT patched to fake_root."""
    # Force fresh import + patch PROJECT_ROOT to point at our fake project
    mod_name = "infra.migrate_daria_to_db"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Make sure project's infra/ package is importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    # Ensure infra/__init__.py exists so it can be imported as a package
    infra_init = Path(__file__).resolve().parent.parent / "infra" / "__init__.py"
    if not infra_init.exists():
        infra_init.touch()
    mod = importlib.import_module(mod_name)
    mod.PROJECT_ROOT = fake_root
    return mod


def test_migration_creates_user_and_moves_files(fake_project):
    mig = _import_migration(fake_project)
    rc = mig.main()
    assert rc == 0

    # User created
    import db
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 12345)
        assert user is not None
        assert user.subscription_status == "promo"
        assert user.display_name == "Daria"
        assert user.timezone == "Asia/Jerusalem"

    # Files moved into per-user dir
    user_dir = Path(os.environ["DATA_DIR"]) / "users" / "1"
    assert (user_dir / "conversation_history.json").exists()
    assert (user_dir / "diary.md").exists()
    assert (user_dir / "session_summaries.jsonl").exists()

    # Sources are gone (moved, not copied)
    assert not (fake_project / "conversation_history.json").exists()
    assert not (fake_project / "diary.md").exists()


def test_migration_stores_encrypted_google_token(fake_project):
    mig = _import_migration(fake_project)
    mig.main()
    import db, crypto
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 12345)
        tok = user.google_token
        assert tok is not None
        decrypted = crypto.decrypt_json(tok.token_json_encrypted)
        assert decrypted["token"] == "tok"
        assert decrypted["refresh_token"] == "ref"
        assert "calendar" in tok.scopes


def test_migration_idempotent(fake_project):
    mig = _import_migration(fake_project)
    mig.main()
    # Second run should not error and should not double-redeem the promo
    mig.main()
    import db
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 12345)
        assert user.subscription_status == "promo"
        promo = s.query(db.PromoCode).filter_by(code="LEVITANONLY").one()
        assert promo.redeemed_count == 1


def test_migration_missing_token_file_is_ok(fake_project):
    # Remove google_token.json before running
    (fake_project / "google_token.json").unlink()
    mig = _import_migration(fake_project)
    rc = mig.main()
    assert rc == 0
    import db
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, 12345)
        assert user is not None
        assert user.google_token is None


def test_migration_target_already_present_skipped(fake_project):
    # Put a pre-existing file in the target dir BEFORE migration
    user_dir = Path(os.environ["DATA_DIR"]) / "users" / "1"
    user_dir.mkdir(parents=True, exist_ok=True)
    pre_existing = user_dir / "diary.md"
    pre_existing.write_text("DO NOT OVERWRITE", encoding="utf-8")

    mig = _import_migration(fake_project)
    mig.main()

    # Target preserved
    assert pre_existing.read_text(encoding="utf-8") == "DO NOT OVERWRITE"
    # Source still there (not moved because target existed)
    assert (fake_project / "diary.md").exists()
