"""User-facing string catalog for ru / en / he.

LLM prompts (digest, agent system prompt, whatsapp_summary) have their own
language plumbing; this module is for fixed UI labels — onboarding wizard,
/settings menu, /help, access denial messages, profile prompts.

Resolution rule:
  t("key", lang) → catalog[lang][key] → catalog["ru"][key] → key (literal)

So a missing translation never crashes — falls back to Russian, then to
the key name itself.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("ru", "en", "he")
DEFAULT_LANGUAGE = "ru"


TRANSLATIONS: dict[str, dict[str, str]] = {
    "ru": {
        # ─── Onboarding wizard ─────────────────────────────────────────────
        "onboard.choose_language": "🌍 Choose your language / Выберите язык / בחר שפה",
        "onboard.welcome": "Привет! Я твой личный AI-ассистент. Помогаю с календарём, почтой, WhatsApp, дайджестами, дневником.",
        "onboard.ask_name": "Как мне к тебе обращаться?",
        "onboard.use_telegram_name": "Использовать «{name}»",
        "onboard.type_other_name": "Ввести другое",
        "onboard.ask_city": "В каком городе живёшь? Это нужно для погоды в дайджесте.",
        "onboard.city_not_found": "Не нашёл город «{city}». Попробуй ещё раз — на русском или английском.",
        "onboard.ask_timezone": "Какой у тебя часовой пояс?",
        "onboard.ask_morning_time": "Во сколько присылать утренний дайджест?",
        "onboard.ask_evening_time": "А вечернюю проверку?",
        "onboard.ask_google": "Подключи Google аккаунт (календарь, Gmail):\n\n{url}",
        "onboard.ask_news": "Какие новости включить в дайджест? Можно выбрать несколько.",
        "onboard.ask_personality": "Какой стиль общения тебе ближе?",
        "onboard.ask_first_task": "У меня два типа задач: ⚡ короткие (1-3 дня) и 🎯 долгосрочные. Добавь одну короткую задачу прямо сейчас (или пропусти):",
        "onboard.ask_first_goal": "А теперь долгосрочная цель — что-то большое на месяцы. Я буду напоминать о прогрессе в утренних дайджестах. (Или пропусти):",
        "onboard.ask_integrations": "Хочешь подключить что-то ещё? WhatsApp, Pushover, умный дом, дневник в Docs.",
        "onboard.complete": "Готово, {name}! Дайджест в {time} {tz}. Если что-то нужно изменить — /profile. Команды — /help.",
        "onboard.skip": "Пропустить",
        # ─── Personality presets ───────────────────────────────────────────
        "personality.warm": "🙂 Тёплый и дружелюбный",
        "personality.business": "💼 Деловой и краткий",
        "personality.funny": "😄 С юмором",
        "personality.calm": "🧘 Спокойный и заботливый",
        # ─── Settings menu ─────────────────────────────────────────────────
        "settings.title": "*Настройки интеграций*\nВыбери что подключить/изменить:",
        "settings.connected": "✅ Подключено",
        "settings.disconnected": "⚪️ Не подключено",
        "settings.google": "🔑 Google",
        "settings.whatsapp": "💬 WhatsApp",
        "settings.pushover": "📱 Pushover",
        "settings.alice": "🔊 Yandex Алиса",
        "settings.tuya": "💡 Tuya",
        "settings.vesync": "🔌 VeSync",
        "settings.diary_doc": "📓 Дневник в Docs",
        "settings.close": "⬅️ Закрыть",
        "settings.back": "⬅️ Назад",
        "settings.closed": "Закрыто. Открой снова через /settings.",
        # ─── Access denial ─────────────────────────────────────────────────
        "access.no_subscription": "🔒 Для использования бота нужна активная подписка ($20/мес) или промокод.\n\nНажми /start чтобы начать настройку, или введи /promo КОД если у тебя уже есть код.",
        "access.needs_onboarding": "👋 Привет! Похоже мы ещё не знакомы. Нажми /start чтобы начать.",
        # ─── /profile (re-onboarding) ──────────────────────────────────────
        "profile.title": "*Профиль*\nПройдись по каждому пункту: Оставить или Изменить.",
        "profile.keep": "✅ Оставить",
        "profile.change": "✏️ Изменить",
        "profile.current": "Сейчас: {value}",
        "profile.updated": "Профиль обновлён.",
        # ─── Generic buttons ────────────────────────────────────────────────
        "btn.next": "Дальше",
        "btn.cancel": "Отмена",
        "btn.done": "✅ Готово",
        "btn.other": "Другое",
        "btn.skip": "Пропустить",
        # ─── Dashboard ─────────────────────────────────────────────────────
        "dashboard.title": "📊 *Сводка по {name}*\n_{date}_",
        "dashboard.no_data": "Пока нет задач и целей. Используй /add или скажи «добавь цель: …»",
        "dashboard.active_tasks": "📋 *Активные задачи*",
        "dashboard.short_tasks": "⚡ Краткосрочные: {n}",
        "dashboard.long_tasks": "🎯 Долгосрочные:  {n}",
        "dashboard.streak": "🔥 *Стрики*\nДней подряд с записями: {current}\nСамый длинный: {longest}",
        "dashboard.goals_header": "🎯 *Прогресс по долгосрочным целям*",
        "dashboard.no_goals": "Целей пока нет. Скажи «добавь цель: …»",
        "dashboard.today_calendar": "🗓️ *Сегодня в календаре*",
        "dashboard.no_calendar": "Сегодня в календаре пусто.",
        "dashboard.open_browser": "📊 Открыть в браузере",
        "dashboard.goals_btn": "🎯 Цели",
        "dashboard.tasks_btn": "📋 Задачи",
        # ─── /help (Phase I) ───────────────────────────────────────────────
        "help.full_text": (
            "🤖 *Что я умею*\n"
            "\n"
            "Я твой личный AI-ассистент. Пиши обычными словами — я разберусь.\n"
            "Команды ниже — это шорткаты.\n"
            "\n"
            "📋 *Задачи и календарь*\n"
            "  /tasks         — список активных задач\n"
            "  /add           — быстро добавить задачу\n"
            "  /done          — отметить задачу выполненной\n"
            "  /digest        — утренний дайджест прямо сейчас\n"
            "  /progress      — записать прогресс за день\n"
            "\n"
            "🎯 *Цели и статистика*\n"
            "  /dashboard     — обзор задач, целей, стриков, прогресса\n"
            "\n"
            "👥 *Семья / группы*\n"
            "  /group_list      — мои группы\n"
            "  /group_create    — создать группу\n"
            "  /group_invite    — пригласить кого-то\n"
            "  /group_remove    — убрать из группы\n"
            "  /group_members   — кто в группе\n"
            "  /group_leave     — выйти из группы\n"
            "  /group_promote   — сделать админом\n"
            "  /group_demote    — снять админку\n"
            "  /group_rename    — переименовать группу\n"
            "  /group_delete    — удалить группу\n"
            "\n"
            "🧠 *Память и личность*\n"
            "  /memory        — что я помню о тебе\n"
            "  /profile       — изменить имя/город/язык/время/личность\n"
            "\n"
            "⚙️ *Настройки и интеграции*\n"
            "  /settings      — WhatsApp, Pushover, Алиса, Tuya, VeSync, Дневник\n"
            "\n"
            "💳 *Подписка*\n"
            "  /subscribe     — оформить подписку\n"
            "  /promo КОД     — активировать промокод\n"
            "  /cancel        — отменить подписку\n"
            "\n"
            "❓ */help* — показать снова\n"
            "\n"
            "Пиши обычными словами:\n"
            "  «напомни через час позвонить маме»\n"
            "  «что в WhatsApp?»\n"
            "  «добавь долгосрочную цель: выучить итальянский»"
        ),
        "help.short_text": (
            "Команды через /, или пиши обычными словами.\n"
            "/dashboard, /tasks, /settings, /profile, /group_list — основное.\n"
            "/help — полный список."
        ),
        # ─── Groups (Phase H) ──────────────────────────────────────────────
        "groups.created": "👥 Группа «{name}» создана (id={group_id}).",
        "groups.list_header": "👥 *Твои группы:*",
        "groups.list_empty": "Ты пока не состоишь ни в одной группе. Создай через /group_create <название>.",
        "groups.list_item": "  {group_id}. {name} — {role}, участников: {member_count}",
        "groups.list_item_pending": "  {group_id}. {name} — приглашение, ожидает решения",
        "groups.members_header": "👥 *Участники группы «{name}»:*",
        "groups.member_admin": "  • {display} ({username}) — админ",
        "groups.member_normal": "  • {display} ({username}) — участник",
        "groups.member_pending": "  • {display} ({username}) — приглашён, не подтвердил",
        "groups.invite_sent_to_user": "✉️ Приглашение отправлено @{username} в личку.",
        "groups.invite_link_ready": "✉️ Дай ссылку @{username} (он/она ещё не пользуется ботом):\n{link}",
        "groups.invite_notice": "👋 {inviter} приглашает тебя в группу «{group_name}». Хочешь присоединиться?",
        "groups.invite_already_member": "Этот пользователь уже в группе.",
        "groups.invite_already_invited": "Этот пользователь уже приглашён — ждём ответа.",
        "groups.accept": "✅ Принять",
        "groups.decline": "❌ Отклонить",
        "groups.accepted": "✅ Ты в группе «{name}».",
        "groups.declined": "❌ Приглашение отклонено.",
        "groups.removed": "🗑 Участник удалён.",
        "groups.left": "👋 Ты вышел(а) из группы.",
        "groups.promoted": "⬆️ Готово — теперь админ.",
        "groups.demoted": "⬇️ Готово — больше не админ.",
        "groups.renamed": "✏️ Группа переименована в «{name}».",
        "groups.deleted": "🗑 Группа удалена. Общие цели стали личными.",
        "groups.added_via_invite": "👥 Ты добавлен(а) в группу «{group_name}».",
        # usage hints
        "groups.usage.create": "Использование: /group_create <название>",
        "groups.usage.members": "Использование: /group_members <id группы>",
        "groups.usage.invite": "Использование: /group_invite <id группы> <@username>",
        "groups.usage.remove": "Использование: /group_remove <id группы> <user_id>",
        "groups.usage.leave": "Использование: /group_leave <id группы>",
        "groups.usage.promote": "Использование: /group_promote <id группы> <user_id>",
        "groups.usage.demote": "Использование: /group_demote <id группы> <user_id>",
        "groups.usage.rename": "Использование: /group_rename <id группы> <новое название>",
        "groups.usage.delete": "Использование: /group_delete <id группы>",
        # errors
        "groups.error.not_found": "Группа не найдена.",
        "groups.error.not_admin": "Только админ группы может это сделать.",
        "groups.error.not_member": "Ты не участник этой группы.",
        "groups.error.user_not_found": "Пользователь не найден.",
        "groups.error.value": "Не получилось: {reason}",
        "groups.error.invite_token_invalid": "Ссылка-приглашение некорректна.",
        "groups.error.invite_token_expired": "Ссылка-приглашение истекла (старше 7 дней).",
        # ─── Phase E wizard (12-step onboarding) / Phase F /profile ────────
        "wizard.lang.choose": "🌍 Choose your language\nВыберите язык\nבחר שפה",
        "wizard.name.ask": "Как мне к тебе обращаться?",
        "wizard.name.use_first_name": "Использовать «{name}»",
        "wizard.name.type_other": "Ввести другое",
        "wizard.city.ask": "В каком городе ты живёшь? Это нужно для погоды.",
        "wizard.city.not_found": "Не нашёл такой город, попробуй ещё.",
        "wizard.city.preview": "Сейчас в {city}: {weather}",
        "wizard.tz.ask": "Какой у тебя часовой пояс?",
        "wizard.tz.other": "Другой",
        "wizard.tz.type": "Напиши название часового пояса (например, Asia/Jerusalem).",
        "wizard.tz.invalid": "Не распознал часовой пояс. Попробуй ещё раз (формат IANA, например Europe/Berlin).",
        "wizard.time.ask_morning": "Во сколько присылать утренний дайджест?",
        "wizard.time.ask_evening": "А вечерний чек-ин?",
        "wizard.time.other": "⏰ Другое",
        "wizard.time.type": "Напиши время в формате ЧЧ:ММ (например, 07:15).",
        "wizard.time.invalid": "Не понял время. Формат ЧЧ:ММ, например 07:15.",
        "wizard.google.ask": "Подключи Google аккаунт (Календарь, Gmail, Контакты):\n\n{url}\n\nПосле авторизации нажми кнопку ниже.",
        "wizard.google.done": "✅ Готово, авторизовался",
        "wizard.google.skip": "Пропустить",
        "wizard.news.ask": "Какие новости включить в дайджест? Можно выбрать несколько стран и нажать «Готово».",
        "wizard.news.preset.IL": "🇮🇱 Израиль",
        "wizard.news.preset.RU": "🇷🇺 Россия",
        "wizard.news.preset.UA": "🇺🇦 Украина",
        "wizard.news.preset.US": "🇺🇸 США",
        "wizard.news.preset.EU": "🇪🇺 Европа",
        "wizard.news.preset.World": "🌍 Мир",
        "wizard.news.custom": "➕ Свой RSS",
        "wizard.news.none": "⛔ Без новостей",
        "wizard.news.custom_name": "Как назвать этот источник?",
        "wizard.news.custom_url": "Пришли URL RSS-ленты.",
        "wizard.news.custom_invalid": "Не похоже на URL. Должен начинаться с http:// или https://.",
        "wizard.personality.ask": "Какой стиль общения тебе ближе?",
        "wizard.personality.warm": "🙂 Тёплый",
        "wizard.personality.business": "💼 Деловой",
        "wizard.personality.funny": "😄 С юмором",
        "wizard.personality.calm": "🧘 Спокойный",
        "wizard.task.ask": "Добавь одну короткую задачу (на ближайшие пару дней) — или пропусти.",
        "wizard.goal.ask": "Теперь долгосрочная цель на 2-3 месяца — или пропусти.",
        "wizard.skip": "Пропустить",
        "wizard.integrations.ask": "Подключи дополнительные интеграции (можно потом через /settings):",
        "wizard.integrations.done": "✅ Готово, завершить",
        "wizard.done": "Готово, {name}! Я настроен. Если что-то нужно изменить — /profile. Все команды — /help.",
        "wizard.profile.kept": "Оставил как есть.",
        "wizard.profile.changed": "Обновил.",
        "wizard.profile.done": "Профиль обновлён.",
        "wizard.profile.current_is": "Сейчас: {value}",
        "wizard.profile.label.language": "🌍 Язык интерфейса",
        "wizard.profile.label.name": "👤 Имя (как обращаться)",
        "wizard.profile.label.city": "🏙 Город (для прогноза погоды)",
        "wizard.profile.label.timezone": "🕒 Часовой пояс",
        "wizard.profile.label.morning_time": "☀️ Время утреннего дайджеста",
        "wizard.profile.label.evening_time": "🌙 Время вечернего чек-ина",
        "wizard.profile.label.google": "🔐 Google аккаунт (календарь, Gmail, дневник)",
        "wizard.profile.label.news": "📰 Страны для новостей в дайджесте",
        "wizard.profile.label.personality": "🎭 Стиль общения бота",
        "wizard.keep": "✅ Оставить",
        "wizard.change": "✏️ Изменить",
    },
    "en": {
        "onboard.choose_language": "🌍 Choose your language / Выберите язык / בחר שפה",
        "onboard.welcome": "Hi! I'm your personal AI assistant. I help with calendar, email, WhatsApp, digests, and a personal diary.",
        "onboard.ask_name": "What should I call you?",
        "onboard.use_telegram_name": "Use «{name}»",
        "onboard.type_other_name": "Enter another",
        "onboard.ask_city": "What city are you in? Needed for weather in the digest.",
        "onboard.city_not_found": "I couldn't find «{city}». Try again — English works too.",
        "onboard.ask_timezone": "What's your timezone?",
        "onboard.ask_morning_time": "What time should I send the morning digest?",
        "onboard.ask_evening_time": "And the evening check-in?",
        "onboard.ask_google": "Connect Google (Calendar, Gmail):\n\n{url}",
        "onboard.ask_news": "Which news sources do you want in your digest? You can pick several.",
        "onboard.ask_personality": "What conversational style do you prefer?",
        "onboard.ask_first_task": "I have two types of tasks: ⚡ short (1-3 days) and 🎯 long-term. Add a short task right now (or skip):",
        "onboard.ask_first_goal": "Now a long-term goal — something big over months. I'll remind you of progress in the morning digest. (Or skip):",
        "onboard.ask_integrations": "Anything else to connect? WhatsApp, Pushover, smart home, diary in Docs.",
        "onboard.complete": "Done, {name}! Digest at {time} {tz}. Use /profile to change anything later. /help for commands.",
        "onboard.skip": "Skip",
        "personality.warm": "🙂 Warm and friendly",
        "personality.business": "💼 Business and concise",
        "personality.funny": "😄 Funny",
        "personality.calm": "🧘 Calm and caring",
        "settings.title": "*Integration settings*\nPick what to connect or change:",
        "settings.connected": "✅ Connected",
        "settings.disconnected": "⚪️ Not connected",
        "settings.google": "🔑 Google",
        "settings.whatsapp": "💬 WhatsApp",
        "settings.pushover": "📱 Pushover",
        "settings.alice": "🔊 Yandex Alice",
        "settings.tuya": "💡 Tuya",
        "settings.vesync": "🔌 VeSync",
        "settings.diary_doc": "📓 Diary in Docs",
        "settings.close": "⬅️ Close",
        "settings.back": "⬅️ Back",
        "settings.closed": "Closed. Reopen with /settings.",
        "access.no_subscription": "🔒 An active subscription ($20/mo) or promo code is required.\n\nPress /start to begin, or /promo CODE if you have one.",
        "access.needs_onboarding": "👋 Hi! We haven't met yet. Press /start to begin.",
        "profile.title": "*Profile*\nFor each item: Keep or Change.",
        "profile.keep": "✅ Keep",
        "profile.change": "✏️ Change",
        "profile.current": "Currently: {value}",
        "profile.updated": "Profile updated.",
        "btn.next": "Next",
        "btn.cancel": "Cancel",
        "btn.done": "✅ Done",
        "btn.other": "Other",
        "btn.skip": "Skip",
        "dashboard.title": "📊 *Summary for {name}*\n_{date}_",
        "dashboard.no_data": "No tasks or goals yet. Use /add or say «add goal: …»",
        "dashboard.active_tasks": "📋 *Active tasks*",
        "dashboard.short_tasks": "⚡ Short-term: {n}",
        "dashboard.long_tasks": "🎯 Long-term:  {n}",
        "dashboard.streak": "🔥 *Streaks*\nDays in a row with entries: {current}\nLongest: {longest}",
        "dashboard.goals_header": "🎯 *Long-term goals progress*",
        "dashboard.no_goals": "No goals yet. Say «add goal: …»",
        "dashboard.today_calendar": "🗓️ *Today's calendar*",
        "dashboard.no_calendar": "Calendar is empty today.",
        "dashboard.open_browser": "📊 Open in browser",
        "dashboard.goals_btn": "🎯 Goals",
        "dashboard.tasks_btn": "📋 Tasks",
        # ─── /help (Phase I) ───────────────────────────────────────────────
        "help.full_text": (
            "🤖 *What I can do*\n"
            "\n"
            "I'm your personal AI assistant. Just write in plain language — I'll figure it out.\n"
            "The commands below are shortcuts.\n"
            "\n"
            "📋 *Tasks and calendar*\n"
            "  /tasks         — list active tasks\n"
            "  /add           — quickly add a task\n"
            "  /done          — mark a task as completed\n"
            "  /digest        — morning digest right now\n"
            "  /progress      — log your progress for the day\n"
            "\n"
            "🎯 *Goals and stats*\n"
            "  /dashboard     — overview of tasks, goals, streaks, progress\n"
            "\n"
            "👥 *Family / groups*\n"
            "  /group_list      — my groups\n"
            "  /group_create    — create a group\n"
            "  /group_invite    — invite someone\n"
            "  /group_remove    — remove from a group\n"
            "  /group_members   — who's in the group\n"
            "  /group_leave     — leave a group\n"
            "  /group_promote   — make admin\n"
            "  /group_demote    — remove admin\n"
            "  /group_rename    — rename a group\n"
            "  /group_delete    — delete a group\n"
            "\n"
            "🧠 *Memory and personality*\n"
            "  /memory        — what I remember about you\n"
            "  /profile       — change name/city/language/time/personality\n"
            "\n"
            "⚙️ *Settings and integrations*\n"
            "  /settings      — WhatsApp, Pushover, Alice, Tuya, VeSync, Diary\n"
            "\n"
            "💳 *Subscription*\n"
            "  /subscribe     — start a subscription\n"
            "  /promo CODE    — activate a promo code\n"
            "  /cancel        — cancel subscription\n"
            "\n"
            "❓ */help* — show this again\n"
            "\n"
            "Write in plain language:\n"
            "  \"remind me in an hour to call mom\"\n"
            "  \"what's in WhatsApp?\"\n"
            "  \"add a long-term goal: learn Italian\""
        ),
        "help.short_text": (
            "Use / commands, or just write in plain language.\n"
            "/dashboard, /tasks, /settings, /profile, /group_list — the basics.\n"
            "/help — full list."
        ),
        # ─── Groups (Phase H) ──────────────────────────────────────────────
        "groups.created": "👥 Group «{name}» created (id={group_id}).",
        "groups.list_header": "👥 *Your groups:*",
        "groups.list_empty": "You're not in any groups yet. Create one with /group_create <name>.",
        "groups.list_item": "  {group_id}. {name} — {role}, members: {member_count}",
        "groups.list_item_pending": "  {group_id}. {name} — invitation pending",
        "groups.members_header": "👥 *Members of «{name}»:*",
        "groups.member_admin": "  • {display} ({username}) — admin",
        "groups.member_normal": "  • {display} ({username}) — member",
        "groups.member_pending": "  • {display} ({username}) — invited, not yet accepted",
        "groups.invite_sent_to_user": "✉️ Invitation sent to @{username} in DM.",
        "groups.invite_link_ready": "✉️ Share this link with @{username} (not in the bot yet):\n{link}",
        "groups.invite_notice": "👋 {inviter} is inviting you to the group «{group_name}». Join?",
        "groups.invite_already_member": "This user is already in the group.",
        "groups.invite_already_invited": "Already invited — waiting for them to accept.",
        "groups.accept": "✅ Accept",
        "groups.decline": "❌ Decline",
        "groups.accepted": "✅ You joined «{name}».",
        "groups.declined": "❌ Invitation declined.",
        "groups.removed": "🗑 Member removed.",
        "groups.left": "👋 You left the group.",
        "groups.promoted": "⬆️ Done — now admin.",
        "groups.demoted": "⬇️ Done — no longer admin.",
        "groups.renamed": "✏️ Group renamed to «{name}».",
        "groups.deleted": "🗑 Group deleted. Shared goals are now personal.",
        "groups.added_via_invite": "👥 You were added to «{group_name}».",
        "groups.usage.create": "Usage: /group_create <name>",
        "groups.usage.members": "Usage: /group_members <group id>",
        "groups.usage.invite": "Usage: /group_invite <group id> <@username>",
        "groups.usage.remove": "Usage: /group_remove <group id> <user_id>",
        "groups.usage.leave": "Usage: /group_leave <group id>",
        "groups.usage.promote": "Usage: /group_promote <group id> <user_id>",
        "groups.usage.demote": "Usage: /group_demote <group id> <user_id>",
        "groups.usage.rename": "Usage: /group_rename <group id> <new name>",
        "groups.usage.delete": "Usage: /group_delete <group id>",
        "groups.error.not_found": "Group not found.",
        "groups.error.not_admin": "Only a group admin can do this.",
        "groups.error.not_member": "You're not a member of this group.",
        "groups.error.user_not_found": "User not found.",
        "groups.error.value": "Failed: {reason}",
        "groups.error.invite_token_invalid": "Invite link is invalid.",
        "groups.error.invite_token_expired": "Invite link expired (older than 7 days).",
        # ─── Phase E wizard (12-step onboarding) / Phase F /profile ────────
        "wizard.lang.choose": "🌍 Choose your language\nВыберите язык\nבחר שפה",
        "wizard.name.ask": "What should I call you?",
        "wizard.name.use_first_name": "Use «{name}»",
        "wizard.name.type_other": "Enter another",
        "wizard.city.ask": "What city do you live in? Needed for weather.",
        "wizard.city.not_found": "I couldn't find that city — try again.",
        "wizard.city.preview": "Right now in {city}: {weather}",
        "wizard.tz.ask": "What's your timezone?",
        "wizard.tz.other": "Other",
        "wizard.tz.type": "Type an IANA timezone name (e.g., Asia/Jerusalem).",
        "wizard.tz.invalid": "That doesn't look like a valid timezone. Try the IANA format (e.g., Europe/Berlin).",
        "wizard.time.ask_morning": "What time should I send the morning digest?",
        "wizard.time.ask_evening": "And the evening check-in?",
        "wizard.time.other": "⏰ Other",
        "wizard.time.type": "Type a time as HH:MM (e.g., 07:15).",
        "wizard.time.invalid": "I didn't get that time. Format is HH:MM, e.g., 07:15.",
        "wizard.google.ask": "Connect your Google account (Calendar, Gmail, Contacts):\n\n{url}\n\nClick below after authorizing.",
        "wizard.google.done": "✅ Done, authorized",
        "wizard.google.skip": "Skip",
        "wizard.news.ask": "Which news to include in the digest? You can pick several countries, then «Done».",
        "wizard.news.preset.IL": "🇮🇱 Israel",
        "wizard.news.preset.RU": "🇷🇺 Russia",
        "wizard.news.preset.UA": "🇺🇦 Ukraine",
        "wizard.news.preset.US": "🇺🇸 USA",
        "wizard.news.preset.EU": "🇪🇺 Europe",
        "wizard.news.preset.World": "🌍 World",
        "wizard.news.custom": "➕ Custom RSS",
        "wizard.news.none": "⛔ No news",
        "wizard.news.custom_name": "Name this source:",
        "wizard.news.custom_url": "Send the RSS feed URL.",
        "wizard.news.custom_invalid": "That doesn't look like a URL. Must start with http:// or https://.",
        "wizard.personality.ask": "What communication style do you prefer?",
        "wizard.personality.warm": "🙂 Warm",
        "wizard.personality.business": "💼 Business",
        "wizard.personality.funny": "😄 Funny",
        "wizard.personality.calm": "🧘 Calm",
        "wizard.task.ask": "Add one short task (next few days) — or skip.",
        "wizard.goal.ask": "Now a long-term goal for 2-3 months — or skip.",
        "wizard.skip": "Skip",
        "wizard.integrations.ask": "Connect additional integrations (you can also do this later via /settings):",
        "wizard.integrations.done": "✅ Done, finish",
        "wizard.done": "All set, {name}! I'm configured. To change anything: /profile. All commands: /help.",
        "wizard.profile.kept": "Kept as-is.",
        "wizard.profile.changed": "Updated.",
        "wizard.profile.done": "Profile updated.",
        "wizard.profile.current_is": "Currently: {value}",
        "wizard.profile.label.language": "🌍 Interface language",
        "wizard.profile.label.name": "👤 Name (how I address you)",
        "wizard.profile.label.city": "🏙 City (for weather forecast)",
        "wizard.profile.label.timezone": "🕒 Timezone",
        "wizard.profile.label.morning_time": "☀️ Morning digest time",
        "wizard.profile.label.evening_time": "🌙 Evening check-in time",
        "wizard.profile.label.google": "🔐 Google account (Calendar, Gmail, diary)",
        "wizard.profile.label.news": "📰 News countries in the digest",
        "wizard.profile.label.personality": "🎭 Bot's conversation style",
        "wizard.keep": "✅ Keep",
        "wizard.change": "✏️ Change",
    },
    "he": {
        "onboard.choose_language": "🌍 Choose your language / Выберите язык / בחר שפה",
        "onboard.welcome": "שלום! אני העוזר האישי שלך. עוזר עם יומן, מייל, WhatsApp, דייג'סטים ויומן אישי.",
        "onboard.ask_name": "איך לפנות אליך?",
        "onboard.use_telegram_name": "להשתמש ב-«{name}»",
        "onboard.type_other_name": "להזין שם אחר",
        "onboard.ask_city": "באיזו עיר אתה גר? נחוץ לתחזית מזג האוויר.",
        "onboard.city_not_found": "לא מצאתי את «{city}». נסה שוב — אפשר באנגלית.",
        "onboard.ask_timezone": "מה אזור הזמן שלך?",
        "onboard.ask_morning_time": "באיזו שעה לשלוח את דייג'סט הבוקר?",
        "onboard.ask_evening_time": "ומתי הצ'ק-אין של הערב?",
        "onboard.ask_google": "חבר את Google (יומן, Gmail):\n\n{url}",
        "onboard.ask_news": "אילו חדשות לכלול בדייג'סט? אפשר לבחור כמה.",
        "onboard.ask_personality": "איזה סגנון תקשורת מעדיף?",
        "onboard.ask_first_task": "יש לי שני סוגי משימות: ⚡ קצרות (1-3 ימים) ו-🎯 ארוכות טווח. הוסף משימה קצרה עכשיו (או דלג):",
        "onboard.ask_first_goal": "עכשיו מטרה ארוכת טווח — משהו גדול לחודשים. אזכיר על ההתקדמות בדייג'סט הבוקר. (או דלג):",
        "onboard.ask_integrations": "רוצה לחבר עוד משהו? WhatsApp, Pushover, בית חכם, יומן ב-Docs.",
        "onboard.complete": "סיימנו, {name}! דייג'סט ב-{time} {tz}. /profile לשינוי. /help לפקודות.",
        "onboard.skip": "דלג",
        "personality.warm": "🙂 חמים וידידותי",
        "personality.business": "💼 עסקי ותמציתי",
        "personality.funny": "😄 עם הומור",
        "personality.calm": "🧘 רגוע ואכפתי",
        "settings.title": "*הגדרות אינטגרציה*\nבחר מה לחבר או לשנות:",
        "settings.connected": "✅ מחובר",
        "settings.disconnected": "⚪️ לא מחובר",
        "settings.google": "🔑 Google",
        "settings.whatsapp": "💬 WhatsApp",
        "settings.pushover": "📱 Pushover",
        "settings.alice": "🔊 Yandex Alice",
        "settings.tuya": "💡 Tuya",
        "settings.vesync": "🔌 VeSync",
        "settings.diary_doc": "📓 יומן ב-Docs",
        "settings.close": "⬅️ סגור",
        "settings.back": "⬅️ חזרה",
        "settings.closed": "נסגר. פתח שוב עם /settings.",
        "access.no_subscription": "🔒 נדרש מנוי פעיל ($20/חודש) או קוד פרומו.\n\nלחץ /start כדי להתחיל, או /promo קוד אם יש לך.",
        "access.needs_onboarding": "👋 שלום! לא הכרנו עדיין. לחץ /start.",
        "profile.title": "*פרופיל*\nלכל פריט: השאר או שנה.",
        "profile.keep": "✅ השאר",
        "profile.change": "✏️ שנה",
        "profile.current": "כרגע: {value}",
        "profile.updated": "הפרופיל עודכן.",
        "btn.next": "הבא",
        "btn.cancel": "ביטול",
        "btn.done": "✅ סיום",
        "btn.other": "אחר",
        "btn.skip": "דלג",
        "dashboard.title": "📊 *סיכום עבור {name}*\n_{date}_",
        "dashboard.no_data": "אין עדיין משימות או מטרות. השתמש ב-/add או אמור «הוסף מטרה: …»",
        "dashboard.active_tasks": "📋 *משימות פעילות*",
        "dashboard.short_tasks": "⚡ קצרות טווח: {n}",
        "dashboard.long_tasks": "🎯 ארוכות טווח:  {n}",
        "dashboard.streak": "🔥 *רצפים*\nימים ברצף עם רישומים: {current}\nהארוך ביותר: {longest}",
        "dashboard.goals_header": "🎯 *התקדמות במטרות ארוכות טווח*",
        "dashboard.no_goals": "אין עדיין מטרות. אמור «הוסף מטרה: …»",
        "dashboard.today_calendar": "🗓️ *היומן של היום*",
        "dashboard.no_calendar": "היומן ריק היום.",
        "dashboard.open_browser": "📊 פתח בדפדפן",
        "dashboard.goals_btn": "🎯 מטרות",
        "dashboard.tasks_btn": "📋 משימות",
        # ─── /help (Phase I) ───────────────────────────────────────────────
        "help.full_text": (
            "🤖 *מה אני יודע לעשות*\n"
            "\n"
            "אני העוזר האישי שלך. כתוב במילים רגילות — אני אבין.\n"
            "הפקודות למטה הן קיצורי דרך.\n"
            "\n"
            "📋 *משימות ויומן*\n"
            "  /tasks         — רשימת משימות פעילות\n"
            "  /add           — הוספת משימה במהירות\n"
            "  /done          — סימון משימה כבוצעה\n"
            "  /digest        — דייג'סט בוקר עכשיו\n"
            "  /progress      — תיעוד התקדמות יומית\n"
            "\n"
            "🎯 *מטרות וסטטיסטיקה*\n"
            "  /dashboard     — סקירת משימות, מטרות, רצפים, התקדמות\n"
            "\n"
            "👥 *משפחה / קבוצות*\n"
            "  /group_list      — הקבוצות שלי\n"
            "  /group_create    — יצירת קבוצה\n"
            "  /group_invite    — הזמנה לקבוצה\n"
            "  /group_remove    — הסרה מקבוצה\n"
            "  /group_members   — מי בקבוצה\n"
            "  /group_leave     — יציאה מקבוצה\n"
            "  /group_promote   — הפיכת חבר למנהל\n"
            "  /group_demote    — ביטול מנהל\n"
            "  /group_rename    — שינוי שם הקבוצה\n"
            "  /group_delete    — מחיקת קבוצה\n"
            "\n"
            "🧠 *זיכרון ואישיות*\n"
            "  /memory        — מה אני זוכר עליך\n"
            "  /profile       — שינוי שם/עיר/שפה/שעה/אישיות\n"
            "\n"
            "⚙️ *הגדרות ואינטגרציות*\n"
            "  /settings      — WhatsApp, Pushover, Alice, Tuya, VeSync, יומן\n"
            "\n"
            "💳 *מנוי*\n"
            "  /subscribe     — הרשמה למנוי\n"
            "  /promo קוד     — הפעלת קוד פרומו\n"
            "  /cancel        — ביטול מנוי\n"
            "\n"
            "❓ */help* — להציג שוב\n"
            "\n"
            "אפשר לכתוב במילים רגילות:\n"
            "  «תזכיר לי בעוד שעה להתקשר לאמא»\n"
            "  «מה יש ב-WhatsApp?»\n"
            "  «הוסף מטרה ארוכת טווח: ללמוד איטלקית»"
        ),
        "help.short_text": (
            "פקודות עם /, או פשוט תכתוב במילים רגילות.\n"
            "/dashboard, /tasks, /settings, /profile, /group_list — הבסיס.\n"
            "/help — רשימה מלאה."
        ),
        # ─── Groups (Phase H) ──────────────────────────────────────────────
        "groups.created": "👥 הקבוצה «{name}» נוצרה (id={group_id}).",
        "groups.list_header": "👥 *הקבוצות שלך:*",
        "groups.list_empty": "אינך בקבוצה כרגע. צור עם /group_create <שם>.",
        "groups.list_item": "  {group_id}. {name} — {role}, חברים: {member_count}",
        "groups.list_item_pending": "  {group_id}. {name} — הזמנה ממתינה",
        "groups.members_header": "👥 *חברים בקבוצה «{name}»:*",
        "groups.member_admin": "  • {display} ({username}) — מנהל",
        "groups.member_normal": "  • {display} ({username}) — חבר",
        "groups.member_pending": "  • {display} ({username}) — הוזמן, טרם אישר",
        "groups.invite_sent_to_user": "✉️ הזמנה נשלחה ל-@{username}.",
        "groups.invite_link_ready": "✉️ שתף את הקישור עם @{username} (אינו עדיין בבוט):\n{link}",
        "groups.invite_notice": "👋 {inviter} מזמין אותך לקבוצה «{group_name}». להצטרף?",
        "groups.invite_already_member": "המשתמש כבר בקבוצה.",
        "groups.invite_already_invited": "כבר הוזמן — בהמתנה לתשובה.",
        "groups.accept": "✅ אישור",
        "groups.decline": "❌ דחייה",
        "groups.accepted": "✅ הצטרפת ל-«{name}».",
        "groups.declined": "❌ ההזמנה נדחתה.",
        "groups.removed": "🗑 חבר הוסר.",
        "groups.left": "👋 יצאת מהקבוצה.",
        "groups.promoted": "⬆️ כעת מנהל.",
        "groups.demoted": "⬇️ כבר לא מנהל.",
        "groups.renamed": "✏️ הקבוצה שונתה ל-«{name}».",
        "groups.deleted": "🗑 הקבוצה נמחקה. המטרות המשותפות הפכו לאישיות.",
        "groups.added_via_invite": "👥 נוספת לקבוצה «{group_name}».",
        "groups.usage.create": "שימוש: /group_create <שם>",
        "groups.usage.members": "שימוש: /group_members <id קבוצה>",
        "groups.usage.invite": "שימוש: /group_invite <id קבוצה> <@username>",
        "groups.usage.remove": "שימוש: /group_remove <id קבוצה> <user_id>",
        "groups.usage.leave": "שימוש: /group_leave <id קבוצה>",
        "groups.usage.promote": "שימוש: /group_promote <id קבוצה> <user_id>",
        "groups.usage.demote": "שימוש: /group_demote <id קבוצה> <user_id>",
        "groups.usage.rename": "שימוש: /group_rename <id קבוצה> <שם חדש>",
        "groups.usage.delete": "שימוש: /group_delete <id קבוצה>",
        "groups.error.not_found": "הקבוצה לא נמצאה.",
        "groups.error.not_admin": "רק מנהל הקבוצה יכול לעשות זאת.",
        "groups.error.not_member": "אינך חבר בקבוצה זו.",
        "groups.error.user_not_found": "המשתמש לא נמצא.",
        "groups.error.value": "נכשל: {reason}",
        "groups.error.invite_token_invalid": "קישור ההזמנה אינו תקין.",
        "groups.error.invite_token_expired": "קישור ההזמנה פג תוקף (מעל 7 ימים).",
        # ─── Phase E wizard (12-step onboarding) / Phase F /profile ────────
        "wizard.lang.choose": "🌍 Choose your language\nВыберите язык\nבחר שפה",
        "wizard.name.ask": "איך לפנות אליך?",
        "wizard.name.use_first_name": "להשתמש ב-«{name}»",
        "wizard.name.type_other": "להזין שם אחר",
        "wizard.city.ask": "באיזו עיר אתה גר? נחוץ לתחזית.",
        "wizard.city.not_found": "לא מצאתי את העיר, נסה שוב.",
        "wizard.city.preview": "כרגע ב-{city}: {weather}",
        "wizard.tz.ask": "מה אזור הזמן שלך?",
        "wizard.tz.other": "אחר",
        "wizard.tz.type": "הקלד אזור זמן (IANA, לדוגמה Asia/Jerusalem).",
        "wizard.tz.invalid": "אזור הזמן לא תקין. נסה שוב בפורמט IANA.",
        "wizard.time.ask_morning": "באיזו שעה לשלוח את דייג'סט הבוקר?",
        "wizard.time.ask_evening": "ומתי הצ'ק-אין של הערב?",
        "wizard.time.other": "⏰ אחר",
        "wizard.time.type": "הקלד שעה בפורמט HH:MM (לדוגמה 07:15).",
        "wizard.time.invalid": "לא הצלחתי לפענח. פורמט HH:MM.",
        "wizard.google.ask": "חבר את חשבון Google (יומן, Gmail, אנשי קשר):\n\n{url}\n\nלחץ אישור אחרי שתאשר.",
        "wizard.google.done": "✅ סיום, אישרתי",
        "wizard.google.skip": "דלג",
        "wizard.news.ask": "אילו חדשות לכלול בדייג'סט? אפשר לבחור כמה מדינות ואז «סיום».",
        "wizard.news.preset.IL": "🇮🇱 ישראל",
        "wizard.news.preset.RU": "🇷🇺 רוסיה",
        "wizard.news.preset.UA": "🇺🇦 אוקראינה",
        "wizard.news.preset.US": "🇺🇸 ארה\"ב",
        "wizard.news.preset.EU": "🇪🇺 אירופה",
        "wizard.news.preset.World": "🌍 עולם",
        "wizard.news.custom": "➕ RSS מותאם",
        "wizard.news.none": "⛔ בלי חדשות",
        "wizard.news.custom_name": "תן שם למקור:",
        "wizard.news.custom_url": "שלח URL של הפיד.",
        "wizard.news.custom_invalid": "לא נראה כמו URL. חייב להתחיל ב-http:// או https://.",
        "wizard.personality.ask": "איזה סגנון תקשורת מעדיף?",
        "wizard.personality.warm": "🙂 חמים",
        "wizard.personality.business": "💼 עסקי",
        "wizard.personality.funny": "😄 עם הומור",
        "wizard.personality.calm": "🧘 רגוע",
        "wizard.task.ask": "הוסף משימה קצרה (לימים הקרובים) — או דלג.",
        "wizard.goal.ask": "עכשיו מטרה ארוכת טווח ל-2-3 חודשים — או דלג.",
        "wizard.skip": "דלג",
        "wizard.integrations.ask": "חבר אינטגרציות נוספות (אפשר גם אחר כך דרך /settings):",
        "wizard.integrations.done": "✅ סיום",
        "wizard.done": "הכל מוכן, {name}! /profile לשינויים. /help לפקודות.",
        "wizard.profile.kept": "השארתי כמו שזה.",
        "wizard.profile.changed": "עודכן.",
        "wizard.profile.done": "הפרופיל עודכן.",
        "wizard.profile.current_is": "כרגע: {value}",
        "wizard.profile.label.language": "🌍 שפת הממשק",
        "wizard.profile.label.name": "👤 איך לפנות אליך",
        "wizard.profile.label.city": "🏙 עיר (לתחזית מזג האוויר)",
        "wizard.profile.label.timezone": "🕒 אזור זמן",
        "wizard.profile.label.morning_time": "☀️ שעת הדייג'סט הבוקרי",
        "wizard.profile.label.evening_time": "🌙 שעת הצ'ק-אין הערב",
        "wizard.profile.label.google": "🔐 חשבון Google (יומן, Gmail, יומן אישי)",
        "wizard.profile.label.news": "📰 מדינות לחדשות בדייג'סט",
        "wizard.profile.label.personality": "🎭 סגנון השיחה של הבוט",
        "wizard.keep": "✅ השאר",
        "wizard.change": "✏️ שנה",
    },
}


def t(key: str, language: Optional[str] = None, **fmt) -> str:
    """Resolve a translation key for the given language.

    Fallback chain: target lang → Russian (default) → the key itself as
    last resort so a missing translation never crashes the bot.
    """
    lang = (language or DEFAULT_LANGUAGE).lower()
    catalog = TRANSLATIONS.get(lang) or TRANSLATIONS[DEFAULT_LANGUAGE]
    s = catalog.get(key) or TRANSLATIONS[DEFAULT_LANGUAGE].get(key) or key
    if fmt:
        try:
            return s.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return s
    return s


def user_language(user_id: Optional[int]) -> str:
    """Look up the language preference for a user. Defaults to 'ru' for
    legacy (user_id=None) or unknown users."""
    if user_id is None:
        return DEFAULT_LANGUAGE
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u and getattr(u, "language", None):
                return u.language
    except Exception as e:
        logger.warning("user_language lookup failed for %s: %s", user_id, e)
    return DEFAULT_LANGUAGE


# Unicode RTL embedding markers — wrap Hebrew text that contains embedded
# Latin/digit fragments to prevent direction "jumps".
_RLE = "‫"  # Right-To-Left Embedding
_PDF = "‬"  # Pop Directional Formatting


def wrap_rtl(text: str) -> str:
    """Wrap text with Unicode RTL embedding markers. Useful for Hebrew
    strings that include embedded Latin runs (URLs, names) so direction
    stays consistent on rendering."""
    return f"{_RLE}{text}{_PDF}"
