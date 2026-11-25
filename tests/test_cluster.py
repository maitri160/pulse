from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import engine
from app.events import cluster
from app.events.cluster import CLUSTER_LOCK_ID, THRESHOLD, WINDOW, cluster_pending
from app.models import EMBEDDING_DIM, Article, Event, Source

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def basis(k: int) -> np.ndarray:
    v = np.zeros(EMBEDDING_DIM)
    v[k] = 1.0
    return v


def similar_to_base(sim: float, k: int) -> list[float]:
    """A unit vector with cosine similarity exactly `sim` to basis(0).

    Each k (>= 1) gives a different direction, so two of these are `sim**2` similar to
    each other. That's how the tests control "same event" vs "different event".
    """
    return (sim * basis(0) + np.sqrt(1 - sim**2) * basis(k)).tolist()


def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


@pytest.fixture
def sources(db_session):
    made = [Source(name=f"Outlet {n}", feed_url=f"https://outlet{n}.example/rss") for n in (1, 2)]
    db_session.add_all(made)
    db_session.flush()
    return made


_counter = iter(range(10**6))


def add_article(db_session, source, embedding=None, published=NOW, **overrides) -> Article:
    n = next(_counter)
    fields = {
        "source_id": source.id,
        "url": f"https://x.example/{n}",
        "canonical_url": f"https://x.example/{n}",
        "title": f"Story {n}",
        "content_hash": f"{n:064d}",
        "embedding": embedding,
        "published_at": published,
        "fetched_at": NOW + timedelta(days=1),
    }
    article = Article(**(fields | overrides))
    db_session.add(article)
    db_session.flush()
    return article


def events(db_session) -> list[Event]:
    db_session.expire_all()  # the clustering SQL updated rows behind the ORM's back
    return list(db_session.query(Event).order_by(Event.id))


def test_first_article_starts_an_event(db_session, sources):
    article = add_article(db_session, sources[0], basis(0).tolist(), title="Storm hits coast")

    assert cluster_pending(db_session) == 1

    [event] = events(db_session)
    assert (event.title, event.article_count, event.source_count) == ("Storm hits coast", 1, 1)
    assert event.last_article_at == NOW
    assert cosine(event.centroid, basis(0)) == pytest.approx(1.0)
    assert db_session.get(Article, article.id).event_id == event.id


def test_close_article_joins_and_distant_one_starts_a_new_event(db_session, sources):
    first = add_article(db_session, sources[0], basis(0).tolist())
    later = NOW + timedelta(hours=2)
    close = add_article(db_session, sources[1], similar_to_base(0.9, 1), published=later)
    distant = add_article(db_session, sources[1], similar_to_base(0.5, 2))

    assert cluster_pending(db_session) == 3

    storm, other = events(db_session)
    assert db_session.get(Article, first.id).event_id == storm.id
    assert db_session.get(Article, close.id).event_id == storm.id
    assert db_session.get(Article, distant.id).event_id == other.id
    assert (storm.article_count, storm.source_count) == (2, 2)
    assert storm.last_article_at == later  # the newest member's time


def test_just_above_threshold_joins_and_just_below_does_not(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist())
    add_article(db_session, sources[0], similar_to_base(THRESHOLD + 0.001, 1))
    add_article(db_session, sources[0], similar_to_base(THRESHOLD - 0.001, 2))

    cluster_pending(db_session)

    assert [e.article_count for e in events(db_session)] == [2, 1]


def test_centroid_is_the_average_of_its_articles(db_session, sources):
    a = add_article(db_session, sources[0], basis(0).tolist())
    b = add_article(db_session, sources[1], similar_to_base(0.9, 1))

    cluster_pending(db_session)

    [event] = events(db_session)
    mean = (np.asarray(a.embedding) + np.asarray(b.embedding)) / 2
    assert np.allclose(event.centroid, mean, atol=1e-6)


def test_old_events_cannot_take_new_articles(db_session, sources):
    add_article(
        db_session, sources[0], basis(0).tolist(), published=NOW - WINDOW - timedelta(hours=1)
    )
    add_article(db_session, sources[0], basis(0).tolist())  # identical meaning, but 3+ days on

    cluster_pending(db_session)

    assert len(events(db_session)) == 2


