"""Generate the morning digest using Claude API."""

import datetime
from typing import Optional

import anthropic
import pytz

import config


def _format_events(events: list[dict]) -> str:
    if not events:
        return "Нет запланированных событий."
    lines = []
    for ev in events:
        time = ev["time"]
        if "T" in time:
            try:
                dt = datetime.datetime.fromisoformat(time)
                tz = pytz.timezone(config.TIMEZONE)
                dt = dt.astimezone(tz)
                time = dt.strftime("%H:%M")
            except Exception:
                pass
        lines.append(f"• {time} — {ev['title']}")
    return "\n".join(lines)


def _format_tasks(tasks: list[dict]) -> str:
    if not tasks:
        return "Нет активных задач."
    lines = []
    for i, t in enumerate(tasks, 1):
        lines.append(f"{i}. {t['title']} (до {t['due']})")
    return "\n".join(lines)


def _format_emails(emails: list[dict]) -> str:
    if not emails:
        return "Нет непрочитанных писем."
    lines = []
    for e in emails:
        lines.append(f"• От: {e['from']}\n  Тема: {e['subject']}\n  {e['snippet']}")
    return "\n".join(lines)


def generate_morning_digest(
    calendar_events: list[dict],
    short_tasks: list[dict],
    long_tasks: list[dict],
    yesterday_progress: Optional[str],
    emails: Optional[list[dict]] = None,
    target_date: Optional[datetime.date] = None,
    weather: Optional[str] = None,
) -> str:
    tz = pytz.timezone(config.TIMEZONE)
    ref_dt = (
        datetime.datetime.combine(target_date, datetime.time(), tzinfo=tz)
        if target_date
        else datetime.datetime.now(tz)
    )
    weekday_ru = [
        "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"
    ][ref_dt.weekday()]
    date_str = ref_dt.strftime(f"%d %B %Y, {weekday_ru}")

    emails_section = ""
    if emails is not None:
        emails_section = f"\nНЕПРОЧИТАННЫЕ ПИСЬМА (последние 2 дня):\n{_format_emails(emails)}\n"

    weather_section = f"\nПОГОДА В НЕШЕРЕ: {weather}\n" if weather else ""

    prompt = f"""Ты личный ИИ-ассистент. Составь утренний дайджест на русском языке для {date_str}.

СОБЫТИЯ В КАЛЕНДАРЕ:
{_format_events(calendar_events)}

КРАТКОСРОЧНЫЕ ЗАДАЧИ (1-3 дня):
{_format_tasks(short_tasks)}

ДОЛГОСРОЧНЫЕ ЗАДАЧИ (недели/месяцы):
{_format_tasks(long_tasks)}

ПРОГРЕСС ЗА ВЧЕРА:
{yesterday_progress or "Нет данных."}
{weather_section}{emails_section}
Напиши дружелюбный, мотивирующий дайджест. Структура:
1. Приветствие с датой и погодой (если есть)
2. Что сегодня в расписании
3. На чём сосредоточиться из задач (приоритеты)
4. Важные письма — только если есть что-то требующее ответа или действия (1-3 письма максимум, остальные игнорируй)
5. Напоминание о долгосрочных целях
6. Одна идея для отдыха или развития на сегодня
7. Короткое мотивирующее напутствие

Будь конкретным, не повторяй просто список — дай осмысленные рекомендации. Пиши живым языком, без канцеляризма."""

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def generate_evening_checkin() -> str:
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.datetime.now(tz)
    weekday_ru = [
        "понедельника", "вторника", "среды", "четверга", "пятницы", "субботы", "воскресенья"
    ][today.weekday()]

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": f"Напиши короткое (2-3 предложения) дружелюбное вечернее сообщение для {weekday_ru}. Попроси рассказать о прогрессе по задачам за день: что сделано, что нет, что перенесено. Без вступлений, сразу текст сообщения.",
            }
        ],
    )
    return message.content[0].text
