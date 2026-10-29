"""Fetch one source and store its new articles, with exact deduplication."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ingest.extract import fetch_article_text
from app.ingest.fetcher import fetch_feed
from app.ingest.normalize import canonicalize_url, content_hash
from app.models import Article, Source


@dataclass
class IngestStats:
    not_modified: bool = False
    seen: int = 0  # entries in the feed
    skipped: int = 0  # URL already stored
    originals: int = 0
    duplicates: int = 0  # new URL, but same content as an article we already have


def ingest_source(session: Session, client: httpx.Client, source: Source) -> IngestStats:
    """Fetch `source`'s feed and insert new articles. The caller commits.

    Safe to run any number of times: a URL is only ever stored once.
    """
    stats = IngestStats()
    now = datetime.now(UTC)
    source.last_fetched_at = now

    result = fetch_feed(client, source.feed_url, source.etag, source.last_modified)
    source.last_success_at = now
    if result.not_modified:
        stats.not_modified = True
        return stats
    source.etag, source.last_modified = result.etag, result.last_modified

    # Keyed by canonical URL, which also drops repeats within the same feed.
    # setdefault keeps the FIRST entry for a URL (a plain dict comprehension would keep the last).
    entries = {}
    for entry in result.entries:
        entries.setdefault(canonicalize_url(entry.url), entry)
    stats.seen = len(entries)

    # One query for all URLs we already have, so we don't download their pages again.
    already_stored = set(
        session.scalars(select(Article.canonical_url).where(Article.canonical_url.in_(entries)))
    )

    for canonical, entry in entries.items():
        if canonical in already_stored:
            stats.skipped += 1
            continue

        content = fetch_article_text(client, entry.url)
        # Hash the body if we got one. Headlines often get rewritten for the same wire copy,
        # so the body is the better fingerprint. Fall back to what the feed gave us.
        digest = content_hash(content or entry.summary or entry.title)
        original_id = session.scalar(
            select(Article.id)
            .where(Article.content_hash == digest, Article.duplicate_of_id.is_(None))
            .limit(1)
        )

        # ON CONFLICT DO NOTHING: if another worker stored this URL since our check above,
        # Postgres silently skips the row instead of raising. RETURNING gives back the new
        # id, or None when the row was skipped.
        inserted_id = session.scalar(
            insert(Article)
            .values(
                source_id=source.id,
                url=entry.url,
                canonical_url=canonical,
                title=entry.title,
                rss_summary=entry.summary,
                content=content,
                content_hash=digest,
                duplicate_of_id=original_id,
                published_at=entry.published_at,
            )
            .on_conflict_do_nothing(index_elements=["canonical_url"])
            .returning(Article.id)
        )

        if inserted_id is None:
            stats.skipped += 1
        elif original_id is None:
            stats.originals += 1
        else:
            stats.duplicates += 1

    return stats
