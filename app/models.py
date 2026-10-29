"""Database tables, defined as Python classes (SQLAlchemy 2.0 style).

Alembic reads these classes to generate migrations, so this file is the single
place where the schema is described in code.
"""

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)

    url: Mapped[str]  # the link exactly as the feed gave it
    # Cleaned-up URL (tracking params removed, etc). UNIQUE is what makes ingestion
    # idempotent: storing the same article twice is impossible, not just avoided.
    canonical_url: Mapped[str] = mapped_column(unique=True)

    title: Mapped[str]
    rss_summary: Mapped[str | None]  # short description from the feed itself
    content: Mapped[str | None]  # full text from trafilatura; None if extraction failed

    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256 hex = 64 chars
    # 128 MinHash values. BigInteger because they go up to 2^32 - 1,
    # which overflows Postgres's 4-byte `integer` (max ~2.1 billion).
    minhash: Mapped[list[int] | None] = mapped_column(ARRAY(BigInteger))
    # NULL = this is an original. Otherwise points at the article this one copies.
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), index=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Indexed because dedupe loads "originals fetched in the last 3 days".
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Event(Base):
    """A real-world happening that groups many articles. Filled in from Week 2."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    summary: Mapped[str | None]  # LLM summary (Week 3)

    article_count: Mapped[int] = mapped_column(server_default="0")
    source_count: Mapped[int] = mapped_column(server_default="0")

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
