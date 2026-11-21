import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ingest import ingest
from app.ingest.extract import extract_text
from app.ingest.ingest import ingest_source
from app.ingest.normalize import content_hash
from app.models import Article, Source
from tests.fakes import FEED_URL, RATES_BODY, FakeWeb, article_page


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


def rates_hash() -> str:
    # The hash ingest computes for the rates story: sha256 of the text extracted from its page.
    return content_hash(extract_text(article_page(RATES_BODY)))


def test_race_with_other_worker_saves_article_as_duplicate(db_session, monkeypatch):
    source = make_source(db_session)
    # "Another worker" already stored the rates story as an original, from a different feed.
    other_source = Source(name="Wire Service", feed_url="https://wire.example.net/rss")
    db_session.add(other_source)
    db_session.flush()
    other_original = Article(
        source_id=other_source.id,
        url="https://wire.example.net/rates",
        canonical_url="https://wire.example.net/rates",
        title="Rates rise",
        content_hash=rates_hash(),
    )
    db_session.add(other_original)
    db_session.flush()

    # Our first lookup runs "before the other worker committed", so it finds nothing.
    real_find_original = ingest._find_original
    calls = []

    def find_original_racing(session, digest):
        calls.append(digest)
        return None if len(calls) == 1 else real_find_original(session, digest)

    monkeypatch.setattr(ingest, "_find_original", find_original_racing)

    stats = ingest_source(db_session, FakeWeb().client(), source)  # must not raise

    # rates: race -> duplicate on retry; phone: original; wire copy: duplicate.
    assert (stats.originals, stats.duplicates, stats.skipped) == (1, 2, 0)
    rates = articles_by_url(db_session)["https://news.example.com/rates"]
    assert rates.duplicate_of_id == other_original.id


def test_only_one_original_per_content_hash(db_session):
    source = make_source(db_session)

    def article(n: int, duplicate_of_id=None) -> Article:
        return Article(
            source_id=source.id,
            url=f"https://example.com/{n}",
            canonical_url=f"https://example.com/{n}",
            title="Same story",
            content_hash="b" * 64,
            duplicate_of_id=duplicate_of_id,
        )

    original = article(1)
    db_session.add(original)
    db_session.flush()
    # Any number of duplicates may share the hash: the index only covers originals.
    db_session.add_all([article(2, original.id), article(3, original.id)])
    db_session.flush()

    db_session.add(article(4))  # a second original with the same hash
    with pytest.raises(IntegrityError, match="uq_articles_original_content_hash"):
        db_session.flush()
