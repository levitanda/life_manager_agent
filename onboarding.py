"""Telegram onboarding wizard for new users.

Triggered by /start. The flow:

  1. Greet, explain features, ask for subscription vs promo.
  2. If subscribed via Stripe Checkout → webhook activates → bot pings.
     If promo → /promo CODE flow handles it inline.
  3. Ask user to authorize Google (OAuth URL with signed state).
  4. Pick timezone, morning_time, evening_time.
  5. Optional integrations (settings menu).
  6. Done — agent is ready.

Pure handler module. The bot wires these into its dispatcher in
bot_handlers.py.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


WELCOME_TEXT = (
    "👋 *Привет! Я личный AI-ассистент.*\n\n"
    "Помогаю с календарём, почтой, WhatsApp, дайджестами, дневником, "
    "умным домом. Работаю каждый день на твоём собственном расписании.\n\n"
    "🚧 Сейчас я в закрытой бете. Скоро буду доступен каждому — "
    "следи за обновлениями.\n\n"
    "🎁 Если есть промокод — введи `/promo КОД`."
)

COMING_SOON_TEXT = (
    "🚧 Платежи пока не подключены.\n\n"
    "Скоро бот будет доступен по подписке всем — следи за обновлениями. "
    "Если у тебя есть промокод, попробуй `/promo КОД`."
)


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 У меня промокод", callback_data="onboard:promo")],
        [InlineKeyboardButton("ℹ️ Что я умею", callback_data="onboard:features")],
        [InlineKeyboardButton("🚧 Подписка (скоро)", callback_data="onboard:subscribe")],
    ])


# ─── /start ───────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the user, ensure a DB row exists, prompt for subscription/promo."""
    import db

    tg_user = update.effective_user
    tg_chat = update.effective_chat
    name = (tg_user.first_name or tg_user.username or "друг").strip()

    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None:
            user = db.create_user(
                s,
                telegram_user_id=tg_user.id,
                telegram_chat_id=tg_chat.id,
                display_name=name,
            )
        was_active = user.has_access()
        user_id = user.id

    if was_active:
        await update.effective_message.reply_text(
            f"С возвращением, {name}! 🙂 Доступ активен — пиши что нужно.",
        )
        return

    await update.effective_message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_start_keyboard(),
    )


# ─── /promo CODE ──────────────────────────────────────────────────────────────


async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a promo code to the user. Creates the user row on the fly if needed."""
    import db

    if not context.args:
        await update.effective_message.reply_text(
            "Использование: `/promo КОД`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    code = " ".join(context.args).strip()
    tg_user = update.effective_user

    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None:
            user = db.create_user(
                s,
                telegram_user_id=tg_user.id,
                telegram_chat_id=update.effective_chat.id,
                display_name=(tg_user.first_name or tg_user.username or None),
            )
        ok, msg = db.redeem_promo(s, user, code)
        was_active = user.has_access()

    await update.effective_message.reply_text(msg)
    if ok and was_active:
        await _send_post_subscribe_steps(update, context)


# ─── /subscribe ───────────────────────────────────────────────────────────────


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscription is not enabled in Israel (Stripe doesn't work there yet).
    Show a friendly 'coming soon' message and prompt for promo instead.
    """
    await update.effective_message.reply_text(COMING_SOON_TEXT, parse_mode=ParseMode.MARKDOWN)


# ─── /cancel ─────────────────────────────────────────────────────────────────


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the user's Stripe subscription at end of current period."""
    import db
    import stripe_client

    tg_user = update.effective_user
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None or not user.stripe_subscription_id:
            await update.effective_message.reply_text(
                "У тебя нет активной подписки для отмены."
            )
            return
        sub_id = user.stripe_subscription_id
        user_id = user.id

    try:
        stripe_client.cancel_subscription(user_id)
        await update.effective_message.reply_text(
            "✅ Подписка отменится в конце текущего расчётного периода. "
            "До конца этого периода всё работает как обычно."
        )
    except Exception as e:
        logger.exception("Stripe cancel failed: %s", e)
        await update.effective_message.reply_text(
            "⚠️ Не получилось отменить. Напиши, разберёмся вручную."
        )


# ─── Post-subscribe / post-promo nudge ───────────────────────────────────────


GOOGLE_AUTH_PROMPT = (
    "🎉 Доступ открыт. Следующий шаг — подключи Google аккаунт "
    "(календарь, Gmail, дневник в Docs):\n\n"
    "{url}\n\n"
    "После авторизации возвращайся в этот чат — настроим часовой пояс и время дайджеста."
)


async def _send_post_subscribe_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """After subscription/promo activates, show the next setup step."""
    import os
    import web

    user_id = update.effective_user.id  # caller already ensured user exists
    # Resolve our internal user_id
    import db
    with db.session_scope() as s:
        u = db.get_user_by_telegram_id(s, user_id)
        if u is None:
            return
        internal_id = u.id

    base = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
    state = web.sign_state(internal_id)
    url = f"{base}/oauth/start?state={state}"
    await update.effective_message.reply_text(
        GOOGLE_AUTH_PROMPT.format(url=url),
    )


# ─── Callback handler for the welcome keyboard ───────────────────────────────


async def cb_onboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle taps on the welcome inline keyboard."""
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]

    if action == "subscribe":
        # Reuse cmd_subscribe; need to fake the update.effective_message routing
        # by attaching message text directly via reply.
        await cmd_subscribe(update, context)
    elif action == "promo":
        await query.message.reply_text(
            "Введи команду: `/promo КОД` (твой код).",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif action == "features":
        await query.message.reply_text(
            "Что я умею:\n"
            "• Веду календарь, добавляю/удаляю/переношу задачи\n"
            "• Читаю Gmail, рекомендую важные письма\n"
            "• Утренний дайджест + вечерний чек-ин\n"
            "• WhatsApp: сводка непрочитанных по приоритетам\n"
            "• Личный дневник в Google Docs\n"
            "• Управление умным домом (Tuya/VeSync)\n"
            "• Яндекс Алиса как голосовой интерфейс\n"
            "• Планирование действий («напомни в 18:00»)\n"
            "• Подключение к другим A2A-агентам"
        )
