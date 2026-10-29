"""Turn messy real-world URLs and text into stable forms we can compare and hash."""

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query params that only track where a click came from; they never change the article.
TRACKING_PARAMS = {"fbclid", "gclid", "ref", "cmpid", "ocid", "mc_cid", "mc_eid", "at_medium"}
TRACKING_PREFIXES = ("utm_",)


def _is_tracking_param(name: str) -> bool:
    name = name.lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def canonicalize_url(url: str) -> str:
    """Return one standard spelling of a URL so the same article always gets the same key.

    http://www.Example.com/story/?utm_source=rss&b=2&a=1#comments
      -> https://example.com/story?a=1&b=2
    """
    parts = urlsplit(url.strip())

    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    # Drop tracking params, then sort the rest so ?a=1&b=2 and ?b=2&a=1 match.
    params = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(k)
    )
    # The #fragment ("" here) only scrolls within a page, so it is dropped.
    return urlunsplit((scheme, host, path, urlencode(params), ""))


def normalize_text(text: str) -> str:
    """Lowercase, unify unicode look-alikes, and collapse all whitespace to single spaces."""
    text = unicodedata.normalize("NFKC", text)  # e.g. non-breaking space -> normal space
    return re.sub(r"\s+", " ", text).strip().lower()


def content_hash(text: str) -> str:
    """sha256 of the normalized text: identical content -> identical 64-char hex string."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
