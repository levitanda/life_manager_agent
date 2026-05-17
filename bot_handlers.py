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

import calendar_client
import config
import contacts_client
import conversation
import weather_client
import digest as digest_module
import gmail_client
import parser
import pushover_client

logger = logging.getLogger(__name__)

WAITING_PROGRESS = 1


def _is_owner(update: Update) -> bool:
    return update.effective_user.id == config.TELEGRAM_CHAT_ID


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    summaries = conversation.get_recent_summaries(10)
    if not summaries:
        await update.message.reply_text("Пока нет сохранённых резюме сессий.")
        return
    lines = ["🧠 *Долгосрочная память (последние сессии)*\n"]
    for s in reversed(summaries):
        lines.append(f"*{s['date']}*\n{s['summary']}\n")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    await update.message.reply_text(
        "Привет! Я твой личный агент. Просто пиши мне что нужно сделать — я пойму.\n\n"
        "Команды (не обязательны):\n"
        "/tasks — список задач\n"
        "/digest — дайджест прямо сейчас\n"
        "/memory — моя долгосрочная память о тебе\n"
        "/progress — записать прогресс за день\n"
        "/help — эта справка"
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
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

        tz = pytz.timezone(config.TIMEZONE)
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
    if not _is_owner(update):
        return

    short = calendar_client.get_active_tasks("short")
    long_ = calendar_client.get_active_tasks("long")

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

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return

    if not context.args:
        await update.message.reply_text("Использование: /done <номер задачи>")
        return

    try:
        num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Укажи номер задачи числом.")
        return

    short = calendar_client.get_active_tasks("short")
    long_ = calendar_client.get_active_tasks("long")
    all_tasks = short + long_

    if num < 1 or num > len(all_tasks):
        await update.message.reply_text(f"Задача #{num} не найдена. Список: /tasks")
        return

    task = all_tasks[num - 1]
    calendar_client.complete_task(task["id"], task["cal_id"])
    await update.message.reply_text(f"✅ Выполнено: {task['title']}")


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    await update.message.reply_text("⏳ Генерирую дайджест...")
    await _send_morning_digest(context.application, target_date=None)


async def cmd_progress_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_owner(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "Расскажи о прогрессе за сегодня — что сделано, что перенесено, какие мысли."
    )
    return WAITING_PROGRESS


async def cmd_progress_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_owner(update):
        return ConversationHandler.END
    text = update.message.text
    calendar_client.save_progress(text)
    await update.message.reply_text("✍️ Прогресс сохранён. Учту завтра утром!")
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отмена.")
    return ConversationHandler.END


async def handle_natural(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
        return
    await _process_natural(update.message.text, update, context)


async def _process_natural(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Handle "waiting for email address" state
    if context.user_data.get("waiting_for") == "email":
        context.user_data["pending_email"]["to_email"] = text.strip()
        context.user_data.pop("waiting_for")
        await _show_email_preview(update, context)
        return

    short = calendar_client.get_active_tasks("short")
    long_ = calendar_client.get_active_tasks("long")
    all_tasks = short + long_

    history = conversation.get_history()
    summaries = conversation.get_recent_summaries()
    parsed = parser.parse_message(text, all_tasks, history, summaries)

    # Support both {"actions": [...]} (new) and {"intent": ...} (fallback)
    actions = parsed.get("actions")
    if not isinstance(actions, list) or not actions:
        actions = [parsed]

    response_parts = []
    for action in actions:
        resp = await _execute_action(action, all_tasks, text, update, context)
        if resp:
            response_parts.append(resp)

    if response_parts:
        conversation.add(text, "\n\n".join(response_parts))


async def _execute_action(
    action: dict,
    all_tasks: list,
    original_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> str | None:
    intent = action.get("intent")

    if intent == "add_task":
        task_type = action.get("task_type") or "short"
        title = action.get("title") or original_text
        time_str = action.get("time")
        date_str = action.get("date")
        end_date_str = action.get("end_date")
        duration_minutes = int(action.get("duration_minutes") or 60)

        tz = pytz.timezone(config.TIMEZONE)
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
            email = contacts_client.find_contact_email(name)
            (attendee_emails if email else not_found).append(email or name)

        try:
            calendar_client.add_task(title, task_type, due_date=due_date, end_date=end_date,
                                     start_dt=start_dt, duration_minutes=duration_minutes,
                                     attendees=attendee_emails or None)
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
                calendar_client.delete_task(task["id"], task["cal_id"])
                response_text = f"🗑 Удалено: {task['title']}"
            else:
                calendar_client.complete_task(task["id"], task["cal_id"])
                response_text = f"✅ Выполнено: {task['title']}"
        else:
            response_text = "Не нашёл такую задачу. Напиши /tasks чтобы увидеть список с номерами."
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
        await _send_morning_digest(context.application, target_date)
        return "[дайджест отправлен]"

    elif intent == "save_progress":
        calendar_client.save_progress(original_text)
        response_text = "✍️ Прогресс сохранён. Учту завтра утром!"
        await update.message.reply_text(response_text)
        return response_text

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
    query = update.callback_query
    await query.answer()
    data = context.user_data.pop("pending_email", {})

    if query.data == "email_confirm":
        if not data.get("to_email"):
            await query.edit_message_text("Адрес не указан, письмо не отправлено.")
            return
        try:
            gmail_client.send_email(data["to_email"], data["subject"], data["body"])
            await query.edit_message_text(f"✉️ Письмо отправлено на {data['to_email']}")
        except Exception as e:
            logger.error("send_email failed: %s", e)
            await query.edit_message_text(f"Ошибка отправки: {e}")
    else:
        await query.edit_message_text("Отменено.")


async def _send_morning_digest(app: Application, target_date: datetime.date | None = None) -> None:
    last_error = None
    for attempt in range(3):
        try:
            events = calendar_client.get_todays_calendar_events(target_date)
            short = calendar_client.get_active_tasks("short", target_date)
            long_ = calendar_client.get_active_tasks("long", target_date)
            yesterday = calendar_client.get_progress_before_date(target_date)
            emails = gmail_client.get_unread_emails() if target_date is None else []
            weather = weather_client.get_weather(target_date)

            text = digest_module.generate_morning_digest(events, short, long_, yesterday, emails, target_date, weather)

            with open(config.ALICE_DIGEST_FILE, "w", encoding="utf-8") as f:
                f.write(text)

            await app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text)
            pushover_client.send_push(text[:1024], title="☀️ Доброе утро!")
            return
        except Exception as e:
            last_error = e
            logger.warning("Morning digest attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(10)

    logger.error("Morning digest failed after 3 attempts: %s", last_error)
    await app.bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=f"⚠️ Ошибка генерации дайджеста: {last_error}",
    )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_owner(update):
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


async def _send_evening_checkin(app: Application) -> None:
    try:
        text = digest_module.generate_evening_checkin()
        await app.bot.send_message(chat_id=config.TELEGRAM_CHAT_ID, text=text)
    except Exception as e:
        logger.error("Evening check-in failed: %s", e)


def register_handlers(app: Application) -> None:
    progress_conv = ConversationHandler(
        entry_points=[CommandHandler("progress", cmd_progress_start)],
        states={WAITING_PROGRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_progress_receive)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(progress_conv)
    app.add_handler(CallbackQueryHandler(callback_email, pattern="^email_"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    # natural language — lowest priority, catches everything else
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural))
