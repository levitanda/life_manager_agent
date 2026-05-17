"""Fetch news headlines from RSS feeds."""

import logging
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

NEWS_FEEDS = {
    "Кан 11": "https://rss.kan.org.il/Rss/RssKan.aspx?id=28",
    "Кешет 12": "https://www.mako.co.il/rss/news.xml",
    "Дождь": "https://tvrain.ru/lite/rss/",
}


def get_news_headlines(max_per_source: int = 3) -> list[dict]:
    """Fetch top headlines from configured RSS feeds. Fails gracefully per source."""
    headlines = []
    for source, url in NEWS_FEEDS.items():
        try:
            resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall(".//item")[:max_per_source]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append({"source": source, "title": title_el.text.strip()})
        except Exception as e:
            logger.warning("News feed %r failed: %s", source, e)
    return headlines
