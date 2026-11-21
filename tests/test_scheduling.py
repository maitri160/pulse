from datetime import UTC, datetime, timedelta

import pytest

from app.ingest.scheduling import claim_due_sources, next_fetch_delay, run_source
from app.models import Source
from tests.fakes import FEED_URL, FakeWeb

MIN = timedelta(minutes=1)


@pytest.mark.parametrize(
    ("failures", "minutes"),
    [(0, 5), (1, 10), (2, 20), (3, 40), (4, 80), (6, 320), (7, 360), (50, 360)],
)
def test_backoff_doubles_per_failure_and_caps_at_6_hours(failures, minutes):
    assert next_fetch_delay(failures, jitter=1.0) == minutes * MIN


def test_jitter_stays_within_10_percent():
    for _ in range(200):
        assert 4.5 * MIN <= next_fetch_delay(0) <= 5.5 * MIN


def add_source(db_session, **fields) -> Source:
    source = Source(name="Example News", feed_url=fields.pop("feed_url", FEED_URL), **fields)
    db_session.add(source)
    # Commit (a savepoint in tests), so run_source's rollback on failure can't undo it.
    db_session.commit()
    return source


def test_claim_picks_only_due_active_feeds_and_leases_them(db_session):
    now = datetime.now(UTC)
    due = add_source(db_session, feed_url="https://a.example/rss", next_fetch_at=now - MIN)
    add_source(db_session, feed_url="https://b.example/rss", next_fetch_at=now + 10 * MIN)
    add_source(
        db_session, feed_url="https://c.example/rss", next_fetch_at=now - MIN, is_active=False
    )

    assert claim_due_sources(db_session) == [due.id]

    db_session.refresh(due)
    assert due.next_fetch_at > now + 14 * MIN  # pushed forward by the 15-min lease
    # The next Beat tick must not dispatch it again while it is leased.
    assert claim_due_sources(db_session) == []


def test_success_schedules_normal_interval(db_session):
    source = add_source(db_session)

    stats = run_source(db_session, FakeWeb().client(), source.id)

    assert stats is not None and stats.originals == 2
    db_session.refresh(source)
    assert source.consecutive_failures == 0
    assert source.last_error is None
    delay = source.next_fetch_at - datetime.now(UTC)
    assert 4 * MIN < delay <= 5.5 * MIN


def test_failure_backs_off_and_records_error(db_session):
    source = add_source(db_session)

    stats = run_source(db_session, FakeWeb(feed_status=500).client(), source.id)

    assert stats is None  # no exception escapes: a broken feed must not crash the worker
    db_session.refresh(source)
    assert source.consecutive_failures == 1
    assert "500" in source.last_error
    delay = source.next_fetch_at - datetime.now(UTC)
    assert 8.5 * MIN < delay <= 11 * MIN  # 10 min +/- 10%


def test_failures_accumulate_then_reset_on_success(db_session):
    source = add_source(db_session, consecutive_failures=3)

    run_source(db_session, FakeWeb(feed_status=503).client(), source.id)
    db_session.refresh(source)
    assert source.consecutive_failures == 4
    delay = source.next_fetch_at - datetime.now(UTC)
    assert 71 * MIN < delay <= 88 * MIN  # 80 min +/- 10%

    run_source(db_session, FakeWeb().client(), source.id)
    db_session.refresh(source)
    assert source.consecutive_failures == 0


def test_inactive_source_is_not_fetched(db_session):
    source = add_source(db_session, is_active=False)
    web = FakeWeb()

    assert run_source(db_session, web.client(), source.id) is None
    assert web.requests == []


def test_sources_toml_is_valid():
    from pathlib import Path

    from scripts.seed_sources import load_sources

    sources = load_sources(Path("sources.toml"))

    assert len(sources) >= 20
    assert {s["category"] for s in sources} <= {"world", "business", "tech"}
    urls = [s["feed_url"] for s in sources]
    assert len(urls) == len(set(urls))  # no feed listed twice
