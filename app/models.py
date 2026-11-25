"""Database tables, defined as Python classes (SQLAlchemy 2.0 style).

Alembic reads these classes to generate migrations, so this file is the single
place where the schema is described in code.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, BigInteger, DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# all-MiniLM-L6-v2 turns any text into exactly 384 numbers.
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class Source(Base):
    """One RSS feed we pull from."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    feed_url: Mapped[str] = mapped_column(unique=True)
    category: Mapped[str] = mapped_column(server_default="general")  # e.g. world, tech, business
    is_active: Mapped[bool] = mapped_column(server_default="true")

    # Conditional GET: send these back to the feed server; it replies 304 if nothing changed.
    etag: Mapped[str | None]
    last_modified: Mapped[str | None]

    # Backoff state. The scheduler fetches a source only when next_fetch_at <= now.
    # Living in the DB (not in Celery) means it survives restarts and every worker sees it.
    consecutive_failures: Mapped[int] = mapped_column(server_default="0")
    next_fetch_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Article(Base):
    """One article from a feed. Duplicates are stored too, linked to their original."""

    __tablename__ = "articles"
    __table_args__ = (
        # At most ONE original per text. Partial index: it only covers rows where
        # duplicate_of_id IS NULL, so any number of duplicates can share that hash.
        # Without it, two workers saving the same wire story at the same moment could
        # both store it as an original.
        Index(
            "uq_articles_original_content_hash",
            "content_hash",
            unique=True,
            postgresql_where=text("duplicate_of_id IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    url: Mapped[str]  # the link exactly as the feed gave it
    # Cleaned-up URL (tracking params removed, etc). UNIQUE is what makes ingestion
    # idempotent: storing the same article twice is impossible, not just avoided.
    canonical_url: Mapped[str] = mapped_column(unique=True)

    title: Mapped[str]
    rss_summary: Mapped[str | None]  # short description from the feed itself
    content: Mapped[str | None]  # full text from trafilatura; None if extraction failed

    # sha256 hex = 64 chars. No plain index: the only lookup ("the original with this hash")
    # uses uq_articles_original_content_hash (in __table_args__), whose WHERE matches it.
    content_hash: Mapped[str] = mapped_column(String(64))
    # 128 MinHash values. BigInteger because they go up to 2^32 - 1,
    # which overflows Postgres's 4-byte `integer` (max ~2.1 billion).
    minhash: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    # NULL = this is an original. Otherwise points at the article this one copies.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), index=True)

    # Meaning vector (pgvector). Only originals get one; duplicates follow their original.
    # NULL = not embedded yet. Comes back from the DB as a numpy array, so test it with
    # `is None`, never with `if article.embedding:`.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # The event this article is about. NULL = not clustered yet. Duplicates get their
    # original's event, so counting sources per event is one simple query.
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Indexed because dedupe loads "originals fetched in the last 3 days".
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Event(Base):
    """A real-world happening that groups many articles (see app/events/cluster.py)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]  # the first article's title, until the LLM summary (Week 3)
    summary: Mapped[str | None]  # LLM summary (Week 3)

    # The average of its original articles' embeddings: the event's "centre of meaning".
    # A new article joins the event whose centroid is closest, if it's close enough.
    centroid: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    # When its newest article was published. Only events with recent articles can take
    # new ones (so "storm hits coast" in March doesn't join last October's storm).
    last_article_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    article_count: Mapped[int] = mapped_column(server_default="0")
    source_count: Mapped[int] = mapped_column(server_default="0")

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
