import base64
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event

from app.api import events as api
from app.main import app
from app.models import EMBEDDING_DIM, Article, Event, Source

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[api.get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_event(db_session, title, last=NOW, **fields) -> Event:
    fields = {"source_count": 1, "article_count": 1} | fields  # real events have at least one
    event = Event(title=title, centroid=[1.0] * EMBEDDING_DIM, last_article_at=last, **fields)
    db_session.add(event)
    db_session.flush()
    return event


def add_article(db_session, source, event, n, **fields) -> Article:
    article = Article(
        source_id=source.id,
        url=f"https://x.example/{n}",
        canonical_url=f"https://x.example/{n}",
        title=f"Story {n}",
        content="full text",
        content_hash=f"{n:064d}",
        event_id=event.id,
        **fields,
    )
    db_session.add(article)
    db_session.flush()
    return article


def titles(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [e["title"] for e in response.json()["items"]]


def test_no_events(client):
    assert client.get("/events").json() == {"items": [], "next_cursor": None}


def test_newest_activity_first_and_only_public_fields(client, db_session):
    add_event(db_session, "old", NOW - timedelta(days=1))
    add_event(db_session, "new")

    response = client.get("/events")

    assert titles(response) == ["new", "old"]
    assert set(response.json()["items"][0]) == set(api.EventOut.model_fields)  # no centroid


def test_paging_visits_every_event_once_even_with_equal_times(client, db_session):
    for n in range(5):
        add_event(db_session, f"e{n}")  # all the same time: only the id separates them

    seen, cursor = [], None
    for _ in range(3):
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        response = client.get("/events", params=params)
        seen += titles(response)
        cursor = response.json()["next_cursor"]

    assert seen == ["e4", "e3", "e2", "e1", "e0"]
    assert cursor is None


def test_order_is_by_time_not_id_across_pages_and_with_a_filter(client, db_session):
    hours = {"a": 0, "b": 2, "c": 1, "d": 3, "e": 1}  # hours ago; insert order disagrees
    for title, h in hours.items():
        add_event(db_session, title, NOW - timedelta(hours=h), source_count=2)
    add_event(db_session, "hidden", NOW - timedelta(minutes=90), source_count=1)

    seen, cursor = [], None
    for _ in range(3):
        params = {"limit": 2, "min_sources": 2} | ({"cursor": cursor} if cursor else {})
        response = client.get("/events", params=params)
        seen += titles(response)
        cursor = response.json()["next_cursor"]

    assert seen == ["a", "e", "c", "b", "d"]


def test_exactly_full_last_page_has_no_next_cursor(client, db_session):
    add_event(db_session, "a")
    add_event(db_session, "b")
    assert client.get("/events?limit=2").json()["next_cursor"] is None


def test_event_arriving_between_pages_does_not_repeat_items(client, db_session):
    for n in range(4):
        add_event(db_session, f"e{n}", NOW + timedelta(minutes=n))
    first = client.get("/events?limit=2")
    add_event(db_session, "breaking", NOW + timedelta(hours=1))

    params = {"limit": 2, "cursor": first.json()["next_cursor"]}
    second = client.get("/events", params=params)

    assert titles(first) + titles(second) == ["e3", "e2", "e1", "e0"]


def test_min_sources_filter(client, db_session):
    add_event(db_session, "one outlet", source_count=1)
    add_event(db_session, "three outlets", source_count=3)
    assert titles(client.get("/events?min_sources=2")) == ["three outlets"]


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


@pytest.mark.parametrize(
    "cursor",
    ["", "garbage", "é", b64("no-separator"), b64("not-a-date|1"), b64("2026-11-25T12:00:00|1")]
    + [b64(f"2026-11-25T12:00:00+00:00|{n}") for n in ("x", 0, 2**31)]
    + [base64.urlsafe_b64encode(b"\xff\xfe").decode()],
)
def test_bad_cursor_is_422(client, cursor):
    assert client.get("/events", params={"cursor": cursor}).status_code == 422


@pytest.mark.parametrize(
    "path",
    ["/events?limit=0", "/events?limit=101", "/events?min_sources=0", "/events/0"]
    + ["/events/abc", f"/events/{2**31}", f"/events?min_sources={2**31}"],
)
def test_out_of_range_input_is_422(client, path):
    assert client.get(path).status_code == 422


def test_extreme_but_valid_cursor_is_not_a_500(client):
    cursor = b64("0001-01-01T00:00:00+23:59|1")
    assert client.get("/events", params={"cursor": cursor}).status_code == 200


def test_real_session_dependency_works():
    assert TestClient(app).get("/events").status_code == 200


def test_unknown_event_is_404(client):
    assert client.get("/events/999999").status_code == 404


def test_event_detail_lists_its_articles_newest_first(client, db_session):
    source = Source(name="Outlet", feed_url="https://o.example/rss")
    db_session.add(source)
    db_session.flush()
    event, other = add_event(db_session, "mine"), add_event(db_session, "other")
    old = add_article(db_session, source, event, 1, published_at=NOW - timedelta(days=1))
    copy = add_article(db_session, source, event, 2, published_at=NOW, duplicate_of_id=old.id)
    undated = add_article(
        db_session, source, event, 3, published_at=None, fetched_at=NOW - timedelta(hours=1)
    )
    add_article(db_session, source, other, 4, published_at=NOW)

    body = client.get(f"/events/{event.id}").json()

    assert body["title"] == "mine"
    assert [a["id"] for a in body["articles"]] == [copy.id, undated.id, old.id]
    assert body["articles"][0]["duplicate_of_id"] == old.id
    assert body["articles"][0]["source"] == "Outlet"
    assert set(body["articles"][0]) == set(api.ArticleOut.model_fields)  # no full text


def test_future_dated_article_does_not_stay_on_top(client, db_session):
    source = Source(name="Outlet", feed_url="https://o.example/rss")
    db_session.add(source)
    db_session.flush()
    event = add_event(db_session, "e")
    typo = add_article(
        db_session, source, event, 1, published_at=NOW + timedelta(days=1500), fetched_at=NOW
    )
    later = NOW + timedelta(hours=1)
    newer = add_article(db_session, source, event, 2, published_at=later, fetched_at=later)

    body = client.get(f"/events/{event.id}").json()

    assert [a["id"] for a in body["articles"]] == [newer.id, typo.id]


def test_detail_is_two_queries_and_capped(client, db_session, monkeypatch):
    monkeypatch.setattr(api, "MAX_ARTICLES", 2)
    event = add_event(db_session, "big", article_count=3)
    for n in range(3):
        source = Source(name=f"O{n}", feed_url=f"https://o{n}.example/rss")
        db_session.add(source)
        db_session.flush()
        add_article(db_session, source, event, n)
    statements = []
    conn = db_session.connection()
    sa_event.listen(conn, "before_cursor_execute", lambda *a: statements.append(a[2]))

    body = client.get(f"/events/{event.id}").json()

    assert len([s for s in statements if s.startswith("SELECT")]) == 2  # not one per article
    assert (len(body["articles"]), body["article_count"]) == (2, 3)
