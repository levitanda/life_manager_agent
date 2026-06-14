"""Per-user `/settings` Telegram menu — manage optional integrations.

Integrations covered:
  - google         (always required, shown for re-auth only)
  - whatsapp       (per-user Baileys process via whatsapp_supervisor)
  - pushover       (USER_KEY + APP_TOKEN)
  - alice          (per-user secret, URL shown to user)
  - tuya           (API_KEY + SECRET + REGION + USER_ID)
  - vesync         (EMAIL + PASSWORD + COUNTRY)
  - diary_doc      (toggle Google Doc mirror on/off)

Each integration row stores its config Fernet-encrypted in
user_integrations.config_json_encrypted.
"""

from __future__ import annotations

import logging
import os
import secrets as pysecrets
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

logger = logging.getLogger(__name__)


INTEGRATIONS = [
    ("google", "🔑 Google"),
    ("whatsapp", "💬 WhatsApp"),
    ("pushover", "📱 Pushover"),
    ("alice", "🔊 Yandex Алиса"),
    ("tuya", "💡 Tuya"),
    ("vesync", "🔌 VeSync"),
    ("diary_doc", "📓 Дневник в Docs"),
]


# Conversation states for inline data entry
(
    AWAIT_PUSHOVER, AWAIT_TUYA, AWAIT_VESYNC,
) = range(3)


# ─── DB helpers ──────────────────────────────────────────────────────────────


def _is_enabled(user_id: int, integration: str) -> bool:
    import db
    with db.session_scope() as s:
        row = (
            s.query(db.UserIntegration)
            .filter_by(user_id=user_id, integration=integration)
            .one_or_none()
        )
        return bool(row and row.enabled)


def _upsert_config(user_id: int, integration: str, config: dict, enabled: bool = True) -> None:
    import db
    import crypto
    with db.session_scope() as s:
        row = (
            s.query(db.UserIntegration)
            .filter_by(user_id=user_id, integration=integration)
            .one_or_none()
        )
        enc = crypto.encrypt_json(config)
        if row is None:
            s.add(db.UserIntegration(
                user_id=user_id,
                integration=integration,
                enabled=1 if enabled else 0,
                config_json_encrypted=enc,
            ))
        else:
            row.enabled = 1 if enabled else 0
            row.config_json_encrypted = enc


def _disable(user_id: int, integration: str) -> None:
    import db
    with db.session_scope() as s:
        row = (
            s.query(db.UserIntegration)
            .filter_by(user_id=user_id, integration=integration)
            .one_or_none()
        )
        if row:
            row.enabled = 0


def _get_config(user_id: int, integration: str) -> Optional[dict]:
    import db
    import crypto
    with db.session_scope() as s:
        row = (
            s.query(db.UserIntegration)
            .filter_by(user_id=user_id, integration=integration)
            .one_or_none()
        )
        if not row or not row.config_json_encrypted:
            return None
        try:
            return crypto.decrypt_json(row.config_json_encrypted)
        except Exception:
            return None


# ─── Menu rendering ──────────────────────────────────────────────────────────