def test_duplicates_follow_their_original_and_count_as_sources(db_session, sources):
    original = add_article(db_session, sources[0], basis(0).tolist())
    early_copy = add_article(db_session, sources[1], duplicate_of_id=original.id)

    cluster_pending(db_session)
    late_copy = add_article(db_session, sources[1], duplicate_of_id=original.id)
    cluster_pending(db_session)  # nothing new to cluster, but the late copy is attached

    [event] = events(db_session)
    assert db_session.get(Article, early_copy.id).event_id == event.id
    assert db_session.get(Article, late_copy.id).event_id == event.id
    assert (event.article_count, event.source_count) == (3, 2)
    assert cosine(event.centroid, basis(0)) == pytest.approx(1.0)  # copies don't shift it


def test_skips_unembedded_and_is_safe_to_rerun(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist())
    waiting = add_article(db_session, sources[0], embedding=None)

    assert cluster_pending(db_session) == 1
    assert cluster_pending(db_session) == 0
    assert db_session.get(Article, waiting.id).event_id is None  # clustered once embedded


def test_max_articles_caps_one_run(db_session, sources):
    for k in range(1, 4):
        add_article(db_session, sources[0], similar_to_base(0.1, k))

    assert cluster_pending(db_session, max_articles=2) == 2


def test_only_one_clustering_run_at_a_time():
    """A second run waits for the lock instead of clustering alongside the first."""
    with engine.connect() as run_a:
        run_a.begin()
        run_a.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": CLUSTER_LOCK_ID})

        with Session(engine) as run_b:
            run_b.execute(text("SET lock_timeout = '200ms'"))  # give up instead of waiting
            with pytest.raises(OperationalError, match="lock timeout"):
                cluster_pending(run_b)
        run_a.rollback()


# --- dates from feeds can't be trusted ---


def test_missing_date_uses_fetch_time(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist(), published=None)
    cluster_pending(db_session)
    assert events(db_session)[0].last_article_at == NOW + timedelta(days=1)


def test_future_date_is_capped_at_fetch_time(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist(), published=NOW + timedelta(days=1500))
    cluster_pending(db_session)
    assert events(db_session)[0].last_article_at == NOW + timedelta(days=1)


def test_old_article_cannot_join_a_much_later_event(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist())
    old = NOW - WINDOW - timedelta(hours=1)
    add_article(db_session, sources[0], basis(0).tolist(), published=old)
    cluster_pending(db_session)
    assert len(events(db_session)) == 2


def test_late_arrival_joins_without_moving_event_time_back(db_session, sources):
    add_article(db_session, sources[0], basis(0).tolist())
    earlier = NOW - timedelta(days=1)
    add_article(db_session, sources[0], basis(0).tolist(), published=earlier)
    cluster_pending(db_session)
    [event] = events(db_session)
    assert (event.article_count, event.last_article_at) == (2, NOW)


def test_joins_the_closer_of_two_possible_events(db_session, sources):
    # A and B are 0.6 alike, so separate events. The third is ~0.8 to A and ~0.96 to B.
    add_article(db_session, sources[0], basis(0).tolist())
    b = add_article(db_session, sources[0], similar_to_base(0.6, 1))
    near_b = add_article(db_session, sources[0], similar_to_base(0.8, 1))
    cluster_pending(db_session)
    db_session.expire_all()
    assert len(events(db_session)) == 2
    assert near_b.event_id == b.event_id


def test_one_bad_article_does_not_block_newer_ones(db_session, sources, monkeypatch):
    bad = add_article(db_session, sources[0], basis(0).tolist(), title="bad")
    good = add_article(db_session, sources[0], basis(1).tolist())
    db_session.commit()  # a failure rolls back; the test's own rows must survive that
    real, attempts = cluster.cluster_one, []

    def flaky(session, article, threshold):
        if article.title == "bad":
            attempts.append(article.id)
            raise RuntimeError("boom")
        return real(session, article, threshold)

    monkeypatch.setattr(cluster, "cluster_one", flaky)

    assert cluster_pending(db_session) == 1
    assert len(attempts) == 1  # not picked again and again
    db_session.expire_all()
    assert bad.event_id is None and good.event_id is not None
