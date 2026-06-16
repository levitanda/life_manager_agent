"""Preset RSS news feeds per country/region.

Used by the onboarding wizard (Phase E) when the user picks one of the
country chips. Each preset is a list of (source_name, url) tuples that get
inserted as `user_news_feeds` rows.

Keep the URLs stable across deploys — these are persisted into the DB and
existing rows are not retroactively migrated when the preset changes.
"""

from __future__ import annotations

NEWS_PRESETS: dict[str, list[tuple[str, str]]] = {
    "IL": [
        ("Ynet", "https://www.ynet.co.il/Integration/StoryRss2.xml"),
        ("Walla", "https://rss.walla.co.il/feed/1"),
    ],
    "RU": [
        ("Дождь", "https://tvrain.ru/export/rss/all.xml"),
        ("Meduza", "https://meduza.io/rss/all"),
    ],
    "UA": [
        ("Українська правда", "https://www.pravda.com.ua/rss/"),
    ],
    "US": [
        ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
        ("BBC World", "http://feeds.bbci.co.uk/news/rss.xml"),
    ],
    "EU": [
        ("Euronews", "https://www.euronews.com/rss"),
    ],
    "World": [
        ("Reuters World", "https://feeds.reuters.com/Reuters/worldNews"),
        ("AP Top News", "https://rsshub.app/apnews/topics/apf-topnews"),
    ],
}
