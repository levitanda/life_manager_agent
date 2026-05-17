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


def _format_news(news: list[dict]) -> str:
    if not news:
        return "Новости недоступны."
    lines = []
    current_source = None
    for item in news:
        if item["source"] != current_source:
            current_source = item["source"]
            lines.append(f"\n{current_source}:")
        lines.append(f"  • {item['title']}")
    return "\n".join(lines).strip()


def _format_birthdays(birthdays: list[dict]) -> str:
    if not birthdays:
        return ""
    names = ", ".join(b["name"] for b in birthdays)
    return f"Сегодня день рождения: {names}!"


def generate_morning_digest(
    calendar_events: list[dict],
    short_tasks: list[dict],
    long_tasks: list[dict],
    yesterday_progress: Optional[str],
    emails: Optional[list[dict]] = None,
    target_date: Optional[datetime.date] = None,
    weather: Optional[str] = None,
    news: Optional[list[dict]] = None,
    birthdays: Optional[list[dict]] = None,
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

    weather_section = f"\nПОГОДА: {weather}\n" if weather else ""
    news_section = f"\nНОВОСТИ:\n{_format_news(news)}\n" if news else ""
    birthday_section = f"\n🎂 {_format_birthdays(birthdays)}\n" if birthdays else ""

    prompt = f"""Ты личный ИИ-ассистент. Составь утренний дайджест на русском языке для {date_str}.

СОБЫТИЯ В КАЛЕНДАРЕ:
{_format_events(calendar_events)}

КРАТКОСРОЧНЫЕ ЗАДАЧИ (1-3 дня):
{_format_tasks(short_tasks)}

ДОЛГОСРОЧНЫЕ ЗАДАЧИ (недели/месяцы):
{_format_tasks(long_tasks)}

ПРОГРЕСС ЗА ВЧЕРА:
{yesterday_progress or "Нет данных."}
{weather_section}{birthday_section}{emails_section}{news_section}
Напиши дружелюбный, мотивирующий дайджест. Структура:
1. Приветствие с датой и погодой (если есть); если есть дни рождения — обязательно упомяни их тепло
2. Что сегодня в расписании
3. На чём сосредоточиться из задач (приоритеты)
4. Важные письма — только если есть что-то требующее ответа или действия (1-3 письма максимум)
5. Новости — ОБЯЗАТЕЛЬНО отдельно по каждому каналу:
   • Кан 11: 2-3 главные темы + одна фраза — общий фон новостей канала
   • Кешет 12: 2-3 главные темы + одна фраза — общий фон
   • Дождь: 2-3 главные темы + одна фраза — общий фон
   Если какого-то канала нет в данных — пропусти его без упоминания.
6. Напоминание о долгосрочных целях
7. Одна идея для отдыха или развития на сегодня
8. Короткое мотивирующее напутствие

Будь конкретным, не повторяй просто список — дай осмысленные рекомендации. Пиши живым языком, без канцеляризма."""

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def generate_weekly_digest(
    week_events: dict,
    short_tasks: list[dict],
    long_tasks: list[dict],
) -> str:
    tz = pytz.timezone(config.TIMEZONE)
    today = datetime.datetime.now(tz).date()

    weekday_ru_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    events_lines = []
    for date_str in sorted(week_events.keys()):
        date = datetime.date.fromisoformat(date_str)
        label = weekday_ru_short[date.weekday()]
        day_evs = week_events[date_str]
        day_lines = "  " + "\n  ".join(f"{ev['time']} — {ev['title']}" for ev in day_evs)
        events_lines.append(f"{label} {date.strftime('%d.%m')}:\n{day_lines}")

    events_text = "\n".join(events_lines) if events_lines else "Событий не запланировано."

    prompt = f"""Ты личный ИИ-ассистент. Составь еженедельный обзор расписания на русском языке (неделя с {today.strftime('%d.%m.%Y')}).

СОБЫТИЯ НА НЕДЕЛЮ:
{events_text}

КРАТКОСРОЧНЫЕ ЗАДАЧИ:
{_format_tasks(short_tasks)}

ДОЛГОСРОЧНЫЕ ЗАДАЧИ:
{_format_tasks(long_tasks)}

Составь дружелюбный и полезный обзор недели. Структура:
1. Общая картина недели: насколько она загружена
2. Самые важные события и встречи по дням
3. Задачи — на что обратить внимание, приоритеты
4. Рекомендации по планированию: где есть окна, что лучше сделать заранее
5. Короткое мотивирующее слово

Пиши конкретно и по делу, без пустых фраз."""

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
