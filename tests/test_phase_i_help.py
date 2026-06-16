"""Tests for Phase I: /help redesign + morning-digest /help reminder.

What's covered:
  - cmd_help renders the per-language catalog text (ru/en/he).
  - `/help short` returns a compact (<500 char) overview.
  - Meta-test: every CommandHandler registered in bot_handlers.register_handlers
    is actually mentioned in the Russian full help text, modulo a small allow-list.
  - The morning-digest prompt sent to the LLM includes the localized /help
    reminder at the very end.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet


# ─── Fixture: isolated DB / data dir / crypto state ────────────────────────────


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


# ─── helpers ───────────────────────────────────────────────────────────────────


def _mk_user(tg=222, *, name="Tester", language="ru"):
    """Create an active (promo) user with the requested language."""
    import db
    with db.session_scope() as s:
        u = db.create_user(
            s, telegram_user_id=tg, telegram_chat_id=tg, display_name=name
        )
        u.subscription_status = "promo"
        u.timezone = "Europe/Moscow"
        u.language = language
        return u.id, tg


def _make_update(tg_user_id: int) -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_message.reply_text = AsyncMock()
    update.effective_user.id = tg_user_id
    update.effective_chat.id = tg_user_id
    return update


def _make_context(args=None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = list(args) if args else []
    ctx.user_data = {}
    ctx.application = MagicMock()
    return ctx


def _run(coro):
    return asyncio.run(coro)


def _capture_reply(update: MagicMock) -> str:
    """Pull the first positional arg of the latest reply_text call."""
    assert update.message.reply_text.await_count >= 1, "expected reply_text to be called"
    args, kwargs = update.message.reply_text.await_args
    return args[0] if args else kwargs.get("text", "")


# ─── /help in each language ────────────────────────────────────────────────────


def test_help_full_text_in_each_language():
    """cmd_help sends the catalog `help.full_text` for the user's language."""
    import bot_handlers, i18n

    for lang, marker in [
        ("ru", "Задачи и календарь"),
        ("en", "Tasks and calendar"),
        ("he", "משימות ויומן"),
    ]:
        user_id, tg = _mk_user(tg=1000 + hash(lang) % 1000, language=lang)
        update = _make_update(tg)
        ctx = _make_context(args=[])

        _run(bot_handlers.cmd_help(update, ctx))

        sent = _capture_reply(update)
        assert marker in sent, f"language {lang}: marker {marker!r} not in reply"
        # The slash commands stay latin in every language.
        assert "/tasks" in sent
        assert "/help" in sent


def test_help_short_mode_is_under_500_chars():
    """`/help short` returns a compact version under 500 characters."""
    import bot_handlers, i18n

    user_id, tg = _mk_user(tg=4242, language="ru")
    update = _make_update(tg)
    ctx = _make_context(args=["short"])

    _run(bot_handlers.cmd_help(update, ctx))

    sent = _capture_reply(update)
    assert len(sent) <= 500, f"short help is {len(sent)} chars, expected ≤ 500"
    assert "/help" in sent


def test_help_short_mode_localized():
    """`/help short` honors the user's language preference."""
    import bot_handlers

    user_id, tg = _mk_user(tg=4243, language="en")
    update = _make_update(tg)
    ctx = _make_context(args=["short"])

    _run(bot_handlers.cmd_help(update, ctx))

    sent = _capture_reply(update)
    assert "plain language" in sent.lower() or "basics" in sent.lower()
    assert len(sent) <= 500


# ─── Meta-test: every registered command appears in help ───────────────────────


# Commands we knowingly don't list (or list under a different name).
# /start: handled by onboarding wizard, not user-facing post-onboarding.
# /cancel: dual-use (progress conv fallback AND subscription cancel) — already
#          mentioned under the Subscription section.
# /help itself is trivially present.
HELP_LISTING_WHITELIST = {"/start", "/cancel", "/help"}


def test_help_lists_every_registered_command():
    """For every CommandHandler(...) in register_handlers, the command name
    is mentioned (as `/<name>`) in the Russian help.full_text catalog entry.

    Allows a small whitelist of commands that are intentionally absent.
    """
    import i18n

    src_path = Path(__file__).parent.parent / "bot_handlers.py"
    src = src_path.read_text(encoding="utf-8")

    # Find register_handlers source body — restricting the regex search to
    # this function avoids accidentally matching command literals from
    # docstrings or comments elsewhere in the file.
    m = re.search(r"def register_handlers\([^)]*\)\s*->\s*[^:]*:(.*)$", src, re.DOTALL)
    assert m, "could not locate register_handlers in bot_handlers.py"
    body = m.group(1)

    # CommandHandler("name", ...) — extract the first argument string.
    cmd_names = set(re.findall(r"CommandHandler\(\s*['\"]([a-zA-Z_][a-zA-Z0-9_]*)['\"]", body))
    assert cmd_names, "no CommandHandler() registrations found — regex likely broken"

    help_text = i18n.t("help.full_text", "ru")

    missing = []
    for name in sorted(cmd_names):
        slash = f"/{name}"
        if slash in HELP_LISTING_WHITELIST:
            continue
        if slash not in help_text:
            missing.append(slash)

    assert not missing, (
        f"these registered commands are missing from help.full_text (ru): {missing}. "
        f"If intentional, add them to HELP_LISTING_WHITELIST."
    )


# ─── Digest prompt includes /help reminder ─────────────────────────────────────


def _llm_result(text: str = "Digest text") -> dict:
    return {"text": text, "tool_uses": [], "stop_reason": "end_turn", "raw": {}}


def _prompt_from_mock(mock_chat) -> str:
    args, kwargs = mock_chat.call_args
    if "messages" in kwargs:
        messages = kwargs["messages"]
    else:
        messages = args[2]
    return messages[0]["content"]


def test_digest_prompt_includes_help_reminder():
    """The prompt sent to the LLM ends with a /help reminder line."""
    import digest as digest_module

    events = [{"title": "Standup", "time": "09:00"}]
    short = [{"title": "Fix bug", "due": "2026-06-17"}]
    long_ = [{"title": "Write book", "due": "2026-12-31"}]

    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            events, short, long_, None,
            user_name="Daria", user_language="ru",
        )
        prompt = _prompt_from_mock(mock_chat)

    assert "/help" in prompt
    # The exact full reminder text should be embedded near the bottom.
    assert "Хорошего дня, Daria" in prompt


def test_digest_reminder_localized_for_en_user():
    """English-language users get the English reminder phrase."""
    import digest as digest_module

    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            [], [], [], None,
            user_name="Jony", user_language="en",
        )
        prompt = _prompt_from_mock(mock_chat)

    assert "/help" in prompt
    assert "Have a great day, Jony" in prompt
    # And the Russian variant should NOT also be embedded.
    assert "Хорошего дня" not in prompt


def test_digest_reminder_localized_for_he_user():
    """Hebrew users get the Hebrew reminder."""
    import digest as digest_module

    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            [], [], [], None,
            user_name="Noa", user_language="he",
        )
        prompt = _prompt_from_mock(mock_chat)

    assert "/help" in prompt
    assert "יום נפלא, Noa" in prompt


def test_digest_reminder_defaults_to_ru_for_unknown_language():
    """An unknown language code falls back to the Russian reminder, not a crash."""
    import digest as digest_module

    with patch("llm.chat", return_value=_llm_result()) as mock_chat:
        digest_module.generate_morning_digest(
            [], [], [], None,
            user_name="X", user_language="zz",
        )
        prompt = _prompt_from_mock(mock_chat)

    assert "/help" in prompt
    assert "Хорошего дня, X" in prompt
