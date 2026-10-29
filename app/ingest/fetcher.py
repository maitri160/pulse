"""Download an RSS/Atom feed and turn it into a list of plain entries."""

import calendar
from dataclasses import dataclass, field
from datetime import UTC, datetime

import feedparser
import httpx

USER_AGENT = "PulseBot/0.1 (news aggregator; portfolio project)"


def make_http_client() -> httpx.Client:
    # Explicit timeout: without one, a single unresponsive server could hang a worker forever.
    return httpx.Client(timeout=10.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})


@dataclass
class FeedEntry:
    url: str
    title: str
    summary: str | None
    published_at: datetime | None


@dataclass
class FeedResult:
    not_modified: bool  # True when the server replied 304: nothing new since last time
    entries: list[FeedEntry] = field(default_factory=list)
    etag: str | None = None
    last_modified: str | None = None


def fetch_feed(
    client: httpx.Client,
    feed_url: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedResult:
    """GET the feed, sending back the ETag/Last-Modified we saved last time.

    Raises httpx.HTTPError on network errors or 4xx/5xx responses.
    """
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    response = client.get(feed_url, headers=headers)
    if response.status_code == 304:
        return FeedResult(not_modified=True, etag=etag, last_modified=last_modified)
    response.raise_for_status()

    return FeedResult(
        not_modified=False,
        entries=parse_entries(response.content),
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
    )


def parse_entries(content: bytes) -> list[FeedEntry]:
    """Parse feed XML. Entries missing a link or title are skipped."""
    parsed = feedparser.parse(content)
    # feedparser sets `bozo` for malformed XML but still returns what it could read.
    # Only treat it as a failure if nothing usable came out.
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Could not parse feed: {parsed.bozo_exception}")

    entries = []
    for item in parsed.entries:
        url, title = item.get("link"), item.get("title")
        if not url or not title:
            continue
        entries.append(
            FeedEntry(
                url=url,
                title=title.strip(),
                summary=item.get("summary") or None,
                published_at=_parse_date(item),
            )
        )
    return entries


def _parse_date(item) -> datetime | None:
    # feedparser gives dates as time.struct_time already converted to UTC.
    struct = item.get("published_parsed") or item.get("updated_parsed")
    if struct is None:
        return None
    # calendar.timegm treats the struct as UTC (time.mktime would wrongly use local time).
    return datetime.fromtimestamp(calendar.timegm(struct), tz=UTC)
