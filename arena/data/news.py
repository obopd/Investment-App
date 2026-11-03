"""News from public RSS feeds. Respects robots.txt and never fetches article pages (no paywall
bypassing): only the title and snippet the publisher puts in its own feed are stored."""
import calendar
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import feedparser

from arena.data.common import (clean_text, domain_of, get_or_create_sources, normalize_url,
                               safe_published_at, store_articles)
from arena.http import TIMEOUT


class RobotsChecker:
    """Caches robots.txt per host.

    Follows RFC 9309: a 4xx robots.txt means "no rules" (allowed); a 5xx or network error
    means "assume disallowed" to stay on the safe side.
    """

    def __init__(self, session):
        self.s = session
        self.cache: dict[str, RobotFileParser | bool] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.cache:
            try:
                r = self.s.get(f"{host}/robots.txt", timeout=TIMEOUT)
                if r.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self.cache[host] = rp
                else:
                    self.cache[host] = 400 <= r.status_code < 500
            except Exception:
                self.cache[host] = False
        rule = self.cache[host]
        if isinstance(rule, bool):
            return rule
        return rule.can_fetch(self.s.headers.get("User-Agent", "*"), url)


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return None


def parse_feed(content: bytes, feed: dict, tagger, collected_at: datetime,
               max_items: int = 50, snippet_chars: int = 500) -> list[dict]:
    parsed = feedparser.parse(content)
    out = []
    for e in parsed.entries[:max_items]:
        link, title = e.get("link"), clean_text(e.get("title"))
        if not link or not title:
            continue
        snippet = clean_text(e.get("summary"), snippet_chars)
        tickers = feed.get("tickers") or tagger.tag(title, snippet)
        out.append({
            "url": normalize_url(link),
            "kind": "news",
            "feed": feed["name"],
            "title": title[:500],
            "snippet": snippet,
            "published_at": safe_published_at(_entry_time(e), collected_at),
            "collected_at": collected_at,
            "tickers": sorted(set(tickers)),
        })
    return out


def collect_news(db, session, feeds: list[dict], tagger, collected_at: datetime,
                 max_items: int = 50, snippet_chars: int = 500) -> dict:
    robots = RobotsChecker(session)
    failed_hosts: set[str] = set()
    summary = {"feeds_ok": 0, "new_articles": 0, "skipped_robots": [], "errors": {}}
    items: list[dict] = []
    for feed in feeds:
        host = urlsplit(feed["url"]).netloc
        if host in failed_hosts:   # one failure per host is enough; don't hammer it
            summary["errors"][feed["name"]] = "skipped: host failed earlier this run"
            continue
        if not robots.allowed(feed["url"]):
            summary["skipped_robots"].append(feed["name"])
            continue
        try:
            r = session.get(feed["url"], timeout=TIMEOUT)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            items += parse_feed(r.content, feed, tagger, collected_at, max_items, snippet_chars)
            summary["feeds_ok"] += 1
        except Exception as e:
            failed_hosts.add(host)
            summary["errors"][feed["name"]] = str(e)[:200]

    # Source = the publisher's domain (what trust scoring rates), named after the feed when the
    # feed belongs to that publisher.
    names = {}
    for it in items:
        d = domain_of(it["url"])
        feed_domain = domain_of(next(f["url"] for f in feeds if f["name"] == it["feed"]))
        names.setdefault(d, it["feed"] if feed_domain == d else d)
    source_ids = get_or_create_sources(db, names)
    for it in items:
        it["source_id"] = source_ids[domain_of(it["url"])]
    summary["new_articles"] = store_articles(db, items)
    return summary
