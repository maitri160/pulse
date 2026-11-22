from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.ingest.ingest import ingest_source
from app.ingest.neardup import MIN_WORDS, NUM_PERM, NearDupIndex, shingles, signature
from app.ingest.normalize import content_hash
from app.models import Article, Source
from scripts.backfill_minhash import backfill
from tests.fakes import FEED_URL, FakeWeb, article_page

# ~170 words, the shape of a real wire story.
STORM = (
    "A powerful storm swept across the northern coast on Tuesday night, cutting power to more "
    "than two hundred thousand homes and forcing the closure of several major roads. Emergency "
    "services said they had received hundreds of calls about fallen trees and flooded streets, "
    "and urged residents to stay indoors until the winds eased. The national weather agency "
    "had issued a red warning earlier in the day, its highest level, predicting gusts of up to "
    "one hundred and forty kilometres per hour along exposed parts of the coastline. Rail "
    "operators cancelled most evening services and said disruption was likely to continue into "
    "Wednesday morning while crews inspected the lines for damage. Schools in the worst affected "
    "districts will remain closed, local officials said, and a number of evacuation centres "
    "have been opened for people whose homes were damaged. The energy company said engineers "
    "were working through the night but warned that some rural areas could be without "
    "electricity for several days. Forecasters expect the storm to weaken as it moves inland."
)

# The same story as republished by another outlet: agency prefix, two words changed,
# and an extra line at the end. Different text, so a different sha256.
STORM_EDITED = (
    "(Reuters) - "
    + STORM.replace("Tuesday night", "Tuesday evening").replace("major roads", "main roads")
    + " Reporting by our correspondent; editing by the news desk."
)

# A different event, written in the same news style.
ELECTION = (
    "Voters in the capital went to the polls on Sunday in a closely watched election for mayor, "
    "with turnout reported to be higher than at any local vote in the past decade. The two "
    "leading candidates spent the final days of the campaign arguing over housing costs, public "
    "transport and the city budget, which has been under pressure since the pandemic. Polling "
    "stations opened at seven in the morning and long queues formed in several neighbourhoods "
    "before lunchtime. Election officials said counting would begin as soon as polls closed and "
    "that a result was expected in the early hours of Monday. Both campaigns urged supporters "
    "to remain patient, and observers from a civic group said voting had gone smoothly apart "
    "from minor delays at a handful of locations. The winner will take office next month and "
    "faces an immediate decision on whether to raise fares on the metro system."
)


def make_source(db_session) -> Source:
    source = Source(name="Example News", feed_url=FEED_URL)
    db_session.add(source)
    db_session.flush()
    return source


def make_article(db_session, source, n: int, text: str, **overrides) -> Article:
    fields = {
        "source_id": source.id,
        "url": f"https://example.com/{n}",
        "canonical_url": f"https://example.com/{n}",
        "title": f"Story {n}",
        "content": text,
        "content_hash": f"{n:064d}",
        "minhash": signature(text),
    }
    article = Article(**(fields | overrides))
    db_session.add(article)
    db_session.flush()
    return article


# --- signatures ---


def test_shingles_ignore_case_and_punctuation():
    assert shingles("The Bank, raised RATES today!") == shingles("the bank raised rates today")
    assert shingles("one two three four five six") == {
        "one two three four five",
        "two three four five six",
    }


def test_short_or_missing_text_gets_no_signature():
    assert signature(None) is None
    assert signature("The central bank raised rates by a quarter point.") is None
    assert signature(" ".join(["word"] * (MIN_WORDS - 1))) is None


def test_signature_is_stable_and_fits_bigint_column():
    sig = signature(STORM)
    assert sig == signature(STORM)  # fixed seed: same text, same numbers, every run
    assert len(sig) == NUM_PERM
    assert all(0 <= v < 2**32 for v in sig)


# --- matching ---


def test_edited_copy_matches_and_different_story_does_not():
    index = NearDupIndex()
    index.add(1, signature(STORM))

    assert index.find_original(signature(STORM_EDITED)) == 1
    assert index.find_original(signature(ELECTION)) is None


def test_picks_the_most_similar_original():
    index = NearDupIndex()
    index.add(1, signature(ELECTION))
    index.add(2, signature(STORM))

    assert index.find_original(signature(STORM_EDITED)) == 2


