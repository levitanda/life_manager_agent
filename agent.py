"""Anthropic tool-use agent: replaces the JSON-parser intent system.

Flow:
  1. Build messages from history + current user input
  2. Call Claude with TOOL_SCHEMAS, looped until end_turn or safety cap
  3. Each tool_use block → dispatched to tools.TOOL_FUNCS
  4. Returns ({"text": ..., "actions": [...]}) where actions may signal
     side-effects (send_digest, needs_confirmation) the bot handler executes

Side-effects (digests, inline keyboards) are signalled via tool result
"action" fields, not done inside tools — keeps tools pure and testable.
"""

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Optional

import pytz

import config
import llm
import tools

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
# Sonnet 4.6 via Bedrock — same model as the original Anthropic SDK call.
# Nova Pro is cheaper but calls tools much more eagerly than Sonnet, which
# resulted in unprompted "WhatsApp summary" replies for every user message.
MODEL = llm.MODEL_SONNET_BEDROCK
LEGACY_PERSONALITY_PATH = Path(__file__).parent / "personality.json"


def _personality_path(user_id: Optional[int]) -> Path:
    """Per-user personality.json under data/users/{id}/ (falls back to project root in legacy mode)."""
    if user_id is None:
        return LEGACY_PERSONALITY_PATH
    import db
    return Path(db.data_dir(), "users", str(user_id), "personality.json")


def _load_personality(user_id: Optional[int] = None) -> dict:
    path = _personality_path(user_id)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("personality.json load failed (path=%s): %s", path, e)
        return {"name": "ассистент", "humor": 50, "warmth": 70, "terseness": 50, "honesty": 90, "proactivity": 50, "style_hints": []}


def _build_system_prompt(
    persona: dict,
    active_tasks: list,
    summaries: list,
    *,
    user_name: Optional[str] = None,
    user_timezone: Optional[str] = None,
) -> str:
    tz_name = user_timezone or config.TIMEZONE
    tz = pytz.timezone(tz_name)
    now = datetime.datetime.now(tz)

    dials = (
        f"humor={persona.get('humor', 50)}/100, "
        f"warmth={persona.get('warmth', 70)}/100, "
        f"terseness={persona.get('terseness', 50)}/100, "
        f"honesty={persona.get('honesty', 90)}/100, "
        f"proactivity={persona.get('proactivity', 50)}/100"
    )
    hints = "\n".join(f"- {h}" for h in persona.get("style_hints", []))

    tasks_block = ""
    if active_tasks:
        lines = [f"{i}. [{t.get('type','?')}] {t['title']}" for i, t in enumerate(active_tasks, 1)]
        tasks_block = "\n\nАктивные задачи пользователя:\n" + "\n".join(lines)

    summaries_block = ""
    if summaries:
        lines = [f"[{s['date']}] {s['summary']}" for s in summaries[-5:]]
        summaries_block = "\n\nДолгосрочная память (резюме прошлых сессий):\n" + "\n\n".join(lines)

    persona_role = persona.get("name") or "личный ассистент"
    user_block = (
        f"Ты разговариваешь с человеком по имени {user_name}. "
        f"Когда обращаешься — используй это имя."
    ) if user_name else "Имя пользователя пока неизвестно."

    return f"""Ты — {persona_role}. {user_block}

Сейчас {now.strftime('%Y-%m-%d %H:%M')}, часовой пояс {tz_name}.

Личность: {dials}
Стиль:
{hints}

У тебя есть инструменты для управления календарём, задачами, почтой, погодой, дайджестами, Алисой.
Используй их активно, без лишних разъяснений. Если пользователь попросил несколько вещей — выполни их все
(можно параллельно вызывать инструменты), потом одним коротким сообщением подытожь.
Не задавай уточняющих вопросов, если из контекста можно понять — действуй.
Если не нужен инструмент — отвечай текстом напрямую (chat-режим).{tasks_block}{summaries_block}"""


