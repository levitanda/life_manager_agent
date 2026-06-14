"""Personal diary — local Markdown file mirrored to a Google Doc.

Two modes:

- **Legacy single-user (user_id=None)**: paths come from `config.DIARY_FILE` and
  `config.DIARY_DOC_CACHE`. Google credentials from the single shared token file.

- **Multi-tenant (user_id given)**: paths are `data/users/{user_id}/diary.md` and
  `data/users/{user_id}/diary_doc.json`. Google credentials come from the
  encrypted per-user token in the DB.

Source of truth in both modes is the local Markdown file. The Google Doc
mirror is best-effort — failures don't break the write.

Public API:
    append(text, when=None, user_id=None)
    read(period="today", limit_chars=8000, user_id=None)
    doc_url(user_id=None)
    backfill_from_summaries(summaries_file=None, user_id=None)
    resync_doc(user_id=None)
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import pytz

import config
from google_auth import get_credentials

logger = logging.getLogger(__name__)

_DAY_HEADER_RE = re.compile(r"^# (\d{4}-\d{2}-\d{2})\b", re.MULTILINE)
_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_RU_WEEKDAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


# ─── Path resolution ──────────────────────────────────────────────────────────

def _diary_file(user_id: Optional[int]) -> str:
    if user_id is None:
        return config.DIARY_FILE
    import db
    base = Path(db.data_dir(), "users", str(user_id))
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "diary.md")


def _doc_cache_file(user_id: Optional[int]) -> str:
    if user_id is None:
        return config.DIARY_DOC_CACHE
    import db
    base = Path(db.data_dir(), "users", str(user_id))
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "diary_doc.json")


def _summaries_file_for(user_id: Optional[int]) -> str:
    """Default location for session_summaries.jsonl used by backfill."""
    if user_id is None:
        return "session_summaries.jsonl"
    import db
    return str(Path(db.data_dir(), "users", str(user_id), "session_summaries.jsonl"))


def _now() -> datetime.datetime:
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def _format_day_header(d: datetime.date) -> str:
    """Returns '# 2026-06-14 — суббота, 14 июня 2026' (ISO + human)."""
    human = f"{_RU_WEEKDAYS[d.weekday()]}, {d.day} {_RU_MONTHS[d.month - 1]} {d.year}"
    return f"# {d.isoformat()} — {human}"


# ─── Local file (source of truth) ─────────────────────────────────────────────

def _read_local(user_id: Optional[int] = None) -> str:
    path = _diary_file(user_id)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_local(content: str, user_id: Optional[int] = None) -> None:
    with open(_diary_file(user_id), "w", encoding="utf-8") as f:
        f.write(content)


def _prepend_to_local(new_day_header: str, body: str, user_id: Optional[int] = None) -> None:
    """Insert a fresh day section at the top of the file."""
    existing = _read_local(user_id)
    block = f"{new_day_header}\n{body}\n"
    if existing:
        block = block + "\n" + existing
    _write_local(block, user_id)


def _append_paragraph_to_today_local(
    day: datetime.date, paragraph: str, user_id: Optional[int] = None
) -> bool:
    """If today's section exists at the top, append paragraph under it.

    Returns True if today's section was found and updated, False otherwise.
    """
    content = _read_local(user_id)
    if not content:
        return False
    iso = day.isoformat()
    m = _DAY_HEADER_RE.search(content)
    if not m or m.group(1) != iso:
        return False
    next_header = _DAY_HEADER_RE.search(content, m.end())
    insert_at = next_header.start() if next_header else len(content)
    head = content[:insert_at].rstrip()
    tail = content[insert_at:]
    new_content = f"{head}\n{paragraph}\n"
    if tail:
        new_content = new_content + "\n" + tail
    _write_local(new_content, user_id)
    return True


# ─── Google Doc mirror ───────────────────────────────────────────────────────

def _docs_service(user_id: Optional[int] = None):
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=get_credentials(user_id))


def _drive_service(user_id: Optional[int] = None):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=get_credentials(user_id))


def _load_doc_id(user_id: Optional[int] = None) -> Optional[str]:
    path = _doc_cache_file(user_id)
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path)).get("doc_id")
    except Exception:
        return None


def _save_doc_id(doc_id: str, user_id: Optional[int] = None) -> None:
    with open(_doc_cache_file(user_id), "w") as f:
        json.dump({"doc_id": doc_id}, f)


def _create_doc(user_id: Optional[int] = None) -> str:
    drive = _drive_service(user_id)
    result = drive.files().create(
        body={
            "name": config.DIARY_DOC_TITLE,
            "mimeType": "application/vnd.google-apps.document",
        },
        fields="id, webViewLink",
    ).execute()
    doc_id = result["id"]
    logger.info("Created diary Google Doc %s (%s)", doc_id, result.get("webViewLink"))
    _save_doc_id(doc_id, user_id)
    return doc_id


def _get_or_create_doc_id(user_id: Optional[int] = None) -> str:
    doc_id = _load_doc_id(user_id)
    if doc_id:
        return doc_id
    return _create_doc(user_id)


def _prepend_to_doc(doc_id: str, text: str, user_id: Optional[int] = None) -> None:
    """Insert `text` at the very top of the doc (just after document start)."""
    docs = _docs_service(user_id)
    requests = [{"insertText": {"location": {"index": 1}, "text": text}}]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()


def _read_doc_text(doc_id: str, user_id: Optional[int] = None) -> str:
    """Read full plain text of a Google Doc."""
    docs = _docs_service(user_id)
    doc = docs.documents().get(documentId=doc_id).execute()
    chunks = []
    for el in doc.get("body", {}).get("content", []):
        for run in el.get("paragraph", {}).get("elements", []):
            t = run.get("textRun", {}).get("content")
            if t:
                chunks.append(t)
    return "".join(chunks)


def _today_section_exists_in_doc(
    doc_id: str, day: datetime.date, user_id: Optional[int] = None
) -> bool:
    text = _read_doc_text(doc_id, user_id)
    m = _DAY_HEADER_RE.search(text)
    return bool(m and m.group(1) == day.isoformat())


def _append_paragraph_to_today_in_doc(
    doc_id: str, day: datetime.date, paragraph: str, user_id: Optional[int] = None
) -> bool:
    """If today's section is at the very top of the doc, insert paragraph right after the header line."""
    docs = _docs_service(user_id)
    doc = docs.documents().get(documentId=doc_id).execute()
    header_end_index: Optional[int] = None
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        line = "".join(
            r.get("textRun", {}).get("content", "")
            for r in para.get("elements", [])
        )
        m = _DAY_HEADER_RE.match(line.strip())
        if m:
            if m.group(1) == day.isoformat():
                header_end_index = el["endIndex"]
            break
    if header_end_index is None:
        return False
    requests = [{
        "insertText": {
            "location": {"index": header_end_index - 1},
            "text": paragraph + "\n",
        }
    }]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return True


