"""Tests for news_client.py"""

from unittest.mock import patch, MagicMock
import pytest

import news_client


RSS_SAMPLE = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<rss><channel>"
    b"<item><title>Headline one</title></item>"
    b"<item><title>Headline two</title></item>"
    b"<item><title>Headline three</title></item>"
    b"<item><title>Headline four - should be excluded</title></item>"
    b"</channel></rss>"
)


def _mock_response(content=RSS_SAMPLE, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def test_get_news_headlines_returns_up_to_max_per_source():
    with patch("requests.get", return_value=_mock_response()) as mock_get:
        results = news_client.get_news_headlines(max_per_source=3)
    # 3 sources × 3 headlines = 9 max
    assert len(results) <= 9
    for item in results:
        assert "source" in item
        assert "title" in item


def test_get_news_headlines_source_names():
    with patch("requests.get", return_value=_mock_response()):
        results = news_client.get_news_headlines(max_per_source=1)
    sources = {r["source"] for r in results}
    # Legacy mode (no user_id) walks the legacy global feed list
    assert sources == set(news_client.LEGACY_NEWS_FEEDS.keys())


def test_get_news_headlines_respects_max():
    with patch("requests.get", return_value=_mock_response()):
        results = news_client.get_news_headlines(max_per_source=2)
    per_source: dict[str, int] = {}
    for item in results:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
    for count in per_source.values():
        assert count <= 2


def test_get_news_headlines_fallback_on_error():
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = Exception("timeout")

    good_resp = _mock_response()

    call_count = 0

    def side_effect(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return bad_resp
        return good_resp

    with patch("requests.get", side_effect=side_effect):
        results = news_client.get_news_headlines(max_per_source=3)

    # Should still return results from the two working sources
    sources = {r["source"] for r in results}
    assert len(sources) >= 2


def test_get_news_headlines_all_fail():
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = Exception("network error")

    with patch("requests.get", return_value=bad_resp):
        results = news_client.get_news_headlines()

    assert results == []


def test_headline_titles_are_stripped():
    rss = b"""<rss><channel>
      <item><title>  Padded title  </title></item>
    </channel></rss>"""
    with patch("requests.get", return_value=_mock_response(content=rss)):
        results = news_client.get_news_headlines(max_per_source=1)
    for item in results:
        assert item["title"] == item["title"].strip()
