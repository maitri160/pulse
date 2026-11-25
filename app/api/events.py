"""Read-only events API. Workers write events; these endpoints only SELECT."""

import base64
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Article, Event, Source

router = APIRouter(prefix="/events", tags=["events"])

MAX_INT = 2**31 - 1  # largest Postgres integer; a bigger id would be a DB error (500)
MAX_ARTICLES = 100  # newest articles returned for one event; article_count has the total


def get_session() -> Iterator[Session]:
    """One session per request, always closed. Tests swap this for their own session."""
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


# Response shapes. Listing fields here (instead of returning the table rows) keeps internal
# columns such as the centroid out of the API.
class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # can be built from a DB row

    id: int
    title: str
    summary: str | None
    article_count: int
    source_count: int
    first_seen_at: datetime
    last_article_at: datetime


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str
    source: str
    published_at: datetime | None
    duplicate_of_id: int | None


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None  # pass back as ?cursor= for the next page; None = last page


class EventDetail(EventOut):
    articles: list[ArticleOut]


# Select only what we return: a whole Event row would also load its 384-number centroid.
EVENT_COLUMNS = [getattr(Event, name) for name in EventOut.model_fields]


def encode_cursor(last_article_at: datetime, event_id: int) -> str:
    """The sort position of the last event on a page, as one URL-safe string."""
    return base64.urlsafe_b64encode(f"{last_article_at.isoformat()}|{event_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Undo encode_cursor. Clients can send anything, so bad input is a 422, not a crash."""
    try:
        when, event_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        position = datetime.fromisoformat(when), int(event_id)
        if position[0].tzinfo is None or not 0 < position[1] <= MAX_INT:
            raise ValueError("out of range")
    except ValueError as exc:  # includes bad base64 and bad UTF-8, which are ValueErrors
        raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    return position


@router.get("")
def list_events(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    min_sources: Annotated[int, Query(ge=1, le=MAX_INT)] = 1,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> EventPage:
    """Events, most recently active first. (Ranking by importance comes in phase 3.)"""
    query = (
        select(*EVENT_COLUMNS)
        .where(Event.source_count >= min_sources)
        # id breaks ties, so the order is always the same.
        .order_by(Event.last_article_at.desc(), Event.id.desc())
        .limit(limit + 1)  # one extra row tells us whether there is a next page
    )
    if cursor is not None:
        # Keyset pagination: "rows after this position". OFFSET would count rows from the
        # top, so events arriving between two requests would shift and repeat items. (An
        # event that jumps to the top mid-paging can be missed; a refresh shows it.)
        query = query.where(tuple_(Event.last_article_at, Event.id) < decode_cursor(cursor))

    rows = session.execute(query).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor(page[-1].last_article_at, page[-1].id)
    return EventPage(items=[EventOut.model_validate(r) for r in page], next_cursor=next_cursor)


@router.get("/{event_id}")
def get_event(session: SessionDep, event_id: Annotated[int, Path(ge=1, le=MAX_INT)]) -> EventDetail:
    """One event with its newest articles (copies included, marked by duplicate_of_id)."""
    # Two SELECTs: if a worker commits in between, counts can be off by one until the next
    # request. Fine for a live feed.
    event = session.execute(select(*EVENT_COLUMNS).where(Event.id == event_id)).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    articles = session.execute(
        select(
            Article.id,
            Article.title,
            Article.url,
            # One JOIN, not one extra query per article for its source (the N+1 problem).
            Source.name.label("source"),
            Article.published_at,
            Article.duplicate_of_id,
        )
        .join(Source, Source.id == Article.source_id)
        .where(Article.event_id == event_id)
        # Feed dates can't be trusted: never later than the fetch, as in cluster.article_time.
        # Postgres least() ignores NULLs, so a missing date falls back to fetched_at.
        .order_by(func.least(Article.published_at, Article.fetched_at).desc(), Article.id.desc())
        .limit(MAX_ARTICLES)
    ).all()
    return EventDetail(
        **EventOut.model_validate(event).model_dump(),
        articles=[ArticleOut.model_validate(a) for a in articles],
    )