def _menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Build the settings menu inline keyboard with current state markers."""
    rows = []
    for key, label in INTEGRATIONS:
        on = _integration_is_live(user_id, key)
        marker = "✅" if on else "⚪️"
        rows.append([
            InlineKeyboardButton(f"{marker} {label}", callback_data=f"settings:open:{key}"),
        ])
    rows.append([InlineKeyboardButton("⬅️ Закрыть", callback_data="settings:close")])
    return InlineKeyboardMarkup(rows)


def _integration_is_live(user_id: int, integration: str) -> bool:
    """Truth source for the ✅/⚪ marker.

    - google: a stored OAuth token is enough proof
    - whatsapp: bridge process must be paired (status check), NOT just enabled flag
    - everything else: the user_integrations.enabled flag
    """
    if integration == "google":
        return _has_google_token(user_id)
    if integration == "whatsapp":
        try:
            import whatsapp_client
            return bool(whatsapp_client.status(user_id=user_id).get("ready"))
        except Exception:
            return False
    return _is_enabled(user_id, integration)


def _has_google_token(user_id: int) -> bool:
    import db
    with db.session_scope() as s:
        return s.get(db.GoogleToken, user_id) is not None


def _integration_actions_keyboard(integration: str, enabled: bool) -> InlineKeyboardMarkup:
    rows = []
    if integration == "google":
        rows.append([InlineKeyboardButton("🔄 Перевыпустить", callback_data="settings:google:reauth")])
    elif integration == "whatsapp":
        if enabled:
            rows.append([InlineKeyboardButton("🛑 Отключить", callback_data="settings:whatsapp:off")])
            rows.append([InlineKeyboardButton("🔄 Переподключить", callback_data="settings:whatsapp:restart")])
        else:
            rows.append([InlineKeyboardButton("📲 Подключить по коду", callback_data="settings:whatsapp:pair")])
            rows.append([InlineKeyboardButton("🟫 Альтернатива: QR", callback_data="settings:whatsapp:on")])
    elif integration == "diary_doc":
        if enabled:
            rows.append([InlineKeyboardButton("🛑 Отключить", callback_data="settings:diary_doc:off")])
        else:
            rows.append([InlineKeyboardButton("✅ Включить", callback_data="settings:diary_doc:on")])
    else:
        if enabled:
            rows.append([InlineKeyboardButton("✏️ Изменить", callback_data=f"settings:{integration}:edit")])
            rows.append([InlineKeyboardButton("🛑 Удалить", callback_data=f"settings:{integration}:off")])
        else:
            rows.append([InlineKeyboardButton("➕ Подключить", callback_data=f"settings:{integration}:edit")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="settings:back")])
    return InlineKeyboardMarkup(rows)


# ─── /settings entry point ────────────────────────────────────────────────────


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import db
    user = db.get_user_by_telegram_id_from_update(update) if hasattr(db, "get_user_by_telegram_id_from_update") else None
    if user is None:
        # Reuse the access module path for symmetry
        import access
        user = access.get_user_from_update(update)
    if user is None:
        await update.effective_message.reply_text("Сначала /start — мы ещё не знакомы.")
        return
    if not user.has_access():
        await update.effective_message.reply_text(
            "🔒 Для настроек нужна активная подписка или промокод.\n\n"
            "Используй /promo КОД если у тебя он есть."
        )
        return
    await update.effective_message.reply_text(
        "*Настройки интеграций*\nВыбери что подключить/изменить:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_menu_keyboard(user.id),
    )


# ─── Callback dispatcher ──────────────────────────────────────────────────────


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle taps on the settings inline keyboard."""
    query = update.callback_query
    await query.answer()
    parts = (query.data or "").split(":")
    # parts: ["settings", action, ...]
    if len(parts) < 2:
        return

    import access
    user = access.get_user_from_update(update)
    if user is None or not user.has_access():
        await query.edit_message_text("🔒 Доступ закрыт.")
        return
    user_id = user.id

    action = parts[1]

    if action == "close":
        await query.edit_message_text("Закрыто. Открой снова через /settings.")
        return

    if action == "back":
        await query.edit_message_text(
            "*Настройки интеграций*\nВыбери что подключить/изменить:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_menu_keyboard(user_id),
        )
        return

    if action == "open":
        integration = parts[2]
        await _render_integration(query, user_id, integration)
        return

    if action == "google":
        await _handle_google(query, user_id, parts)
        return
    if action == "whatsapp":
        await _handle_whatsapp(query, user_id, parts, context=context)
        return
    if action == "alice":
        await _handle_alice(query, user_id, parts)
        return
    if action == "diary_doc":
        await _handle_diary_doc(query, user_id, parts)
        return
    if action in ("pushover", "tuya", "vesync"):
        await _handle_credential_integration(query, context, user_id, action, parts)
        return


async def _render_integration(query, user_id: int, integration: str) -> None:
    label = dict(INTEGRATIONS).get(integration, integration)
    enabled = _integration_is_live(user_id, integration)
    descs = {
        "google": "Календарь, Gmail, Контакты, Drive, Docs. Обязательно для работы бота.",
        "whatsapp": "Подключение твоего номера через QR. Бот будет читать непрочитанные и слать сообщения от твоего имени.",
        "pushover": "Push-уведомления на телефон. Нужны USER_KEY и APP_TOKEN из pushover.net.",
        "alice": "Голосовой интерфейс через Яндекс Алису. Бот выдаёт URL для навыка.",
        "tuya": "Умный дом Tuya. Нужны API_KEY, SECRET, REGION, USER_ID.",
        "vesync": "Умный дом VeSync (Levoit, Cosori). Нужны EMAIL и PASSWORD.",
        "diary_doc": "Зеркало личного дневника в Google Docs. По умолчанию включено.",
    }
    status = "✅ Подключено" if enabled else "⚪️ Не подключено"
    text = f"*{label}*\n\n_Статус:_ {status}\n\n{descs.get(integration, '')}"
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_integration_actions_keyboard(integration, enabled),
    )


