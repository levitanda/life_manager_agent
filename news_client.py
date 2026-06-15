"""Fetch news headlines from RSS feeds, per user.

Two modes:
- Legacy single-user (no user_id): used a global dict NEWS_FEEDS. Kept here as
  LEGACY_NEWS_FEEDS for backward compatibility — Daria pre-migration falls
  back to it. New users never touch this.
- Multi-tenant (user_id given): reads enabled rows from user_news_feeds
  in the SQLite DB. If a user has no rows, returns [] — they explicitly
  opted out of news during onboarding, so the digest skips the section.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Legacy single-user feeds (Daria pre-migration). New users never see these.
LEGACY_NEWS_FEEDS = {
    "Кан 11 / Ynet": "https://www.ynet.co.il/Integration/StoryRss2.xml",
    "Кешет 12 / Walla": "https://rss.walla.co.il/feed/1",
    "Дождь": "https://tvrain.ru/export/rss/all.xml",
}


def _fetch_feed(source: str, url: str, max_per_source: int) -> list[dict]:
    """Best-effort fetch of one RSS feed. Returns up to max_per_source items."""
    try:
        resp = requests.get(
            url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, verify=False,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        out = []
        for item in root.findall(".//item")[:max_per_source]:
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                out.append({"source": source, "title": title_el.text.strip()})
        return out
    except Exception as e:
        logger.warning("News feed %r failed: %s", source, e)
        return []


def get_news_headlines(
    max_per_source: int = 3, *, user_id: Optional[int] = None
) -> list[dict]:
    """Fetch top headlines for a user. Iterates over the user's enabled
    user_news_feeds rows; falls back to the legacy global feed list for
    user_id=None (Daria pre-migration only)."""
    if user_id is None:
        # Legacy path: global feeds
        headlines = []
        for source, url in LEGACY_NEWS_FEEDS.items():
            headlines.extend(_fetch_feed(source, url, max_per_source))
        return headlines

    # Multi-tenant: user-specific feeds (table created in Phase C; until then
    # try the lookup but tolerate missing schema so this commit is safe to
    # deploy ahead of the migration)
    try:
        import db
        from sqlalchemy import text
        with db.session_scope() as s:
            try:
                rows = s.execute(
                    text(
                        "SELECT source_name, url FROM user_news_feeds "
                        "WHERE user_id = :uid AND enabled = 1"
                    ),
                    {"uid": user_id},
                ).all()
            except Exception:
                # Table doesn't exist yet → no feeds for this user
                return []
    except Exception as e:
        logger.warning("user_news_feeds lookup failed for user %s: %s", user_id, e)
        return []

    headlines = []
    for source, url in rows:
        headlines.extend(_fetch_feed(source, url, max_per_source))
    return headlines
