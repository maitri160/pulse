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
# One article per transaction. Measured on CPU: encoding 64 texts at once took 0.52 s and
# 64 one at a time 0.58 s, so batching buys ~10% speed but meant one bad article could sink
# 63 good ones and needed a whole fallback path. Not worth it (revisit with a GPU).
MAX_PER_RUN = 1280  # cap per task run (~15 s), so one run can't go on forever

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


def embed_pending(session: Session, embedder: Embedder, max_articles: int = MAX_PER_RUN) -> int:
    """Embed originals that have no embedding yet, one per transaction. Returns how many.

    Duplicates are skipped: they're copies, so they join whatever event their original
    joins. Safe to run any number of times: done rows are no longer NULL.
    """
    done = 0
    failed: set[int] = set()  # failed in THIS run; skipped until the next run tries again
    for _ in range(max_articles):
        article = session.scalars(
            select(Article)
            .where(
                Article.embedding.is_(None),
                Article.duplicate_of_id.is_(None),
                # Without this, a bad article (oldest first) would be picked again straight
                # away, and fail again, until the cap: nothing newer would get embedded.
                Article.id.not_in(list(failed)),
            )
            .order_by(Article.id)  # oldest first
            .limit(1)
            .with_for_update(**ROW_LOCK)
        ).first()
        if article is None:
            break  # nothing left (or everything left is locked by another run)
        article_id = article.id  # read now: after a rollback the object's fields expire

        try:
            text = embedding_text(article.title, article.content, article.rss_summary)
            article.embedding = embedder.encode([text])[0]
            session.commit()  # saves the vector and releases this row's lock
            done += 1
        except Exception:
            session.rollback()  # loses only this one article; earlier commits are safe
            failed.add(article_id)
            logger.exception("Embedding article %d failed; will retry next run", article_id)
    return done