async def _handle_google(query, user_id: int, parts: list[str]) -> None:
    import web
    state = web.sign_state(user_id)
    base = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
    url = f"{base}/oauth/start?state={state}"
    await query.edit_message_text(
        f"Открой ссылку и авторизуйся:\n\n{url}",
    )


async def _handle_whatsapp(query, user_id: int, parts: list[str], context=None) -> None:
    """Spawn or stop a per-user Baileys bridge.

    "Connected" is reported only after the WhatsApp app has paired and the
    bridge reports status='running'. Until then we keep enabled=0 so the
    settings menu doesn't lie.
    """
    action = parts[2] if len(parts) > 2 else "on"
    import whatsapp_supervisor

    if action == "off":
        whatsapp_supervisor.stop_bridge(user_id)
        _disable(user_id, "whatsapp")
        await query.edit_message_text("🛑 WhatsApp отключён.")
        return

    if action == "pair":
        # Pairing-by-phone-code: spawn bridge, then ask user for phone number.
        try:
            whatsapp_supervisor.start_bridge(user_id)
            _upsert_config(user_id, "whatsapp", {"managed": True}, enabled=False)
        except Exception as e:
            logger.exception("WhatsApp bridge start failed: %s", e)
            await query.edit_message_text(f"⚠️ Не удалось запустить bridge: {e}")
            return
        if context is not None and context.user_data is not None:
            context.user_data["pending_integration"] = "whatsapp_pair"
        await query.edit_message_text(
            "📲 *Подключение WhatsApp по коду*\n\n"
            "Пришли свой номер WhatsApp одним сообщением в формате `+972501234567` "
            "(с плюсом и кодом страны, без пробелов).\n\n"
            "Я попрошу WhatsApp выдать 8-значный код, ты введёшь его в приложении.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action in ("on", "restart"):
        if action == "restart":
            whatsapp_supervisor.stop_bridge(user_id)
            _disable(user_id, "whatsapp")
        try:
            whatsapp_supervisor.start_bridge(user_id)
            # Persist a row so the user can come back and check QR, but DO NOT
            # set enabled=1 yet — that flips only after successful pairing.
            _upsert_config(user_id, "whatsapp", {"managed": True}, enabled=False)
        except Exception as e:
            logger.exception("WhatsApp bridge start failed: %s", e)
            await query.edit_message_text(f"⚠️ Не удалось запустить bridge: {e}")
            return

        # Baileys typically emits the QR within 5-20s on cold start; give 45s.
        qr = whatsapp_supervisor.get_qr(user_id, timeout_seconds=45)
        if qr is None:
            await query.edit_message_text(
                "⏳ Bridge запускается, но QR-код пока не получен.\n\n"
                "Подожди 30 секунд и нажми снова `/settings → WhatsApp → Подключить`. "
                "Статус останется *Не подключено* пока ты не отсканируешь QR.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await query.edit_message_text(
            f"📲 Отсканируй этот QR в WhatsApp → Связанные устройства:\n\n"
            f"`{qr}`\n\n"
            f"После успешной привязки вернись в `/settings → WhatsApp` — "
            f"статус сменится на ✅ Подключено.",
            parse_mode=ParseMode.MARKDOWN,
        )


def _whatsapp_is_paired(user_id: int) -> bool:
    """True if the bridge for this user is up AND already paired with WhatsApp."""
    try:
        import whatsapp_client
        s = whatsapp_client.status(user_id=user_id)
        return bool(s.get("ready"))
    except Exception:
        return False


async def _handle_alice(query, user_id: int, parts: list[str]) -> None:
    sub = parts[2] if len(parts) > 2 else "edit"
    if sub == "off":
        _disable(user_id, "alice")
        await query.edit_message_text("🛑 Алиса отключена.")
        return
    # 'edit' generates a fresh secret and shows the URL
    secret = pysecrets.token_urlsafe(16)
    _upsert_config(user_id, "alice", {"secret": secret}, enabled=True)
    base = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
    url = f"{base}/alice/{user_id}/{secret}"
    await query.edit_message_text(
        f"🔊 Yandex Алиса\n\nВ настройках навыка укажи URL:\n\n`{url}`\n\n"
        f"Этот URL — секретный, никому не давай.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _handle_diary_doc(query, user_id: int, parts: list[str]) -> None:
    sub = parts[2] if len(parts) > 2 else "on"
    if sub == "off":
        _disable(user_id, "diary_doc")
        await query.edit_message_text("🛑 Зеркало дневника в Google Docs отключено. Локальный файл продолжает работать.")
        return
    _upsert_config(user_id, "diary_doc", {"enabled": True}, enabled=True)
    await query.edit_message_text("✅ Зеркало дневника в Google Docs включено.")


async def _handle_credential_integration(query, context, user_id: int, integration: str, parts: list[str]) -> None:
    """Pushover / Tuya / VeSync — ask the user to send credentials as a one-shot message."""
    sub = parts[2] if len(parts) > 2 else "edit"
    if sub == "off":
        _disable(user_id, integration)
        await query.edit_message_text(f"🛑 {integration} отключён.")
        return
    # Set per-integration prompt + store expected key in context.user_data
    prompts = {
        "pushover": "Пришли одной строкой: `USER_KEY APP_TOKEN`",
        "tuya": "Пришли одной строкой через пробел: `API_KEY API_SECRET REGION USER_ID`",
        "vesync": "Пришли одной строкой: `EMAIL PASSWORD COUNTRY_CODE`",
    }
    context.user_data["pending_integration"] = integration
    await query.edit_message_text(
        f"{prompts[integration]}\n\nПосле отправки данные будут зашифрованы и сохранены.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ─── Free-text capture for credential integrations ───────────────────────────


async def capture_credential_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Called by the bot's natural-language handler.

    If the user has a pending integration setup, parse the message and store
    credentials. Returns True if the message was consumed (do not pass to agent).
    """
    pending = context.user_data.get("pending_integration") if context.user_data else None
    if not pending:
        return False
    import access
    user = access.get_user_from_update(update)
    if user is None:
        return False
    text = (update.effective_message.text or "").strip()
    parts = text.split()
    # Special pseudo-integration: WhatsApp pairing — capture phone, request code
    if pending == "whatsapp_pair":
        phone_digits = "".join(c for c in text if c.isdigit())
        if len(phone_digits) < 8:
            await update.effective_message.reply_text(
                "Не похоже на номер. Пришли в формате `+972501234567`.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return True
        await update.effective_message.reply_text("⏳ Запрашиваю код у WhatsApp…")
        import whatsapp_supervisor
        result = whatsapp_supervisor.request_pairing_code(user.id, phone_digits, timeout_seconds=45)
        context.user_data.pop("pending_integration", None)
        if not result.get("ok"):
            err = result.get("error") or "unknown error"
            if result.get("already_paired"):
                await update.effective_message.reply_text(
                    "✅ Эта сессия уже привязана к WhatsApp. Открой `/settings → WhatsApp` — статус должен быть зелёным."
                )
            else:
                await update.effective_message.reply_text(f"⚠️ Не удалось получить код: {err}\nПопробуй заново через `/settings → WhatsApp`.")
            return True
        code = result["code"]
        await update.effective_message.reply_text(
            "📲 *Подключение WhatsApp — инструкция*\n\n"
            f"Твой код: `{code}`\n\n"
            "_(нажми на код чтобы скопировать — тапни и подержи)_\n\n"
            "*Что делать:*\n"
            "1. Открой *WhatsApp* на телефоне (на котором этот номер)\n"
            "2. ⚙️ *Настройки* → *Связанные устройства*\n"
            "3. Нажми *«Привязать устройство»*\n"
            "4. На экране сканера QR — нажми *«Привязать по номеру телефона»* (или *Link with phone number*)\n"
            "5. Введи код: `{c}`\n\n"
            "Когда увидишь «✅ Устройство привязано» — вернись сюда и открой `/settings → WhatsApp`. "
            "Статус сменится на 🟢 Подключено.\n\n"
            "_Код действителен 60 секунд. Если не успел — открой меню снова._".format(c=code),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    parsers = {
        "pushover": (2, lambda p: {"user_key": p[0], "app_token": p[1]}),
        "tuya": (4, lambda p: {"api_key": p[0], "api_secret": p[1], "region": p[2], "user_id": p[3]}),
        "vesync": (3, lambda p: {"email": p[0], "password": p[1], "country": p[2]}),
    }
    if pending not in parsers:
        context.user_data.pop("pending_integration", None)
        return False
    expected, builder = parsers[pending]
    if len(parts) < expected:
        await update.effective_message.reply_text(
            f"Нужно {expected} значения, получил {len(parts)}. Попробуй снова."
        )
        return True
    _upsert_config(user.id, pending, builder(parts), enabled=True)
    context.user_data.pop("pending_integration", None)
    await update.effective_message.reply_text(f"✅ {pending} подключён.")
    return True
