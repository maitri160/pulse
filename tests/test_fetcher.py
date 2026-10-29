from datetime import UTC, datetime

import httpx
import pytest

from app.ingest.extract import extract_text
from app.ingest.fetcher import fetch_feed, parse_entries
from tests.fakes import FEED_URL, FIXTURES, RATES_BODY, FakeWeb, article_page


def test_parse_entries_skips_items_without_link():
    entries = parse_entries((FIXTURES / "sample_feed.xml").read_bytes())

    assert len(entries) == 4
    assert entries[0].title == "Central bank raises interest rates"
    assert entries[0].summary == "The central bank raised rates by a quarter point."


def test_parse_entries_converts_dates_to_utc():
    entries = parse_entries((FIXTURES / "sample_feed.xml").read_bytes())

    assert entries[0].published_at == datetime(2026, 10, 29, 14, 0, tzinfo=UTC)
    # "12:00 +0200" is 10:00 UTC
    assert entries[1].published_at == datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    assert entries[2].published_at is None  # no pubDate in the feed


def test_parse_entries_rejects_garbage():
    with pytest.raises(ValueError):
        parse_entries(b"this is not xml at all <<<")


def test_fetch_feed_returns_etag():
    result = fetch_feed(FakeWeb().client(), FEED_URL)

    assert result.not_modified is False
    assert result.etag == '"v1"'
    assert len(result.entries) == 4


def test_fetch_feed_sends_etag_and_handles_304():
    web = FakeWeb()

    result = fetch_feed(web.client(), FEED_URL, etag='"v1"')

    assert web.requests[0].headers["If-None-Match"] == '"v1"'
    assert result.not_modified is True
    assert result.entries == []


def test_fetch_feed_raises_on_server_error():
    with pytest.raises(httpx.HTTPStatusError):
        fetch_feed(FakeWeb(feed_status=500).client(), FEED_URL)


def test_extract_text_drops_page_chrome():
    text = extract_text(article_page(RATES_BODY))

    assert text is not None
    assert "quarter of a percentage point" in text
    assert "Home | World" not in text  # navigation menu removed
