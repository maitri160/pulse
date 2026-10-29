import pytest

from app.ingest.normalize import canonicalize_url, content_hash, normalize_text


@pytest.mark.parametrize(
    ("messy", "clean"),
    [
        # tracking params removed, host lowercased, www dropped, http -> https
        (
            "http://www.Example.com/story?utm_source=rss&utm_medium=feed",
            "https://example.com/story",
        ),
        # real params kept and sorted; fragment and trailing slash dropped
        ("https://example.com/story/?b=2&a=1#comments", "https://example.com/story?a=1&b=2"),
        ("https://example.com/story?fbclid=xyz&id=7", "https://example.com/story?id=7"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_canonicalize_url(messy, clean):
    assert canonicalize_url(messy) == clean


def test_same_article_with_different_tracking_gets_same_key():
    a = canonicalize_url("https://news.com/x?utm_source=twitter")
    b = canonicalize_url("https://www.news.com/x/?utm_source=rss&ref=home")
    assert a == b


def test_normalize_text_collapses_whitespace_and_case():
    assert normalize_text("  Fed RAISES\n\trates today ") == "fed raises rates today"


def test_content_hash_ignores_formatting_but_not_words():
    assert content_hash("Fed raises rates.") == content_hash("  fed  RAISES rates. ")
    assert content_hash("Fed raises rates.") != content_hash("Fed cuts rates.")
    assert len(content_hash("anything")) == 64
