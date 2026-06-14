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

import anthropic
import pytz

import config
import tools

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
MODEL = "claude-sonnet-4-6"
PERSONALITY_PATH = Path(__file__).parent / "personality.json"


def _load_personality() -> dict:
    try:
        with open(PERSONALITY_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("personality.json load failed: %s", e)
        return {"name": "ассистент", "humor": 50, "warmth": 70, "terseness": 50, "honesty": 90, "proactivity": 50, "style_hints": []}


def _build_system_prompt(persona: dict, active_tasks: list, summaries: list) -> str:
    tz = pytz.timezone(config.TIMEZONE)
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

    return f"""Ты — {persona.get('name', 'личный ассистент')} по имени Дарья.

Сейчас {now.strftime('%Y-%m-%d %H:%M')}, часовой пояс {config.TIMEZONE}.

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
    persona = _load_personality()
    system = _build_system_prompt(persona, active_tasks or [], summaries or [])
    messages = _build_messages(text, history)

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    text_parts: list[str] = []
    actions: list[dict] = []

    for iteration in range(MAX_ITERATIONS):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=[{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=tools.TOOL_SCHEMAS,
                messages=messages,
            )
        except Exception as e:
            logger.error("agent API call failed: %s", e)
            return {"text": "Не получилось обработать запрос, попробуй ещё раз.", "actions": []}

        # Collect any text blocks
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                text_parts.append(block.text.strip())

        if resp.stop_reason != "tool_use":
            break

        # Dispatch each tool_use block
        messages.append({"role": "assistant", "content": resp.content})
        tool_results = []
        stop_for_confirmation = False

        for block in resp.content:
            if block.type != "tool_use":
                continue
            func = tools.TOOL_FUNCS.get(block.name)
            if not func:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": f"Unknown tool: {block.name}",
                    "is_error": True,
                })
                continue
            try:
                result = func(
                    **block.input,
                    _context=context,
                    _active_tasks=active_tasks or [],
                    _user_id=user_id,
                )
            except Exception as e:
                logger.exception("tool %s crashed", block.name)
                result = {"status": "error", "summary": f"Внутренняя ошибка: {e}"}

            text_parts.append(result.get("summary", ""))

            data = result.get("data") or {}
            if data.get("action"):
                actions.append({"action": data["action"], "target_date": data.get("target_date")})
            if result.get("status") == "needs_confirmation":
                actions.append({"action": "needs_confirmation", "kind": data.get("kind", "")})
                stop_for_confirmation = True

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result.get("summary", "ok"),
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
