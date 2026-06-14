"""Cross-user isolation tests.

For every per-user resource, verify that user A cannot read/write user B's
data. Failures here = data leak risk in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TIMEZONE", "Asia/Jerusalem")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield tmp_path
    db.reset_for_tests()
    crypto.reset_for_tests()


def _make_two_users() -> tuple[int, int]:
    import db
    with db.session_scope() as s:
        a = db.create_user(s, telegram_user_id=100, telegram_chat_id=100, display_name="A")
        b = db.create_user(s, telegram_user_id=200, telegram_chat_id=200, display_name="B")
        return a.id, b.id


# ─── Diary ────────────────────────────────────────────────────────────────────


def test_diary_isolation():
    import diary
    a, b = _make_two_users()

    # Mock Google Doc layer so no API calls happen
    from unittest.mock import patch
    with patch.object(diary, "_docs_service") as docs, \
         patch.object(diary, "_drive_service") as drive:
        docs.return_value.documents.return_value.get.return_value.execute.return_value = {"body": {"content": []}}
        docs.return_value.documents.return_value.batchUpdate.return_value.execute.return_value = {}
        drive.return_value.files.return_value.create.return_value.execute.return_value = {"id": "x"}

        diary.append("секрет А", user_id=a)
        diary.append("секрет Б", user_id=b)

    assert "секрет А" in diary.read("today", user_id=a)
    assert "секрет Б" not in diary.read("today", user_id=a)
    assert "секрет Б" in diary.read("today", user_id=b)
    assert "секрет А" not in diary.read("today", user_id=b)


# ─── Conversation ────────────────────────────────────────────────────────────


def test_conversation_history_isolation():
    import conversation
    a, b = _make_two_users()

    conversation.add("A спросил", "A ответ", user_id=a)
    conversation.add("B спросил", "B ответ", user_id=b)

    ha = conversation.get_history(user_id=a)
    hb = conversation.get_history(user_id=b)

    assert any("A спросил" in m["content"] for m in ha)
    assert not any("B спросил" in m["content"] for m in ha)
    assert any("B спросил" in m["content"] for m in hb)
    assert not any("A спросил" in m["content"] for m in hb)


def test_conversation_summaries_isolation():
    import conversation
    from pathlib import Path
    a, b = _make_two_users()

    # Manually write a summary jsonl for each user
    import db
    pa = Path(db.data_dir(), "users", str(a), "session_summaries.jsonl")
    pb = Path(db.data_dir(), "users", str(b), "session_summaries.jsonl")
    pa.parent.mkdir(parents=True, exist_ok=True)
    pb.parent.mkdir(parents=True, exist_ok=True)
    pa.write_text('{"date":"2026-06-01","summary":"summary of A"}\n', encoding="utf-8")
    pb.write_text('{"date":"2026-06-01","summary":"summary of B"}\n', encoding="utf-8")

    sa = conversation.get_recent_summaries(user_id=a)
    sb = conversation.get_recent_summaries(user_id=b)
    assert sa == [{"date": "2026-06-01", "summary": "summary of A"}]
    assert sb == [{"date": "2026-06-01", "summary": "summary of B"}]


# ─── Scheduled actions ───────────────────────────────────────────────────────


def test_scheduled_actions_isolation():
    import scheduled_actions, datetime, pytz
    a, b = _make_two_users()

    tz = pytz.timezone("Asia/Jerusalem")
    future = datetime.datetime.now(tz) + datetime.timedelta(hours=1)

    scheduled_actions.schedule_action("включи свет у А", future, user_id=a)
    scheduled_actions.schedule_action("включи свет у Б", future, user_id=b)

    la = scheduled_actions.list_actions(user_id=a)
    lb = scheduled_actions.list_actions(user_id=b)
    assert len(la) == 1 and la[0]["action_text"] == "включи свет у А"
    assert len(lb) == 1 and lb[0]["action_text"] == "включи свет у Б"


# ─── WhatsApp groups registry ────────────────────────────────────────────────


def test_whatsapp_groups_registry_isolation():
    import whatsapp_client, db
    a, b = _make_two_users()

    pa = Path(db.data_dir(), "users", str(a), "whatsapp_groups.json")
    pb = Path(db.data_dir(), "users", str(b), "whatsapp_groups.json")
    pa.parent.mkdir(parents=True, exist_ok=True)
    pb.parent.mkdir(parents=True, exist_ok=True)
    pa.write_text(json.dumps({"мама": "111@s.whatsapp.net"}), encoding="utf-8")
    pb.write_text(json.dumps({"мама": "222@s.whatsapp.net"}), encoding="utf-8")

    ra = whatsapp_client._load_registry(user_id=a)
    rb = whatsapp_client._load_registry(user_id=b)
    assert ra["мама"]["chat_id"] == "111@s.whatsapp.net"
    assert rb["мама"]["chat_id"] == "222@s.whatsapp.net"


# ─── Google tokens ───────────────────────────────────────────────────────────


def test_google_tokens_isolation():
    import db, crypto
    a, b = _make_two_users()

    payload_a = {"token": "TOK_A", "scopes": ["calendar"]}
    payload_b = {"token": "TOK_B", "scopes": ["calendar"]}
    with db.session_scope() as s:
        s.add(db.GoogleToken(user_id=a, token_json_encrypted=crypto.encrypt_json(payload_a), scopes="calendar"))
        s.add(db.GoogleToken(user_id=b, token_json_encrypted=crypto.encrypt_json(payload_b), scopes="calendar"))

    with db.session_scope() as s:
        ta = s.get(db.GoogleToken, a)
        tb = s.get(db.GoogleToken, b)
        assert crypto.decrypt_json(ta.token_json_encrypted)["token"] == "TOK_A"
        assert crypto.decrypt_json(tb.token_json_encrypted)["token"] == "TOK_B"


# ─── User integrations ───────────────────────────────────────────────────────


def test_user_integrations_isolation():
    import db, crypto
    a, b = _make_two_users()
    with db.session_scope() as s:
        s.add(db.UserIntegration(
            user_id=a, integration="pushover", enabled=1,
            config_json_encrypted=crypto.encrypt_json({"user_key": "A_KEY"}),
        ))
        s.add(db.UserIntegration(
            user_id=b, integration="pushover", enabled=1,
            config_json_encrypted=crypto.encrypt_json({"user_key": "B_KEY"}),
        ))

    with db.session_scope() as s:
        ia = s.query(db.UserIntegration).filter_by(user_id=a).one()
        ib = s.query(db.UserIntegration).filter_by(user_id=b).one()
        assert crypto.decrypt_json(ia.config_json_encrypted)["user_key"] == "A_KEY"
        assert crypto.decrypt_json(ib.config_json_encrypted)["user_key"] == "B_KEY"
