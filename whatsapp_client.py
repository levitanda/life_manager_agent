"""HTTP client for the local Baileys WhatsApp bridge.

The bridge is a Node.js sidecar (whatsapp_bridge/server.js) that maintains
a persistent WhatsApp session and exposes a tiny REST API on 127.0.0.1.

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

import json
import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3030")
GROUPS_FILE = Path(__file__).parent / "whatsapp_groups.json"


def _normalize_entry(value) -> dict:
    """Convert flat string format into a dict, leave dict as-is."""
    if isinstance(value, str):
        return {"chat_id": value}
    if isinstance(value, dict):
        return value
    return {}


def _load_registry() -> dict:
    """Returns {name_lower: {chat_id, signature?, aliases?}} from whatsapp_groups.json.
    Aliases are also indexed under their own lowercase keys.
    """
    if not GROUPS_FILE.exists():
        return {}
    try:
        raw = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("whatsapp_groups.json load failed: %s", e)
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


def status() -> dict:
    try:
        r = requests.get(f"{BRIDGE_URL}/status", timeout=3)
        return r.json()
    except Exception as e:
        return {"ready": False, "error": str(e)}


def list_groups() -> list[dict]:
    """Live fetch from the bridge — requires authenticated session."""
    try:
        r = requests.get(f"{BRIDGE_URL}/groups", timeout=15)
        r.raise_for_status()
        return r.json().get("groups", [])
    except Exception as e:
        logger.warning("WhatsApp list_groups failed: %s", e)
        return []


def unread_chats() -> list[dict]:
    """Return chats with unread messages, each with up to 15 recent messages."""
    try:
        r = requests.get(f"{BRIDGE_URL}/unread", timeout=15)
        r.raise_for_status()
        return r.json().get("chats", [])
    except Exception as e:
        logger.warning("WhatsApp unread_chats failed: %s", e)
        return []


def get_chat_messages(chat_id: str, limit: int = 20) -> list[dict]:
    try:
        r = requests.get(f"{BRIDGE_URL}/chat/{chat_id}/messages", params={"limit": limit}, timeout=10)
        r.raise_for_status()
        return r.json().get("messages", [])
    except Exception as e:
        logger.warning("WhatsApp get_messages failed: %s", e)
        return []


def find_chats(query: str) -> list[dict]:
    """Fuzzy search by chat name (groups + 1-on-1 contacts the bridge knows)."""
    try:
        r = requests.post(f"{BRIDGE_URL}/find", json={"query": query}, timeout=10)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        logger.warning("WhatsApp find_chats failed: %s", e)
        return []


def phone_to_jid(phone: str) -> str:
    """Convert a phone string (any format) to WhatsApp personal JID."""
    digits = "".join(c for c in phone if c.isdigit())
    return f"{digits}@s.whatsapp.net"


def send_to_chat(chat_id: str, text: str) -> tuple[bool, str]:
    # First message to an unseen JID may take 20-25s while Baileys negotiates
    # the session, so we allow a generous timeout.
    try:
        r = requests.post(
            f"{BRIDGE_URL}/send",
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


def send_to_name(name: str, text: str) -> tuple[bool, str]:
    """Resolve a friendly name (or alias) and send. Appends configured signature."""
    registry = _load_registry()
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
    return send_to_chat(entry["chat_id"], full_text)
