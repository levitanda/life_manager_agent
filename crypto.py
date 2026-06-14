"""Symmetric encryption for OAuth tokens and integration secrets stored in DB.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.
Master key comes from `MASTER_KEY` env. For test/dev a key is generated
on first use and warned about.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def _master_key() -> bytes:
    raw = os.environ.get("MASTER_KEY")
    if raw:
        return raw.encode() if isinstance(raw, str) else raw
    # Dev/test fallback: stable per-process key. Anyone running prod without
    # MASTER_KEY set will see this warning on import.
    key = Fernet.generate_key()
    os.environ["MASTER_KEY"] = key.decode()
    logger.warning(
        "MASTER_KEY not set — generated a throwaway key. Set MASTER_KEY in .env for prod."
    )
    return key


_fernet: Fernet | None = None


def _cipher() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_master_key())
    return _fernet


def reset_for_tests() -> None:
    """For tests: drop the cached cipher so MASTER_KEY env changes take effect."""
    global _fernet
    _fernet = None


def encrypt_json(payload: Any) -> bytes:
    """Encrypt a JSON-serializable payload, return the ciphertext bytes."""
    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _cipher().encrypt(plaintext)


def decrypt_json(ciphertext: bytes) -> Any:
    """Decrypt and JSON-load. Raises cryptography.fernet.InvalidToken on bad key/data."""
    plaintext = _cipher().decrypt(ciphertext)
    return json.loads(plaintext.decode("utf-8"))


def encrypt_str(text: str) -> bytes:
    return _cipher().encrypt(text.encode("utf-8"))


def decrypt_str(ciphertext: bytes) -> str:
    return _cipher().decrypt(ciphertext).decode("utf-8")
