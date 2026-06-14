"""Tests for db.py and crypto.py — foundation of multi-tenant migration."""

from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite + data dir + master key."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    import db
    import crypto
    db.reset_for_tests()
    crypto.reset_for_tests()
    db.init_db()
    yield tmp_path
    db.reset_for_tests()
    crypto.reset_for_tests()


# ─── Crypto ───────────────────────────────────────────────────────────────────


def test_crypto_roundtrip_json():
    import crypto
    payload = {"token": "abc", "scopes": ["x", "y"], "nested": {"k": 1}}
    enc = crypto.encrypt_json(payload)
    assert isinstance(enc, bytes) and enc != json.dumps(payload).encode()
    dec = crypto.decrypt_json(enc)
    assert dec == payload


def test_crypto_roundtrip_str():
    import crypto
    enc = crypto.encrypt_str("hello мир")
    assert crypto.decrypt_str(enc) == "hello мир"


def test_crypto_different_key_fails(monkeypatch):
    import crypto
    enc = crypto.encrypt_str("secret")
    # Replace key, reset cipher
    monkeypatch.setenv("MASTER_KEY", Fernet.generate_key().decode())
    crypto.reset_for_tests()
    with pytest.raises(InvalidToken):
        crypto.decrypt_str(enc)


# ─── DB schema ────────────────────────────────────────────────────────────────


def test_init_db_creates_schema(isolated_db):
    import db
    # init_db already ran via fixture; tables should exist
    with db.session_scope() as s:
        assert s.query(db.User).count() == 0
        assert s.query(db.PromoCode).filter_by(code="LEVITANONLY").one().grants == "lifetime_free"


def test_init_db_idempotent(isolated_db):
    import db
    db.init_db()  # second time
    db.init_db()  # third time
    # promo not duplicated
    with db.session_scope() as s:
        assert s.query(db.PromoCode).count() == 1


# ─── Users ────────────────────────────────────────────────────────────────────


def test_create_and_get_user(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=42, telegram_chat_id=42, display_name="Test")
        assert u.id is not None
        assert u.subscription_status == "inactive"
        assert u.has_access() is False
    with db.session_scope() as s:
        got = db.get_user_by_telegram_id(s, 42)
        assert got is not None and got.display_name == "Test"


def test_telegram_user_id_unique(isolated_db):
    import db
    from sqlalchemy.exc import IntegrityError
    with db.session_scope() as s:
        db.create_user(s, telegram_user_id=99, telegram_chat_id=99)
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            db.create_user(s, telegram_user_id=99, telegram_chat_id=100)


def test_user_data_dir_is_per_user(isolated_db, tmp_path):
    import db
    with db.session_scope() as s:
        u1 = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        u2 = db.create_user(s, telegram_user_id=2, telegram_chat_id=2)
        d1 = u1.data_dir
        d2 = u2.data_dir
    assert d1 != d2
    assert d1.exists() and d2.exists()
    assert d1.parent == d2.parent
    assert d1.name == "1" and d2.name == "2"


