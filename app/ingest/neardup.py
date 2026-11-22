"""Near-duplicate detection with MinHash + LSH.

The exact content hash catches identical text. But outlets often republish a wire story
with small edits: a "(Reuters) -" prefix, a changed headline word, an extra line at the end.
One changed character gives a completely different sha256, so those slip through.

MinHash turns a text into 128 numbers so that two texts' numbers agree in roughly the same
share of positions as the texts share 5-word phrases (their Jaccard similarity). LSH then
finds likely matches without comparing the new article with every stored one.
Read later: https://ekzhu.com/datasketch/lsh.html
"""

import re
from datetime import UTC, datetime, timedelta

import numpy as np
from datasketch import MinHash, MinHashLSH
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.normalize import normalize_text
from app.models import Article

NUM_PERM = 128  # numbers per signature; must match the 128 stored in articles.minhash
SHINGLE_WORDS = 5  # compare overlapping 5-word phrases
THRESHOLD = 0.8  # estimated Jaccard similarity at or above this = near-duplicate
# LSH is tuned looser than THRESHOLD on purpose. At its own tuning point LSH only finds a
# match about half the time (the S-curve), so tuning it to 0.8 would miss many real 0.8+
# pairs. Tuned to 0.5 it finds nearly every 0.8+ pair; extra candidates are then
# rejected by the exact THRESHOLD check in find_original.
CANDIDATE_THRESHOLD = 0.5
MIN_WORDS = 50  # shorter texts (RSS blurbs) share too few phrases for a fair comparison
WINDOW = timedelta(days=3)  # wire copies appear within hours; older articles aren't checked
SEED = 1  # fixed, so the same text always gives the same signature (also across restarts)
# datasketch 2.0's default hashing scheme. Named explicitly because stored numbers carry no
# trace of the scheme that made them, and comparing numbers from two schemes gives nonsense.
# Values are 32-bit, so they fit the bigint[] column.
SCHEME = "affine32"


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", normalize_text(text))


def shingles(text: str) -> set[str]:
    """The overlapping 5-word phrases of a text, ignoring case and punctuation."""
    words = _words(text)
    return {" ".join(words[i : i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def signature(text: str | None) -> list[int] | None:
    """128-number MinHash signature, or None if the text is too short to compare fairly."""
    if not text or len(_words(text)) < MIN_WORDS:
        return None
    m = MinHash(num_perm=NUM_PERM, seed=SEED, scheme=SCHEME)
    m.update_batch([s.encode("utf-8") for s in shingles(text)])
    # numpy uint32 -> plain Python ints, which SQLAlchemy stores in the bigint[] column.
    return [int(v) for v in m.hashvalues]


def _to_minhash(sig: list[int]) -> MinHash:
    """Rebuild a MinHash object from the numbers stored in the database.

    Both add() and find_original() go through here, so LSH always sees the same uint32
    numbers on both sides; never query with a MinHash built some other way.
    """
    return MinHash(
        num_perm=NUM_PERM, seed=SEED, scheme=SCHEME, hashvalues=np.array(sig, dtype=np.uint32)
    )


class NearDupIndex:
    """In-memory LSH index of recent ORIGINAL articles (duplicates are never matched against,
    so a near-duplicate always points straight at an original, never at another copy)."""

    def __init__(self) -> None:
        # datasketch picks the band layout (how many bands, rows per band) for this threshold.
        self._lsh = MinHashLSH(threshold=CANDIDATE_THRESHOLD, num_perm=NUM_PERM)
        self._minhashes: dict[int, MinHash] = {}

    @classmethod
    def load(cls, session: Session) -> "NearDupIndex":
        """Build the index from originals fetched in the last WINDOW that have a signature.

        Rebuilt per feed fetch instead of kept in Redis: a few thousand rows load in well
        under a second, and the database stays the only copy of the truth.
        """
        index = cls()
        rows = session.execute(
            select(Article.id, Article.minhash).where(
                Article.duplicate_of_id.is_(None),
                Article.minhash.is_not(None),
                Article.fetched_at >= datetime.now(UTC) - WINDOW,
            )
        )
        for article_id, sig in rows:
            index.add(article_id, sig)
        return index

    def add(self, article_id: int, sig: list[int]) -> None:
        m = _to_minhash(sig)
        self._lsh.insert(article_id, m)
        self._minhashes[article_id] = m

    def find_original(self, sig: list[int]) -> int | None:
        """Id of the most similar original at or above THRESHOLD, or None.

        LSH returns *candidates* (articles sharing at least one band). Some are false
        positives, so each one is checked with the full 128-number similarity estimate.
        Ties go to the lowest id (the oldest article), so the result doesn't depend on
        set ordering.
        """
        m = _to_minhash(sig)
        best_id, best_sim = None, 0.0
        for candidate in sorted(self._lsh.query(m)):  # ascending id, so ">" keeps the oldest
            sim = m.jaccard(self._minhashes[candidate])
            if sim >= THRESHOLD and sim > best_sim:
                best_id, best_sim = candidate, sim
        return best_id
