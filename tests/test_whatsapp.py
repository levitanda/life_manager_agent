"""Tests for whatsapp_client + WhatsApp tools."""

import json
from unittest.mock import MagicMock, patch

import pytest

import whatsapp_client
import tools


# ─── client: registry loading ────────────────────────────────────────────────

def test_load_registry_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", tmp_path / "missing.json")
    assert whatsapp_client._load_registry() == {}


def test_load_registry_flat_format(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"семья": "120363@g.us", "Покупки": "120364@g.us"}), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    reg = whatsapp_client._load_registry()
    assert "семья" in reg
    assert "покупки" in reg
    assert reg["покупки"]["chat_id"] == "120364@g.us"


def test_load_registry_rich_format_with_aliases(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({
        "женя": {
            "chat_id": "972501234567@s.whatsapp.net",
            "signature": "— Личный ассистент Дарьи",
            "aliases": ["муж", "Жене"]
        }
    }), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    reg = whatsapp_client._load_registry()
    assert "женя" in reg
    assert "муж" in reg
    assert "жене" in reg  # alias normalized
    assert reg["муж"]["chat_id"] == "972501234567@s.whatsapp.net"
    assert reg["муж"]["signature"] == "— Личный ассистент Дарьи"


# ─── client: HTTP wrappers ───────────────────────────────────────────────────

def _mock_resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.raise_for_status = MagicMock()
    if status >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return r


def test_status_ready():
    with patch("requests.get", return_value=_mock_resp(200, {"ready": True, "has_qr": False})):
        st = whatsapp_client.status()
    assert st["ready"] is True


def test_status_handles_connection_error():
    with patch("requests.get", side_effect=Exception("connection refused")):
        st = whatsapp_client.status()
    assert st["ready"] is False
    assert "connection" in st.get("error", "")


def test_list_groups_success():
    payload = {"groups": [{"id": "120363@g.us", "name": "Семья", "size": 4}]}
    with patch("requests.get", return_value=_mock_resp(200, payload)):
        groups = whatsapp_client.list_groups()
    assert len(groups) == 1
    assert groups[0]["name"] == "Семья"


def test_list_groups_bridge_error():
    with patch("requests.get", side_effect=Exception("down")):
        assert whatsapp_client.list_groups() == []


def test_send_to_chat_success():
    with patch("requests.post", return_value=_mock_resp(200, {"ok": True})):
        ok, msg = whatsapp_client.send_to_chat("120363@g.us", "hi")
    assert ok is True


def test_send_to_chat_bridge_error():
    with patch("requests.post", return_value=_mock_resp(503, {"error": "not_ready"})):
        ok, msg = whatsapp_client.send_to_chat("x", "hi")
    assert ok is False
    assert "not_ready" in msg


def test_send_to_name_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", tmp_path / "g.json")
    ok, msg = whatsapp_client.send_to_name("несуществующая", "hi")
    assert ok is False
    assert "не настроено" in msg


def test_send_to_name_flat_format(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"покупки": "120363@g.us"}), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    with patch("requests.post", return_value=_mock_resp(200, {"ok": True})) as mp:
        ok, msg = whatsapp_client.send_to_name("Покупки", "молоко")
    assert ok is True
    sent = mp.call_args.kwargs["json"]
    assert sent["chatId"] == "120363@g.us"
    assert sent["text"] == "молоко"