def test_has_access_per_status(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        for status, expected in [
            ("inactive", False),
            ("active", True),
            ("promo", True),
            ("cancelled", False),
            ("past_due", False),
        ]:
            u.subscription_status = status
            assert u.has_access() is expected, status


# ─── Promo ────────────────────────────────────────────────────────────────────


def test_redeem_levitanonly_grants_lifetime(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        ok, msg = db.redeem_promo(s, u, "LEVITANONLY")
        assert ok is True
        assert u.subscription_status == "promo"
        assert u.has_access() is True
        promo = s.query(db.PromoCode).filter_by(code="LEVITANONLY").one()
        assert promo.redeemed_count == 1


def test_redeem_lowercase_promo(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        ok, _ = db.redeem_promo(s, u, "levitanonly")
        assert ok is True


def test_redeem_unknown_promo_fails(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        ok, msg = db.redeem_promo(s, u, "FAKE")
        assert ok is False
        assert u.subscription_status == "inactive"


def test_redeem_twice_fails(isolated_db):
    import db
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        db.redeem_promo(s, u, "LEVITANONLY")
        ok, msg = db.redeem_promo(s, u, "LEVITANONLY")
        assert ok is False
        # but only one redemption recorded
        promo = s.query(db.PromoCode).filter_by(code="LEVITANONLY").one()
        assert promo.redeemed_count == 1


def test_redeem_max_redemptions_enforced(isolated_db):
    import db
    with db.session_scope() as s:
        s.add(db.PromoCode(code="ONESHOT", grants="lifetime_free", max_redemptions=1))
    with db.session_scope() as s:
        u1 = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        ok, _ = db.redeem_promo(s, u1, "ONESHOT")
        assert ok is True
    with db.session_scope() as s:
        u2 = db.create_user(s, telegram_user_id=2, telegram_chat_id=2)
        ok, msg = db.redeem_promo(s, u2, "ONESHOT")
        assert ok is False
        assert "лимит" in msg.lower()


# ─── Encrypted token storage ──────────────────────────────────────────────────


def test_google_token_roundtrip(isolated_db):
    import db, crypto
    payload = {
        "token": "ya29.xxx",
        "refresh_token": "1//yyy",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
    }
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.add(db.GoogleToken(
            user_id=u.id,
            token_json_encrypted=crypto.encrypt_json(payload),
            scopes=" ".join(payload["scopes"]),
            refreshed_at=datetime.datetime.utcnow(),
        ))
    with db.session_scope() as s:
        u = db.get_user_by_telegram_id(s, 1)
        tok = u.google_token
        assert tok is not None
        decrypted = crypto.decrypt_json(tok.token_json_encrypted)
        assert decrypted == payload


def test_user_integration_encrypted_config(isolated_db):
    import db, crypto
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        cfg = {"user_key": "p123", "app_token": "atok"}
        s.add(db.UserIntegration(
            user_id=u.id, integration="pushover", enabled=1,
            config_json_encrypted=crypto.encrypt_json(cfg),
        ))
    with db.session_scope() as s:
        u = db.get_user_by_telegram_id(s, 1)
        integ = u.integrations[0]
        assert integ.integration == "pushover"
        assert crypto.decrypt_json(integ.config_json_encrypted) == cfg


def test_unique_user_integration(isolated_db):
    import db
    from sqlalchemy.exc import IntegrityError
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.add(db.UserIntegration(user_id=u.id, integration="pushover", enabled=1))
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            u = db.get_user_by_telegram_id(s, 1)
            s.add(db.UserIntegration(user_id=u.id, integration="pushover", enabled=0))


def test_whatsapp_bridge_unique_port(isolated_db):
    import db
    from sqlalchemy.exc import IntegrityError
    with db.session_scope() as s:
        u1 = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.add(db.WhatsAppBridge(user_id=u1.id, port=3030, auth_dir="data/users/1/wa"))
    with pytest.raises(IntegrityError):
        with db.session_scope() as s:
            u2 = db.create_user(s, telegram_user_id=2, telegram_chat_id=2)
            s.add(db.WhatsAppBridge(user_id=u2.id, port=3030, auth_dir="data/users/2/wa"))


# ─── Cascade delete ──────────────────────────────────────────────────────────


def test_cascade_delete_user_clears_dependents(isolated_db):
    import db, crypto
    with db.session_scope() as s:
        u = db.create_user(s, telegram_user_id=1, telegram_chat_id=1)
        s.add(db.GoogleToken(
            user_id=u.id, token_json_encrypted=crypto.encrypt_json({}), scopes="x"
        ))
        s.add(db.UserIntegration(user_id=u.id, integration="pushover", enabled=1))
        s.add(db.WhatsAppBridge(user_id=u.id, port=3030, auth_dir="x"))
    with db.session_scope() as s:
        u = db.get_user_by_telegram_id(s, 1)
        s.delete(u)
    with db.session_scope() as s:
        assert s.query(db.GoogleToken).count() == 0
        assert s.query(db.UserIntegration).count() == 0
        assert s.query(db.WhatsAppBridge).count() == 0


# Need this import at top — added later to allow test imports to find json
import json  # noqa: E402
