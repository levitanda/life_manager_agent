"""Tests for settings_menu.py — /settings + callback handlers + credential capture."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_user(tg_id=42, status="promo"):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=tg_id, telegram_chat_id=tg_id)
        u.subscription_status = status
        return u.id


def _mk_update(tg_id=42, callback_data=None):
    upd = MagicMock()
    upd.effective_user.id = tg_id
    upd.effective_chat.id = tg_id
    upd.effective_message.reply_text = AsyncMock()
    upd.effective_message.text = ""
    if callback_data is not None:
        upd.callback_query = MagicMock()
        upd.callback_query.answer = AsyncMock()
        upd.callback_query.data = callback_data
        upd.callback_query.edit_message_text = AsyncMock()
        upd.callback_query.message = MagicMock()
        upd.callback_query.message.reply_text = AsyncMock()
    else:
        upd.callback_query = None
    return upd


def _mk_context():
    ctx = MagicMock()
    ctx.user_data = {}
    return ctx


# ─── /settings entry ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_unknown_user_redirects_to_start():
    import settings_menu
    upd = _mk_update(999)
    await settings_menu.cmd_settings(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "/start" in text


@pytest.mark.asyncio
async def test_settings_inactive_user_blocked():
    _mk_user(42, status="inactive")
    import settings_menu
    upd = _mk_update(42)
    await settings_menu.cmd_settings(upd, _mk_context())
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "подписк" in text.lower() or "промокод" in text.lower()


@pytest.mark.asyncio
async def test_settings_renders_menu_for_promo_user():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42)
    await settings_menu.cmd_settings(upd, _mk_context())
    kwargs = upd.effective_message.reply_text.call_args.kwargs
    kb = kwargs.get("reply_markup")
    assert kb is not None
    # Welcome text has each integration name
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Google" in t for t in labels)
    assert any("WhatsApp" in t for t in labels)
    assert any("Закрыть" in t for t in labels)


# ─── Callback: close / back ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_close():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:close")
    await settings_menu.cb_settings(upd, _mk_context())
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert "/settings" in text or "Закрыто" in text


@pytest.mark.asyncio
async def test_cb_back_redraws_menu():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:back")
    await settings_menu.cb_settings(upd, _mk_context())
    kwargs = upd.callback_query.edit_message_text.call_args.kwargs
    assert kwargs.get("reply_markup") is not None


# ─── Callback: open integration card ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_open_whatsapp_shows_card():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:open:whatsapp")
    await settings_menu.cb_settings(upd, _mk_context())
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert "WhatsApp" in text
    assert "Не подключено" in text or "Подключено" in text


@pytest.mark.asyncio
async def test_cb_open_pushover():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:open:pushover")
    await settings_menu.cb_settings(upd, _mk_context())
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert "Pushover" in text


# ─── Callback: google reauth ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_google_reauth_shows_oauth_link():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:google:reauth")
    await settings_menu.cb_settings(upd, _mk_context())
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert "/oauth/start?state=" in text
    assert "test.example" in text


# ─── Callback: whatsapp on/off ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_whatsapp_on_spawns_bridge_and_shows_qr():
    user_id = _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42, callback_data="settings:whatsapp:on")
    with patch("whatsapp_supervisor.start_bridge", return_value=3030) as start, \
         patch("whatsapp_supervisor.get_qr", return_value="QRDATA123"):
        await settings_menu.cb_settings(upd, _mk_context())
    start.assert_called_once_with(user_id)
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert "QRDATA123" in text


@pytest.mark.asyncio
async def test_cb_whatsapp_off_stops_and_disables():
    user_id = _mk_user(42, status="promo")
    import db, settings_menu
    with db.session_scope() as s:
        s.add(db.UserIntegration(user_id=user_id, integration="whatsapp", enabled=1))
    upd = _mk_update(42, callback_data="settings:whatsapp:off")
    with patch("whatsapp_supervisor.stop_bridge") as stop:
        await settings_menu.cb_settings(upd, _mk_context())
    stop.assert_called_once_with(user_id)
    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="whatsapp").one()
        assert row.enabled == 0


# ─── Callback: Alice provisions a secret URL ─────────────────────────────────


@pytest.mark.asyncio
async def test_cb_alice_edit_provisions_secret_url():
    user_id = _mk_user(42, status="promo")
    import db, crypto, settings_menu
    upd = _mk_update(42, callback_data="settings:alice:edit")
    await settings_menu.cb_settings(upd, _mk_context())
    text = upd.callback_query.edit_message_text.call_args[0][0]
    assert f"/alice/{user_id}/" in text
    # config saved + enabled
    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="alice").one()
        cfg = crypto.decrypt_json(row.config_json_encrypted)
        assert cfg["secret"] in text
        assert row.enabled == 1


# ─── Callback: diary_doc toggle ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cb_diary_doc_on_off_round_trip():
    user_id = _mk_user(42, status="promo")
    import db, settings_menu
    upd_on = _mk_update(42, callback_data="settings:diary_doc:on")
    await settings_menu.cb_settings(upd_on, _mk_context())
    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="diary_doc").one()
        assert row.enabled == 1

    upd_off = _mk_update(42, callback_data="settings:diary_doc:off")
    await settings_menu.cb_settings(upd_off, _mk_context())
    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="diary_doc").one()
        assert row.enabled == 0


# ─── Credential capture (pushover / tuya / vesync) ───────────────────────────


@pytest.mark.asyncio
async def test_capture_credential_no_pending_returns_false():
    import settings_menu
    upd = _mk_update(42)
    upd.effective_message.text = "привет"
    ctx = _mk_context()
    consumed = await settings_menu.capture_credential_message(upd, ctx)
    assert consumed is False


@pytest.mark.asyncio
async def test_capture_pushover_saves_encrypted():
    user_id = _mk_user(42, status="promo")
    import settings_menu, db, crypto
    upd = _mk_update(42)
    upd.effective_message.text = "PK123 AK456"
    ctx = _mk_context()
    ctx.user_data["pending_integration"] = "pushover"

    consumed = await settings_menu.capture_credential_message(upd, ctx)
    assert consumed is True

    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="pushover").one()
        assert row.enabled == 1
        cfg = crypto.decrypt_json(row.config_json_encrypted)
        assert cfg == {"user_key": "PK123", "app_token": "AK456"}
    # Pending cleared
    assert "pending_integration" not in ctx.user_data


@pytest.mark.asyncio
async def test_capture_tuya_requires_4_values():
    _mk_user(42, status="promo")
    import settings_menu
    upd = _mk_update(42)
    upd.effective_message.text = "only two values"
    ctx = _mk_context()
    ctx.user_data["pending_integration"] = "tuya"

    consumed = await settings_menu.capture_credential_message(upd, ctx)
    assert consumed is True
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "Нужно 4" in text
    # Pending still set so user can retry
    assert ctx.user_data.get("pending_integration") == "tuya"


@pytest.mark.asyncio
async def test_capture_vesync_full_path():
    user_id = _mk_user(42, status="promo")
    import settings_menu, db, crypto
    upd = _mk_update(42)
    upd.effective_message.text = "me@x.com pass123 IL"
    ctx = _mk_context()
    ctx.user_data["pending_integration"] = "vesync"
    await settings_menu.capture_credential_message(upd, ctx)
    with db.session_scope() as s:
        row = s.query(db.UserIntegration).filter_by(user_id=user_id, integration="vesync").one()
        cfg = crypto.decrypt_json(row.config_json_encrypted)
        assert cfg["email"] == "me@x.com"
        assert cfg["country"] == "IL"
