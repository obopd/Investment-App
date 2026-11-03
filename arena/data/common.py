"""Helpers shared by the news and filings collectors."""
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRACKING = ("utm_", ".tsrc", "cmpid", "mod")


def clean_text(s: str | None, limit: int | None = None) -> str:
    s = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", s or ""))).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def normalize_url(url: str) -> str:
    """Drop tracking parameters and fragments so the same article isn't stored twice."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def domain_of(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def safe_published_at(published: datetime | None, collected: datetime) -> datetime:
    """No-look-ahead safety: a missing or future timestamp becomes the collection time.

    The true publish time can't be later than when we saw the item, so this never lets an
    item look older than it is.
    """
    if published is None or published > collected:
        return collected
    return published


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_sources(db, names_by_domain: dict[str, str]) -> dict[str, int]:
    """Return {domain: source_id}, creating sources that don't exist yet."""
    if not names_by_domain:
        return {}
    ids = {}
    for domain in names_by_domain:
        found = db.select("sources", {"domain": domain}, columns="id,domain")
        if found:
            ids[domain] = found[0]["id"]
    missing = [{"domain": d, "name": n} for d, n in names_by_domain.items() if d not in ids]
    if missing:
        for row in db.upsert("sources", missing, on_conflict="domain", returning=True):
            ids[row["domain"]] = row["id"]
    return ids


def store_articles(db, articles: list[dict]) -> int:
    """Insert articles, skipping URLs already stored. Returns how many were new."""
    if not articles:
        return 0
    # Same URL can appear in two feeds within one run; keep the first.
    unique = list({a["url"]: a for a in reversed(articles)}.values())
    new = db.upsert("articles", unique, on_conflict="url", ignore_duplicates=True, returning=True)
    return len(new)
