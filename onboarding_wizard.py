"""12-step onboarding / re-onboarding wizard (Phase E & F).

A ConversationHandler-based wizard that walks a freshly-promo'd user
through configuring everything: language, name, city, timezone, digest
times, Google OAuth, news feeds, personality, first task, first goal,
optional integrations.

The same engine powers `/profile` re-onboarding (Phase F) — distinguished
by `context.user_data["wizard"]["mode"]` being either `"onboarding"` or
`"profile"`. In `"profile"` mode each step first offers Keep/Change.

In-memory state lives in `context.user_data["wizard"]`:

    {
        "mode": "onboarding" | "profile",
        "step": int,           # current step index
        "data": dict,          # collected values, flushed to DB on finalize
        "news_countries": set,
        "custom_rss_name": str | None,
    }

Step functions are deliberately split so tests can drive them directly
without instantiating a ConversationHandler.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ConversationHandler

logger = logging.getLogger(__name__)


# ─── State enum (one int per step) ────────────────────────────────────────────

(
    STEP_LANGUAGE,
    STEP_NAME,
    STEP_CITY,
    STEP_TIMEZONE,
    STEP_MORNING_TIME,
    STEP_EVENING_TIME,
    STEP_GOOGLE,
    STEP_NEWS,
    STEP_PERSONALITY,
    STEP_FIRST_TASK,
    STEP_FIRST_GOAL,
    STEP_INTEGRATIONS,
    # Sub-state for custom RSS (waiting for URL after name).
    SUBSTATE_RSS_URL,
    # Free-text fall-through states (e.g. typed timezone, typed time, typed city)
) = range(13)

# We keep a single state per "step" rather than two-per-step because in
# profile mode the keep/change decision lives in an inline keyboard, not a
# new state — the same step state both renders the keep/change prompt
# (entry) and handles the actual collection (after keep/change tap).


# Popular IANA timezones — first 8 buttons in step 3.
TZ_PRESETS = [
    "Asia/Jerusalem",
    "Europe/Moscow",
    "Europe/Kyiv",
    "Europe/Berlin",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
]


# Pre-set digest times.
MORNING_TIME_PRESETS = ["06:00", "06:30", "07:00", "07:30", "08:00"]
EVENING_TIME_PRESETS = ["20:00", "20:30", "21:00", "21:30", "22:00"]


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _wizard_state(context) -> dict:
    """Get or initialize the wizard state in user_data."""
    if context.user_data is None:
        # MagicMock-style test contexts may have user_data set explicitly.
        return {}
    w = context.user_data.get("wizard")
    if w is None:
        w = {
            "mode": "onboarding",
            "step": STEP_LANGUAGE,
            "data": {},
            "news_countries": set(),
            "custom_rss_name": None,
            "user_id": None,
        }
        context.user_data["wizard"] = w
    # Defensive: callers may have only created the dict shell.
    w.setdefault("data", {})
    w.setdefault("news_countries", set())
    w.setdefault("custom_rss_name", None)
    return w


def _t(key: str, lang: str, **kwargs) -> str:
    from i18n import t
    return t(key, lang, **kwargs)


def _resolve_lang(context) -> str:
    """Best-effort language for the current wizard step.

    Priority: in-progress wizard data → DB-stored language → "ru".
    """
    w = context.user_data.get("wizard", {}) if context.user_data else {}
    data = w.get("data") or {}
    if data.get("language"):
        return data["language"]
    user_id = w.get("user_id")
    if user_id is not None:
        try:
            from i18n import user_language
            return user_language(user_id)
        except Exception:
            pass
    return "ru"


async def _send(update: Update, text: str, reply_markup=None, parse_mode=None) -> None:
    """Send a message, working for both regular updates and callback queries."""
    if update.callback_query is not None:
        # On callback path, send a fresh message (we don't edit the original
        # button message — keeps the trail readable in the chat).
        try:
            await update.callback_query.message.reply_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode,
            )
        except Exception:
            # Fall back to effective_message
            await update.effective_message.reply_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode,
            )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode,
        )


def _internal_user_id_from_update(update: Update) -> Optional[int]:
    """Look up our internal users.id from the Telegram user on the update."""
    try:
        import db
        with db.session_scope() as s:
            u = db.get_user_by_telegram_id(s, update.effective_user.id)
            return u.id if u else None
    except Exception:
        return None


# ─── Step 0: language ─────────────────────────────────────────────────────────


def _build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="wiz:lang:ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="wiz:lang:en")],
        [InlineKeyboardButton("🇮🇱 עברית", callback_data="wiz:lang:he")],
    ])


async def step_language_prompt(update: Update, context) -> int:
    """Render the language picker. First step has no localized fallback —
    show the trilingual welcome on three lines so users see their own language.

    In profile mode, show Keep/Change first (using the user's current
    language as the current value).
    """
    w = _wizard_state(context)
    w["step"] = STEP_LANGUAGE
    w["user_id"] = w.get("user_id") or _internal_user_id_from_update(update)
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_LANGUAGE, "language", _step_lang_ask,
            label_key="wizard.profile.label.language",
        )
    return await _step_lang_ask(update, context)


async def _step_lang_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_LANGUAGE
    text = _t("wizard.lang.choose", "ru")
    await _send(update, text, reply_markup=_build_language_keyboard())
    return STEP_LANGUAGE


async def step_language_handle(update: Update, context) -> int:
    """User tapped a language button → save and advance to STEP_NAME."""
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    payload = (query.data or "").split(":")
    if len(payload) < 3:
        return STEP_LANGUAGE
    lang = payload[2]
    if lang not in ("ru", "en", "he"):
        return STEP_LANGUAGE
    w["data"]["language"] = lang

    # Persist immediately so subsequent steps render in chosen language.
    user_id = w.get("user_id")
    if user_id is not None:
        try:
            import db
            with db.session_scope() as s:
                u = s.get(db.User, user_id)
                if u is not None:
                    u.language = lang
        except Exception as e:
            logger.warning("language persist failed: %s", e)

    return await step_name_prompt(update, context)


# ─── Profile-mode helpers (Keep / Change UI) ──────────────────────────────────


def _keep_change_keyboard(lang: str, step_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(_t("wizard.keep", lang), callback_data=f"wiz:keep:{step_id}"),
        InlineKeyboardButton(_t("wizard.change", lang), callback_data=f"wiz:change:{step_id}"),
    ]])


def _current_value(user_id: Optional[int], field: str) -> Optional[str]:
    """Look up the current value of a users column (for profile-mode preview)."""
    if user_id is None:
        return None
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u is None:
                return None
            return getattr(u, field, None)
    except Exception:
        return None


_LANGUAGE_DISPLAY = {"ru": "Русский", "en": "English", "he": "עברית"}


def _format_field_value(field: str, value, lang: str) -> str:
    """Render a raw users-column value into something human readable."""
    if value is None or value == "":
        return "—"
    if field == "language":
        return _LANGUAGE_DISPLAY.get(value, value)
    if field == "news_country":
        try:
            parts = [p.strip() for p in str(value).split(",") if p.strip()]
            named = [_t(f"wizard.news.preset.{p}", lang) for p in parts]
            return ", ".join(named) if named else str(value)
        except Exception:
            return str(value)
    return str(value)


async def _maybe_profile_preview(
    update: Update, context, step_id: int, field: str, ask_fn,
    *, label_key: str,
) -> int:
    """In profile mode, show "<label>\\nCurrently: X [Keep][Change]" before the
    actual collection UI. In onboarding mode, fall straight through to ask_fn.
    """
    w = _wizard_state(context)
    if w.get("mode") != "profile":
        return await ask_fn(update, context)
    lang = _resolve_lang(context)
    value = _current_value(w.get("user_id"), field)
    pretty = _format_field_value(field, value, lang)
    label = _t(label_key, lang)
    current_line = _t("wizard.profile.current_is", lang, value=pretty)
    await _send(
        update,
        f"*{label}*\n{current_line}",
        reply_markup=_keep_change_keyboard(lang, step_id),
        parse_mode=ParseMode.MARKDOWN,
    )
    w["step"] = step_id
    return step_id


# ─── Step 1: name ─────────────────────────────────────────────────────────────


def _build_name_keyboard(first_name: str, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if first_name:
        rows.append([InlineKeyboardButton(
            _t("wizard.name.use_first_name", lang, name=first_name),
            callback_data=f"wiz:name:use_first",
        )])
    return InlineKeyboardMarkup(rows) if rows else None


async def step_name_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_NAME
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_NAME, "display_name", _step_name_ask,
            label_key="wizard.profile.label.name",
        )
    return await _step_name_ask(update, context)


async def _step_name_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_NAME
    lang = _resolve_lang(context)
    first_name = (update.effective_user.first_name or "").strip()
    kb = _build_name_keyboard(first_name, lang)
    await _send(update, _t("wizard.name.ask", lang), reply_markup=kb)
    return STEP_NAME


async def step_name_handle_button(update: Update, context) -> int:
    """User tapped 'Use <first_name>'."""
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    first_name = (update.effective_user.first_name or "").strip()
    if not first_name:
        return STEP_NAME
    w["data"]["display_name"] = first_name
    return await step_city_prompt(update, context)


async def step_name_handle_text(update: Update, context) -> int:
    w = _wizard_state(context)
    text = (update.effective_message.text or "").strip()
    if not text:
        return STEP_NAME
    w["data"]["display_name"] = text
    return await step_city_prompt(update, context)


# ─── Step 2: city ─────────────────────────────────────────────────────────────


async def step_city_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_CITY
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_CITY, "city", _step_city_ask,
            label_key="wizard.profile.label.city",
        )
    return await _step_city_ask(update, context)


async def _step_city_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_CITY
    lang = _resolve_lang(context)
    await _send(update, _t("wizard.city.ask", lang))
    return STEP_CITY


async def step_city_handle_text(update: Update, context) -> int:
    """Validate via weather_client; re-ask if not found."""
    w = _wizard_state(context)
    lang = _resolve_lang(context)
    raw = (update.effective_message.text or "").strip()
    if not raw:
        await _send(update, _t("wizard.city.ask", lang))
        return STEP_CITY
    import weather_client
    weather = None
    try:
        weather = weather_client.get_weather(city=raw)
    except Exception as e:
        logger.warning("weather lookup failed: %s", e)
    if not weather:
        await _send(update, _t("wizard.city.not_found", lang))
        return STEP_CITY
    w["data"]["city"] = raw
    await _send(update, _t("wizard.city.preview", lang, city=raw, weather=weather))
    return await step_timezone_prompt(update, context)


# ─── Step 3: timezone ─────────────────────────────────────────────────────────


def _build_timezone_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = []
    for tz in TZ_PRESETS:
        rows.append([InlineKeyboardButton(tz, callback_data=f"wiz:tz:{tz}")])
    rows.append([InlineKeyboardButton(_t("wizard.tz.other", lang), callback_data="wiz:tz:_other_")])
    return InlineKeyboardMarkup(rows)


async def step_timezone_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_TIMEZONE
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_TIMEZONE, "timezone", _step_timezone_ask,
            label_key="wizard.profile.label.timezone",
        )
    return await _step_timezone_ask(update, context)


async def _step_timezone_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_TIMEZONE
    lang = _resolve_lang(context)
    await _send(update, _t("wizard.tz.ask", lang), reply_markup=_build_timezone_keyboard(lang))
    return STEP_TIMEZONE


async def step_timezone_handle_button(update: Update, context) -> int:
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    lang = _resolve_lang(context)
    payload = (query.data or "").split(":", 2)
    if len(payload) < 3:
        return STEP_TIMEZONE
    value = payload[2]
    if value == "_other_":
        # Prompt user to type a timezone.
        w["awaiting"] = "timezone_text"
        await _send(update, _t("wizard.tz.type", lang))
        return STEP_TIMEZONE
    try:
        pytz.timezone(value)
    except pytz.UnknownTimeZoneError:
        await _send(update, _t("wizard.tz.invalid", lang))
        return STEP_TIMEZONE
    w["data"]["timezone"] = value
    w.pop("awaiting", None)
    return await step_morning_time_prompt(update, context)


async def step_timezone_handle_text(update: Update, context) -> int:
    w = _wizard_state(context)
    lang = _resolve_lang(context)
    raw = (update.effective_message.text or "").strip()
    try:
        pytz.timezone(raw)
    except pytz.UnknownTimeZoneError:
        await _send(update, _t("wizard.tz.invalid", lang))
        return STEP_TIMEZONE
    w["data"]["timezone"] = raw
    w.pop("awaiting", None)
    return await step_morning_time_prompt(update, context)


# ─── Step 4 / 5: morning_time / evening_time ──────────────────────────────────


def _build_time_keyboard(presets: list[str], slot: str, lang: str) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(p, callback_data=f"wiz:time:{slot}:{p}") for p in presets]
    other = InlineKeyboardButton(_t("wizard.time.other", lang), callback_data=f"wiz:time:{slot}:_other_")
    # Place 5 presets on first row, "Other" on second row for readability.
    return InlineKeyboardMarkup([row, [other]])


async def step_morning_time_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_MORNING_TIME
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_MORNING_TIME, "morning_time", _step_morning_ask,
            label_key="wizard.profile.label.morning_time",
        )
    return await _step_morning_ask(update, context)


async def _step_morning_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_MORNING_TIME
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.time.ask_morning", lang),
        reply_markup=_build_time_keyboard(MORNING_TIME_PRESETS, "morning", lang),
    )
    return STEP_MORNING_TIME


async def step_evening_time_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_EVENING_TIME
    if w.get("mode") == "profile":
        return await _maybe_profile_preview(
            update, context, STEP_EVENING_TIME, "evening_time", _step_evening_ask,
            label_key="wizard.profile.label.evening_time",
        )
    return await _step_evening_ask(update, context)


async def _step_evening_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_EVENING_TIME
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.time.ask_evening", lang),
        reply_markup=_build_time_keyboard(EVENING_TIME_PRESETS, "evening", lang),
    )
    return STEP_EVENING_TIME


async def step_time_handle_button(update: Update, context) -> int:
    """Shared handler for both morning & evening preset taps."""
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    lang = _resolve_lang(context)
    payload = (query.data or "").split(":", 3)
    if len(payload) < 4:
        return w.get("step", STEP_MORNING_TIME)
    slot = payload[2]  # 'morning' or 'evening'
    value = payload[3]
    field = "morning_time" if slot == "morning" else "evening_time"

    if value == "_other_":
        w["awaiting"] = f"time_{slot}_text"
        await _send(update, _t("wizard.time.type", lang))
        return STEP_MORNING_TIME if slot == "morning" else STEP_EVENING_TIME

    if not _TIME_RE.match(value):
        await _send(update, _t("wizard.time.invalid", lang))
        return STEP_MORNING_TIME if slot == "morning" else STEP_EVENING_TIME

    w["data"][field] = value
    w.pop("awaiting", None)
    if slot == "morning":
        return await step_evening_time_prompt(update, context)
    return await step_google_prompt(update, context)


async def step_time_handle_text(update: Update, context) -> int:
    """Handle typed time after the user picked "Other"."""
    w = _wizard_state(context)
    lang = _resolve_lang(context)
    raw = (update.effective_message.text or "").strip()
    if not _TIME_RE.match(raw):
        await _send(update, _t("wizard.time.invalid", lang))
        return w.get("step", STEP_MORNING_TIME)

    awaiting = w.get("awaiting", "")
    if "morning" in awaiting:
        w["data"]["morning_time"] = raw
        w.pop("awaiting", None)
        return await step_evening_time_prompt(update, context)
    elif "evening" in awaiting:
        w["data"]["evening_time"] = raw
        w.pop("awaiting", None)
        return await step_google_prompt(update, context)
    # Defensive fallback.
    return w.get("step", STEP_MORNING_TIME)


# ─── Step 6: Google OAuth ─────────────────────────────────────────────────────


def _build_google_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t("wizard.google.done", lang), callback_data="wiz:google:done")],
        [InlineKeyboardButton(_t("wizard.google.skip", lang), callback_data="wiz:google:skip")],
    ])


async def step_google_prompt(update: Update, context) -> int:
    """Show the Google OAuth URL and advance on button tap.

    In profile mode, show Keep/Change first — we don't want to spam an
    already-connected user with a fresh consent URL on every /profile.
    """
    w = _wizard_state(context)
    w["step"] = STEP_GOOGLE
    if w.get("mode") == "profile":
        lang = _resolve_lang(context)
        # No DB column for Google connection — just show a yes/no badge.
        user_id = w.get("user_id")
        connected = False
        if user_id is not None:
            try:
                import db
                with db.session_scope() as s:
                    connected = s.get(db.GoogleToken, user_id) is not None
            except Exception:
                pass
        current = "✅" if connected else "⚪"
        label = _t("wizard.profile.label.google", lang)
        current_line = _t("wizard.profile.current_is", lang, value=current)
        await _send(
            update,
            f"*{label}*\n{current_line}",
            reply_markup=_keep_change_keyboard(lang, STEP_GOOGLE),
            parse_mode=ParseMode.MARKDOWN,
        )
        return STEP_GOOGLE
    return await _step_google_ask(update, context)


async def _step_google_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_GOOGLE
    lang = _resolve_lang(context)
    user_id = w.get("user_id") or _internal_user_id_from_update(update)
    w["user_id"] = user_id
    url = "(no URL — user not in DB)"
    if user_id is not None:
        try:
            import web
            base = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
            url = f"{base}/oauth/start?state={web.sign_state(user_id)}"
        except Exception as e:
            logger.warning("OAuth URL build failed: %s", e)
    await _send(
        update,
        _t("wizard.google.ask", lang, url=url),
        reply_markup=_build_google_keyboard(lang),
    )
    return STEP_GOOGLE


async def step_google_handle_button(update: Update, context) -> int:
    """User clicked either Done or Skip — advance either way."""
    query = update.callback_query
    await query.answer()
    return await step_news_prompt(update, context)


# ─── Step 7: news multi-select ────────────────────────────────────────────────


def _build_news_keyboard(chosen: set, lang: str) -> InlineKeyboardMarkup:
    """Render the news country chips. Picked items get a leading ✓."""
    rows = []
    presets = ["IL", "RU", "UA", "US", "EU", "World"]
    for c in presets:
        label = _t(f"wizard.news.preset.{c}", lang)
        if c in chosen:
            label = "✓ " + label
        rows.append([InlineKeyboardButton(label, callback_data=f"wiz:news:toggle:{c}")])
    rows.append([InlineKeyboardButton(_t("wizard.news.custom", lang), callback_data="wiz:news:custom")])
    rows.append([InlineKeyboardButton(_t("wizard.news.none", lang), callback_data="wiz:news:none")])
    rows.append([InlineKeyboardButton(_t("btn.done", lang), callback_data="wiz:news:done")])
    return InlineKeyboardMarkup(rows)


async def step_news_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_NEWS
    # In profile mode there's no clean single-value preview for a multi-select.
    # Show the current set as a comma-joined string instead.
    if w.get("mode") == "profile":
        lang = _resolve_lang(context)
        raw = _current_value(w.get("user_id"), "news_country")
        pretty = _format_field_value("news_country", raw, lang)
        label = _t("wizard.profile.label.news", lang)
        current_line = _t("wizard.profile.current_is", lang, value=pretty)
        await _send(
            update,
            f"*{label}*\n{current_line}",
            reply_markup=_keep_change_keyboard(lang, STEP_NEWS),
            parse_mode=ParseMode.MARKDOWN,
        )
        return STEP_NEWS
    return await _step_news_ask(update, context)


async def _step_news_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_NEWS
    lang = _resolve_lang(context)
    chosen = w.get("news_countries") or set()
    await _send(
        update,
        _t("wizard.news.ask", lang),
        reply_markup=_build_news_keyboard(chosen, lang),
    )
    return STEP_NEWS


async def step_news_handle_button(update: Update, context) -> int:
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    lang = _resolve_lang(context)
    payload = (query.data or "").split(":", 3)
    if len(payload) < 3:
        return STEP_NEWS
    action = payload[2]

    chosen: set = w.setdefault("news_countries", set())

    if action == "toggle" and len(payload) >= 4:
        c = payload[3]
        if c in chosen:
            chosen.remove(c)
        else:
            chosen.add(c)
        await _send(
            update,
            _t("wizard.news.ask", lang),
            reply_markup=_build_news_keyboard(chosen, lang),
        )
        return STEP_NEWS

    if action == "custom":
        w["awaiting"] = "rss_name"
        w["custom_rss_name"] = None
        await _send(update, _t("wizard.news.custom_name", lang))
        return STEP_NEWS

    if action == "none":
        # Clear the set; user explicitly wants no news.
        w["news_countries"] = set()
        w["data"]["news_country"] = ""
        return await step_personality_prompt(update, context)

    if action == "done":
        # Persist the multi-select + any custom RSS rows.
        country_csv = ",".join(sorted(chosen)) if chosen else ""
        w["data"]["news_country"] = country_csv
        return await step_personality_prompt(update, context)

    return STEP_NEWS


async def step_news_handle_text(update: Update, context) -> int:
    """Free-text input is used for custom RSS name → URL flow."""
    w = _wizard_state(context)
    lang = _resolve_lang(context)
    raw = (update.effective_message.text or "").strip()
    awaiting = w.get("awaiting")

    if awaiting == "rss_name":
        if not raw:
            await _send(update, _t("wizard.news.custom_name", lang))
            return STEP_NEWS
        w["custom_rss_name"] = raw
        w["awaiting"] = "rss_url"
        await _send(update, _t("wizard.news.custom_url", lang))
        return STEP_NEWS

    if awaiting == "rss_url":
        if not _URL_RE.match(raw):
            await _send(update, _t("wizard.news.custom_invalid", lang))
            return STEP_NEWS
        custom_list = w.setdefault("custom_rss", [])
        custom_list.append({"source_name": w.get("custom_rss_name") or "Custom RSS", "url": raw})
        w["custom_rss_name"] = None
        w["awaiting"] = None
        chosen = w.get("news_countries") or set()
        await _send(
            update,
            _t("wizard.news.ask", lang),
            reply_markup=_build_news_keyboard(chosen, lang),
        )
        return STEP_NEWS

    # Anything else at news step → re-show menu.
    chosen = w.get("news_countries") or set()
    await _send(
        update,
        _t("wizard.news.ask", lang),
        reply_markup=_build_news_keyboard(chosen, lang),
    )
    return STEP_NEWS


# ─── Step 8: personality ──────────────────────────────────────────────────────


def _build_personality_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_t("wizard.personality.warm", lang), callback_data="wiz:persona:warm")],
        [InlineKeyboardButton(_t("wizard.personality.business", lang), callback_data="wiz:persona:business")],
        [InlineKeyboardButton(_t("wizard.personality.funny", lang), callback_data="wiz:persona:funny")],
        [InlineKeyboardButton(_t("wizard.personality.calm", lang), callback_data="wiz:persona:calm")],
    ]
    return InlineKeyboardMarkup(rows)


async def step_personality_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_PERSONALITY
    if w.get("mode") == "profile":
        lang = _resolve_lang(context)
        # Personality has no DB column — read the persona file if it exists.
        user_id = w.get("user_id")
        current = "—"
        if user_id is not None:
            try:
                import db
                path = Path(db.data_dir(), "users", str(user_id), "personality.json")
                if path.exists():
                    persona = json.loads(path.read_text(encoding="utf-8"))
                    current = persona.get("name", "—")
            except Exception:
                pass
        label = _t("wizard.profile.label.personality", lang)
        current_line = _t("wizard.profile.current_is", lang, value=current)
        await _send(
            update,
            f"*{label}*\n{current_line}",
            reply_markup=_keep_change_keyboard(lang, STEP_PERSONALITY),
            parse_mode=ParseMode.MARKDOWN,
        )
        return STEP_PERSONALITY
    return await _step_personality_ask(update, context)


async def _step_personality_ask(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_PERSONALITY
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.personality.ask", lang),
        reply_markup=_build_personality_keyboard(lang),
    )
    return STEP_PERSONALITY


async def step_personality_handle_button(update: Update, context) -> int:
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    payload = (query.data or "").split(":", 2)
    if len(payload) < 3:
        return STEP_PERSONALITY
    preset_key = payload[2]
    w["data"]["personality_preset"] = preset_key
    return await step_first_task_prompt(update, context)


# ─── Step 9: first task ───────────────────────────────────────────────────────


def _build_skip_keyboard(lang: str, slot: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(_t("wizard.skip", lang), callback_data=f"wiz:skip:{slot}"),
    ]])


async def step_first_task_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_FIRST_TASK
    if w.get("mode") == "profile":
        # In profile mode, skip these steps entirely — they're one-time
        # onboarding niceties, not editable preferences.
        return await step_first_goal_prompt(update, context)
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.task.ask", lang),
        reply_markup=_build_skip_keyboard(lang, "task"),
    )
    return STEP_FIRST_TASK


async def step_first_task_handle_text(update: Update, context) -> int:
    w = _wizard_state(context)
    text = (update.effective_message.text or "").strip()
    if not text:
        return STEP_FIRST_TASK
    w["data"]["first_task"] = text
    return await step_first_goal_prompt(update, context)


async def step_first_task_handle_skip(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    return await step_first_goal_prompt(update, context)


# ─── Step 10: first goal ──────────────────────────────────────────────────────


async def step_first_goal_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_FIRST_GOAL
    if w.get("mode") == "profile":
        return await step_integrations_prompt(update, context)
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.goal.ask", lang),
        reply_markup=_build_skip_keyboard(lang, "goal"),
    )
    return STEP_FIRST_GOAL


async def step_first_goal_handle_text(update: Update, context) -> int:
    w = _wizard_state(context)
    text = (update.effective_message.text or "").strip()
    if not text:
        return STEP_FIRST_GOAL
    w["data"]["first_goal"] = text
    return await step_integrations_prompt(update, context)


async def step_first_goal_handle_skip(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    return await step_integrations_prompt(update, context)


# ─── Step 11: integrations ────────────────────────────────────────────────────


def _build_integrations_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("💬 WhatsApp", callback_data="settings:open:whatsapp")],
        [InlineKeyboardButton("📱 Pushover", callback_data="settings:open:pushover")],
        [InlineKeyboardButton("🔊 Yandex Алиса", callback_data="settings:open:alice")],
        [InlineKeyboardButton("💡 Tuya", callback_data="settings:open:tuya")],
        [InlineKeyboardButton("🔌 VeSync", callback_data="settings:open:vesync")],
        [InlineKeyboardButton("📓 Дневник в Docs", callback_data="settings:open:diary_doc")],
        [InlineKeyboardButton(_t("wizard.integrations.done", lang), callback_data="wiz:integrations:done")],
    ]
    return InlineKeyboardMarkup(rows)


async def step_integrations_prompt(update: Update, context) -> int:
    w = _wizard_state(context)
    w["step"] = STEP_INTEGRATIONS
    lang = _resolve_lang(context)
    await _send(
        update,
        _t("wizard.integrations.ask", lang),
        reply_markup=_build_integrations_keyboard(lang),
    )
    return STEP_INTEGRATIONS


async def step_integrations_handle_done(update: Update, context) -> int:
    query = update.callback_query
    await query.answer()
    return await finalize_wizard(update, context)


# ─── Finalize: atomic flush to DB ─────────────────────────────────────────────


async def finalize_wizard(update: Update, context) -> int:
    """Write all collected fields to the DB in one session_scope.

    In onboarding mode, marks users.onboarding_state='completed' and sends
    the 'all set' message; in profile mode, sends a shorter 'profile
    updated' message.
    """
    w = _wizard_state(context)
    data = w.get("data") or {}
    user_id = w.get("user_id") or _internal_user_id_from_update(update)
    lang = _resolve_lang(context)
    mode = w.get("mode", "onboarding")

    # Resolve a few computed values BEFORE opening the session so an
    # exception in calendar_client / db.create_goal doesn't poison the
    # session commit.
    today = datetime.date.today()
    first_task_text = data.get("first_task")
    first_goal_text = data.get("first_goal")
    personality_preset = data.get("personality_preset")

    # ── Open the single atomic transaction for all DB writes ─────────────
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id) if user_id is not None else None
            if u is not None:
                if "language" in data:
                    u.language = data["language"]
                if "display_name" in data:
                    u.display_name = data["display_name"]
                if "city" in data:
                    u.city = data["city"]
                if "timezone" in data:
                    u.timezone = data["timezone"]
                if "morning_time" in data:
                    u.morning_time = data["morning_time"]
                if "evening_time" in data:
                    u.evening_time = data["evening_time"]
                if "news_country" in data:
                    u.news_country = data["news_country"] or None
                if mode == "onboarding":
                    u.onboarding_state = "completed"

            # News feeds: replace the user's current set when the wizard
            # provided a choice (could be empty → "no news").
            if user_id is not None and "news_country" in data:
                # Wipe existing rows; rebuild from presets + custom.
                s.query(db.UserNewsFeed).filter_by(user_id=user_id).delete()
                from news_presets import NEWS_PRESETS
                country_csv = data.get("news_country") or ""
                for c in [x for x in country_csv.split(",") if x]:
                    for name, url in NEWS_PRESETS.get(c, []):
                        s.add(db.UserNewsFeed(
                            user_id=user_id, source_name=name, url=url, enabled=1,
                        ))
                for custom in w.get("custom_rss") or []:
                    s.add(db.UserNewsFeed(
                        user_id=user_id,
                        source_name=custom["source_name"],
                        url=custom["url"],
                        enabled=1,
                    ))

            # First goal — create inside the same session.
            if first_goal_text and user_id is not None:
                db.create_goal(
                    s,
                    user_id=user_id,
                    title=first_goal_text,
                    target_date=today + datetime.timedelta(days=90),
                )
    except Exception as e:
        logger.exception("wizard finalize DB write failed: %s", e)

    # ── Personality file: written outside the DB session ─────────────────
    if personality_preset and user_id is not None:
        try:
            import db
            from personality_presets import build_persona
            display_name = data.get("display_name") or _current_value(user_id, "display_name") or ""
            persona = build_persona(personality_preset, lang, display_name)
            persona_dir = Path(db.data_dir(), "users", str(user_id))
            persona_dir.mkdir(parents=True, exist_ok=True)
            (persona_dir / "personality.json").write_text(
                json.dumps(persona, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("personality.json write failed: %s", e)

    # ── First task: calendar_client.add_task (Google API call) ───────────
    if first_task_text and user_id is not None:
        try:
            import calendar_client
            calendar_client.add_task(
                first_task_text,
                "short",
                due_date=today + datetime.timedelta(days=3),
                user_id=user_id,
            )
        except Exception as e:
            logger.warning("first task creation failed: %s", e)

    # ── Final message ──────────────────────────────────────────────────────
    display_name = data.get("display_name") or _current_value(user_id, "display_name") or ""
    if mode == "profile":
        await _send(update, _t("wizard.profile.done", lang))
    else:
        await _send(update, _t("wizard.done", lang, name=display_name))

    # Clear wizard state.
    if context.user_data is not None:
        context.user_data.pop("wizard", None)
    return ConversationHandler.END


# ─── Keep / Change callback (profile mode) ────────────────────────────────────


async def cb_keep_or_change(update: Update, context) -> int:
    """Handle the profile-mode Keep/Change buttons.

    On Keep → advance to the next step without overwriting the DB value.
    On Change → run the normal collection UI for the same step.
    """
    w = _wizard_state(context)
    query = update.callback_query
    await query.answer()
    payload = (query.data or "").split(":", 2)
    if len(payload) < 3:
        return w.get("step", STEP_LANGUAGE)
    action = payload[1]  # 'keep' or 'change'
    try:
        step_id = int(payload[2])
    except ValueError:
        return w.get("step", STEP_LANGUAGE)

    if action == "keep":
        # Skip to next step without writing anything for this step.
        return await _advance_to(update, context, step_id)
    if action == "change":
        return await _run_change_ui(update, context, step_id)
    return w.get("step", STEP_LANGUAGE)


async def _advance_to(update: Update, context, current_step: int) -> int:
    """In profile mode, jump from current_step to the next prompt."""
    transitions = {
        STEP_LANGUAGE: step_name_prompt,
        STEP_NAME: step_city_prompt,
        STEP_CITY: step_timezone_prompt,
        STEP_TIMEZONE: step_morning_time_prompt,
        STEP_MORNING_TIME: step_evening_time_prompt,
        STEP_EVENING_TIME: step_google_prompt,
        STEP_GOOGLE: step_news_prompt,
        STEP_NEWS: step_personality_prompt,
        STEP_PERSONALITY: step_first_task_prompt,
        STEP_FIRST_TASK: step_first_goal_prompt,
        STEP_FIRST_GOAL: step_integrations_prompt,
        STEP_INTEGRATIONS: finalize_wizard,
    }
    fn = transitions.get(current_step)
    if fn is None:
        return await finalize_wizard(update, context)
    return await fn(update, context)


async def _run_change_ui(update: Update, context, current_step: int) -> int:
    """In profile mode + user tapped 'Change' → present the collection UI
    that step would show in onboarding mode."""
    asks = {
        STEP_LANGUAGE: _step_lang_change_ask,
        STEP_NAME: _step_name_ask,
        STEP_CITY: _step_city_ask,
        STEP_TIMEZONE: _step_timezone_ask,
        STEP_MORNING_TIME: _step_morning_ask,
        STEP_EVENING_TIME: _step_evening_ask,
        STEP_GOOGLE: _step_google_ask,
        STEP_NEWS: _step_news_ask,
        STEP_PERSONALITY: _step_personality_ask,
    }
    fn = asks.get(current_step)
    if fn is None:
        return current_step
    return await fn(update, context)


async def _step_lang_change_ask(update: Update, context) -> int:
    """Profile mode: 'Change' on language step → show the language picker
    directly (bypassing the Keep/Change preview wrapper)."""
    return await _step_lang_ask(update, context)


# ─── Entrypoints / driving the wizard ─────────────────────────────────────────


async def start_wizard(update: Update, context, *, mode: str = "onboarding") -> int:
    """Initialize wizard state and render step 0."""
    if context.user_data is not None:
        context.user_data["wizard"] = {
            "mode": mode,
            "step": STEP_LANGUAGE,
            "data": {},
            "news_countries": set(),
            "custom_rss_name": None,
            "user_id": _internal_user_id_from_update(update),
            "custom_rss": [],
        }
    return await step_language_prompt(update, context)
