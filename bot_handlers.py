"""Telegram bot command and message handlers."""

import datetime
import logging
import tempfile

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import openai

import asyncio

import agent
import calendar_client
import config
import contacts_client
import conversation
import weather_client
import digest as digest_module
import gmail_client
import news_client
import birthday_client
import whatsapp_client
import whatsapp_summary
import parser
import pushover_client

logger = logging.getLogger(__name__)

WAITING_PROGRESS = 1


def _is_owner(update: Update) -> bool:
    return update.effective_user.id == config.TELEGRAM_CHAT_ID


def _resolve_user_id(update: Update) -> int | None:
    """Map Telegram user to internal user_id from DB. None if not yet migrated."""
    try:
        import db
        with db.session_scope() as s:
            user = db.get_user_by_telegram_id(s, update.effective_user.id)
            return user.id if user else None
    except Exception:
        return None


def _user_tz(user_id: int | None):
    """Resolve the timezone (pytz) for a user. Falls back to config.TIMEZONE
    when user_id is None (legacy single-user path) or DB lookup fails."""
    if user_id is None:
        return pytz.timezone(config.TIMEZONE)
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u and u.timezone:
                return pytz.timezone(u.timezone)
    except Exception:
        pass
    return pytz.timezone(config.TIMEZONE)


async def _authorize(update: Update):
    """Multi-user gate. Returns (user, user_id) for the authorized caller, or
    (None, None) if access denied (caller is then silently dropped — never
    spam strangers who poked the bot).

    Access rules:
      - DB user with subscription_status in ('active','promo')  → authorized
      - DB user with no access  → polite reply, deny
      - Not in DB AND matches legacy TELEGRAM_CHAT_ID env  → authorized as
        Daria, user_id may be None (pre-migration) or her DB id
      - Not in DB AND not Daria  → silent deny
    """
    try:
        import access
        user = access.get_user_from_update(update)
        if user is not None:
            if user.has_access():
                return user, user.id
            try:
                await update.effective_message.reply_text(
                    "🔒 Подписка неактивна. Используй /promo КОД или /subscribe."
                )
            except Exception:
                pass
            return None, None
    except Exception as e:
        logger.warning("access lookup failed: %s", e)
    # Legacy fallback for Daria pre-migration
    if _is_owner(update):
        return None, _resolve_user_id(update)  # user_id may still be None
    return None, None


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user's dashboard — task counts, goals, streaks, today's calendar."""
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    if user_id is None:
        await update.message.reply_text("Дашборд доступен после миграции в базу. Открой /profile.")
        return
    try:
        import dashboard as dash
        text, keyboard = dash.build_telegram_dashboard(user_id)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
    except Exception as e:
        logger.exception("dashboard build failed: %s", e)
        await update.message.reply_text(f"⚠️ Не удалось построить дашборд: {e}")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    summaries = conversation.get_recent_summaries(10, user_id=user_id)
    if not summaries:
        await update.message.reply_text("Пока нет сохранённых резюме сессий.")
        return
    lines = ["🧠 *Долгосрочная память (последние сессии)*\n"]
    for s in reversed(summaries):
        lines.append(f"*{s['date']}*\n{s['summary']}\n")
    await _reply_split(update, "\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline help for already-onboarded users. /start is handled by onboarding.

    Supports `/help short` for a compact (~under 500 chars) overview, otherwise
    sends the full categorized command list from the i18n catalog. The whole
    text is wrapped in RTL embedding markers for Hebrew users so the embedded
    latin command names render in the right direction.
    """
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return

    import i18n
    lang = i18n.user_language(user_id)

    args = context.args or []
    mode = (args[0].lower() if args else "")
    key = "help.short_text" if mode == "short" else "help.full_text"
    text = i18n.t(key, lang)
    if lang == "he":
        text = i18n.wrap_rtl(text)

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Использование: /add short <задача> или /add long <задача>\n"
            "Примеры:\n"
            "  /add short Встреча с врачом в 14:00\n"
            "  /add short Тренировка завтра в 18:30 на 1.5 часа\n"
            "  /add long Сдать проект к 1 июня"
        )
        return

    task_type = args[0].lower()
    if task_type not in ("short", "long"):
        await update.message.reply_text("Тип задачи: short (краткосрочная) или long (долгосрочная)")
        return

    raw_text = " ".join(args[1:])
    try:
        parsed = parser.parse_task(raw_text)
        title = parsed.get("title") or raw_text
        date_str = parsed.get("date")
        end_date_str = parsed.get("end_date")
        time_str = parsed.get("time")
        duration_minutes = int(parsed.get("duration_minutes") or 60)

        tz = _user_tz(user_id)
        start_dt = None
        due_date = None
        end_date = None

        if time_str:
            base_date = (
                datetime.date.fromisoformat(date_str)
                if date_str
                else datetime.datetime.now(tz).date()
            )
            h, m = map(int, time_str.split(":"))
            start_dt = tz.localize(datetime.datetime(base_date.year, base_date.month, base_date.day, h, m))
        else:
            if date_str:
                due_date = datetime.date.fromisoformat(date_str)
            if end_date_str:
                end_date = datetime.date.fromisoformat(end_date_str)

        calendar_client.add_task(
            title, task_type,
            due_date=due_date, end_date=end_date,
            start_dt=start_dt, duration_minutes=duration_minutes,
            user_id=user_id,
        )

        emoji = "⚡" if task_type == "short" else "🎯"
        if start_dt:
            dur_str = f"{duration_minutes} мин." if duration_minutes != 60 else "1 час"
            await update.message.reply_text(
                f"{emoji} Добавлено: {title}\n"
                f"📅 {start_dt.strftime('%d.%m.%Y в %H:%M')} ({dur_str})"
            )
        elif due_date and end_date:
            await update.message.reply_text(
                f"{emoji} Добавлено: {title}\n"
                f"📅 {due_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"
            )
        elif due_date:
            await update.message.reply_text(
                f"{emoji} Добавлено: {title}\n"
                f"📅 до {due_date.strftime('%d.%m.%Y')}"
            )
        else:
            await update.message.reply_text(f"{emoji} Добавлено: {title}")

    except Exception as e:
        logger.error("Failed to add task: %s", e)
        await update.message.reply_text("Ошибка при добавлении задачи. Проверь логи.")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return

    short = calendar_client.get_active_tasks("short", user_id=user_id)
    long_ = calendar_client.get_active_tasks("long", user_id=user_id)

    lines = ["📋 *Активные задачи*\n"]

    def fmt_task(t: dict) -> str:
        if t.get("time"):
            return f"{t['title']} — {t['due']} {t['time']}–{t['end_time']}"
        return f"{t['title']} — до {t['due']}"

    if short:
        lines.append("*⚡ Краткосрочные (1-3 дня):*")
        for i, t in enumerate(short, 1):
            lines.append(f"{i}. {fmt_task(t)}")
    else:
        lines.append("*⚡ Краткосрочных задач нет.*")

    lines.append("")

    if long_:
        lines.append("*🎯 Долгосрочные:*")
        offset = len(short)
        for i, t in enumerate(long_, offset + 1):
            lines.append(f"{i}. {fmt_task(t)}")
    else:
        lines.append("*🎯 Долгосрочных задач нет.*")

    await _reply_split(update, "\n".join(lines))


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return

    if not context.args:
        await update.message.reply_text("Использование: /done <номер задачи>")
        return

    try:
        num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажи номер задачи числом.")
        return

    short = calendar_client.get_active_tasks("short", user_id=user_id)
    long_ = calendar_client.get_active_tasks("long", user_id=user_id)
    all_tasks = short + long_

    if num < 1 or num > len(all_tasks):
        await update.message.reply_text(f"Задача #{num} не найдена. Список: /tasks")
        return

    task = all_tasks[num - 1]
    calendar_client.complete_task(task["id"], task["cal_id"], user_id=user_id)
    await update.message.reply_text(f"✅ Выполнено: {task['title']}")


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    await update.message.reply_text("⏳ Генерирую дайджест...")
    await _send_morning_digest(context.application, target_date=None, target_user_id=user_id)


