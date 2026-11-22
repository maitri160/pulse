"""Compute MinHash signatures for stored articles that don't have one yet.

Needed once after milestone 6: articles saved before then have minhash = NULL, so new
articles couldn't be matched against them. Only fills signatures; it doesn't re-mark old
articles as duplicates (those decisions were made when they arrived).
Safe to rerun: it only touches rows that still have no signature.

    uv run python -m scripts.backfill_minhash
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.ingest.neardup import signature
from app.models import Article


def backfill(session: Session) -> tuple[int, int]:
    """Returns (rows checked, signatures written)."""
    articles = session.scalars(
        select(Article).where(Article.minhash.is_(None), Article.content.is_not(None))
    ).all()
    written = 0
    for article in articles:
        sig = signature(article.content)
        if sig is not None:  # None = body too short; stays NULL, which is correct
            article.minhash = sig
            written += 1
    session.commit()
    return len(articles), written


if __name__ == "__main__":
    with SessionLocal() as db:
        checked, written = backfill(db)
    print(f"Checked {checked} articles, wrote {written} signatures")
