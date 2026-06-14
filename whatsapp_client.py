"""HTTP client for the local Baileys WhatsApp bridge.

Two modes:

- **Legacy single-user (user_id=None)**: bridge URL from env WHATSAPP_BRIDGE_URL
  (default 127.0.0.1:3030), registry from project-root whatsapp_groups.json.

- **Multi-tenant (user_id given)**: bridge URL constructed from the
  `whatsapp_bridges.port` row for the user (falls back to legacy env if no
  row exists). Registry at data/users/{user_id}/whatsapp_groups.json.

Registry file `whatsapp_groups.json` supports both flat and rich format:

  {
    "покупки": "120363047599035994@g.us",          ← flat (chatId only)
    "женя": {                                      ← rich
      "chat_id": "972501234567@s.whatsapp.net",
      "signature": "— Личный ассистент Дарьи",
      "aliases": ["муж", "жене"]
    }
  }
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LEGACY_BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3030")
LEGACY_GROUPS_FILE = Path(__file__).parent / "whatsapp_groups.json"


# ─── Per-user URL + registry path resolution ─────────────────────────────────


def _bridge_url(user_id: Optional[int]) -> str:
    if user_id is None:
        return LEGACY_BRIDGE_URL
    try:
        import db
        with db.session_scope() as s:
            row = s.get(db.WhatsAppBridge, user_id)
            if row and row.port:
                return f"http://127.0.0.1:{int(row.port)}"
    except Exception as e:
        logger.warning("WhatsApp bridge lookup failed for user %s: %s", user_id, e)
    return LEGACY_BRIDGE_URL


def _groups_file(user_id: Optional[int]) -> Path:
    if user_id is None:
        return LEGACY_GROUPS_FILE
    import db
    base = Path(db.data_dir(), "users", str(user_id))
    base.mkdir(parents=True, exist_ok=True)
    return base / "whatsapp_groups.json"


# ─── Registry ────────────────────────────────────────────────────────────────


def _normalize_entry(value) -> dict:
    """Convert flat string format into a dict, leave dict as-is."""
    if isinstance(value, str):
        return {"chat_id": value}
    if isinstance(value, dict):
        return value
    return {}


def _load_registry(user_id: Optional[int] = None) -> dict:
    """Returns {name_lower: {chat_id, signature?, aliases?}} from the user's registry."""
    groups_file = _groups_file(user_id)
    if not groups_file.exists():
        return {}
    try:
        raw = json.loads(groups_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("%s load failed: %s", groups_file, e)
        return {}

    registry: dict = {}
    for name, value in raw.items():
        entry = _normalize_entry(value)
        if not entry.get("chat_id"):
            continue
        key = name.lower().strip()
        registry[key] = entry
        for alias in entry.get("aliases", []):
            registry[alias.lower().strip()] = entry
    return registry


# ─── Bridge HTTP API ──────────────────────────────────────────────────────────


def status(user_id: Optional[int] = None) -> dict:
    try:
        r = requests.get(f"{_bridge_url(user_id)}/status", timeout=3)
        return r.json()
    except Exception as e:
        return {"ready": False, "error": str(e)}


def list_groups(user_id: Optional[int] = None) -> list[dict]:
    """Live fetch from the bridge — requires authenticated session."""
    try:
        r = requests.get(f"{_bridge_url(user_id)}/groups", timeout=15)
        r.raise_for_status()
        return r.json().get("groups", [])
    except Exception as e:
        logger.warning("WhatsApp list_groups failed: %s", e)
        return []


def unread_chats(user_id: Optional[int] = None) -> list[dict]:
    """Return chats with unread messages, each with up to 15 recent messages."""
    try:
        r = requests.get(f"{_bridge_url(user_id)}/unread", timeout=15)
        r.raise_for_status()
        return r.json().get("chats", [])
    except Exception as e:
        logger.warning("WhatsApp unread_chats failed: %s", e)
        return []


def get_chat_messages(
    chat_id: str, limit: int = 20, *, user_id: Optional[int] = None
) -> list[dict]:
    try:
        r = requests.get(
            f"{_bridge_url(user_id)}/chat/{chat_id}/messages",
            params={"limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("messages", [])
    except Exception as e:
        logger.warning("WhatsApp get_messages failed: %s", e)
        return []


def find_chats(query: str, *, user_id: Optional[int] = None) -> list[dict]:
    """Fuzzy search by chat name (groups + 1-on-1 contacts the bridge knows)."""
    try:
        r = requests.post(
            f"{_bridge_url(user_id)}/find", json={"query": query}, timeout=10
        )
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        logger.warning("WhatsApp find_chats failed: %s", e)
        return []


def phone_to_jid(phone: str) -> str:
    """Convert a phone string (any format) to WhatsApp personal JID."""
    try:
        import phonenumbers
        region = os.environ.get("DEFAULT_PHONE_REGION", "IL")
        parsed = phonenumbers.parse(phone, region)
        if phonenumbers.is_valid_number(parsed):
            digits = f"{parsed.country_code}{parsed.national_number}"
            return f"{digits}@s.whatsapp.net"
        logger.warning("phone_to_jid: invalid number %r (region %s)", phone, region)
    except Exception as e:
        logger.warning("phone_to_jid: failed to parse %r: %s", phone, e)
    digits = "".join(c for c in phone if c.isdigit())
    return f"{digits}@s.whatsapp.net"


def send_to_chat(
    chat_id: str, text: str, *, user_id: Optional[int] = None
) -> tuple[bool, str]:
    try:
        r = requests.post(
            f"{_bridge_url(user_id)}/send",
            json={"chatId": chat_id, "text": text},
            timeout=45,
        )
        if r.status_code == 200:
            return True, "Отправлено"
        try:
            err = r.json().get("error", f"HTTP {r.status_code}")
        except Exception:
            err = f"HTTP {r.status_code}"
        return False, err
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)
        return False, str(e)


def send_to_name(
    name: str, text: str, *, user_id: Optional[int] = None
) -> tuple[bool, str]:
    """Resolve a friendly name (or alias) and send. Appends configured signature."""
    registry = _load_registry(user_id)
    entry = registry.get(name.lower().strip())
    if not entry:
        return (
            False,
            f"«{name}» не настроено в whatsapp_groups.json. Используй whatsapp_list_groups чтобы найти id.",
        )
    full_text = text
    sig = entry.get("signature")
    if sig:
        full_text = f"{text}\n\n{sig}"
    return send_to_chat(entry["chat_id"], full_text, user_id=user_id)
