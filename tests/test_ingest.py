from sqlalchemy import select

from app.ingest.ingest import ingest_source
from app.models import Article, Source
from tests.fakes import FEED_URL, FakeWeb


def make_source(db_session) -> Source:
    source = Source(name="Example News", feed_url=FEED_URL)
    db_session.add(source)
    db_session.flush()
    return source


def articles_by_url(db_session) -> dict[str, Article]:
    return {a.canonical_url: a for a in db_session.scalars(select(Article))}


def test_first_run_stores_articles_and_marks_exact_duplicate(db_session):
    source = make_source(db_session)

    stats = ingest_source(db_session, FakeWeb().client(), source)

    # 4 entries, but two are the same URL (different tracking params) -> 3 unique.
    assert stats.seen == 3
    assert (stats.originals, stats.duplicates, stats.skipped) == (2, 1, 0)

    stored = articles_by_url(db_session)
    original = stored["https://news.example.com/rates"]
    wire_copy = stored["https://other.example.org/wire/rates"]
    assert original.duplicate_of_id is None
    assert wire_copy.duplicate_of_id == original.id  # same body, different URL
    assert "quarter of a percentage point" in original.content


def test_second_run_is_idempotent(db_session):
    source = make_source(db_session)
    web = FakeWeb()
    ingest_source(db_session, web.client(), source)
    source.etag = None  # force a full download again instead of a 304

    stats = ingest_source(db_session, web.client(), source)

    assert (stats.originals, stats.duplicates, stats.skipped) == (0, 0, 3)
    assert len(articles_by_url(db_session)) == 3
    # Article pages were not downloaded again: only the 2 feed requests + 3 pages in total.
    assert len(web.requests) == 5


def test_not_modified_feed_stores_nothing(db_session):
    source = make_source(db_session)
    source.etag = '"v1"'

    stats = ingest_source(db_session, FakeWeb().client(), source)

    assert stats.not_modified is True
    assert articles_by_url(db_session) == {}
    assert source.last_success_at is not None


def test_falls_back_to_feed_summary_when_page_fails(db_session):
    source = make_source(db_session)

    ingest_source(db_session, FakeWeb(pages={}).client(), source)  # every page 404s

    article = articles_by_url(db_session)["https://news.example.com/phone"]
    assert article.content is None
    assert article.rss_summary == "A new phone was announced today."
