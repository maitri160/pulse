"""A fake internet for tests: maps URLs to canned responses, records requests."""

from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
FEED_URL = "https://news.example.com/rss"

RATES_BODY = (
    "The central bank raised its main interest rate by a quarter of a percentage point on "
    "Wednesday, the first increase in more than a year. Officials said inflation had stayed "
    "above target for longer than expected and that further moves would depend on the data. "
    "Markets had largely priced in the decision, and stocks fell slightly after the announcement."
)
PHONE_BODY = (
    "A technology company unveiled its newest phone at an event on Wednesday. The device has a "
    "larger battery, a brighter screen and a redesigned camera system. It goes on sale next "
    "month, and analysts expect strong demand despite a higher price than last year's model."
)


def article_page(body: str) -> str:
    return f"<html><body><nav>Home | World</nav><article><p>{body}</p></article></body></html>"


# The wire copy on other.example.org has the same body as the original rates story.
DEFAULT_PAGES = {
    "https://news.example.com/rates": article_page(RATES_BODY),
    "https://news.example.com/phone": article_page(PHONE_BODY),
    "https://other.example.org/wire/rates": article_page(RATES_BODY),
}


class FakeWeb:
    def __init__(self, pages: dict[str, str] | None = None, feed_status: int = 200):
        self.pages = DEFAULT_PAGES if pages is None else pages
        self.feed_status = feed_status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # Ignore query strings so ?utm_source=... links resolve to the same page.
        url = f"{request.url.scheme}://{request.url.host}{request.url.path}"
        if url == FEED_URL:
            if self.feed_status != 200:
                return httpx.Response(self.feed_status)
            if request.headers.get("If-None-Match") == '"v1"':
                return httpx.Response(304)
            content = (FIXTURES / "sample_feed.xml").read_bytes()
            return httpx.Response(200, content=content, headers={"ETag": '"v1"'})
        if url.rstrip("/") in self.pages:
            return httpx.Response(200, text=self.pages[url.rstrip("/")])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
