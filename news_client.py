"""Fetch news headlines from RSS feeds."""

import logging
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

NEWS_FEEDS = {
    # Кан 11 блокирует все RSS-запросы → заменён на Ynet (ведущий израильский портал)
    "Кан 11 / Ynet": "https://www.ynet.co.il/Integration/StoryRss2.xml",
    # Кешет 12 блокирует прямой RSS → заменён на Walla News
    "Кешет 12 / Walla": "https://rss.walla.co.il/feed/1",
    "Дождь": "https://tvrain.ru/export/rss/all.xml",
}


def get_news_headlines(max_per_source: int = 3) -> list[dict]:
    """Fetch top headlines from configured RSS feeds. Fails gracefully per source."""
    headlines = []
    for source, url in NEWS_FEEDS.items():
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}, verify=False)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:max_per_source]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append({"source": source, "title": title_el.text.strip()})
        except Exception as e:
            logger.warning("News feed %r failed: %s", source, e)
    return headlines
