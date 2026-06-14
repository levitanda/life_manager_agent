"""Personal diary — local Markdown file mirrored to a Google Doc.

Design:
- Single Markdown file `diary.md` is the source of truth on disk.
- A Google Doc (auto-created on first write) mirrors the same content.
- One entry per calendar day, newest day on top.
- Within a day, paragraphs are appended in time order with `[HH:MM]` prefix.
- Reads always come from the local file (fast, no quota).

Public API:
    append(text, when=None)           # write an entry to today's section
    read(period="today", limit=None)  # return diary text for the given period
"""

import datetime
import json
import logging
import os
import re
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


def _now() -> datetime.datetime:
    return datetime.datetime.now(pytz.timezone(config.TIMEZONE))


def _format_day_header(d: datetime.date) -> str:
    """Returns '# 2026-06-14 — суббота, 14 июня 2026' (ISO + human)."""
    human = f"{_RU_WEEKDAYS[d.weekday()]}, {d.day} {_RU_MONTHS[d.month - 1]} {d.year}"
    return f"# {d.isoformat()} — {human}"


# ─── Local file (source of truth) ─────────────────────────────────────────────

def _read_local() -> str:
    if not os.path.exists(config.DIARY_FILE):
        return ""
    with open(config.DIARY_FILE, encoding="utf-8") as f:
        return f.read()


def _write_local(content: str) -> None:
    with open(config.DIARY_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def _prepend_to_local(new_day_header: str, body: str) -> None:
    """Insert a fresh day section at the top of the file."""
    existing = _read_local()
    block = f"{new_day_header}\n{body}\n"
    if existing:
        block = block + "\n" + existing
    _write_local(block)


def _append_paragraph_to_today_local(day: datetime.date, paragraph: str) -> bool:
    """If today's section exists at the top, append paragraph under it.

    Returns True if today's section was found and updated, False otherwise.
    """
    content = _read_local()
    if not content:
        return False
    iso = day.isoformat()
    m = _DAY_HEADER_RE.search(content)
    if not m or m.group(1) != iso:
        return False
    # Find where today's section ends (next # header or end of file)
    next_header = _DAY_HEADER_RE.search(content, m.end())
    insert_at = next_header.start() if next_header else len(content)
    head = content[:insert_at].rstrip()
    tail = content[insert_at:]
    new_content = f"{head}\n{paragraph}\n"
    if tail:
        new_content = new_content + "\n" + tail
    _write_local(new_content)
    return True


# ─── Google Doc mirror ───────────────────────────────────────────────────────

def _docs_service():
    from googleapiclient.discovery import build
    return build("docs", "v1", credentials=get_credentials())


def _drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=get_credentials())


def _load_doc_id() -> Optional[str]:
    if not os.path.exists(config.DIARY_DOC_CACHE):
        return None
    try:
        return json.load(open(config.DIARY_DOC_CACHE)).get("doc_id")
    except Exception:
        return None


def _save_doc_id(doc_id: str) -> None:
    with open(config.DIARY_DOC_CACHE, "w") as f:
        json.dump({"doc_id": doc_id}, f)


def _create_doc() -> str:
    drive = _drive_service()
    result = drive.files().create(
        body={
            "name": config.DIARY_DOC_TITLE,
            "mimeType": "application/vnd.google-apps.document",
        },
        fields="id, webViewLink",
    ).execute()
    doc_id = result["id"]
    logger.info("Created diary Google Doc %s (%s)", doc_id, result.get("webViewLink"))
    _save_doc_id(doc_id)
    return doc_id


def _get_or_create_doc_id() -> str:
    doc_id = _load_doc_id()
    if doc_id:
        return doc_id
    return _create_doc()


def _prepend_to_doc(doc_id: str, text: str) -> None:
    """Insert `text` at the very top of the doc (just after document start).

    Doc indexing: index 1 is the start of body. We insert there so newest
    content lands on top.
    """
    docs = _docs_service()
    requests = [{"insertText": {"location": {"index": 1}, "text": text}}]
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()


