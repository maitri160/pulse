"""Fetch one source and store its new articles, with exact and near-duplicate detection."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.ingest.extract import fetch_article_text
from app.ingest.fetcher import fetch_feed
from app.ingest.neardup import NearDupIndex, signature
from app.ingest.normalize import canonicalize_url, content_hash
from app.models import Article, Source


@dataclass
class IngestStats:
    not_modified: bool = False
    seen: int = 0  # entries in the feed
    skipped: int = 0  # URL already stored
    originals: int = 0
    duplicates: int = 0  # new URL, but exactly the same text as an article we already have
    near_duplicates: int = 0  # new URL, text very similar (MinHash) to a recent original


def _find_original(session: Session, digest: str) -> int | None:
    """Id of the original article with this content hash, or None if there isn't one."""
    return session.scalar(
        select(Article.id)
        .where(Article.content_hash == digest, Article.duplicate_of_id.is_(None))
        .limit(1)
    )


def _insert_article(session: Session, values: dict, duplicate_of_id: int | None) -> int | None:
    """INSERT one article. Returns its new id, or None if Postgres skipped it.

    ON CONFLICT DO NOTHING with no conflict target: a clash with ANY unique rule skips
    the row. There are two: one row per canonical_url, and one original per
    content_hash. Naming a target would cover only one of them; a clash with the
    other would raise IntegrityError instead.
    """
    return session.scalar(
        insert(Article)
        .values(**values, duplicate_of_id=duplicate_of_id)
        .on_conflict_do_nothing()
        .returning(Article.id)
    )


def _url_exists(session: Session, canonical: str) -> bool:
    return session.scalar(select(Article.id).where(Article.canonical_url == canonical)) is not None


def ingest_source(session: Session, client: httpx.Client, source: Source) -> IngestStats:
    """Fetch `source`'s feed and insert new articles.

    Commits after every article, so this worker never holds a lock on an uncommitted
    row for long and blocks another worker saving the same text. The feed's
    etag/last_modified are saved only at the end: after a crash halfway through, the
    next run downloads the full feed again and skips the URLs already saved.

    Safe to run any number of times: a URL is only ever stored once.
    """
    stats = IngestStats()
    now = datetime.now(UTC)
    source.last_fetched_at = now

    result = fetch_feed(client, source.feed_url, source.etag, source.last_modified)
    if result.not_modified:
        stats.not_modified = True
        source.last_success_at = now
        session.commit()
        return stats

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

    # Loaded on first use: most fetches are a 304 or all-known URLs and never need it.
    near_index: NearDupIndex | None = None

    for canonical, entry in entries.items():
        if canonical in already_stored:
            stats.skipped += 1
            continue

        content = fetch_article_text(client, entry.url)
        # Hash the body if we got one. Headlines often get rewritten for the same wire copy,
        # so the body is the better fingerprint. Fall back to what the feed gave us.
        digest = content_hash(content or entry.summary or entry.title)
        # Only the full body gets a signature; None if there's no body or it's too short.
        sig = signature(content)
        values = {
            "source_id": source.id,
            "url": entry.url,
            "canonical_url": canonical,
            "title": entry.title,
            "rss_summary": entry.summary,
            "content": content,
            "content_hash": digest,
            "minhash": sig,
            "published_at": entry.published_at,
        }

        # Exact match first (cheap, certain). Only if that finds nothing, try near match.
        original_id = _find_original(session, digest)
        is_near = False
        if original_id is None and sig is not None:
            if near_index is None:
                near_index = NearDupIndex.load(session)
            original_id = near_index.find_original(sig)
            is_near = original_id is not None
        inserted_id = _insert_article(session, values, original_id)

        if inserted_id is not None:
            if original_id is None:
                stats.originals += 1
                if sig is not None and near_index is not None:
                    # So a later article in this same feed can match it too.
                    near_index.add(inserted_id, sig)
            elif is_near:
                stats.near_duplicates += 1
            else:
                stats.duplicates += 1
        elif original_id is not None or _url_exists(session, canonical):
            # Skipped, and the reason can only be this URL: either we were saving a
            # duplicate (duplicates never clash on the hash rule), or the URL is now stored.
            stats.skipped += 1
        else:
            # Race: we tried to save an original, but another worker committed an original
            # with the same text after our _find_original ran. Look it up again (that new
            # query sees the other worker's commit) and retry once, as a duplicate of it.
            original_id = _find_original(session, digest)
            retry_id = None
            if original_id is not None:
                retry_id = _insert_article(session, values, original_id)
            if retry_id is not None:
                stats.duplicates += 1
            else:
                # The retry clashed on the URL (the other worker stored this exact URL too),
                # or the original disappeared before our second lookup. Either way, skip.
                stats.skipped += 1

        session.commit()

    source.etag, source.last_modified = result.etag, result.last_modified
    source.last_success_at = now
    session.commit()
    return stats
