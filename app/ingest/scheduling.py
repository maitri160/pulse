"""When each feed gets fetched: backoff maths, claiming due feeds, recording the outcome."""

import logging
import random
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.config import settings
from app.ingest.ingest import IngestStats, ingest_source
from app.models import Source

logger = logging.getLogger(__name__)


def next_fetch_delay(consecutive_failures: int, jitter: float | None = None) -> timedelta:
    """How long to wait before fetching a feed again.

    0 failures -> the normal interval (5 min). Each failure in a row doubles it:
    10, 20, 40, 80 min ... capped at 6 hours. Then a random +/-10% (jitter), so feeds
    that failed at the same moment don't all retry at the same second.
    `jitter` can be passed in so tests get exact numbers.
    """
    seconds = min(
        settings.fetch_interval_seconds * 2**consecutive_failures, settings.max_backoff_seconds
    )
    if jitter is None:
        jitter = random.uniform(0.9, 1.1)
    return timedelta(seconds=seconds * jitter)


def claim_due_sources(session: Session) -> list[int]:
    """Pick the feeds that are due and push their next_fetch_at forward, in ONE statement.

    The pushed-forward time is a lease: while a feed is queued or being fetched, the next
    Beat tick won't dispatch it again. If the worker dies, the lease runs out and the feed
    simply becomes due again. One UPDATE ... RETURNING does "find and mark" atomically, so
    there is no gap between finding a feed and marking it. The caller commits.
    """
    lease_until = func.now() + timedelta(seconds=settings.fetch_lease_seconds)
    claimed = session.scalars(
        update(Source)
        .where(Source.is_active, Source.next_fetch_at <= func.now())
        .values(next_fetch_at=lease_until)
        .returning(Source.id)
    )
    return list(claimed)


def run_source(session: Session, client: httpx.Client, source_id: int) -> IngestStats | None:
    """Fetch one feed and record the outcome for backoff.

    Returns None if the fetch failed or the source was skipped.
    """
    source = session.get(Source, source_id)
    if source is None or not source.is_active:
        return None

    try:
        stats = ingest_source(session, client, source)
    # Deliberately broad: network errors, HTTP 4xx/5xx, unparseable XML, or a bug in our
    # code. Whatever the cause, the feed backs off instead of being retried every minute.
    except Exception as exc:
        session.rollback()  # discard the half-done article; already-committed ones stay
        source.consecutive_failures += 1
        source.last_error = f"{type(exc).__name__}: {exc}"[:500]
        source.last_fetched_at = datetime.now(UTC)
        source.next_fetch_at = datetime.now(UTC) + next_fetch_delay(source.consecutive_failures)
        session.commit()
        logger.warning(
            "Fetching %s failed (%d in a row): %s",
            source.feed_url,
            source.consecutive_failures,
            source.last_error,
        )
        return None

    source.consecutive_failures = 0
    source.last_error = None
    source.next_fetch_at = datetime.now(UTC) + next_fetch_delay(0)
    session.commit()
    return stats
