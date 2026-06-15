"""Cheap Nova-Lite-based summarizer for WhatsApp unread chats.

Both the morning digest and the on-demand `whatsapp_unread_chats` tool feed
their raw chat data through `summarize_unread_chats` instead of stuffing
raw messages into a Sonnet prompt. Output is a prioritized digest:

  🔴 ВАЖНО ОТВЕТИТЬ — direct questions, mentions, urgent asks, close people
  🟡 МОЖНО ОТВЕТИТЬ ПОЗЖЕ — work/friends, no urgency
  ⚪ МОЖНО ПРОПУСТИТЬ — group flood, spam, news channels

`user_name` is the display name of the WhatsApp owner — used as the
self-sender label in the raw context and as the pronoun anchor in the
LLM prompt. When None, falls back to a neutral «пользователь»; legacy
callers (single-user) pre-migration get «Дарья» behavior unchanged
because db.User row resolution returns Daria's display_name.
"""

from __future__ import annotations

import logging
from typing import Optional

import config
import llm

logger = logging.getLogger(__name__)

MODEL = llm.MODEL_NOVA_LITE
MAX_CHATS = 25
MAX_MSGS_PER_CHAT = 15


def _resolve_user_name(user_id: Optional[int]) -> str:
    """Look up the display_name for the WhatsApp owner. Falls back to a
    neutral generic noun when unknown so prompts never get baked with
    another user's name."""
    if user_id is None:
        return "пользователь"
    try:
        import db
        with db.session_scope() as s:
            u = s.get(db.User, user_id)
            if u and u.display_name:
                return u.display_name
    except Exception as e:
        logger.warning("user lookup failed for WhatsApp summary: %s", e)
    return "пользователь"


def _build_raw_context(unread: list[dict], user_name: str) -> str:
    blocks = []
    for chat in unread[:MAX_CHATS]:
        name = chat.get("name") or chat.get("id", "?")
        n = chat.get("unreadCount", 0)
        msgs = []
        for m in (chat.get("recentMessages") or [])[-MAX_MSGS_PER_CHAT:]:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            sender = user_name if m.get("fromMe") else (m.get("senderName") or "?")
            msgs.append(f"  {sender}: {text}")
        if not msgs:
            continue
        blocks.append(f"### {name} ({n} непрочит.)\n" + "\n".join(msgs))
    return "\n\n".join(blocks)


def summarize_unread_chats(
    unread: list[dict], *, user_id: Optional[int] = None
) -> str:
    """Return a structured Russian-language summary of unread WhatsApp chats.

    Empty string if there's nothing to summarize. On API failure, returns a
    conservative fallback so the caller can still produce output.
    """
    if not unread:
        return ""

    user_name = _resolve_user_name(user_id)
    raw = _build_raw_context(unread, user_name)
    if not raw:
        return ""

    prompt = f"""Ты помощник {user_name}. Перед тобой непрочитанные WhatsApp-чаты {user_name}. Сам {user_name} помечен как "{user_name}" в сообщениях, остальные участники — собеседники.

Твоя задача: сделать структурированную сводку на русском. Не цитируй сообщения дословно — пересказывай суть. У {user_name} есть доступ к WhatsApp, при необходимости откроет чат сам.

Формат строго такой:

🔴 ВАЖНО ОТВЕТИТЬ
• <имя чата> — <2-3 предложения: о чём говорят, что хотят от {user_name}, важный контекст, что обещано/не отвечено>

🟡 МОЖНО ОТВЕТИТЬ ПОЗЖЕ
• <имя чата> — <1 предложение: суть>

⚪ МОЖНО ПРОПУСТИТЬ
• <имя чата>, <имя чата> — <одно слово почему: флуд / реклама / новости>

Правила приоритезации:
- 🔴: прямой вопрос {user_name}, упоминание имени или @, срочная просьба, личный разговор с близкими, эмоциональное содержание, незакрытая договорённость
- 🟡: рабочий чат где мнение {user_name} полезно, друзья делятся новостями, ничего срочного
- ⚪: групповой флуд, мемы, реклама, новостные каналы, рассылки

Если в категории пусто — раздел не выводи вовсе.
Никаких заголовков и преамбул — только три раздела, как описано.

Чаты:

{raw}"""

    try:
        result = llm.chat(
            MODEL,
            "",
            [{"role": "user", "content": prompt}],
            max_tokens=800,
        )
        return result["text"].strip()
    except Exception as e:
        logger.warning("WhatsApp summary failed: %s", e)
        total = sum(c.get("unreadCount", 0) for c in unread)
        return f"⚠️ Не удалось сделать сводку. Непрочитанных: {total} в {len(unread)} чатах."
