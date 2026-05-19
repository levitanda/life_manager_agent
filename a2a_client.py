"""A2A client — call other A2A-compatible agents from this bot.

Registry of known agents persists in `a2a_agents.json`:
  {
    "research": {
      "url": "https://research.example.com",
      "api_key": "...",  # optional
      "description": "...",
      "skills": [...]    # cached from Agent Card
    }
  }

All functions return tuples (ok: bool, payload: dict|str) for easy use in
the tool layer.
"""

import json
import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

REGISTRY_FILE = Path(__file__).parent / "a2a_agents.json"


def _load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("a2a_agents.json load failed: %s", e)
        return {}


def _save_registry(reg: dict) -> None:
    REGISTRY_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def list_known_agents() -> list[dict]:
    """Return list of {name, url, description, skill_ids}."""
    out = []
    for name, data in _load_registry().items():
        out.append({
            "name": name,
            "url": data.get("url"),
            "description": data.get("description"),
            "skill_ids": [s.get("id") for s in data.get("skills", []) if s.get("id")],
        })
    return out


def discover_agent(
    url: str,
    friendly_name: Optional[str] = None,
    api_key: Optional[str] = None,
) -> tuple[bool, dict | str]:
    """Fetch /.well-known/agent.json from `url` and save under `friendly_name`."""
    base = url.rstrip("/")
    card_url = f"{base}/.well-known/agent.json"
    try:
        r = requests.get(card_url, timeout=10)
        r.raise_for_status()
        card = r.json()
    except Exception as e:
        return False, f"Could not fetch Agent Card from {card_url}: {e}"

    name = friendly_name or card.get("name") or base
    name_key = name.lower().strip()

    reg = _load_registry()
    reg[name_key] = {
        "url": card.get("url") or base,
        "api_key": api_key,
        "description": card.get("description", ""),
        "skills": card.get("skills", []),
        "agent_name": card.get("name"),
        "version": card.get("version"),
    }
    _save_registry(reg)
    return True, {
        "registered_as": name_key,
        "agent_name": card.get("name"),
        "skills": [s.get("id") for s in card.get("skills", [])],
    }


def remove_agent(name: str) -> tuple[bool, str]:
    reg = _load_registry()
    key = name.lower().strip()
    if key not in reg:
        return False, f"No agent registered as '{name}'."
    del reg[key]
    _save_registry(reg)
    return True, f"Removed '{key}'."


def call_agent(
    name: str,
    tool: str,
    params: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[bool, dict | str]:
    """Send a task to a registered agent."""
    reg = _load_registry()
    entry = reg.get(name.lower().strip())
    if not entry:
        return False, f"No agent registered as '{name}'. Use a2a_list_known_agents."

    url = entry.get("url", "").rstrip("/")
    if not url:
        return False, f"Agent '{name}' has no URL configured."
    endpoint = f"{url}/tasks/send" if url.endswith("/a2a") else f"{url}/a2a/tasks/send"

    headers = {"Content-Type": "application/json"}
    if entry.get("api_key"):
        headers["Authorization"] = f"Bearer {entry['api_key']}"

    body = {
        "id": None,
        "message": {"role": "user", "parts": []},
        "metadata": {"tool": tool, "params": params or {}},
    }

    try:
        r = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
    except Exception as e:
        return False, f"Network error calling {endpoint}: {e}"

    if r.status_code >= 400:
        try:
            return False, f"HTTP {r.status_code}: {r.json()}"
        except Exception:
            return False, f"HTTP {r.status_code}"
    try:
        return True, r.json()
    except Exception as e:
        return False, f"Bad JSON in response: {e}"
