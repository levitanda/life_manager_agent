"""Tests for Phase E (12-step onboarding wizard) and Phase F (/profile).

The wizard exposes a flat set of per-step handler functions in
`onboarding_wizard`. Each test drives the engine directly — building fake
Update/Context objects and asserting on the side effects (DB rows, file
writes, message contents).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://test.example")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


def _mk_user(tg=42, *, name="Daria", username="daria", language="ru",
             subscription_status="promo"):
    import db
    with db.session_scope() as s:
        u = db.create_user(
            s,
            telegram_user_id=tg,
            telegram_chat_id=tg,
            display_name=name,
            telegram_username=username,
        )
        u.subscription_status = subscription_status
        u.language = language
        u.timezone = "Europe/Moscow"
        u.city = None
        u.morning_time = "06:30"
        u.evening_time = "21:30"
        u.onboarding_state = "pending"
        return u.id


def _mk_update(tg=42, *, first_name="Daria", text=None, callback_data=None):
    """Build a MagicMock Update for the wizard handler functions."""
    upd = MagicMock()
    upd.effective_user.id = tg
    upd.effective_user.first_name = first_name
    upd.effective_user.username = "daria"
    upd.effective_chat.id = tg
    upd.effective_message.reply_text = AsyncMock()
    upd.message = upd.effective_message
    if text is not None:
        upd.effective_message.text = text
    if callback_data is not None:
        cq = MagicMock()
        cq.data = callback_data
        cq.answer = AsyncMock()
        cq.message.reply_text = AsyncMock()
        upd.callback_query = cq
    else:
        upd.callback_query = None
    return upd


def _mk_ctx(mode="onboarding", user_id=None, *, with_state=True):
    """Build a Telegram-style context with user_data populated."""
    ctx = MagicMock()
    ctx.user_data = {}
    ctx.args = []
    if with_state:
        ctx.user_data["wizard"] = {
            "mode": mode,
            "step": 0,
            "data": {},
            "news_countries": set(),
            "custom_rss_name": None,
            "custom_rss": [],
            "user_id": user_id,
        }
    return ctx


# ─── Step 0: language ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step0_language_select_persists():
    uid = _mk_user()
    import onboarding_wizard as w, db

    upd = _mk_update(callback_data="wiz:lang:en")
    ctx = _mk_ctx(user_id=uid)
    await w.step_language_handle(upd, ctx)

    with db.session_scope() as s:
        u = s.get(db.User, uid)
        assert u.language == "en"


@pytest.mark.asyncio
async def test_step0_language_localized_buttons():
    """Trilingual welcome prompt rendered with all three labels (RU/EN/HE)."""
    uid = _mk_user()
    import onboarding_wizard as w

    upd = _mk_update()
    ctx = _mk_ctx(user_id=uid)
    await w.step_language_prompt(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    # The trilingual prompt must mention all three locale words/scripts.
    assert "English" in text or "Choose" in text
    assert "Выберите" in text or "язык" in text
    assert "בחר" in text or "שפה" in text


# ─── Step 1: name ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step1_name_uses_first_name_button():
    uid = _mk_user()
    import onboarding_wizard as w

    upd = _mk_update(first_name="Daria", callback_data="wiz:name:use_first")
    ctx = _mk_ctx(user_id=uid)
    await w.step_name_handle_button(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["display_name"] == "Daria"


# ─── Step 2: city ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step2_city_invalid_re_asks():
    uid = _mk_user()
    import onboarding_wizard as w
    upd = _mk_update(text="Atlantis")
    ctx = _mk_ctx(user_id=uid)
    with patch("weather_client.get_weather", return_value=None):
        state = await w.step_city_handle_text(upd, ctx)
    assert state == w.STEP_CITY
    assert "city" not in ctx.user_data["wizard"]["data"]
    # Re-prompted (one message — "city not found")
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "не нашёл" in text.lower() or "couldn't find" in text.lower()


@pytest.mark.asyncio
async def test_step2_city_valid_advances():
    uid = _mk_user()
    import onboarding_wizard as w
    upd = _mk_update(text="Neshe")
    ctx = _mk_ctx(user_id=uid)
    with patch("weather_client.get_weather", return_value="+24°C, sunny"):
        state = await w.step_city_handle_text(upd, ctx)
    # Advanced to timezone step
    assert state == w.STEP_TIMEZONE
    assert ctx.user_data["wizard"]["data"]["city"] == "Neshe"


# ─── Step 3: timezone ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step3_tz_button_persists():
    uid = _mk_user()
    import onboarding_wizard as w
    upd = _mk_update(callback_data="wiz:tz:Asia/Tokyo")
    ctx = _mk_ctx(user_id=uid)
    state = await w.step_timezone_handle_button(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["timezone"] == "Asia/Tokyo"
    assert state == w.STEP_MORNING_TIME


@pytest.mark.asyncio
async def test_step3_tz_other_validates_with_pytz():
    uid = _mk_user()
    import onboarding_wizard as w
    # Type an invalid timezone first
    upd = _mk_update(text="Not/Real")
    ctx = _mk_ctx(user_id=uid)
    state = await w.step_timezone_handle_text(upd, ctx)
    assert state == w.STEP_TIMEZONE
    assert "timezone" not in ctx.user_data["wizard"]["data"]

    # Now a valid one
    upd = _mk_update(text="Asia/Tokyo")
    state = await w.step_timezone_handle_text(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["timezone"] == "Asia/Tokyo"
    assert state == w.STEP_MORNING_TIME


# ─── Step 4: morning_time ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step4_morning_time_preset_persists():
    uid = _mk_user()
    import onboarding_wizard as w
    upd = _mk_update(callback_data="wiz:time:morning:07:00")
    ctx = _mk_ctx(user_id=uid)
    state = await w.step_time_handle_button(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["morning_time"] == "07:00"
    assert state == w.STEP_EVENING_TIME


# ─── Step 6: Google OAuth ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step6_google_done_advances_even_without_token():
    uid = _mk_user()
    import onboarding_wizard as w, db
    # Ensure no GoogleToken row exists
    with db.session_scope() as s:
        assert s.get(db.GoogleToken, uid) is None

    upd = _mk_update(callback_data="wiz:google:done")
    ctx = _mk_ctx(user_id=uid)
    state = await w.step_google_handle_button(upd, ctx)
    assert state == w.STEP_NEWS


@pytest.mark.asyncio
async def test_step6_google_skip_advances():
    uid = _mk_user()
    import onboarding_wizard as w
    upd = _mk_update(callback_data="wiz:google:skip")
    ctx = _mk_ctx(user_id=uid)
    state = await w.step_google_handle_button(upd, ctx)
    assert state == w.STEP_NEWS


# ─── Step 7: news multi-select ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step7_news_preset_writes_user_news_feeds():
    uid = _mk_user()
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    # Toggle IL on
    upd = _mk_update(callback_data="wiz:news:toggle:IL")
    await w.step_news_handle_button(upd, ctx)
    # Toggle RU on
    upd = _mk_update(callback_data="wiz:news:toggle:RU")
    await w.step_news_handle_button(upd, ctx)
    # Click Done — finalize via finalize_wizard so DB writes happen
    upd = _mk_update(callback_data="wiz:news:done")
    # After "done" the wizard advances to personality; finalize doesn't happen
    # yet, so we directly call finalize after a partial fill to test the
    # NEWS_PRESETS expansion.
    # Set required user fields so finalize doesn't choke on missing values
    with db.session_scope() as s:
        u = s.get(db.User, uid)
        u.display_name = "Test"
    # Pretend we collected through every step except news_country, then trigger done
    # The button handler will set news_country in wizard data, then advance.
    state = await w.step_news_handle_button(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["news_country"] == "IL,RU"

    # Now invoke finalize_wizard directly with the populated state
    # (skip going through personality / first_task / first_goal)
    await w.finalize_wizard(upd, ctx)
    with db.session_scope() as s:
        rows = s.query(db.UserNewsFeed).filter_by(user_id=uid).all()
        assert len(rows) == 4
        urls = {r.url for r in rows}
        assert "https://www.ynet.co.il/Integration/StoryRss2.xml" in urls
        assert "https://meduza.io/rss/all" in urls
        u = s.get(db.User, uid)
        assert u.news_country == "IL,RU"


@pytest.mark.asyncio
async def test_step7_news_custom_rss_creates_row():
    uid = _mk_user()
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    # User clicks "Custom RSS"
    upd = _mk_update(callback_data="wiz:news:custom")
    await w.step_news_handle_button(upd, ctx)
    # Sends name
    upd = _mk_update(text="MyBlog")
    await w.step_news_handle_text(upd, ctx)
    # Sends URL
    upd = _mk_update(text="https://example.com/rss")
    await w.step_news_handle_text(upd, ctx)
    # Clicks Done
    upd = _mk_update(callback_data="wiz:news:done")
    with db.session_scope() as s:
        u = s.get(db.User, uid)
        u.display_name = "Test"
    await w.step_news_handle_button(upd, ctx)
    # Finalize to write feeds
    await w.finalize_wizard(upd, ctx)
    with db.session_scope() as s:
        rows = s.query(db.UserNewsFeed).filter_by(user_id=uid).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.source_name == "MyBlog"
        assert r.url == "https://example.com/rss"


@pytest.mark.asyncio
async def test_step7_no_news_advances_without_rows():
    uid = _mk_user()
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    upd = _mk_update(callback_data="wiz:news:none")
    state = await w.step_news_handle_button(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["news_country"] == ""
    # Finalize to write feeds
    with db.session_scope() as s:
        u = s.get(db.User, uid)
        u.display_name = "Test"
    await w.finalize_wizard(upd, ctx)
    with db.session_scope() as s:
        rows = s.query(db.UserNewsFeed).filter_by(user_id=uid).all()
        assert len(rows) == 0


# ─── Step 8: personality ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step8_personality_writes_persona_json(tmp_path):
    uid = _mk_user(language="ru", name="Daria")
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    ctx.user_data["wizard"]["data"]["display_name"] = "Daria"
    upd = _mk_update(callback_data="wiz:persona:warm")
    await w.step_personality_handle_button(upd, ctx)
    # Persona is written during finalize_wizard, not in the handler itself.
    await w.finalize_wizard(upd, ctx)
    persona_path = Path(db.data_dir(), "users", str(uid), "personality.json")
    assert persona_path.exists()
    persona = json.loads(persona_path.read_text(encoding="utf-8"))
    assert persona["name"] == "тёплый помощник"
    assert persona["warmth"] == 90
    assert persona.get("user_name") == "Daria"


# ─── Step 9: first task ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step9_first_task_creates_short_task():
    uid = _mk_user(name="X")
    import onboarding_wizard as w
    ctx = _mk_ctx(user_id=uid)
    ctx.user_data["wizard"]["data"]["display_name"] = "X"
    upd = _mk_update(text="Buy milk")
    await w.step_first_task_handle_text(upd, ctx)
    assert ctx.user_data["wizard"]["data"]["first_task"] == "Buy milk"
    # First task creation happens during finalize_wizard.
    with patch("calendar_client.add_task") as mock_add:
        await w.finalize_wizard(upd, ctx)
    mock_add.assert_called_once()
    call_args = mock_add.call_args
    # Positional args: (title, task_type)
    assert call_args.args[0] == "Buy milk"
    assert call_args.args[1] == "short"
    # Due date 3 days out
    today = datetime.date.today()
    assert call_args.kwargs.get("due_date") == today + datetime.timedelta(days=3)
    assert call_args.kwargs.get("user_id") == uid


@pytest.mark.asyncio
async def test_step9_skip_creates_no_task():
    uid = _mk_user(name="X")
    import onboarding_wizard as w
    ctx = _mk_ctx(user_id=uid)
    ctx.user_data["wizard"]["data"]["display_name"] = "X"
    upd = _mk_update(callback_data="wiz:skip:task")
    await w.step_first_task_handle_skip(upd, ctx)
    with patch("calendar_client.add_task") as mock_add:
        await w.finalize_wizard(upd, ctx)
    mock_add.assert_not_called()


# ─── Step 10: first goal ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step10_first_goal_creates_goal_row():
    uid = _mk_user(name="X")
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    ctx.user_data["wizard"]["data"]["display_name"] = "X"
    upd = _mk_update(text="Learn Italian")
    await w.step_first_goal_handle_text(upd, ctx)
    await w.finalize_wizard(upd, ctx)
    with db.session_scope() as s:
        goals = s.query(db.Goal).filter_by(user_id=uid).all()
        assert len(goals) == 1
        assert goals[0].title == "Learn Italian"
        today = datetime.date.today()
        assert goals[0].target_date == today + datetime.timedelta(days=90)


# ─── Step 11: integrations ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step11_integrations_done_finalizes():
    uid = _mk_user(name="X")
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)
    ctx.user_data["wizard"]["data"]["display_name"] = "X"
    upd = _mk_update(callback_data="wiz:integrations:done")
    await w.step_integrations_handle_done(upd, ctx)
    with db.session_scope() as s:
        u = s.get(db.User, uid)
        assert u.onboarding_state == "completed"


# ─── Full pass through every step ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_wizard_atomic_writes_all_fields():
    """Drive every step end-to-end and confirm the user row is fully populated."""
    uid = _mk_user(name="Daria")
    import onboarding_wizard as w, db
    ctx = _mk_ctx(user_id=uid)

    # Step 0: language
    await w.step_language_handle(
        _mk_update(callback_data="wiz:lang:ru"), ctx,
    )
    # Step 1: name (typed text)
    await w.step_name_handle_text(_mk_update(text="Daria"), ctx)
    # Step 2: city (mock weather)
    with patch("weather_client.get_weather", return_value="+24°C"):
        await w.step_city_handle_text(_mk_update(text="Neshe"), ctx)
    # Step 3: timezone
    await w.step_timezone_handle_button(
        _mk_update(callback_data="wiz:tz:Asia/Jerusalem"), ctx,
    )
    # Step 4: morning time
    await w.step_time_handle_button(
        _mk_update(callback_data="wiz:time:morning:07:00"), ctx,
    )
    # Step 5: evening time
    await w.step_time_handle_button(
        _mk_update(callback_data="wiz:time:evening:22:00"), ctx,
    )
    # Step 6: Google OAuth — Done
    await w.step_google_handle_button(
        _mk_update(callback_data="wiz:google:done"), ctx,
    )
    # Step 7: news — pick IL + Done
    await w.step_news_handle_button(
        _mk_update(callback_data="wiz:news:toggle:IL"), ctx,
    )
    await w.step_news_handle_button(
        _mk_update(callback_data="wiz:news:done"), ctx,
    )
    # Step 8: personality
    await w.step_personality_handle_button(
        _mk_update(callback_data="wiz:persona:business"), ctx,
    )
    # Step 9: first task
    with patch("calendar_client.add_task") as mock_add:
        await w.step_first_task_handle_text(_mk_update(text="Email report"), ctx)
        # Step 10: first goal
        await w.step_first_goal_handle_text(_mk_update(text="Run 10k by Oct"), ctx)
        # Step 11: integrations done
        await w.step_integrations_handle_done(
            _mk_update(callback_data="wiz:integrations:done"), ctx,
        )

    with db.session_scope() as s:
        u = s.get(db.User, uid)
        assert u.language == "ru"
        assert u.display_name == "Daria"
        assert u.city == "Neshe"
        assert u.timezone == "Asia/Jerusalem"
        assert u.morning_time == "07:00"
        assert u.evening_time == "22:00"
        assert u.news_country == "IL"
        assert u.onboarding_state == "completed"
        # News feeds written
        feeds = s.query(db.UserNewsFeed).filter_by(user_id=uid).all()
        assert len(feeds) == 2  # IL has Ynet + Walla
        # Goal written
        goals = s.query(db.Goal).filter_by(user_id=uid).all()
        assert len(goals) == 1
        assert goals[0].title == "Run 10k by Oct"
    # Persona file
    persona_path = Path(db.data_dir(), "users", str(uid), "personality.json")
    assert persona_path.exists()


# ─── Phase F: /profile (keep / change) ───────────────────────────────────────


def _seed_complete_user(uid: int) -> dict:
    """Set every field on a user so /profile has values to preview."""
    import db
    snapshot = {}
    with db.session_scope() as s:
        u = s.get(db.User, uid)
        u.language = "ru"
        u.display_name = "Daria"
        u.city = "Neshe"
        u.timezone = "Asia/Jerusalem"
        u.morning_time = "06:30"
        u.evening_time = "21:30"
        u.news_country = "IL"
        u.onboarding_state = "completed"
        snapshot = {
            "language": u.language,
            "display_name": u.display_name,
            "city": u.city,
            "timezone": u.timezone,
            "morning_time": u.morning_time,
            "evening_time": u.evening_time,
            "news_country": u.news_country,
            "onboarding_state": u.onboarding_state,
        }
    return snapshot


@pytest.mark.asyncio
async def test_profile_keep_each_step_no_writes():
    uid = _mk_user()
    snap = _seed_complete_user(uid)
    import onboarding_wizard as w, db

    ctx = _mk_ctx(mode="profile", user_id=uid)
    # For each profile step (0..6 then 8), tap Keep
    steps = [
        w.STEP_LANGUAGE, w.STEP_NAME, w.STEP_CITY, w.STEP_TIMEZONE,
        w.STEP_MORNING_TIME, w.STEP_EVENING_TIME, w.STEP_GOOGLE,
        w.STEP_NEWS, w.STEP_PERSONALITY,
    ]
    for step_id in steps:
        upd = _mk_update(callback_data=f"wiz:keep:{step_id}")
        await w.cb_keep_or_change(upd, ctx)
    # Now finish at integrations
    upd = _mk_update(callback_data="wiz:integrations:done")
    await w.step_integrations_handle_done(upd, ctx)

    with db.session_scope() as s:
        u = s.get(db.User, uid)
        assert u.language == snap["language"]
        assert u.display_name == snap["display_name"]
        assert u.city == snap["city"]
        assert u.timezone == snap["timezone"]
        assert u.morning_time == snap["morning_time"]
        assert u.evening_time == snap["evening_time"]
        assert u.news_country == snap["news_country"]


@pytest.mark.asyncio
async def test_profile_change_only_city():
    uid = _mk_user()
    snap = _seed_complete_user(uid)
    import onboarding_wizard as w, db

    ctx = _mk_ctx(mode="profile", user_id=uid)
    # Keep language, name; Change city
    await w.cb_keep_or_change(_mk_update(callback_data=f"wiz:keep:{w.STEP_LANGUAGE}"), ctx)
    await w.cb_keep_or_change(_mk_update(callback_data=f"wiz:keep:{w.STEP_NAME}"), ctx)
    # Tap Change on city — runs collection UI
    await w.cb_keep_or_change(_mk_update(callback_data=f"wiz:change:{w.STEP_CITY}"), ctx)
    # Type a new city
    with patch("weather_client.get_weather", return_value="+25°C"):
        await w.step_city_handle_text(_mk_update(text="Tel Aviv"), ctx)
    # Keep the rest
    for step_id in [w.STEP_TIMEZONE, w.STEP_MORNING_TIME, w.STEP_EVENING_TIME,
                    w.STEP_GOOGLE, w.STEP_NEWS, w.STEP_PERSONALITY]:
        await w.cb_keep_or_change(_mk_update(callback_data=f"wiz:keep:{step_id}"), ctx)
    # Finish
    await w.step_integrations_handle_done(
        _mk_update(callback_data="wiz:integrations:done"), ctx,
    )

    with db.session_scope() as s:
        u = s.get(db.User, uid)
        assert u.city == "Tel Aviv"  # changed
        # All other fields preserved
        assert u.language == snap["language"]
        assert u.display_name == snap["display_name"]
        assert u.timezone == snap["timezone"]
        assert u.morning_time == snap["morning_time"]
        assert u.evening_time == snap["evening_time"]


# ─── /start variations ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_user_start_does_not_replay_wizard():
    """A user with onboarding_state='completed' must get the brief
    'С возвращением' path — no wizard re-entry."""
    uid = _mk_user()
    _seed_complete_user(uid)
    import onboarding
    upd = _mk_update()
    ctx = _mk_ctx(with_state=False)
    await onboarding.cmd_start(upd, ctx)
    text = upd.effective_message.reply_text.call_args[0][0]
    assert "возвращением" in text.lower()
    # No wizard state created
    assert "wizard" not in (ctx.user_data or {})


# ─── Meta-test on presets ────────────────────────────────────────────────────


def test_news_preset_data_matches_registry():
    """Every key in NEWS_PRESETS has at least one (name, url) entry."""
    from news_presets import NEWS_PRESETS
    assert NEWS_PRESETS, "NEWS_PRESETS must not be empty"
    for country, feeds in NEWS_PRESETS.items():
        assert feeds, f"{country} has no feeds"
        for entry in feeds:
            assert len(entry) == 2
            name, url = entry
            assert isinstance(name, str) and name
            assert isinstance(url, str) and url.startswith("http")
