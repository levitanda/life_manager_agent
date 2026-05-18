"""Tests for whatsapp_client + WhatsApp tools."""

import json
from unittest.mock import MagicMock, patch

import pytest

import whatsapp_client
import tools


# ─── client: registry loading ────────────────────────────────────────────────

def test_load_group_registry_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", tmp_path / "missing.json")
    assert whatsapp_client._load_group_registry() == {}


def test_load_group_registry_valid(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"семья": "120363@g.us", "Покупки": "120364@g.us"}), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    reg = whatsapp_client._load_group_registry()
    # keys lowercased
    assert "семья" in reg
    assert "покупки" in reg
    assert reg["покупки"] == "120364@g.us"


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


def test_send_to_group_by_name_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", tmp_path / "g.json")
    ok, msg = whatsapp_client.send_to_group_by_name("несуществующая", "hi")
    assert ok is False
    assert "не настроена" in msg


def test_send_to_group_by_name_known(tmp_path, monkeypatch):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"покупки": "120363@g.us"}), encoding="utf-8")
    monkeypatch.setattr(whatsapp_client, "GROUPS_FILE", p)
    with patch("requests.post", return_value=_mock_resp(200, {"ok": True})) as mp:
        ok, msg = whatsapp_client.send_to_group_by_name("Покупки", "молоко")
    assert ok is True
    sent_chat = mp.call_args.kwargs["json"]["chatId"]
    assert sent_chat == "120363@g.us"


# ─── tool layer ──────────────────────────────────────────────────────────────

def test_tool_send_group_success():
    with patch("whatsapp_client.send_to_group_by_name", return_value=(True, "Отправлено")):
        r = tools.whatsapp_send_group(group_name="покупки", message="молоко")
    assert r["status"] == "ok"


def test_tool_send_group_failure():
    with patch("whatsapp_client.send_to_group_by_name", return_value=(False, "не настроена")):
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
    for name in ("whatsapp_send_group", "whatsapp_list_groups"):
        assert name in schema_names
        assert name in tools.TOOL_FUNCS
