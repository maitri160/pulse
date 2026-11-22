"""Lock behaviour of embed_pending, with REAL separate connections.

The usual db_session fixture can't do this: its rows are never committed, so no other
connection can see them. These tests commit real rows to pulse_test and delete them after.
"""

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db import engine
from app.events.embed import ROW_LOCK, embed_pending
from app.models import Article, Source
from tests.test_embed import FakeEmbedder


@pytest.fixture
def two_committed_articles():
    with Session(engine) as s:
        source = Source(name="Lock test", feed_url="https://locktest.example/rss")
        s.add(source)
        s.flush()
        articles = [
            Article(
                source_id=source.id,
                url=f"https://locktest.example/{n}",
                canonical_url=f"https://locktest.example/{n}",
                title=f"Lock story {n}",
                content_hash=f"{n:064d}",
            )
            for n in (1, 2)
        ]
        s.add_all(articles)
        s.commit()
        ids, source_id = [a.id for a in articles], source.id
    yield ids, source_id
    with Session(engine) as s:  # clean up everything this test committed
        s.execute(delete(Article).where(Article.source_id == source_id))
        s.execute(delete(Source).where(Source.id == source_id))
        s.commit()


def test_overlapping_run_skips_locked_rows_and_ingest_is_not_blocked(two_committed_articles):
    (first_id, second_id), source_id = two_committed_articles

    with engine.connect() as run_a:
        run_a.begin()
        # Run A has locked the first article (as a slow embed run would) and not committed.
        run_a.execute(select(Article.id).where(Article.id == first_id).with_for_update(**ROW_LOCK))

        # Run B starts meanwhile: it must take only the unlocked article, not wait for A.
        with Session(engine) as run_b:
            fake = FakeEmbedder()
            assert embed_pending(run_b, fake) == 1
            assert fake.batches == [["Lock story 2"]]

        # Ingest, meanwhile, saves a duplicate whose foreign key points at the locked row.
        # With FOR NO KEY UPDATE this goes straight through; plain FOR UPDATE would block it.
        with engine.connect() as ingest:
            ingest.begin()
            ingest.execute(text("SET LOCAL lock_timeout = '1s'"))  # fail fast if blocked
            ingest.execute(
                text(
                    "INSERT INTO articles (source_id, url, canonical_url, title, content_hash,"
                    " duplicate_of_id) VALUES (:s, 'u', 'https://locktest.example/dup', 't',"
                    " repeat('f', 64), :orig)"
                ),
                {"s": source_id, "orig": first_id},
            )
            ingest.rollback()
        run_a.rollback()

    with Session(engine) as s:
        assert s.get(Article, first_id).embedding is None  # A never finished
        assert s.get(Article, second_id).embedding is not None