def test_load_skips_duplicates_old_articles_and_missing_signatures(db_session):
    source = make_source(db_session)
    recent = make_article(db_session, source, 1, STORM)
    make_article(db_session, source, 2, ELECTION, duplicate_of_id=recent.id)
    make_article(db_session, source, 3, ELECTION, fetched_at=datetime.now(UTC) - timedelta(days=4))
    make_article(db_session, source, 4, ELECTION, minhash=None)

    index = NearDupIndex.load(db_session)

    assert index.find_original(signature(STORM_EDITED)) == recent.id
    # Rows 2, 3 and 4 are all ELECTION but each is excluded for a different reason.
    assert index.find_original(signature(ELECTION)) is None


# --- through the real ingest path ---


def test_ingest_marks_edited_wire_copy_as_near_duplicate(db_session):
    source = make_source(db_session)
    web = FakeWeb(
        pages={
            "https://news.example.com/rates": article_page(STORM),
            "https://news.example.com/phone": article_page(ELECTION),
            "https://other.example.org/wire/rates": article_page(STORM_EDITED),
        }
    )

    stats = ingest_source(db_session, web.client(), source)

    assert (stats.originals, stats.duplicates, stats.near_duplicates) == (2, 0, 1)
    stored = {a.canonical_url: a for a in db_session.scalars(select(Article))}
    original = stored["https://news.example.com/rates"]
    copy = stored["https://other.example.org/wire/rates"]
    assert copy.content_hash != original.content_hash  # the exact check alone would miss it
    assert copy.duplicate_of_id == original.id
    assert len(copy.minhash) == NUM_PERM


def test_near_duplicate_matches_original_from_an_earlier_fetch(db_session):
    source = make_source(db_session)
    earlier = make_article(db_session, source, 1, STORM)
    web = FakeWeb(
        pages={
            "https://news.example.com/rates": article_page(STORM_EDITED),
            "https://news.example.com/phone": article_page(ELECTION),
            "https://other.example.org/wire/rates": article_page(ELECTION + " Updated."),
        }
    )

    stats = ingest_source(db_session, web.client(), source)

    stored = {a.canonical_url: a for a in db_session.scalars(select(Article))}
    assert stored["https://news.example.com/rates"].duplicate_of_id == earlier.id
    # The ELECTION pair matched each other within the same fetch.
    assert stats.near_duplicates == 2


def test_backfill_fills_missing_signatures_once(db_session):
    source = make_source(db_session)
    old = make_article(db_session, source, 1, STORM, minhash=None)
    make_article(db_session, source, 2, "Too short to sign.", minhash=None)

    assert backfill(db_session) == (2, 1)
    assert old.minhash == signature(STORM)
    assert backfill(db_session) == (1, 0)  # only the short one is left, and it stays NULL


def test_exact_copy_wins_and_index_is_not_loaded(db_session, monkeypatch):
    """An exact hash match is final: no need to load the LSH index at all."""
    source = make_source(db_session)
    loads = []
    real_load = NearDupIndex.load
    monkeypatch.setattr(NearDupIndex, "load", lambda s: loads.append(1) or real_load(s))
    make_article(db_session, source, 1, STORM, content_hash=content_hash(STORM))
    web = FakeWeb(
        pages={
            "https://news.example.com/rates": article_page(STORM),  # exact copy of row 1
            "https://news.example.com/phone": article_page("Short page."),  # too short
            "https://other.example.org/wire/rates": article_page(STORM),  # exact copy too
        }
    )

    stats = ingest_source(db_session, web.client(), source)

    assert (stats.originals, stats.duplicates, stats.near_duplicates) == (1, 2, 0)
    assert loads == []  # every article was settled without the near-dup index


def test_short_body_is_saved_as_original_without_signature(db_session):
    source = make_source(db_session)
    web = FakeWeb(pages={"https://news.example.com/phone": article_page("Brief update only.")})

    ingest_source(db_session, web.client(), source)

    phone = db_session.scalar(
        select(Article).where(Article.canonical_url == "https://news.example.com/phone")
    )
    assert phone.duplicate_of_id is None
    assert phone.minhash is None