def test_send_to_name_appends_signature(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({
        "женя": {
            "chat_id": "972501234567@s.whatsapp.net",
            "signature": "— Личный ассистент Дарьи",
            "aliases": ["муж"]
        }
    }), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    with patch("requests.post", return_value=_mock_resp(200, {"ok": True})) as mp:
        ok, _ = whatsapp_client.send_to_name("муж", "буду через час")
    assert ok is True
    sent = mp.call_args.kwargs["json"]
    assert "буду через час" in sent["text"]
    assert "— Личный ассистент Дарьи" in sent["text"]


# ─── tool layer ──────────────────────────────────────────────────────────────

def test_tool_send_success():
    with patch("whatsapp_client.send_to_name", return_value=(True, "Отправлено")):
        r = tools.whatsapp_send_group(group_name="покупки", message="молоко")
    assert r["status"] == "ok"


def test_tool_send_failure():
    with patch("whatsapp_client.send_to_name", return_value=(False, "не настроено")):
        r = tools.whatsapp_send_group(group_name="bogus", message="hi")
    assert r["status"] == "error"


def test_tool_list_groups_not_ready():
    with patch("whatsapp_client.status", return_value={"ready": False, "error": "no_qr"}):
        r = tools.whatsapp_list_groups()
    assert r["status"] == "error"


def test_tool_list_groups_empty():
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.list_groups", return_value=[]):
        r = tools.whatsapp_list_groups()
    assert r["status"] == "ok"
    assert "не найдено" in r["summary"].lower()


def test_tool_list_groups_populated():
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.list_groups", return_value=[
             {"id": "120363@g.us", "name": "Семья", "size": 4}
         ]):
        r = tools.whatsapp_list_groups()
    assert "Семья" in r["summary"]
    assert "120363@g.us" in r["summary"]


def test_tool_schemas_registered():
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    for name in ("whatsapp_send_group", "whatsapp_list_groups",
                 "whatsapp_review_unread", "whatsapp_send_to_any"):
        assert name in schema_names
        assert name in tools.TOOL_FUNCS


# ─── unread + review ─────────────────────────────────────────────────────────

def test_unread_chats_success():
    payload = {"chats": [
        {"id": "120363@g.us", "name": "Семья", "unreadCount": 3,
         "recentMessages": [{"senderName": "Аня", "text": "позвони", "fromMe": False, "ts": 1}]},
    ]}
    with patch("requests.get", return_value=_mock_resp(200, payload)):
        chats = whatsapp_client.unread_chats()
    assert len(chats) == 1
    assert chats[0]["unreadCount"] == 3


def test_tool_review_unread_empty():
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.unread_chats", return_value=[]):
        r = tools.whatsapp_review_unread()
    assert r["status"] == "ok"
    assert "нет" in r["summary"].lower()


def test_tool_review_unread_with_chats():
    chats = [{
        "id": "120363@g.us", "name": "Семья", "unreadCount": 2,
        "recentMessages": [
            {"senderName": "Мама", "text": "когда придёшь?", "fromMe": False, "ts": 1},
            {"senderName": None, "text": "ok", "fromMe": True, "ts": 2},
        ]
    }]
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.unread_chats", return_value=chats):
        r = tools.whatsapp_review_unread()
    assert "Семья" in r["summary"]
    assert "Мама" in r["summary"]
    assert "когда придёшь" in r["summary"]


def test_tool_review_unread_bridge_down():
    with patch("whatsapp_client.status", return_value={"ready": False, "error": "no_qr"}):
        r = tools.whatsapp_review_unread()
    assert r["status"] == "error"


# ─── send to any (fuzzy search) ──────────────────────────────────────────────

def test_find_chats_returns_matches():
    with patch("requests.post", return_value=_mock_resp(200, {
        "matches": [{"id": "120363@g.us", "name": "Мама и я"}]
    })):
        matches = whatsapp_client.find_chats("мама")
    assert matches[0]["name"] == "Мама и я"


def test_tool_send_to_any_single_match():
    matches = [{"id": "120363@g.us", "name": "Мама"}]
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.find_chats", return_value=matches), \
         patch("whatsapp_client.send_to_chat", return_value=(True, "ok")):
        r = tools.whatsapp_send_to_any(chat_query="мама", message="привет")
    assert r["status"] == "ok"
    assert "Мама" in r["summary"]


def test_tool_send_to_any_no_match():
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.find_chats", return_value=[]):
        r = tools.whatsapp_send_to_any(chat_query="никого", message="hi")
    assert r["status"] == "error"


def test_tool_send_to_any_ambiguous():
    matches = [
        {"id": "1@g.us", "name": "Семья мамы"},
        {"id": "2@g.us", "name": "Семья папы"},
    ]
    with patch("whatsapp_client.status", return_value={"ready": True}), \
         patch("whatsapp_client.find_chats", return_value=matches):
        r = tools.whatsapp_send_to_any(chat_query="семья", message="hi")
    assert r["status"] == "error"
    assert "Уточни" in r["summary"]