def _read_doc_text(doc_id: str) -> str:
    """Read full plain text of a Google Doc."""
    docs = _docs_service()
    doc = docs.documents().get(documentId=doc_id).execute()
    chunks = []
    for el in doc.get("body", {}).get("content", []):
        for run in el.get("paragraph", {}).get("elements", []):
            t = run.get("textRun", {}).get("content")
            if t:
                chunks.append(t)
    return "".join(chunks)


def _today_section_exists_in_doc(doc_id: str, day: datetime.date) -> bool:
    text = _read_doc_text(doc_id)
    m = _DAY_HEADER_RE.search(text)
    return bool(m and m.group(1) == day.isoformat())


def _append_paragraph_to_today_in_doc(doc_id: str, day: datetime.date, paragraph: str) -> bool:
    """If today's section is at the very top of the doc, insert paragraph right after the header line.

    Returns True if today's section was found, False otherwise.
    """
    docs = _docs_service()
    doc = docs.documents().get(documentId=doc_id).execute()
    text_so_far = ""
    header_end_index: Optional[int] = None
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        line = "".join(
            r.get("textRun", {}).get("content", "")
            for r in para.get("elements", [])
        )
        text_so_far += line
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

def append(text: str, when: Optional[datetime.datetime] = None) -> dict:
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
        if not _append_paragraph_to_today_local(day, paragraph):
            _prepend_to_local(_format_day_header(day), paragraph)
    except Exception as e:
        logger.exception("Diary local write failed")
        return {"ok": False, "error": f"local write failed: {e}"}

    # 2. Google Doc mirror (best-effort)
    doc_url = None
    try:
        doc_id = _get_or_create_doc_id()
        if not _append_paragraph_to_today_in_doc(doc_id, day, paragraph):
            header = _format_day_header(day)
            _prepend_to_doc(doc_id, f"{header}\n{paragraph}\n\n")
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    except Exception as e:
        logger.warning("Diary Google Doc sync failed (local copy is safe): %s", e)
        return {"ok": True, "doc_synced": False, "error": str(e)}

    return {"ok": True, "doc_synced": True, "doc_url": doc_url}


def read(period: str = "today", limit_chars: int = 8000) -> str:
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
    content = _read_local()
    if not content:
        return "Дневник пока пуст."

    today = _now().date()

    def _sections() -> list[tuple[datetime.date, str]]:
        """Parse sections: returns [(date, full_section_text), ...]."""
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


def doc_url() -> Optional[str]:
    """Public URL of the diary Google Doc (or None if not created yet)."""
    doc_id = _load_doc_id()
    return f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else None


def backfill_from_summaries(summaries_file: str = "session_summaries.jsonl") -> dict:
    """One-time backfill: pull past session summaries into diary sections.

    For each calendar day that has at least one session summary AND is not
    already present in the diary, prepends a new day section containing all
    that day's summaries as `[HH:MM] Из памяти: …` paragraphs.

    Safe to re-run: days already in the diary are skipped, not overwritten.
    """
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

    existing = _read_local()
    existing_dates = set(_DAY_HEADER_RE.findall(existing))

    added: list[str] = []
    skipped: list[str] = []
    # Oldest first so after sequential prepends the newest day ends up on top.
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
        _prepend_to_local(header, body)
        try:
            doc_id = _get_or_create_doc_id()
            _prepend_to_doc(doc_id, f"{header}\n{body}\n\n")
        except Exception as e:
            logger.warning("Diary backfill: Google Doc sync failed for %s: %s", date_str, e)
        added.append(date_str)
        existing_dates.add(date_str)

    return {
        "ok": True,
        "days_added": len(added),
        "days_skipped": len(skipped),
        "added_dates": added,
        "skipped_dates": skipped,
    }
