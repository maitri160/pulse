import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models import Article, Source


def make_source(**overrides) -> Source:
    fields = {"name": "Example News", "feed_url": "https://example.com/rss"}
    return Source(**(fields | overrides))


def make_article(source: Source, **overrides) -> Article:
    fields = {
        "source_id": source.id,
        "url": "https://example.com/story?utm_source=rss",
        "canonical_url": "https://example.com/story",
        "title": "Something happened",
        "content_hash": "a" * 64,
    }
    return Article(**(fields | overrides))


def test_source_gets_server_defaults(db_session):
    source = make_source()
    db_session.add(source)
    db_session.flush()  # send the INSERT so Postgres fills in the defaults
    db_session.refresh(source)  # read those defaults back

    assert source.category == "general"
    assert source.is_active is True
    assert source.consecutive_failures == 0
    assert source.next_fetch_at is not None  # due immediately on first run


def test_duplicate_canonical_url_is_rejected(db_session):
    source = make_source()
    db_session.add(source)
    db_session.flush()

    db_session.add(make_article(source))
    db_session.flush()

    # Same canonical_url again: the UNIQUE constraint must refuse it.
    db_session.add(make_article(source, url="https://example.com/story?ref=twitter"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_duplicate_links_to_original_and_minhash_fits_bigint(db_session):
    source = make_source()
    db_session.add(source)
    db_session.flush()

    original = make_article(source)
    db_session.add(original)
    db_session.flush()

    # MinHash values go up to 2^32 - 1, which would overflow a 4-byte integer column.
    big_value = 2**32 - 1
    copy = make_article(
        source,
        url="https://other.com/same-story",
        canonical_url="https://other.com/same-story",
        duplicate_of_id=original.id,
        minhash=[big_value] * 128,
    )
    db_session.add(copy)
    db_session.flush()
    db_session.refresh(copy)

    assert copy.duplicate_of_id == original.id
    assert copy.minhash == [big_value] * 128


def test_migration_creates_tables_and_vector_extension(db_session):
    tables = set(inspect(db_session.connection()).get_table_names())
    assert {"sources", "articles", "events"} <= tables

    vector = db_session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
    assert vector == 1
