"""Load the feeds in sources.toml into the database. Safe to run again after editing the file.

Usage: uv run python -m scripts.seed_sources [path/to/sources.toml]
"""

import sys
import tomllib
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import Source


def load_sources(path: Path) -> list[dict]:
    with path.open("rb") as f:
        return tomllib.load(f)["source"]


def seed(path: Path) -> int:
    sources = load_sources(path)
    with SessionLocal() as session:
        for row in sources:
            # New feed -> insert. Known feed_url -> update its name/category, so editing the
            # file and re-running keeps the DB in sync. Backoff state is left untouched.
            session.execute(
                insert(Source)
                .values(name=row["name"], feed_url=row["feed_url"], category=row["category"])
                .on_conflict_do_update(
                    index_elements=["feed_url"],
                    set_={"name": row["name"], "category": row["category"]},
                )
            )
        session.commit()
    return len(sources)


if __name__ == "__main__":
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "sources.toml")
    print(f"Seeded {seed(path)} sources from {path}")
