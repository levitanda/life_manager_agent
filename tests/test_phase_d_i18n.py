"""Tests for i18n.py — translation catalog + user_language resolver."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    import db, crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield
    db.reset_for_tests()
    crypto.reset_for_tests()


# ─── t() resolver ────────────────────────────────────────────────────────────


def test_t_returns_russian_for_ru_user():
    import i18n
    assert "Привет" in i18n.t("onboard.welcome", "ru")


def test_t_returns_english_for_en_user():
    import i18n
    assert "Hi!" in i18n.t("onboard.welcome", "en")


def test_t_returns_hebrew_for_he_user():
    import i18n
    s = i18n.t("onboard.welcome", "he")
    assert "שלום" in s


def test_t_falls_back_to_russian_for_unknown_language():
    import i18n
    s = i18n.t("onboard.welcome", "de")  # not in catalog
    assert "Привет" in s


def test_t_falls_back_to_key_for_unknown_key():
    import i18n
    s = i18n.t("totally.unknown.key", "en")
    assert s == "totally.unknown.key"


def test_t_supports_format_kwargs():
    import i18n
    s = i18n.t("onboard.complete", "ru", name="Mika", time="06:30", tz="IDT")
    assert "Mika" in s
    assert "06:30" in s


def test_t_format_missing_kwarg_returns_unformatted_string():
    import i18n
    # Doesn't pass `name`/`time`/`tz` — should not raise
    s = i18n.t("onboard.complete", "ru")
    assert isinstance(s, str)
    assert len(s) > 0


def test_t_none_language_defaults_to_russian():
    import i18n
    assert i18n.t("onboard.welcome", None).startswith("Привет")


# ─── user_language() ─────────────────────────────────────────────────────────


def test_user_language_default_for_legacy():
    import i18n
    assert i18n.user_language(None) == "ru"


def test_user_language_reads_db():
    import i18n, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42)
        u.language = "en"
        uid = u.id
    assert i18n.user_language(uid) == "en"


def test_user_language_returns_he_when_set():
    import i18n, db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=43, telegram_chat_id=43)
        u.language = "he"
        uid = u.id
    assert i18n.user_language(uid) == "he"


def test_user_language_unknown_user_defaults_to_russian():
    import i18n
    assert i18n.user_language(99999) == "ru"


# ─── wrap_rtl() ──────────────────────────────────────────────────────────────


def test_wrap_rtl_adds_unicode_markers():
    import i18n
    wrapped = i18n.wrap_rtl("hi")
    assert wrapped.startswith("‫")  # RLE
    assert wrapped.endswith("‬")  # PDF
    assert "hi" in wrapped


# ─── Meta-test: catalogs must cover the same keys ────────────────────────────


def test_all_three_catalogs_cover_same_keys():
    """If a translator forgets a key in en or he, this test surfaces it
    immediately rather than waiting for that string to be rendered to a
    user."""
    import i18n
    ru_keys = set(i18n.TRANSLATIONS["ru"].keys())
    en_keys = set(i18n.TRANSLATIONS["en"].keys())
    he_keys = set(i18n.TRANSLATIONS["he"].keys())
    missing_en = ru_keys - en_keys
    missing_he = ru_keys - he_keys
    assert not missing_en, f"Missing in en: {missing_en}"
    assert not missing_he, f"Missing in he: {missing_he}"


def test_supported_languages_constant_includes_three():
    import i18n
    assert set(i18n.SUPPORTED_LANGUAGES) == {"ru", "en", "he"}
