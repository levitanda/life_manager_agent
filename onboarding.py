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
import re
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

_INVITE_ARG_RE = re.compile(r"^invite_(.+)$")


async def _handle_invite_token(update, context, user_id: int, token: str, lang: str) -> None:
    """Verify a group-invite token and add the user to the group.

    Called from `cmd_start` when the start payload is `invite_<token>`.
    """
    try:
        import groups
        from i18n import t

        payload = groups.verify_invite_token(token)
        group_id = int(payload["group_id"])
        group_name = groups.add_user_to_group_via_invite(user_id, group_id)
        await update.effective_message.reply_text(
            t("groups.added_via_invite", lang, group_name=group_name),
        )
    except groups.InviteTokenError as e:  # type: ignore[name-defined]
        from i18n import t
        msg = t("groups.error.invite_token_invalid", lang)
        if "expired" in str(e).lower():
            msg = t("groups.error.invite_token_expired", lang)
        try:
            await update.effective_message.reply_text(msg)
        except Exception:
            pass
    except Exception as e:
        logger.warning("invite token handling failed: %s", e)
        try:
            from i18n import t
            await update.effective_message.reply_text(
                t("groups.error.invite_token_invalid", lang)
            )
        except Exception:
            pass


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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Greet the user, ensure a DB row exists, prompt for subscription/promo.

    Flow split:
      - No row yet → create row, show welcome+promo prompt (pre-promo gate)
      - Row exists, no access → show welcome+promo prompt
      - Row exists, has access, onboarding_state == "completed"
            → brief "С возвращением"
      - Row exists, has access, onboarding_state != "completed"
            → enter the 12-step wizard (Phase E)
    """
    import db

    tg_user = update.effective_user
    tg_chat = update.effective_chat
    name = (tg_user.first_name or tg_user.username or "друг").strip()

    # Phase H: invite-token entrypoint. If the start payload looks like
    # `/start invite_<token>`, attempt to add the user to a group either now
    # (if they already have access) or right after onboarding completes.
    invite_token: Optional[str] = None
    raw_args = list(getattr(context, "args", None) or [])
    if raw_args:
        m = _INVITE_ARG_RE.match(raw_args[0])
        if m:
            invite_token = m.group(1)

    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None:
            user = db.create_user(
                s,
                telegram_user_id=tg_user.id,
                telegram_chat_id=tg_chat.id,
                display_name=name,
                telegram_username=tg_user.username,
            )
        else:
            # Refresh the username on each /start so existing rows pick up
            # the value the first time we deploy this code path.
            if tg_user.username and not user.telegram_username:
                user.telegram_username = tg_user.username.lstrip("@").lower()
        was_active = user.has_access()
        onboarding_done = (getattr(user, "onboarding_state", None) == "completed")
        user_id = user.id
        lang = user.language or "ru"

    if invite_token:
        await _handle_invite_token(update, context, user_id, invite_token, lang)
        if was_active and onboarding_done:
            return

    if was_active and onboarding_done:
        await update.effective_message.reply_text(
            f"С возвращением, {name}! 🙂 Доступ активен — пиши что нужно.",
        )
        return ConversationHandler.END

    if was_active and not onboarding_done:
        # Jony-path: existing user with promo/active but no wizard yet.
        import onboarding_wizard
        return await onboarding_wizard.start_wizard(update, context, mode="onboarding")

    await update.effective_message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_start_keyboard(),
    )
    return ConversationHandler.END


# ─── /promo CODE ──────────────────────────────────────────────────────────────


async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a promo code to the user. Creates the user row on the fly if needed.

    On successful redemption, enters the 12-step wizard (Phase E).
    """
    import db

    if not context.args:
        await update.effective_message.reply_text(
            "Использование: `/promo КОД`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

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
                telegram_username=tg_user.username,
            )
        ok, msg = db.redeem_promo(s, user, code)
        was_active = user.has_access()
        onboarding_done = (getattr(user, "onboarding_state", None) == "completed")

    await update.effective_message.reply_text(msg)
    if ok and was_active and not onboarding_done:
        import onboarding_wizard
        return await onboarding_wizard.start_wizard(update, context, mode="onboarding")
    return ConversationHandler.END


