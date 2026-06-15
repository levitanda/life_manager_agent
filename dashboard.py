"""Dashboard aggregation + formatting.

Two surfaces:
1. Telegram text — build_telegram_dashboard(user_id) returns markdown +
   InlineKeyboardMarkup
2. Web HTML — render_html_dashboard(user_id) returns an SSR HTML page
   for the FastAPI /dashboard endpoint

Sources:
- Active tasks: calendar_client.get_active_tasks (short + long)
- Active goals: goals + goal_progress tables (Phase C schema)
- Streaks: diary file day-header count
- Today's calendar: calendar_client.get_todays_calendar_events
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path
from typing import Optional

import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import calendar_client
import config
from i18n import t, user_language

logger = logging.getLogger(__name__)

_DAY_HEADER_RE = re.compile(r"^# (\d{4}-\d{2}-\d{2})\b", re.MULTILINE)


# ─── Aggregation ──────────────────────────────────────────────────────────────


def _compute_streaks(diary_path: Path) -> tuple[int, int]:
    """Parse day headers from the diary file. Return (current_streak,
    longest_streak) — number of consecutive days ending today (and
    the longest run ever).
    """
    if not diary_path.exists():
        return 0, 0
    try:
        text = diary_path.read_text(encoding="utf-8")
    except Exception:
        return 0, 0
    dates = set()
    for iso in _DAY_HEADER_RE.findall(text):
        try:
            dates.add(datetime.date.fromisoformat(iso))
        except ValueError:
            continue
    if not dates:
        return 0, 0
    sorted_dates = sorted(dates, reverse=True)

    today = datetime.date.today()
    current = 0
    # Count back from today/yesterday
    cursor = today
    while cursor in dates:
        current += 1
        cursor -= datetime.timedelta(days=1)
    # If today wasn't in the set, try counting from yesterday
    if current == 0 and (today - datetime.timedelta(days=1)) in dates:
        cursor = today - datetime.timedelta(days=1)
        while cursor in dates:
            current += 1
            cursor -= datetime.timedelta(days=1)

    # Longest streak: find longest run of consecutive dates in sorted set
    longest = 0
    run = 0
    sorted_asc = sorted(dates)
    prev: Optional[datetime.date] = None
    for d in sorted_asc:
        if prev is not None and (d - prev) == datetime.timedelta(days=1):
            run += 1
        else:
            run = 1
        if run > longest:
            longest = run
        prev = d
    return current, longest


def _user_goals(user_id: int) -> list[dict]:
    """Return active goals for a user (private + via group membership).
    Each dict has: id, title, latest_pct (0-100 or None), latest_ts,
    progress_count."""
    import db
    with db.session_scope() as s:
        # Goals the user owns OR that belong to groups the user is in
        group_ids = [r.group_id for r in s.query(db.GroupMember).filter_by(user_id=user_id).all()]
        q = s.query(db.Goal).filter(
            db.Goal.status == "active",
        )
        from sqlalchemy import or_
        if group_ids:
            q = q.filter(or_(db.Goal.user_id == user_id, db.Goal.group_id.in_(group_ids)))
        else:
            q = q.filter(db.Goal.user_id == user_id)
        goals = q.all()
        out = []
        for g in goals:
            progress_rows = (
                s.query(db.GoalProgress)
                .filter_by(goal_id=g.id)
                .order_by(db.GoalProgress.ts.desc())
                .all()
            )
            latest_pct = progress_rows[0].pct if progress_rows else None
            latest_ts = progress_rows[0].ts if progress_rows else None
            out.append({
                "id": g.id,
                "title": g.title,
                "latest_pct": latest_pct,
                "latest_ts": latest_ts,
                "progress_count": len(progress_rows),
            })
        return out


def _user_context(user_id: int) -> dict:
    import db
    with db.session_scope() as s:
        u = s.get(db.User, user_id)
        if u is None:
            return {"name": None, "timezone": config.TIMEZONE, "language": "ru"}
        return {
            "name": u.display_name or "пользователь",
            "timezone": u.timezone or config.TIMEZONE,
            "language": getattr(u, "language", None) or "ru",
        }


def _format_today(tz_name: str, language: str) -> str:
    tz = pytz.timezone(tz_name)
    now = datetime.datetime.now(tz)
    if language == "en":
        return now.strftime("%A, %B %d %Y")
    if language == "he":
        return now.strftime("%Y-%m-%d")
    weekday_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][now.weekday()]
    return now.strftime(f"%d %B %Y, {weekday_ru}")


def _progress_bar(pct: Optional[int], width: int = 10) -> str:
    if pct is None:
        pct = 0
    filled = max(0, min(width, round(pct * width / 100)))
    return "▓" * filled + "░" * (width - filled)


# ─── Telegram dashboard ──────────────────────────────────────────────────────


def build_telegram_dashboard(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Render the dashboard as markdown + return an inline keyboard."""

    ctx = _user_context(user_id)
    lang = ctx["language"]
    name = ctx["name"]

    try:
        short = calendar_client.get_active_tasks("short", user_id=user_id) or []
    except Exception as e:
        logger.warning("dashboard: short tasks fetch failed: %s", e)
        short = []
    try:
        long_ = calendar_client.get_active_tasks("long", user_id=user_id) or []
    except Exception as e:
        logger.warning("dashboard: long tasks fetch failed: %s", e)
        long_ = []
    try:
        today_events = calendar_client.get_todays_calendar_events(user_id=user_id) or []
    except Exception as e:
        logger.warning("dashboard: today calendar fetch failed: %s", e)
        today_events = []

    goals = _user_goals(user_id)

    import db
    diary_path = Path(db.data_dir(), "users", str(user_id), "diary.md")
    current_streak, longest_streak = _compute_streaks(diary_path)

    parts = [
        t("dashboard.title", lang, name=name, date=_format_today(ctx["timezone"], lang)),
    ]

    # If nothing at all, show the empty-state hint and return early
    if not short and not long_ and not goals and not today_events:
        parts.append("")
        parts.append(t("dashboard.no_data", lang))
        return "\n".join(parts), _dashboard_keyboard(user_id, lang)

    parts.append("")
    parts.append(t("dashboard.active_tasks", lang))
    parts.append(t("dashboard.short_tasks", lang, n=len(short)))
    parts.append(t("dashboard.long_tasks", lang, n=len(long_)))

    parts.append("")
    parts.append(t("dashboard.streak", lang, current=current_streak, longest=longest_streak))

    parts.append("")
    parts.append(t("dashboard.goals_header", lang))
    if not goals:
        parts.append(t("dashboard.no_goals", lang))
    else:
        for g in goals[:10]:
            pct = g["latest_pct"] if g["latest_pct"] is not None else 0
            bar = _progress_bar(g["latest_pct"])
            parts.append(f"• {g['title']}\n  {bar} {pct}% _(записей: {g['progress_count']})_")

    if today_events:
        parts.append("")
        parts.append(t("dashboard.today_calendar", lang))
        for ev in today_events[:8]:
            time_part = ev.get("time") or ""
            if "T" in time_part:
                try:
                    dt = datetime.datetime.fromisoformat(time_part)
                    time_part = dt.strftime("%H:%M")
                except Exception:
                    pass
            parts.append(f"  {time_part} {ev.get('title', '')}")
    else:
        parts.append("")
        parts.append(t("dashboard.no_calendar", lang))

    return "\n".join(parts), _dashboard_keyboard(user_id, lang)


