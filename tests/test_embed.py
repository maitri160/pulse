import hashlib

import numpy as np
from sqlalchemy import select

from app.events.embed import MAX_WORDS, embed_pending, embedding_text
from app.models import EMBEDDING_DIM, Article, Source


class FakeEmbedder:
    """Deterministic stand-in for MiniLM: same text -> same unit-length vector, instantly.

    Records each call's texts, so tests can check what was embedded.
    """

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(texts)
        return [unit_vector(t) for t in texts]


def unit_vector(seed_text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:4], "big")
    v = np.random.default_rng(seed).normal(size=EMBEDDING_DIM)
    return (v / np.linalg.norm(v)).tolist()


def make_source(db_session) -> Source:
    source = Source(name="Example News", feed_url="https://example.com/rss")
    db_session.add(source)
    db_session.flush()
    return source


def make_article(db_session, source, n: int, **overrides) -> Article:
    fields = {
        "source_id": source.id,
        "url": f"https://example.com/{n}",
        "canonical_url": f"https://example.com/{n}",
        "title": f"Story {n}",
        "content": f"Body of story {n}.",
        "content_hash": f"{n:064d}",
    }
    article = Article(**(fields | overrides))
    db_session.add(article)
    db_session.flush()
    return article


# --- what text gets embedded ---


def test_embedding_text_is_title_plus_start_of_body():
    body = " ".join(f"w{i}" for i in range(MAX_WORDS + 50))
    text = embedding_text("Storm hits coast", body, "ignored summary")

    assert text.startswith("Storm hits coast. w0 w1")
    assert len(text.split()) == 3 + MAX_WORDS  # 3 title words + the cut body
    assert f"w{MAX_WORDS}" not in text.split()


def test_embedding_text_falls_back_to_summary_then_title():
    assert embedding_text("Title", None, "Short summary.") == "Title. Short summary."
    assert embedding_text("Title", None, None) == "Title"


# --- embedding pending articles ---


def test_embeds_originals_only_and_skips_done_ones(db_session):
    source = make_source(db_session)
    original = make_article(db_session, source, 1)
    duplicate = make_article(db_session, source, 2, duplicate_of_id=original.id)
    already = make_article(db_session, source, 3, embedding=unit_vector("old"))
    fake = FakeEmbedder()

    assert embed_pending(db_session, fake) == 1

    assert fake.batches == [["Story 1. Body of story 1."]]
    db_session.refresh(original)
    db_session.refresh(duplicate)
    db_session.refresh(already)
    assert len(original.embedding) == EMBEDDING_DIM
    assert duplicate.embedding is None  # copies follow their original, never embedded
    assert np.allclose(already.embedding, unit_vector("old"))  # left untouched


def test_embeds_one_at_a_time_oldest_first_until_nothing_is_left(db_session):
    source = make_source(db_session)
    for n in range(3):
        make_article(db_session, source, n)
    fake = FakeEmbedder()

    assert embed_pending(db_session, fake) == 3
    assert fake.batches == [
        ["Story 0. Body of story 0."],
        ["Story 1. Body of story 1."],
        ["Story 2. Body of story 2."],
    ]
    assert embed_pending(db_session, fake) == 0  # second run: nothing to do


def test_max_articles_caps_one_run(db_session):
    source = make_source(db_session)
    for n in range(5):
        make_article(db_session, source, n)

    assert embed_pending(db_session, FakeEmbedder(), max_articles=2) == 2


def test_stored_vectors_work_with_pgvector_distance(db_session):
    """The column is a real pgvector column: Postgres can rank rows by cosine distance."""
    source = make_source(db_session)
    near = make_article(db_session, source, 1, embedding=unit_vector("storm"))
    make_article(db_session, source, 2, embedding=unit_vector("election"))

    query = unit_vector("storm")
    closest = db_session.scalar(
        select(Article).order_by(Article.embedding.cosine_distance(query)).limit(1)
    )

    assert closest.id == near.id


# --- failures ---


class FlakyEmbedder(FakeEmbedder):
    """Fails on any text with `bad` in it, like a model crash would."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        if any("bad" in t for t in texts):
            self.failures += 1
            raise RuntimeError("model blew up")
        return super().encode(texts)


def test_one_bad_article_does_not_block_the_rest(db_session):
    source = make_source(db_session)
    good_ids = [make_article(db_session, source, n).id for n in (1, 2, 4, 5)]
    bad = make_article(db_session, source, 3, content="a bad body")
    db_session.commit()  # a failure rolls back; the test's own rows must survive that
    flaky = FlakyEmbedder()

    done = embed_pending(db_session, flaky)

    assert done == 4  # 3 failed alone; the ones before AND after it were embedded
    assert flaky.failures == 1  # tried once, not picked again and again in this run
    for article_id in good_ids:
        assert db_session.get(Article, article_id).embedding is not None
    db_session.refresh(bad)
    assert bad.embedding is None  # left for the next run, which will try it again
