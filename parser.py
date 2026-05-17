"""Parse natural language messages: intent detection and task extraction."""

import datetime
import json
import logging

import anthropic
import pytz

import config

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Ты помощник, который извлекает структурированные данные из сообщений пользователя. "
    "Отвечай ТОЛЬКО валидным JSON, без пояснений и markdown."
)


def _call(prompt: str, max_tokens: int = 300) -> dict:
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def parse_message(
    text: str,
    active_tasks: list[dict] | None = None,
    history: list[dict] | None = None,
    summaries: list[dict] | None = None,
) -> dict:
    """
    Determine intent and extract all needed data from a free-form message.

    Returns:
      intent          — add_task | complete_task | show_tasks | get_digest |
                        save_progress | send_email | chat
      task_type       — short | long | null
      title           — clean task title | null
      date            — YYYY-MM-DD | null
      end_date        — YYYY-MM-DD | null
      time            — HH:MM | null
      duration_minutes— int (default 60)
      task_number     — int (1-based index into active_tasks list) | null
      to_email        — recipient email address | null
      to_name         — recipient name | null
      email_subject   — email subject | null
      email_body      — email body text | null
      reply           — string to send back for chat intent | null
    """
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)

    summaries_block = ""
    if summaries:
        lines = [f"[{s['date']}] {s['summary']}" for s in summaries]
        summaries_block = "=== ДОЛГОСРОЧНАЯ ПАМЯТЬ (прошлые сессии) ===\n" + "\n\n".join(lines) + "\n\n"

    tasks_block = ""
    if active_tasks:
        lines = [f"{i}. [{t['type']}] {t['title']}" for i, t in enumerate(active_tasks, 1)]
        tasks_block = "Активные задачи:\n" + "\n".join(lines) + "\n\n"

    history_block = ""
    if history:
        lines = []
        for msg in history[-8:]:
            role = "Пользователь" if msg["role"] == "user" else "Ты"
            lines.append(f"{role}: {msg['content'][:200]}")
        history_block = "=== ТЕКУЩАЯ СЕССИЯ ===\n" + "\n".join(lines) + "\n\n"

    prompt = f"""Ты личный ИИ-ассистент Дарьи. Ты помогаешь ей управлять задачами, письмами, планами и жизнью в целом. Ты знаешь её давно и помнишь историю.

Сейчас {now.strftime('%Y-%m-%d %H:%M')}, часовой пояс {config.TIMEZONE}.

{summaries_block}{history_block}{tasks_block}

{history_block}{tasks_block}Новое сообщение пользователя: «{text}»

Пользователь может попросить сделать НЕСКОЛЬКО вещей в одном сообщении. Верни JSON со списком всех действий:
{{
  "actions": [
    {{
      "intent": "add_task" | "complete_task" | "delete_task" | "reschedule_task" | "show_tasks" | "get_digest" | "get_weekly_digest" | "find_free_time" | "get_weather" | "send_to_alice" | "save_progress" | "send_email" | "chat",
      "task_type": "short" | "long" | null,
      "title": "название без дат/времени" | null,
      "date": "YYYY-MM-DD" | null,
      "end_date": "YYYY-MM-DD" | null,
      "time": "HH:MM" | null,
      "duration_minutes": число (по умолчанию 60),
      "task_number": номер_из_списка_или_null,
      "to_email": "email@example.com или null",
      "to_name": "имя получателя или null",
      "email_subject": "тема письма или null",
      "email_body": "текст письма или null",
      "attendees": ["Имя1", "Имя2"] или null,
      "reply": "короткий ответ если intent=chat" | null
    }}
  ]
}}

Правила intent:
- add_task short: конкретное дело на 1-3 дня
- add_task long: цель/проект на недели и месяцы
- complete_task: сделал/выполнил/завершил — найди task_number по смыслу
- delete_task: удали/убери/отмени задачу или событие — найди task_number по смыслу
- add_task: если упомянуты люди которых нужно пригласить — добавь их имена в attendees
- send_email: хочет отправить письмо/сообщение по email — сгенерируй тему и текст письма
- show_tasks: показать список задач
- get_digest: дайджест или план на день
- get_weekly_digest: план на неделю, что на этой неделе, недельный обзор
- reschedule_task: перенеси/измени время задачи или события — найди task_number по смыслу; новые дата/время в date и time
- find_free_time: найди свободное время, когда я свободна/свободен — заполни date и duration_minutes
- get_weather: узнать погоду — заполни "title" названием города (если не указан — null, используем Нешер)
- send_to_alice: отправить/передать сообщение Алисе чтобы она прочитала вслух — текст сообщения в "reply"
- save_progress: итоги дня, прогресс
- chat: всё остальное, используй историю для контекста
Если одно действие — всё равно верни список из одного элемента."""

    try:
        return _call(prompt)
    except Exception as e:
        logger.warning("parse_message fallback (%s): %s", e, text)
        return {"intent": "chat", "reply": "Не понял, попробуй ещё раз."}


def parse_task(raw_text: str) -> dict:
    """Lightweight parser used by /add command (kept for backwards compat)."""
    tz = pytz.timezone(config.TIMEZONE)
    now = datetime.datetime.now(tz)
    prompt = (
        f"Сейчас {now.strftime('%Y-%m-%d %H:%M')}, часовой пояс {config.TIMEZONE}.\n\n"
        "Извлеки из текста задачи поля и верни ТОЛЬКО JSON:\n"
        '{"title": "название без дат/времени", '
        '"date": "YYYY-MM-DD или null", '
        '"end_date": "YYYY-MM-DD или null (если диапазон дат)", '
        '"time": "HH:MM или null", '
        '"duration_minutes": число_или_60}\n\n'
        f'Текст: "{raw_text}"'
    )
    try:
        return _call(prompt)
    except Exception as e:
        logger.warning("parse_task fallback (%s): %s", e, raw_text)
        return {"title": raw_text, "date": None, "end_date": None, "time": None, "duration_minutes": 60}