def _build_messages(text: str, history: list) -> list:
    msgs = []
    for msg in (history or [])[-8:]:
        msgs.append({"role": msg["role"], "content": msg["content"]})
    msgs.append({"role": "user", "content": text})
    return msgs


def run_agent(
    text: str,
    history: Optional[list] = None,
    summaries: Optional[list] = None,
    active_tasks: Optional[list] = None,
    context=None,
    user_id: Optional[int] = None,
) -> dict:
    """Execute the tool-use loop. Returns:
      {"text": str — aggregated reply,
       "actions": list — side-effect signals (send_digest, needs_confirmation, ...)}

    `user_id` is threaded into every tool call so per-user clients hit the
    right Google account / WhatsApp bridge / data dir. None preserves legacy
    single-user behavior.
    """
    persona = _load_personality(user_id)
    # Per-user context for personalization (no Daria-by-default leaks).
    user_name = None
    user_timezone = None
    if user_id is not None:
        try:
            import db
            with db.session_scope() as s:
                u = s.get(db.User, user_id)
                if u is not None:
                    user_name = u.display_name or None
                    user_timezone = u.timezone or None
        except Exception as e:
            logger.warning("agent: user lookup for %s failed: %s", user_id, e)
    system = _build_system_prompt(
        persona, active_tasks or [], summaries or [],
        user_name=user_name, user_timezone=user_timezone,
    )
    messages = _build_messages(text, history)

    text_parts: list[str] = []
    actions: list[dict] = []

    for iteration in range(MAX_ITERATIONS):
        try:
            result = llm.chat(
                MODEL,
                system,
                messages,
                tools=tools.TOOL_SCHEMAS,
                max_tokens=2048,
            )
        except Exception as e:
            logger.error("agent API call failed: %s", e)
            return {"text": "Не получилось обработать запрос, попробуй ещё раз.", "actions": []}

        # Collect any text from the model reply
        if result["text"]:
            text_parts.append(result["text"])

        if result["stop_reason"] != "tool_use":
            break

        # Re-construct the assistant turn for the next iteration
        assistant_blocks: list[dict] = []
        if result["text"]:
            assistant_blocks.append({"type": "text", "text": result["text"]})
        for tu in result["tool_uses"]:
            assistant_blocks.append({
                "type": "tool_use",
                "id": tu["id"],
                "name": tu["name"],
                "input": tu["input"],
            })
        messages.append({"role": "assistant", "content": assistant_blocks})

        tool_results = []
        stop_for_confirmation = False

        for tu in result["tool_uses"]:
            func = tools.TOOL_FUNCS.get(tu["name"])
            if not func:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": f"Unknown tool: {tu['name']}",
                    "is_error": True,
                })
                continue
            try:
                tool_result = func(
                    **tu["input"],
                    _context=context,
                    _active_tasks=active_tasks or [],
                    _user_id=user_id,
                )
            except Exception as e:
                logger.exception("tool %s crashed", tu["name"])
                tool_result = {"status": "error", "summary": f"Внутренняя ошибка: {e}"}

            text_parts.append(tool_result.get("summary", ""))

            data = tool_result.get("data") or {}
            if data.get("action"):
                actions.append({"action": data["action"], "target_date": data.get("target_date")})
            if tool_result.get("status") == "needs_confirmation":
                actions.append({"action": "needs_confirmation", "kind": data.get("kind", "")})
                stop_for_confirmation = True

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu["id"],
                "content": tool_result.get("summary", "ok"),
            })

        messages.append({"role": "user", "content": tool_results})

        if stop_for_confirmation:
            break
    else:
        logger.warning("agent hit MAX_ITERATIONS=%d for: %r", MAX_ITERATIONS, text[:80])

    # Deduplicate consecutive identical lines
    seen, deduped = set(), []
    for part in text_parts:
        if part and part not in seen:
            seen.add(part)
            deduped.append(part)

    return {"text": "\n\n".join(deduped).strip(), "actions": actions}


def is_enabled() -> bool:
    return os.environ.get("USE_AGENT", "").lower() in ("1", "true", "yes")