async def cmd_progress_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return ConversationHandler.END
    await update.message.reply_text(
        "Расскажи о прогрессе за сегодня — что сделано, что перенесено, какие мысли."
    )
    return WAITING_PROGRESS


async def cmd_progress_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return ConversationHandler.END
    text = update.message.text
    calendar_client.save_progress(text, user_id=user_id)
    await update.message.reply_text("✍️ Прогресс сохранён. Учту завтра утром!")
    return ConversationHandler.END


async def _conversation_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inline ConversationHandler fallback — used to exit /progress state, not user-facing."""
    await update.message.reply_text("Отмена.")
    return ConversationHandler.END


async def handle_natural(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Settings-menu credential capture runs first — it works for any
    # access-bearing user, not just the legacy owner. If the user is in the
    # middle of a /settings flow (e.g. typing a phone number for WhatsApp
    # pairing or Pushover keys), this consumes the message and returns.
    try:
        import settings_menu
        consumed = await settings_menu.capture_credential_message(update, context)
        if consumed:
            return
    except Exception as e:
        logger.warning("settings_menu capture failed: %s", e)

    # Any DB-known user with access/promo gets through (Daria + paying users).
    # If they're not in DB or their subscription lapsed, fall back to the
    # legacy owner check so Daria's pre-migration flow keeps working even
    # if her DB row is somehow missing.
    try:
        import access
        user = access.get_user_from_update(update)
        if user is not None:
            if not user.has_access():
                await update.effective_message.reply_text(
                    "🔒 Подписка неактивна. Используй /promo КОД или /subscribe."
                )
                return
            # Authorized DB user → process
            await _process_natural(update.message.text, update, context)
            return
    except Exception as e:
        logger.warning("access lookup failed: %s", e)

    # Legacy fallback for Daria pre-migration
    if not _is_owner(update):
        return
    await _process_natural(update.message.text, update, context)


async def _process_natural(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = _resolve_user_id(update)

    # Handle "waiting for email address" state
    if context.user_data.get("waiting_for") == "email":
        context.user_data["pending_email"]["to_email"] = text.strip()
        context.user_data.pop("waiting_for")
        await _show_email_preview(update, context)
        return

    short = calendar_client.get_active_tasks("short", user_id=user_id)
    long_ = calendar_client.get_active_tasks("long", user_id=user_id)
    all_tasks = short + long_

    history = conversation.get_history(user_id=user_id)
    summaries = conversation.get_recent_summaries(user_id=user_id)

    # New agent path (feature-flagged via USE_AGENT env var)
    if agent.is_enabled():
        try:
            result = agent.run_agent(
                text,
                history=history,
                summaries=summaries,
                active_tasks=all_tasks,
                context=context,
                user_id=user_id,
            )
            await _handle_agent_result(result, update, context, user_id=user_id)
            if result.get("text"):
                conversation.add(text, result["text"], user_id=user_id)
            return
        except Exception as e:
            logger.exception("agent failed, falling back to legacy parser: %s", e)

    # Legacy parser path
    parsed = parser.parse_message(text, all_tasks, history, summaries)
    actions = parsed.get("actions")
    if not isinstance(actions, list) or not actions:
        actions = [parsed]

    response_parts = []
    for action in actions:
        resp = await _execute_action(action, all_tasks, text, update, context, user_id=user_id)
        if resp:
            response_parts.append(resp)

    if response_parts:
        conversation.add(text, "\n\n".join(response_parts), user_id=user_id)


async def _handle_agent_result(
    result: dict,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int | None = None,
) -> None:
    """Render agent.run_agent output: send text, then execute any side-effect actions."""
    text_out = result.get("text", "").strip()
    actions = result.get("actions", []) or []
    logger.info(
        "_handle_agent_result: user_id=%s effective_user=%s effective_chat=%s actions=%s",
        user_id,
        update.effective_user.id if update.effective_user else None,
        update.effective_chat.id if update.effective_chat else None,
        [a.get("action") for a in actions],
    )

    needs_confirmation = next((a for a in actions if a.get("action") == "needs_confirmation"), None)

    # Send the textual summary first (split if long)
    if text_out:
        if needs_confirmation and needs_confirmation.get("kind") == "conflict_task":
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Добавить всё равно", callback_data="conflict_confirm"),
                InlineKeyboardButton("❌ Отменить", callback_data="conflict_cancel"),
            ]])
            await update.message.reply_text(text_out, reply_markup=keyboard)
        elif needs_confirmation and needs_confirmation.get("kind") == "email_preview":
            await _show_email_preview(update, context)
        else:
            await _reply_split(update, text_out)

    # Dispatch background actions (digests)
    for act in actions:
        kind = act.get("action")
        if kind == "send_digest":
            target_date = None
            if act.get("target_date"):
                try:
                    target_date = datetime.date.fromisoformat(act["target_date"])
                except Exception:
                    pass
            await _send_morning_digest(context.application, target_date, target_user_id=user_id)
        elif kind == "send_weekly_digest":
            tz = _user_tz(user_id)
            today = datetime.datetime.now(tz).date()
            monday = today - datetime.timedelta(days=today.weekday())
            sunday = monday + datetime.timedelta(days=6)
            try:
                week_events = calendar_client.get_week_events(monday, sunday, user_id=user_id)
                short_ = calendar_client.get_active_tasks("short", user_id=user_id)
                long__ = calendar_client.get_active_tasks("long", user_id=user_id)
                weekly_text = digest_module.generate_weekly_digest(week_events, short_, long__)
            except Exception as e:
                logger.error("weekly digest action failed: %s", e)
                weekly_text = "Не удалось создать недельный обзор."
            await _reply_split(update, weekly_text)


async def _execute_action(
    action: dict,
    all_tasks: list,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int | None = None,
) -> str | None:
    intent = action.get("intent")

    if intent == "add_task":
        task_type = action.get("task_type") or "short"
        title = action.get("title") or original_text
        time_str = action.get("time")
        date_str = action.get("date")
        end_date_str = action.get("end_date")
        duration_minutes = int(action.get("duration_minutes") or 60)

        tz = _user_tz(user_id)
        start_dt = None
        due_date = None
        end_date = None

        if time_str:
            base = datetime.date.fromisoformat(date_str) if date_str else datetime.datetime.now(tz).date()
            h, m = map(int, time_str.split(":"))
            start_dt = tz.localize(datetime.datetime(base.year, base.month, base.day, h, m))
        else:
            if date_str:
                due_date = datetime.date.fromisoformat(date_str)
            if end_date_str:
                end_date = datetime.date.fromisoformat(end_date_str)

        attendee_names = action.get("attendees") or []
        attendee_emails, not_found = [], []
        for name in attendee_names:
            email = contacts_client.find_contact_email(name, user_id=user_id)
            (attendee_emails if email else not_found).append(email or name)

        # Conflict detection for timed events
        if start_dt:
            end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
            try:
                conflicts = calendar_client.get_conflicts(start_dt, end_dt, user_id=user_id)
            except Exception:
                conflicts = []
            if conflicts:
                context.user_data["pending_task"] = {
                    "title": title, "task_type": task_type,
                    "due_date": due_date, "end_date": end_date,
                    "start_dt": start_dt, "duration_minutes": duration_minutes,
                    "attendee_emails": attendee_emails, "not_found": not_found,
                }
                conflict_list = ", ".join(conflicts[:3])
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Добавить всё равно", callback_data="conflict_confirm"),
                    InlineKeyboardButton("❌ Отменить", callback_data="conflict_cancel"),
                ]])
                response_text = (
                    f"⚠️ В это время уже есть: {conflict_list}\n"
                    f"Добавить «{title}» на {start_dt.strftime('%d.%m.%Y в %H:%M')} всё равно?"
                )
                await update.message.reply_text(response_text, reply_markup=keyboard)
                return response_text

        try:
            calendar_client.add_task(title, task_type, due_date=due_date, end_date=end_date,
                                     start_dt=start_dt, duration_minutes=duration_minutes,
                                     attendees=attendee_emails or None,
                                     user_id=user_id)
            emoji = "⚡" if task_type == "short" else "🎯"
            if start_dt:
                dur_str = f"{duration_minutes} мин." if duration_minutes != 60 else "1 час"
                response_text = f"{emoji} Добавил: {title}\n📅 {start_dt.strftime('%d.%m.%Y в %H:%M')} ({dur_str})"
            elif due_date and end_date:
                response_text = f"{emoji} Добавил: {title}\n📅 {due_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}"
            elif due_date:
                response_text = f"{emoji} Добавил: {title}\n📅 до {due_date.strftime('%d.%m.%Y')}"
            else:
                response_text = f"{emoji} Добавил: {title}"
            if attendee_emails:
                response_text += f"\n👥 Приглашены: {', '.join(attendee_emails)}"
            if not_found:
                response_text += f"\n⚠️ Не нашёл в контактах: {', '.join(not_found)}"
        except Exception as e:
            logger.error("natural add_task failed: %s", e)
            response_text = "Не смог добавить задачу, попробуй ещё раз."
        await update.message.reply_text(response_text)
        return response_text

    elif intent in ("complete_task", "delete_task"):
        raw_num = action.get("task_number")
        if isinstance(raw_num, list):
            raw_num = raw_num[0] if raw_num else None
        try:
            task_number = int(raw_num) if raw_num is not None else None
        except (ValueError, TypeError):
            task_number = None

        if task_number and 1 <= task_number <= len(all_tasks):
            task = all_tasks[task_number - 1]
            if intent == "delete_task":
                calendar_client.delete_task(task["id"], task["cal_id"], user_id=user_id)
                response_text = f"🗑 Удалено: {task['title']}"
            else:
                calendar_client.complete_task(task["id"], task["cal_id"], user_id=user_id)
                response_text = f"✅ Выполнено: {task['title']}"
        else:
            response_text = "Не нашёл такую задачу. Напиши /tasks чтобы увидеть список с номерами."
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "send_to_alice":
        message = action.get("reply") or action.get("title") or ""
        if message:
            import tools as _tools
            with open(_tools._alice_pending_path(user_id), "w", encoding="utf-8") as f:
                f.write(message)
            response_text = f"📢 Сообщение поставлено в очередь. Скажи «Алиса, открой [название навыка]» — она прочитает его вслух."
        else:
            response_text = "Не понял что передать Алисе, уточни текст."
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "get_weather":
        city = action.get("title") or None
        date_str = action.get("date")
        target_date = datetime.date.fromisoformat(date_str) if date_str else None
        result = weather_client.get_weather(target_date, city)
        response_text = f"🌤 {result}" if result else "Не удалось получить погоду."
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "show_tasks":
        await cmd_tasks(update, context)
        return "[список задач]"

    elif intent == "get_digest":
        date_str = action.get("date")
        target_date = datetime.date.fromisoformat(date_str) if date_str else None
        label = date_str or "сегодня"
        await update.message.reply_text(f"⏳ Генерирую дайджест на {label}...")
        await _send_morning_digest(context.application, target_date, target_user_id=user_id)
        return "[дайджест отправлен]"

    elif intent == "save_progress":
        calendar_client.save_progress(original_text, user_id=user_id)
        response_text = "✍️ Прогресс сохранён. Учту завтра утром!"
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "reschedule_task":
        raw_num = action.get("task_number")
        if isinstance(raw_num, list):
            raw_num = raw_num[0] if raw_num else None
        try:
            task_number = int(raw_num) if raw_num is not None else None
        except (ValueError, TypeError):
            task_number = None

        if not task_number or not (1 <= task_number <= len(all_tasks)):
            response_text = "Не нашёл задачу. Напиши /tasks чтобы увидеть список с номерами."
            await update.message.reply_text(response_text)
            return response_text

        task = all_tasks[task_number - 1]
        date_str = action.get("date")
        time_str = action.get("time")
        duration_minutes = int(action.get("duration_minutes") or 60)

        if not time_str:
            response_text = "Укажи новое время для переноса (например, 'перенеси задачу 2 на завтра в 15:00')."
            await update.message.reply_text(response_text)
            return response_text

        tz = _user_tz(user_id)
        base = datetime.date.fromisoformat(date_str) if date_str else datetime.datetime.now(tz).date()
        h, m = map(int, time_str.split(":"))
        new_start = tz.localize(datetime.datetime(base.year, base.month, base.day, h, m))
        new_end = new_start + datetime.timedelta(minutes=duration_minutes)

        try:
            calendar_client.reschedule_task(task["id"], task["cal_id"], new_start, new_end, user_id=user_id)
            response_text = f"📅 Перенесено: «{task['title']}» → {new_start.strftime('%d.%m.%Y в %H:%M')}"
        except Exception as e:
            logger.error("reschedule_task failed: %s", e)
            response_text = "Не удалось перенести задачу, попробуй ещё раз."
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "find_free_time":
        date_str = action.get("date")
        duration_minutes = int(action.get("duration_minutes") or 60)
        tz = _user_tz(user_id)
        target_date = datetime.date.fromisoformat(date_str) if date_str else datetime.datetime.now(tz).date()
        try:
            slots = calendar_client.find_free_slots(target_date, duration_minutes, user_id=user_id)
            if slots:
                slot_lines = "\n".join(f"• {s['start']}–{s['end']}" for s in slots)
                response_text = f"🕐 Свободное время {target_date.strftime('%d.%m')}:\n{slot_lines}"
            else:
                response_text = f"Свободных окон от {duration_minutes} мин. на {target_date.strftime('%d.%m')} не нашлось."
        except Exception as e:
            logger.error("find_free_time failed: %s", e)
            response_text = "Не удалось проверить расписание."
        await update.message.reply_text(response_text)
        return response_text

    elif intent == "get_weekly_digest":
        tz = _user_tz(user_id)
        today = datetime.datetime.now(tz).date()
        # Start from Monday of current week
        monday = today - datetime.timedelta(days=today.weekday())
        sunday = monday + datetime.timedelta(days=6)
        await update.message.reply_text("⏳ Составляю недельный обзор...")
        try:
            week_events = calendar_client.get_week_events(monday, sunday, user_id=user_id)
            short = calendar_client.get_active_tasks("short", user_id=user_id)
            long_ = calendar_client.get_active_tasks("long", user_id=user_id)
            text = digest_module.generate_weekly_digest(week_events, short, long_)
        except Exception as e:
            logger.error("weekly digest failed: %s", e)
            text = "Не удалось создать недельный обзор."
        await _reply_split(update, text)
        return "[недельный обзор отправлен]"

    elif intent == "send_email":
        context.user_data["pending_email"] = {
            "to_email": action.get("to_email"),
            "to_name": action.get("to_name"),
            "subject": action.get("email_subject") or "Без темы",
            "body": action.get("email_body") or "",
        }
        if not context.user_data["pending_email"]["to_email"]:
            name = action.get("to_name") or "получателя"
            context.user_data["waiting_for"] = "email"
            response_text = f"На какой email отправить письмо для {name}?"
            await update.message.reply_text(response_text)
        else:
            await _show_email_preview(update, context)
            response_text = "[показан предпросмотр письма]"
        return response_text

    else:  # chat
        response_text = action.get("reply") or "Понял! Чем ещё могу помочь?"
        await update.message.reply_text(response_text)
        return response_text


async def _show_email_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.user_data.get("pending_email", {})
    to = data.get("to_name") or data.get("to_email", "")
    preview = (
        f"✉️ Отправить письмо?\n\n"
        f"Кому: {to} <{data.get('to_email', '')}>\n"
        f"Тема: {data.get('subject', '')}\n\n"
        f"{data.get('body', '')}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Отправить", callback_data="email_confirm"),
        InlineKeyboardButton("❌ Отменить", callback_data="email_cancel"),
    ]])
    await update.message.reply_text(preview, reply_markup=keyboard)


async def callback_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    query = update.callback_query
    await query.answer()
    data = context.user_data.pop("pending_email", {})

    if query.data == "email_confirm":
        if not data.get("to_email"):
            await query.edit_message_text("Адрес не указан, письмо не отправлено.")
            return
        try:
            gmail_client.send_email(data["to_email"], data["subject"], data["body"], user_id=user_id)
            await query.edit_message_text(f"✉️ Письмо отправлено на {data['to_email']}")
        except Exception as e:
            logger.error("send_email failed: %s", e)
            await query.edit_message_text(f"Ошибка отправки: {e}")
    else:
        await query.edit_message_text("Отменено.")


async def callback_conflict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    query = update.callback_query
    await query.answer()
    data = context.user_data.pop("pending_task", {})

    if query.data == "conflict_confirm" and data:
        try:
            calendar_client.add_task(
                data["title"], data["task_type"],
                due_date=data.get("due_date"), end_date=data.get("end_date"),
                start_dt=data.get("start_dt"), duration_minutes=data.get("duration_minutes", 60),
                attendees=data.get("attendee_emails") or None,
                user_id=user_id,
            )
            start_dt = data.get("start_dt")
            time_label = start_dt.strftime("%d.%m.%Y в %H:%M") if start_dt else ""
            msg = f"✅ Добавлено: «{data['title']}» на {time_label}"
            if data.get("not_found"):
                msg += f"\n⚠️ Не нашёл в контактах: {', '.join(data['not_found'])}"
        except Exception as e:
            logger.error("conflict_confirm add_task failed: %s", e)
            msg = f"Ошибка при добавлении: {e}"
        await query.edit_message_text(msg)
    else:
        await query.edit_message_text("Отменено.")


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split text into Telegram-safe chunks, breaking on paragraph boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
                current = ""
            if len(paragraph) <= limit:
                current = paragraph
            else:
                for line in paragraph.split("\n"):
                    candidate2 = (current + "\n" + line).strip() if current else line
                    if len(candidate2) <= limit:
                        current = candidate2
                    else:
                        if current:
                            chunks.append(current)
                        current = line[:limit]
    if current:
        chunks.append(current)
    return chunks


async def _reply_split(update: Update, text: str) -> None:
    """Send a potentially long message as multiple reply_text chunks."""
    for chunk in _split_message(text):
        await update.message.reply_text(chunk)


def _collect_shared_goal_attribution(user_id: int) -> list[dict]:
    """For each shared goal in groups the user belongs to, gather member
    display names + who last logged progress.

    Returns a list of dicts ready for digest.generate_morning_digest's
    shared_goals kwarg. Empty list when the user isn't in any group with
    active shared goals.
    """
    import db
    out: list[dict] = []
    try:
        with db.session_scope() as s:
            group_ids = [
                r.group_id for r in s.query(db.GroupMember)
                .filter_by(user_id=user_id)
                .filter(db.GroupMember.accepted_at.isnot(None))
                .all()
            ]
            if not group_ids:
                return []
            goals = (
                s.query(db.Goal)
                .filter(
                    db.Goal.status == "active",
                    db.Goal.group_id.in_(group_ids),
                )
                .all()
            )
            for g in goals:
                # Member display names in the goal's group
                member_rows = (
                    s.query(db.User)
                    .join(db.GroupMember, db.GroupMember.user_id == db.User.id)
                    .filter(
                        db.GroupMember.group_id == g.group_id,
                        db.GroupMember.accepted_at.isnot(None),
                    )
                    .all()
                )
                members = [u.display_name or (u.telegram_username or "?") for u in member_rows]
                # Last progress entry → who logged it
                last = (
                    s.query(db.GoalProgress)
                    .filter_by(goal_id=g.id)
                    .order_by(db.GoalProgress.ts.desc())
                    .first()
                )
                last_by = None
                if last is not None:
                    u = s.get(db.User, last.user_id)
                    if u is not None:
                        last_by = u.display_name or u.telegram_username or "?"
                out.append({
                    "title": g.title,
                    "members": members,
                    "last_progress_by": last_by,
                })
    except Exception as e:
        logger.warning("collect shared goal attribution failed: %s", e)
        return []
    return out


async def _send_morning_digest(
    app: Application,
    target_date: datetime.date | None = None,
    *,
    target_user_id: int | None = None,
) -> None:
    """Generate and send the morning digest.

    When called from the multi-user heartbeat, target_user_id identifies
    whose digest to build and where to send it. When called from /digest
    or the agent, target_user_id=None falls back to legacy Daria-only
    behavior (env-based TELEGRAM_CHAT_ID, root-level files).
    """
    # Resolve target user's chat_id, display_name, timezone, city in one DB
    # lookup. These four pieces drive both the routing of the message AND
    # the personalization of the prompt — no more Daria-by-default leaks.
    chat_id = config.TELEGRAM_CHAT_ID
    user_name: Optional[str] = None
    user_timezone = config.TIMEZONE
    user_city: Optional[str] = None
    user_language = "ru"
    if target_user_id is not None:
        try:
            import db
            with db.session_scope() as s:
                u = s.get(db.User, target_user_id)
                if u is not None:
                    chat_id = int(u.telegram_chat_id)
                    user_name = u.display_name or None
                    user_timezone = u.timezone or config.TIMEZONE
                    user_city = getattr(u, "city", None)
                    user_language = getattr(u, "language", None) or "ru"
        except Exception as e:
            logger.warning("morning digest: user lookup for %s failed: %s", target_user_id, e)
    logger.info(
        "morning digest: target_user_id=%s chat_id=%s name=%s tz=%s city=%s lang=%s",
        target_user_id, chat_id, user_name, user_timezone, user_city, user_language,
    )

    def _safe(fn, default, *, label):
        """Run a data-source fetch; if it explodes (revoked token, 404, RSS
        timeout, …) log it and return `default` so the digest keeps going."""
        try:
            return fn()
        except Exception as exc:
            logger.warning("Morning digest: %s failed: %s", label, exc)
            return default

    last_error = None
    for attempt in range(3):
        try:
            tz = pytz.timezone(user_timezone)
            today = datetime.datetime.now(tz).date()
            is_today = target_date is None or target_date == today

            events = _safe(
                lambda: calendar_client.get_todays_calendar_events(target_date, user_id=target_user_id),
                [], label="events")
            short = _safe(
                lambda: calendar_client.get_active_tasks("short", target_date, user_id=target_user_id),
                [], label="short tasks")
            long_ = _safe(
                lambda: calendar_client.get_active_tasks("long", target_date, user_id=target_user_id),
                [], label="long tasks")
            yesterday = _safe(
                lambda: calendar_client.get_progress_before_date(target_date, user_id=target_user_id),
                "", label="yesterday progress")
            emails = _safe(
                lambda: gmail_client.get_unread_emails(user_id=target_user_id) if target_date is None else [],
                [], label="emails")
            weather = _safe(
                lambda: weather_client.get_weather(target_date, city=user_city),
                "", label="weather")
            news = _safe(
                lambda: news_client.get_news_headlines(max_per_source=5, user_id=target_user_id) if is_today else [],
                [], label="news")
            birthdays = _safe(
                lambda: birthday_client.get_todays_birthdays(user_id=target_user_id) if is_today else [],
                [], label="birthdays")
            recent_msgs = _safe(
                lambda: conversation.get_history(user_id=target_user_id), [], label="history")
            summaries = _safe(
                lambda: conversation.get_recent_summaries(user_id=target_user_id),
                [], label="summaries")
            wa_unread = _safe(
                lambda: whatsapp_client.unread_chats(user_id=target_user_id) if is_today else [],
                [], label="whatsapp unread")
            wa_summary = ""
            if wa_unread:
                wa_summary = _safe(
                    lambda: whatsapp_summary.summarize_unread_chats(wa_unread, user_id=target_user_id),
                    "", label="whatsapp summary")

            # Phase H: attribution for shared (group) goals
            shared_goals = _safe(
                lambda: _collect_shared_goal_attribution(target_user_id) if target_user_id is not None else [],
                [], label="shared goal attribution",
            )

            text = digest_module.generate_morning_digest(
                events, short, long_, yesterday, emails, target_date, weather,
                news=news or None, birthdays=birthdays or None,
                recent_messages=recent_msgs or None,
                summaries=summaries or None,
                whatsapp_summary=wa_summary or None,
                user_name=user_name,
                user_timezone=user_timezone,
                user_language=user_language,
                shared_goals=shared_goals or None,
            )

            # Alice cache is still single-user; only write when sending to Daria.
            if target_user_id is None or chat_id == config.TELEGRAM_CHAT_ID:
                with open(config.ALICE_DIGEST_FILE, "w", encoding="utf-8") as f:
                    f.write(text)

            for chunk in _split_message(text):
                await app.bot.send_message(chat_id=chat_id, text=chunk)
            # Per-user pushover (silently no-op if user hasn't configured it).
            pushover_client.send_push(text[:1024], title="☀️ Доброе утро!", user_id=target_user_id)
            if is_today:
                conversation.add("(утренний дайджест)", text, user_id=target_user_id)
            return
        except Exception as e:
            last_error = e
            logger.warning("Morning digest attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(10)

    logger.error("Morning digest failed after 3 attempts: %s", last_error)
    try:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Ошибка генерации дайджеста: {last_error}",
        )
    except Exception:
        pass


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return

    if not config.OPENAI_API_KEY:
        await update.message.reply_text("Голосовые сообщения не настроены (нет OPENAI_API_KEY).")
        return

    voice = update.message.voice
    tg_file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
        await tg_file.download_to_drive(tmp.name)
        client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
        with open(tmp.name, "rb") as audio:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio,
                language="ru",
            )

    text = transcript.text.strip()
    if not text:
        await update.message.reply_text("Не удалось распознать голосовое сообщение.")
        return

    await _process_natural(text, update, context)


async def _send_evening_checkin(app: Application, *, target_user_id: int | None = None) -> None:
    chat_id = config.TELEGRAM_CHAT_ID
    if target_user_id is not None:
        try:
            import db
            with db.session_scope() as s:
                u = s.get(db.User, target_user_id)
                if u is not None:
                    chat_id = int(u.telegram_chat_id)
        except Exception as e:
            logger.warning("evening check-in: user lookup for %s failed: %s", target_user_id, e)
    try:
        text = digest_module.generate_evening_checkin()
        await app.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error("Evening check-in failed: %s", e)


# ─── Groups (Phase H) ─────────────────────────────────────────────────────────


def _resolve_lang(user_id: int | None) -> str:
    """Look up the user's UI language for i18n.t() calls."""
    if user_id is None:
        return "ru"
    try:
        from i18n import user_language
        return user_language(user_id)
    except Exception:
        return "ru"


