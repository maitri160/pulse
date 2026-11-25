"""Group articles about the same real-world event into events, one article at a time.

Incremental ("single-pass") clustering: each new original is compared with the centroids
(average embeddings) of RECENT events. If the nearest one is similar enough, the article
joins it; otherwise the article starts a new event. Old articles are never re-clustered,
so the cost per new article stays the same however big the archive gets.
Read later: https://github.com/pgvector/pgvector#querying
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, aliased

from app.models import Article, Event

logger = logging.getLogger(__name__)

# Cosine similarity needed to join an event. Picked by looking at real pairs (scratch
# survey, M8): >= 0.80 were the same event; 0.70-0.75 mixed same event with related but
# different ones (two different Russian drone strikes); <= 0.65 were mostly just the same
# topic. Comparing with a centroid is a bit kinder than comparing two articles, because
# an average sits closer to its members than they sit to each other.
THRESHOLD = 0.75
# An article can only join an event whose newest article is within 3 days of it, EITHER side.
WINDOW = timedelta(days=3)
MAX_PER_RUN = 1280  # cap per task run, like embedding
# Any fixed number works, as long as every process uses the same one (see _take_lock).
CLUSTER_LOCK_ID = 8_000_001


def _take_lock(session: Session) -> None:
    """Make clustering one-at-a-time across ALL processes, until this transaction ends.

    Without it, two runs could both read "no event for this storm yet" at the same moment
    and each create one: two events for one story. A row lock can't prevent that, because
    the event row doesn't exist yet. An advisory lock is a lock on a NUMBER, not a row:
    whoever holds 8_000_001 clusters; anyone else waits here. It's released automatically
    at commit or rollback, so a crash can never leave it stuck.
    """
    session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": CLUSTER_LOCK_ID})


def article_time(article: Article) -> datetime:
    """When the article came out. Feed dates can be missing or wrong, so never later than
    the moment we fetched it: a "2030" typo would keep its event "recent" for years."""
    return min(article.published_at or article.fetched_at, article.fetched_at)


def nearest_event(session: Session, article: Article) -> tuple[Event, float] | None:
    """The recent event whose centroid is most similar to this article, and how similar."""
    published = article_time(article)
    distance = Event.centroid.cosine_distance(article.embedding)
    # Exact search, no vector index: only events from the last few days are scanned, and
    # an approximate (HNSW) index with this WHERE filter can silently miss the best match.
    row = session.execute(
        select(Event, distance)
        # Both ends matter: without the upper one, an old item that resurfaces in a feed
        # could join any later event (a 9 Nov article joined an 18 Nov event).
        .where(Event.last_article_at.between(published - WINDOW, published + WINDOW))
        .order_by(distance)
        .limit(1)
    ).first()
    if row is None:
        return None
    event, dist = row
    return event, 1 - dist  # pgvector gives cosine DISTANCE; similarity = 1 - distance


def _refresh_event(session: Session, event_id: int) -> None:
    """Recompute the event's centroid and counts from its articles, in SQL.

    Recomputing (not "+1") keeps them exactly right however we got here. It reads only
    this event's rows (indexed by event_id), so it stays cheap.
    """
    members = (
        select(Article.embedding, Article.source_id).where(Article.event_id == event_id).subquery()
    )
    session.execute(
        update(Event)
        .where(Event.id == event_id)
        .values(
            # pgvector can average vectors in SQL. Duplicates have no embedding (NULL) and
            # avg skips NULLs, so a wire story copied by 20 outlets doesn't pull the centre
            # 20 times harder. No need to rescale to length 1: cosine ignores length.
            centroid=select(func.avg(members.c.embedding)).scalar_subquery(),
            article_count=select(func.count()).select_from(members).scalar_subquery(),
            source_count=select(func.count(members.c.source_id.distinct())).scalar_subquery(),
            last_updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )


def cluster_one(session: Session, article: Article, threshold: float = THRESHOLD) -> int:
    """Put one embedded original into an event (existing or new). Returns the event id."""
    published = article_time(article)
    match = nearest_event(session, article)
    if match is not None and match[1] >= threshold:
        event = match[0]
        event.last_article_at = max(event.last_article_at, published)
    else:
        event = Event(title=article.title, centroid=article.embedding, last_article_at=published)
        session.add(event)
    session.flush()  # a new event gets its id here
    article.event_id = event.id

    # Duplicates already stored follow their original. (One saved after this commit is
    # picked up by _attach_late_duplicates.)
    session.execute(
        update(Article)
        .where(Article.duplicate_of_id == article.id)
        .values(event_id=event.id)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    _refresh_event(session, event.id)
    return event.id


def _attach_late_duplicates(session: Session) -> int:
    """Duplicates saved AFTER their original was clustered: give them its event."""
    original = aliased(Article)
    event_ids = session.scalars(
        update(Article)
        .where(
            Article.event_id.is_(None),
            Article.duplicate_of_id == original.id,
            original.event_id.is_not(None),
        )
        .values(event_id=original.event_id)
        .returning(Article.event_id)
        .execution_options(synchronize_session=False)
    ).all()
    for event_id in set(event_ids):
        _refresh_event(session, event_id)
    return len(event_ids)


def cluster_pending(
    session: Session, threshold: float = THRESHOLD, max_articles: int = MAX_PER_RUN
) -> int:
    """Cluster embedded originals that have no event yet, oldest first. Returns how many.

    One article per transaction, under the clustering lock: the next article must see
    the event the previous one created or moved. Safe to rerun: done rows have an event.
    """
    done = 0
    failed: set[int] = set()  # failed in THIS run; skipped until the next run tries again
    for _ in range(max_articles):
        _take_lock(session)
        article = session.scalars(
            select(Article)
            .where(
                Article.embedding.is_not(None),
                Article.duplicate_of_id.is_(None),
                Article.event_id.is_(None),
                # Otherwise one bad article (oldest first) would block all newer ones.
                Article.id.not_in(list(failed)),
            )
            .order_by(Article.id)
            .limit(1)
        ).first()
        if article is None:
            # End the transaction to release the lock. commit, not rollback: a rollback
            # would also throw away anything the caller added and hasn't saved yet.
            session.commit()
            break
        article_id = article.id  # read now: after a rollback the object's fields expire
        try:
            cluster_one(session, article, threshold)
            session.commit()
            done += 1
        except Exception:
            session.rollback()  # loses only this article; also releases the lock
            failed.add(article_id)
            logger.exception("Clustering article %d failed; will retry next run", article_id)

    _take_lock(session)
    _attach_late_duplicates(session)
    session.commit()
    return done
