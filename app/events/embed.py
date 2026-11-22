"""Turn articles into embeddings: 384 numbers that stand for what the text MEANS.

Two articles about the same event land close together even when their wording is
completely different, which MinHash (shared 5-word phrases) can't see. Clustering
articles into events (M8) is built on this.
Read later: https://sbert.net/docs/quickstart.html
"""

import logging
from functools import cache
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Article

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# The model reads at most 256 word pieces (~200 words) and silently ignores the rest, so we
# cut the text ourselves: the title plus the opening paragraphs, where news puts the facts.
MAX_WORDS = 200
BATCH_SIZE = 64  # articles per transaction; the model encodes them in one go
MAX_BATCHES_PER_RUN = 20  # up to 1,280 articles per task run, so a backlog clears quickly

# If a run is slow and the next one starts, the second skips the rows the first has locked
# instead of embedding them again (SKIP LOCKED). key_share=True asks for FOR NO KEY UPDATE,
# the weaker lock: it still lets ingest insert a duplicate whose foreign key points at one of
# these rows. Plain FOR UPDATE would block that insert (checked, and covered by a test).
ROW_LOCK = {"skip_locked": True, "key_share": True}


class Embedder(Protocol):
    """Anything that turns texts into vectors. Tests pass a fake; production uses MiniLM."""

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class MiniLMEmbedder:
    def __init__(self) -> None:
        # Imported here, not at the top: sentence-transformers loads PyTorch (~1 s, ~400 MB).
        # Only the process that actually embeds should pay that, not the API or fetchers.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(MODEL_NAME, device="cpu")

    def encode(self, texts: list[str]) -> list[list[float]]:
        # normalize_embeddings: every vector gets length 1, so cosine similarity is just a
        # dot product and all vectors are on the same scale.
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return vectors.tolist()


@cache
def get_embedder() -> Embedder:
    """One model per process, loaded on first use and then reused for every task."""
    return MiniLMEmbedder()


def embedding_text(title: str, content: str | None, summary: str | None) -> str:
    """What we embed: the title, then the first MAX_WORDS words of the body (or summary)."""
    # strip() first, so a whitespace-only body falls back to the summary instead of to nothing.
    text = (content or "").strip() or (summary or "").strip()
    body = " ".join(text.split()[:MAX_WORDS])
    return f"{title}. {body}" if body else title


def _embed_and_commit(session: Session, embedder: Embedder, articles: list[Article]) -> None:
    vectors = embedder.encode([embedding_text(a.title, a.content, a.rss_summary) for a in articles])
    for article, vector in zip(articles, vectors, strict=True):
        article.embedding = vector
    session.commit()  # also releases these rows' locks


def _embed_one_by_one(session: Session, embedder: Embedder, ids: list[int]) -> tuple[int, set[int]]:
    """Fallback after a batch failed: try each article alone. Returns (done, failed ids)."""
    done, failed = 0, set()
    for article_id in ids:
        article = session.get(Article, article_id, with_for_update=ROW_LOCK)
        if article is None or article.embedding is not None:
            continue  # another run has it locked, or already embedded it
        try:
            _embed_and_commit(session, embedder, [article])
            done += 1
        except Exception:
            session.rollback()
            failed.add(article_id)
            logger.exception("Embedding article %d failed; will retry next run", article_id)
    return done, failed


def embed_pending(
    session: Session,
    embedder: Embedder,
    batch_size: int = BATCH_SIZE,
    max_batches: int = MAX_BATCHES_PER_RUN,
) -> int:
    """Embed originals that have no embedding yet, in batches. Returns how many were done.

    Duplicates are skipped: they're copies, so they join whatever event their original
    joins. Safe to run any number of times: done rows are no longer NULL.
    """
    done = 0
    failed: set[int] = set()  # failed even alone in THIS run; skipped until the next run
    for _ in range(max_batches):
        batch = session.scalars(
            select(Article)
            .where(
                Article.embedding.is_(None),
                Article.duplicate_of_id.is_(None),
                Article.id.not_in(list(failed)),
            )
            .order_by(Article.id)  # oldest first
            .limit(batch_size)
            .with_for_update(**ROW_LOCK)
        ).all()
        if not batch:
            break
        ids = [a.id for a in batch]

        try:
            _embed_and_commit(session, embedder, batch)
            done += len(batch)
        except Exception:
            # Without this, one bad article would sink its whole batch, and because the
            # query always takes the OLDEST pending rows, the same batch would be picked and
            # fail again on every run: nothing newer would ever be embedded. So retry the
            # batch one article at a time; the good ones go through, the bad one is logged.
            session.rollback()
            logger.warning("Embedding a batch of %d failed; retrying one by one", len(ids))
            one_done, one_failed = _embed_one_by_one(session, embedder, ids)
            done += one_done
            failed |= one_failed

        if len(batch) < batch_size:  # that was the last, partial batch
            break
    return done