def _dashboard_keyboard(user_id: int, language: str) -> InlineKeyboardMarkup:
    rows = []
    try:
        import web
        token = web.sign_dashboard_token(user_id)
        import os
        base = os.environ.get("PUBLIC_BASE_URL", "https://dalev.click")
        url = f"{base}/dashboard/{user_id}?token={token}"
        rows.append([InlineKeyboardButton(t("dashboard.open_browser", language), url=url)])
    except Exception as e:
        logger.warning("dashboard: web token sign failed: %s", e)
    rows.append([
        InlineKeyboardButton(t("dashboard.goals_btn", language), callback_data="dashboard:goals"),
        InlineKeyboardButton(t("dashboard.tasks_btn", language), callback_data="dashboard:tasks"),
    ])
    return InlineKeyboardMarkup(rows)


# ─── Web HTML dashboard ──────────────────────────────────────────────────────


def render_html_dashboard(user_id: int) -> str:

    ctx = _user_context(user_id)
    name = ctx["name"]
    lang = ctx["language"]
    try:
        short = calendar_client.get_active_tasks("short", user_id=user_id) or []
    except Exception:
        short = []
    try:
        long_ = calendar_client.get_active_tasks("long", user_id=user_id) or []
    except Exception:
        long_ = []
    goals = _user_goals(user_id)

    import db
    current_streak, longest_streak = _compute_streaks(
        Path(db.data_dir(), "users", str(user_id), "diary.md")
    )

    goal_rows = "".join(
        f"<li class='py-2'>"
        f"<div class='flex justify-between text-sm'>"
        f"<span>{g['title']}</span>"
        f"<span class='text-gray-500'>{g['latest_pct'] or 0}%</span>"
        f"</div>"
        f"<div class='w-full bg-gray-200 rounded-full h-2'>"
        f"<div class='bg-blue-500 h-2 rounded-full' style='width: {g['latest_pct'] or 0}%'></div>"
        f"</div></li>"
        for g in goals
    ) or "<li class='text-gray-500'>—</li>"

    dir_attr = ' dir="rtl"' if lang == "he" else ''

    return f"""<!DOCTYPE html>
<html lang="{lang}"{dir_attr}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-800 font-sans">
<div class="max-w-2xl mx-auto p-6">
  <h1 class="text-3xl font-bold mb-2">{name}</h1>
  <p class="text-gray-500 mb-8">{_format_today(ctx['timezone'], lang)}</p>

  <div class="grid grid-cols-2 gap-4 mb-8">
    <div class="bg-white rounded-2xl shadow p-4 text-center">
      <div class="text-4xl font-bold text-blue-600">{len(short)}</div>
      <div class="text-sm text-gray-500 mt-1">{t('dashboard.short_tasks', lang, n='')}</div>
    </div>
    <div class="bg-white rounded-2xl shadow p-4 text-center">
      <div class="text-4xl font-bold text-purple-600">{len(long_)}</div>
      <div class="text-sm text-gray-500 mt-1">{t('dashboard.long_tasks', lang, n='')}</div>
    </div>
  </div>

  <div class="bg-white rounded-2xl shadow p-4 mb-8">
    <div class="flex items-center gap-3 text-orange-500">
      <span class="text-3xl">🔥</span>
      <div>
        <div class="text-2xl font-bold">{current_streak}</div>
        <div class="text-xs text-gray-500">текущий стрик • {longest_streak} рекорд</div>
      </div>
    </div>
  </div>

  <div class="bg-white rounded-2xl shadow p-4 mb-8">
    <h2 class="font-semibold mb-3">🎯 {t('dashboard.goals_header', lang)}</h2>
    <ul>{goal_rows}</ul>
  </div>

  <p class="text-xs text-gray-400 text-center">Powered by life-agent</p>
</div>
</body>
</html>"""