# ─── /subscribe ───────────────────────────────────────────────────────────────


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Subscription is not enabled in Israel (Stripe doesn't work there yet).
    Show a friendly 'coming soon' message and prompt for promo instead.
    """
    await update.effective_message.reply_text(COMING_SOON_TEXT, parse_mode=ParseMode.MARKDOWN)


# ─── /cancel ─────────────────────────────────────────────────────────────────


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the user's access.

    Stops the WhatsApp bridge immediately. For Stripe users, also schedules
    a Stripe-side cancellation at the end of the current billing period;
    the final status flip happens on the subscription.deleted webhook.
    For promo / non-paying users, flips status to 'inactive' right away so
    scheduled digests stop and gated commands deny.
    """
    import db

    tg_user = update.effective_user
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None:
            await update.effective_message.reply_text(
                "Нечего отменять — ты ещё не зарегистрирован."
            )
            return
        user_id = user.id
        has_stripe_sub = bool(user.stripe_subscription_id)

    if has_stripe_sub:
        import stripe_client
        try:
            stripe_client.cancel_subscription(user_id)
        except Exception as e:
            logger.exception("Stripe cancel failed: %s", e)
            await update.effective_message.reply_text(
                "⚠️ Не получилось отменить подписку в Stripe. Напиши, разберёмся вручную."
            )
            return
        try:
            import bot_handlers
            bot_handlers.deactivate_and_cleanup(user_id, reason="user_cancel")
        except Exception as e:
            logger.warning("WA cleanup after /cancel failed user=%s: %s", user_id, e)
        await update.effective_message.reply_text(
            "✅ Подписка отменится в конце текущего расчётного периода. "
            "До конца этого периода всё работает как обычно. "
            "WhatsApp-мост отключён сейчас."
        )
        return

    # No Stripe subscription (promo user or never-paid).
    try:
        import bot_handlers
        bot_handlers.deactivate_and_cleanup(user_id, reason="user_cancel")
    except Exception as e:
        logger.warning("cancel cleanup failed user=%s: %s", user_id, e)
    await update.effective_message.reply_text(
        "✅ Доступ отключён. WhatsApp-мост остановлен. "
        "Если захочешь вернуться — /start или /promo КОД."
    )


# ─── /profile (Phase F: re-onboarding) ───────────────────────────────────────


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-enter the wizard in profile mode for an existing user.

    Profile mode shows each step with the current value plus
    Keep/Change buttons so users can selectively re-edit fields.
    """
    import db
    tg_user = update.effective_user
    with db.session_scope() as s:
        user = db.get_user_by_telegram_id(s, tg_user.id)
        if user is None:
            await update.effective_message.reply_text(
                "Сначала /start — мы ещё не знакомы.",
            )
            return ConversationHandler.END
        if not user.has_access():
            await update.effective_message.reply_text(
                "🔒 Подписка неактивна. Используй /promo КОД или /subscribe."
            )
            return ConversationHandler.END

    import onboarding_wizard
    return await onboarding_wizard.start_wizard(update, context, mode="profile")


# ─── ConversationHandler factory ──────────────────────────────────────────────


def build_wizard_conversation_handler() -> ConversationHandler:
    """Return the ConversationHandler that drives both /start (new user
    onboarding) and /profile (existing user re-edit).

    Entry points: /start, /promo (after redemption), /profile.
    Each state has button + text fall-backs as needed.
    """
    import onboarding_wizard as w

    states = {
        w.STEP_LANGUAGE: [
            CallbackQueryHandler(w.step_language_handle, pattern=r"^wiz:lang:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
        ],
        w.STEP_NAME: [
            CallbackQueryHandler(w.step_name_handle_button, pattern=r"^wiz:name:use_first"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_name_handle_text),
        ],
        w.STEP_CITY: [
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_city_handle_text),
        ],
        w.STEP_TIMEZONE: [
            CallbackQueryHandler(w.step_timezone_handle_button, pattern=r"^wiz:tz:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_timezone_handle_text),
        ],
        w.STEP_MORNING_TIME: [
            CallbackQueryHandler(w.step_time_handle_button, pattern=r"^wiz:time:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_time_handle_text),
        ],
        w.STEP_EVENING_TIME: [
            CallbackQueryHandler(w.step_time_handle_button, pattern=r"^wiz:time:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_time_handle_text),
        ],
        w.STEP_GOOGLE: [
            CallbackQueryHandler(w.step_google_handle_button, pattern=r"^wiz:google:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
        ],
        w.STEP_NEWS: [
            CallbackQueryHandler(w.step_news_handle_button, pattern=r"^wiz:news:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_news_handle_text),
        ],
        w.STEP_PERSONALITY: [
            CallbackQueryHandler(w.step_personality_handle_button, pattern=r"^wiz:persona:"),
            CallbackQueryHandler(w.cb_keep_or_change, pattern=r"^wiz:(keep|change):"),
        ],
        w.STEP_FIRST_TASK: [
            CallbackQueryHandler(w.step_first_task_handle_skip, pattern=r"^wiz:skip:task"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_first_task_handle_text),
        ],
        w.STEP_FIRST_GOAL: [
            CallbackQueryHandler(w.step_first_goal_handle_skip, pattern=r"^wiz:skip:goal"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, w.step_first_goal_handle_text),
        ],
        w.STEP_INTEGRATIONS: [
            CallbackQueryHandler(w.step_integrations_handle_done, pattern=r"^wiz:integrations:done"),
        ],
    }

    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("promo", cmd_promo),
            CommandHandler("profile", cmd_profile),
        ],
        states=states,
        fallbacks=[
            CommandHandler("cancel", _wizard_cancel),
        ],
        # Allow re-entry so /profile while inside the wizard restarts it.
        allow_reentry=True,
        per_user=True,
    )


async def _wizard_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Exit the wizard early. Does NOT clear partially-written fields."""
    if context.user_data is not None:
        context.user_data.pop("wizard", None)
    try:
        await update.effective_message.reply_text("Отменено.")
    except Exception:
        pass
    return ConversationHandler.END


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
