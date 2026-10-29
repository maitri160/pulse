"""Get the main article text out of a web page (no menus, ads, footers)."""

import httpx
import trafilatura


def extract_text(html: str) -> str | None:
    """Return the article body as plain text, or None if trafilatura finds none."""
    return trafilatura.extract(html, include_comments=False, include_tables=False)


def fetch_article_text(client: httpx.Client, url: str) -> str | None:
    """Download an article page and extract its text.

    Returns None on any download problem: paywalls, blocks and timeouts are common,
    and a missing body is not worth failing the whole feed over. The caller falls
    back to the feed's own summary.
    """
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return extract_text(response.text)