def _parse_int_arg(args: list[str], idx: int) -> int | None:
    if idx >= len(args):
        return None
    try:
        return int(args[idx])
    except (ValueError, TypeError):
        return None


def _group_error_reply(e: Exception, lang: str) -> str:
    """Translate the exception type into a localized error string."""
    from i18n import t
    if isinstance(e, PermissionError):
        return t("groups.error.not_admin", lang)
    if isinstance(e, LookupError):
        return t("groups.error.not_found", lang)
    if isinstance(e, ValueError):
        return t("groups.error.value", lang, reason=str(e))
    return t("groups.error.value", lang, reason=str(e))


async def cmd_group_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    if not context.args:
        await update.message.reply_text(t("groups.usage.create", lang))
        return
    name = " ".join(context.args).strip()
    try:
        import groups
        res = groups.create_group(user_id, name)
        await update.message.reply_text(
            t("groups.created", lang, name=res["name"], group_id=res["group_id"])
        )
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    try:
        import groups
        rows = groups.list_my_groups(user_id)
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))
        return
    if not rows:
        await update.message.reply_text(t("groups.list_empty", lang))
        return
    lines = [t("groups.list_header", lang)]
    for r in rows:
        if r["pending"]:
            lines.append(t("groups.list_item_pending", lang, group_id=r["group_id"], name=r["name"]))
        else:
            lines.append(t(
                "groups.list_item", lang,
                group_id=r["group_id"], name=r["name"],
                role=r["role"], member_count=r["member_count"],
            ))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_group_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    if gid is None:
        await update.message.reply_text(t("groups.usage.members", lang))
        return
    try:
        import groups, db
        members = groups.list_members(user_id, gid)
        with db.session_scope() as s:
            g = s.get(db.Group, gid)
            gname = g.name if g else "?"
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))
        return
    lines = [t("groups.members_header", lang, name=gname)]
    for m in members:
        display = m.get("display_name") or "?"
        username = ("@" + m["username"]) if m.get("username") else "—"
        if m["pending"]:
            lines.append(t("groups.member_pending", lang, display=display, username=username))
        elif m["role"] == "admin":
            lines.append(t("groups.member_admin", lang, display=display, username=username))
        else:
            lines.append(t("groups.member_normal", lang, display=display, username=username))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_group_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    if gid is None or len(context.args) < 2:
        await update.message.reply_text(t("groups.usage.invite", lang))
        return
    username = context.args[1].lstrip("@").strip()
    try:
        import groups, db
        res = groups.invite_user(user_id, gid, username)
        with db.session_scope() as s:
            g = s.get(db.Group, gid)
            gname = g.name if g else "?"
            inviter = s.get(db.User, user_id)
            inviter_name = inviter.display_name if inviter else "?"
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))
        return

    if res.get("already_member"):
        await update.message.reply_text(t("groups.invite_already_member", lang))
        return
    if res.get("already_invited"):
        await update.message.reply_text(t("groups.invite_already_invited", lang))
        return

    if "invite_token" in res:
        # User not in DB yet → return a deep-link
        try:
            bot_username = context.bot.username
        except Exception:
            bot_username = "your_bot"
        link = f"https://t.me/{bot_username}?start=invite_{res['invite_token']}"
        await update.message.reply_text(
            t("groups.invite_link_ready", lang, username=username, link=link)
        )
        return

    # Existing user → DM them an inline accept/decline keyboard, ack admin
    target_user_id = res["user_id"]
    target_lang = _resolve_lang(target_user_id)
    try:
        import db
        with db.session_scope() as s:
            target = s.get(db.User, target_user_id)
            target_chat = int(target.telegram_chat_id) if target else None
    except Exception:
        target_chat = None
    if target_chat is not None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("groups.accept", target_lang), callback_data=f"group:accept:{gid}"),
            InlineKeyboardButton(t("groups.decline", target_lang), callback_data=f"group:decline:{gid}"),
        ]])
        try:
            await context.bot.send_message(
                chat_id=target_chat,
                text=t("groups.invite_notice", target_lang, inviter=inviter_name, group_name=gname),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.warning("group invite DM failed: %s", e)
    await update.message.reply_text(
        t("groups.invite_sent_to_user", lang, username=username)
    )


async def cmd_group_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    target_id = _parse_int_arg(context.args, 1)
    if gid is None or target_id is None:
        await update.message.reply_text(t("groups.usage.remove", lang))
        return
    try:
        import groups
        groups.remove_member(user_id, gid, target_id)
        await update.message.reply_text(t("groups.removed", lang))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    if gid is None:
        await update.message.reply_text(t("groups.usage.leave", lang))
        return
    try:
        import groups
        groups.leave_group(user_id, gid)
        await update.message.reply_text(t("groups.left", lang))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    target_id = _parse_int_arg(context.args, 1)
    if gid is None or target_id is None:
        await update.message.reply_text(t("groups.usage.promote", lang))
        return
    try:
        import groups
        groups.promote(user_id, gid, target_id)
        await update.message.reply_text(t("groups.promoted", lang))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_demote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    target_id = _parse_int_arg(context.args, 1)
    if gid is None or target_id is None:
        await update.message.reply_text(t("groups.usage.demote", lang))
        return
    try:
        import groups
        groups.demote(user_id, gid, target_id)
        await update.message.reply_text(t("groups.demoted", lang))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    if gid is None or len(context.args) < 2:
        await update.message.reply_text(t("groups.usage.rename", lang))
        return
    new_name = " ".join(context.args[1:]).strip()
    try:
        import groups
        groups.rename(user_id, gid, new_name)
        await update.message.reply_text(t("groups.renamed", lang, name=new_name))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def cmd_group_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    lang = _resolve_lang(user_id)
    gid = _parse_int_arg(context.args, 0)
    if gid is None:
        await update.message.reply_text(t("groups.usage.delete", lang))
        return
    try:
        import groups
        groups.delete_group(user_id, gid)
        await update.message.reply_text(t("groups.deleted", lang))
    except Exception as e:
        await update.message.reply_text(_group_error_reply(e, lang))


async def callback_group_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle accept/decline taps on the invite DM."""
    from i18n import t
    user, user_id = await _authorize(update)
    if user is None and user_id is None:
        return
    query = update.callback_query
    await query.answer()
    lang = _resolve_lang(user_id)
    parts = (query.data or "").split(":")
    if len(parts) != 3:
        return
    action = parts[1]
    try:
        gid = int(parts[2])
    except ValueError:
        return
    try:
        import groups, db
        if action == "accept":
            groups.accept_invite(user_id, gid)
            with db.session_scope() as s:
                g = s.get(db.Group, gid)
                gname = g.name if g else "?"
            await query.edit_message_text(t("groups.accepted", lang, name=gname))
        elif action == "decline":
            groups.decline_invite(user_id, gid)
            await query.edit_message_text(t("groups.declined", lang))
    except Exception as e:
        try:
            await query.edit_message_text(_group_error_reply(e, lang))
        except Exception:
            pass


def register_handlers(app: Application) -> None:
    import onboarding

    # `progress` conversation cancel fallback stays internal (renamed).
    progress_conv = ConversationHandler(
        entry_points=[CommandHandler("progress", cmd_progress_start)],
        states={WAITING_PROGRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_progress_receive)]},
        # `/cancel` inside the progress conversation cancels the progress flow,
        # not the subscription. Outside of progress, `/cancel` hits the
        # subscription cancel handler registered below.
        fallbacks=[CommandHandler("cancel", _conversation_cancel)],
    )

    # New onboarding / billing commands — keep these BEFORE the catch-all
    # text handler so they don't get eaten.
    # The wizard ConversationHandler owns /start, /promo, /profile so they
    # can drive the 12-step state machine. Standalone /subscribe and /cancel
    # are unrelated commands.
    app.add_handler(onboarding.build_wizard_conversation_handler())
    app.add_handler(CommandHandler("subscribe", onboarding.cmd_subscribe))
    app.add_handler(CommandHandler("cancel", onboarding.cmd_cancel))
    app.add_handler(CallbackQueryHandler(onboarding.cb_onboard, pattern="^onboard:"))

    # Settings (per-user integrations)
    import settings_menu
    app.add_handler(CommandHandler("settings", settings_menu.cmd_settings))
    app.add_handler(CallbackQueryHandler(settings_menu.cb_settings, pattern="^settings:"))

    # Existing handlers (still gated by _is_owner for legacy Daria-only access)
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(progress_conv)
    app.add_handler(CallbackQueryHandler(callback_email, pattern="^email_"))
    app.add_handler(CallbackQueryHandler(callback_conflict, pattern="^conflict_"))

    # Groups (Phase H)
    app.add_handler(CommandHandler("group_create", cmd_group_create))
    app.add_handler(CommandHandler("group_list", cmd_group_list))
    app.add_handler(CommandHandler("group_members", cmd_group_members))
    app.add_handler(CommandHandler("group_invite", cmd_group_invite))
    app.add_handler(CommandHandler("group_remove", cmd_group_remove))
    app.add_handler(CommandHandler("group_leave", cmd_group_leave))
    app.add_handler(CommandHandler("group_promote", cmd_group_promote))
    app.add_handler(CommandHandler("group_demote", cmd_group_demote))
    app.add_handler(CommandHandler("group_rename", cmd_group_rename))
    app.add_handler(CommandHandler("group_delete", cmd_group_delete))
    app.add_handler(CallbackQueryHandler(callback_group_invite, pattern=r"^group:(accept|decline):"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # natural language — lowest priority, catches everything else
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural))
