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
