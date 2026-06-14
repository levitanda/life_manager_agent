"""Cheap Haiku-based summarizer for WhatsApp unread chats.

Both the morning digest and the on-demand `whatsapp_unread_chats` tool feed
their raw chat data through `summarize_unread_chats` instead of stuffing
raw messages into a Sonnet prompt. Output is a prioritized digest:

  🔴 ВАЖНО ОТВЕТИТЬ — direct questions, mentions, urgent asks, close people
  🟡 МОЖНО ОТВЕТИТЬ ПОЗЖЕ — work/friends, no urgency
  ⚪ МОЖНО ПРОПУСТИТЬ — group flood, spam, news channels
"""

import logging

import anthropic

import config

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_CHATS = 25
MAX_MSGS_PER_CHAT = 15


def _build_raw_context(unread: list[dict]) -> str:
    blocks = []
    for chat in unread[:MAX_CHATS]:
        name = chat.get("name") or chat.get("id", "?")
        n = chat.get("unreadCount", 0)
        msgs = []
        for m in (chat.get("recentMessages") or [])[-MAX_MSGS_PER_CHAT:]:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            sender = "Дарья" if m.get("fromMe") else (m.get("senderName") or "?")
            msgs.append(f"  {sender}: {text}")
        if not msgs:
            continue
        blocks.append(f"### {name} ({n} непрочит.)\n" + "\n".join(msgs))
    return "\n\n".join(blocks)


def summarize_unread_chats(unread: list[dict]) -> str:
    """Return a structured Russian-language summary of unread WhatsApp chats.

    Empty string if there's nothing to summarize. On API failure, returns a
    conservative fallback so the caller can still produce output.
    """
    if not unread:
        return ""

    raw = _build_raw_context(unread)
    if not raw:
        return ""

    prompt = f"""Ты помощник Дарьи. Перед тобой её непрочитанные WhatsApp-чаты. Сама Дарья помечена как "Дарья" в сообщениях, остальные участники — её собеседники.

Твоя задача: сделать структурированную сводку на русском. Не цитируй сообщения дословно — пересказывай суть. У Дарьи есть доступ к WhatsApp, она при необходимости откроет чат сама.

Формат строго такой:

🔴 ВАЖНО ОТВЕТИТЬ
• <имя чата> — <2-3 предложения: о чём говорят, что хотят от Дарьи, важный контекст, что обещано/не отвечено>

🟡 МОЖНО ОТВЕТИТЬ ПОЗЖЕ
• <имя чата> — <1 предложение: суть>

⚪ МОЖНО ПРОПУСТИТЬ
• <имя чата>, <имя чата> — <одно слово почему: флуд / реклама / новости>

Правила приоритезации:
- 🔴: прямой вопрос Дарье, упоминание её имени или @, срочная просьба, личный разговор с близкими, эмоциональное содержание, незакрытая договорённость
- 🟡: рабочий чат где её мнение полезно, друзья делятся новостями, ничего срочного
- ⚪: групповой флуд, мемы, реклама, новостные каналы, рассылки

Если в категории пусто — раздел не выводи вовсе.
Никаких заголовков и преамбул — только три раздела, как описано.

Чаты:

{raw}"""

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning("WhatsApp summary failed: %s", e)
        total = sum(c.get("unreadCount", 0) for c in unread)
        return f"⚠️ Не удалось сделать сводку. Непрочитанных: {total} в {len(unread)} чатах."