# ─── Public API ──────────────────────────────────────────────────────────────

def append(
    text: str,
    when: Optional[datetime.datetime] = None,
    user_id: Optional[int] = None,
) -> dict:
    """Append a diary entry. Returns {ok, doc_url?, error?}.

    Writes to local file always. Best-effort sync to Google Doc.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty text"}

    when = when or _now()
    day = when.date()
    stamp = when.strftime("%H:%M")
    paragraph = f"[{stamp}] {text}"

    # 1. Local file (must succeed)
    try:
        if not _append_paragraph_to_today_local(day, paragraph, user_id):
            _prepend_to_local(_format_day_header(day), paragraph, user_id)
    except Exception as e:
        logger.exception("Diary local write failed")
        return {"ok": False, "error": f"local write failed: {e}"}

    # 2. Google Doc mirror (best-effort)
    doc_url_val = None
    try:
        doc_id = _get_or_create_doc_id(user_id)
        if not _append_paragraph_to_today_in_doc(doc_id, day, paragraph, user_id):
            header = _format_day_header(day)
            _prepend_to_doc(doc_id, f"{header}\n{paragraph}\n\n", user_id)
        doc_url_val = f"https://docs.google.com/document/d/{doc_id}/edit"
    except Exception as e:
        logger.warning("Diary Google Doc sync failed (local copy is safe): %s", e)
        return {"ok": True, "doc_synced": False, "error": str(e)}

    return {"ok": True, "doc_synced": True, "doc_url": doc_url_val}


def read(
    period: str = "today",
    limit_chars: int = 8000,
    user_id: Optional[int] = None,
) -> str:
    """Return diary content for a period.

    period:
      - "today"       — only today's section
      - "yesterday"   — only yesterday's section
      - "week"        — last 7 days
      - "month"       — last 30 days
      - "all"         — entire file (capped at limit_chars)
      - "YYYY-MM"     — specific month (e.g. "2026-06")
      - "YYYY-MM-DD"  — specific day
    """
    content = _read_local(user_id)
    if not content:
        return "Дневник пока пуст."

    today = _now().date()

    def _sections() -> list[tuple[datetime.date, str]]:
        matches = list(_DAY_HEADER_RE.finditer(content))
        out = []
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            try:
                d = datetime.date.fromisoformat(m.group(1))
            except ValueError:
                continue
            out.append((d, content[start:end].rstrip()))
        return out

    sections = _sections()
    if not sections:
        return content[:limit_chars]

    if period == "today":
        picked = [s for d, s in sections if d == today]
    elif period == "yesterday":
        y = today - datetime.timedelta(days=1)
        picked = [s for d, s in sections if d == y]
    elif period == "week":
        cutoff = today - datetime.timedelta(days=7)
        picked = [s for d, s in sections if d >= cutoff]
    elif period == "month":
        cutoff = today - datetime.timedelta(days=30)
        picked = [s for d, s in sections if d >= cutoff]
    elif period == "all":
        picked = [s for _, s in sections]
    elif re.fullmatch(r"\d{4}-\d{2}", period):
        prefix = period
        picked = [s for d, s in sections if d.isoformat().startswith(prefix)]
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        target = datetime.date.fromisoformat(period)
        picked = [s for d, s in sections if d == target]
    else:
        return f"Не понимаю период «{period}». Используй: today, yesterday, week, month, all, YYYY-MM, YYYY-MM-DD."

    if not picked:
        return f"За «{period}» записей в дневнике нет."
    text = "\n\n".join(picked)
    return text if len(text) <= limit_chars else text[:limit_chars] + "\n…(обрезано)"


def doc_url(user_id: Optional[int] = None) -> Optional[str]:
    """Public URL of the diary Google Doc (or None if not created yet)."""
    doc_id = _load_doc_id(user_id)
    return f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else None


def _sort_local_by_date_desc(user_id: Optional[int] = None) -> None:
    """Re-sort the local diary so day sections appear newest first."""
    content = _read_local(user_id)
    if not content.strip():
        return
    matches = list(_DAY_HEADER_RE.finditer(content))
    if not matches:
        return
    sections: list[tuple[datetime.date, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        try:
            d = datetime.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        sections.append((d, content[start:end].rstrip()))
    sections.sort(key=lambda s: s[0], reverse=True)
    _write_local("\n\n".join(s[1] for s in sections) + "\n", user_id)


def _resync_doc_from_local(user_id: Optional[int] = None) -> None:
    """Replace the entire Google Doc content with the current local file."""
    content = _read_local(user_id)
    doc_id = _get_or_create_doc_id(user_id)
    docs = _docs_service(user_id)
    doc = docs.documents().get(documentId=doc_id).execute()
    body_content = doc.get("body", {}).get("content", [])
    requests = []
    if body_content:
        end_index = body_content[-1].get("endIndex", 1)
        if end_index > 2:
            requests.append({"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end_index - 1}}})
    if content:
        requests.append({"insertText": {"location": {"index": 1}, "text": content}})
    if requests:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()


def backfill_from_summaries(
    summaries_file: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """One-time backfill: pull past session summaries into diary sections.

    Defaults `summaries_file` to the per-user path
    `data/users/{user_id}/session_summaries.jsonl` (or
    `session_summaries.jsonl` in legacy mode).

    Safe to re-run: days already in the diary are skipped, not overwritten.
    """
    summaries_file = summaries_file or _summaries_file_for(user_id)
    if not os.path.exists(summaries_file):
        return {"ok": False, "error": f"summaries file not found: {summaries_file}"}

    by_date: dict[str, list[tuple[str, str]]] = {}
    with open(summaries_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            ts = entry.get("date", "")
            summ = (entry.get("summary") or "").strip()
            if not ts or not summ:
                continue
            date_part, _, time_part = ts.partition(" ")
            try:
                datetime.date.fromisoformat(date_part)
            except ValueError:
                continue
            by_date.setdefault(date_part, []).append((time_part or "23:30", summ))

    existing = _read_local(user_id)
    existing_dates = set(_DAY_HEADER_RE.findall(existing))

    added: list[str] = []
    skipped: list[str] = []
    diary_path = _diary_file(user_id)
    for date_str in sorted(by_date.keys()):
        if date_str in existing_dates:
            skipped.append(date_str)
            continue
        try:
            d = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        paragraphs = []
        for time_str, summ in sorted(by_date[date_str]):
            cleaned = re.sub(r"^#+\s.*?\n+", "", summ).strip()
            paragraphs.append(f"[{time_str}] Из памяти: {cleaned}")
        header = _format_day_header(d)
        body = "\n\n".join(paragraphs)
        # Append to local file unsorted — we'll sort at the end.
        with open(diary_path, "a", encoding="utf-8") as f:
            f.write(("\n\n" if existing else "") + header + "\n" + body + "\n")
        existing = "exists"
        added.append(date_str)
        existing_dates.add(date_str)

    if added:
        _sort_local_by_date_desc(user_id)
        try:
            _resync_doc_from_local(user_id)
        except Exception as e:
            logger.warning("Diary backfill: Google Doc resync failed: %s", e)

    return {
        "ok": True,
        "days_added": len(added),
        "days_skipped": len(skipped),
        "added_dates": added,
        "skipped_dates": skipped,
    }


def resync_doc(user_id: Optional[int] = None) -> dict:
    """Force sort the local diary and rewrite the Google Doc to match."""
    _sort_local_by_date_desc(user_id)
    try:
        _resync_doc_from_local(user_id)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "doc_url": doc_url(user_id)}
