"""Fetch one feed into the dev database and print what happened.

Usage: uv run python -m scripts.fetch_once <feed_url> [name]
"""

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.ingest.fetcher import make_http_client
from app.ingest.ingest import ingest_source
from app.models import Source


def main(feed_url: str, name: str) -> None:
    with SessionLocal() as session, make_http_client() as client:
        source = session.scalar(select(Source).where(Source.feed_url == feed_url))
        if source is None:
            source = Source(name=name, feed_url=feed_url)
            session.add(source)
            session.flush()  # assigns source.id

        stats = ingest_source(session, client, source)
        session.commit()
        print(f"{source.name}: {stats}")


if __name__ == "__main__":
    url = sys.argv[1]
    main(url, sys.argv[2] if len(sys.argv) > 2 else url)
