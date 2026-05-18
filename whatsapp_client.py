"""HTTP client for the local Baileys WhatsApp bridge.

The bridge is a Node.js sidecar (whatsapp_bridge/server.js) that maintains
a persistent WhatsApp session and exposes a tiny REST API on 127.0.0.1.
"""

import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:3030")
GROUPS_FILE = Path(__file__).parent / "whatsapp_groups.json"


def _load_group_registry() -> dict:
    """Friendly-name → chatId mapping stored locally so the user can use short names."""
    if not GROUPS_FILE.exists():
        return {}
    try:
        data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        return {k.lower(): v for k, v in data.items()}
    except Exception as e:
        logger.warning("whatsapp_groups.json load failed: %s", e)
        return {}


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


def send_to_chat(chat_id: str, text: str) -> tuple[bool, str]:
    try:
        r = requests.post(
            f"{BRIDGE_URL}/send",
            json={"chatId": chat_id, "text": text},
            timeout=15,
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


def send_to_group_by_name(group_name: str, text: str) -> tuple[bool, str]:
    """Resolve a friendly name from the registry, then send."""
    registry = _load_group_registry()
    chat_id = registry.get(group_name.lower().strip())
    if not chat_id:
        return (
            False,
            f"Группа «{group_name}» не настроена. Добавь её id в whatsapp_groups.json "
            f"(используй whatsapp_list_groups чтобы получить id).",
        )
    return send_to_chat(chat_id, text)
