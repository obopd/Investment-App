from datetime import datetime, timezone
from pathlib import Path

from arena import config
from arena.data.news import RobotsChecker, collect_news, parse_feed
from arena.data.tagging import TickerTagger
from arena.db.client import MemoryDB
from fakes import FakeResponse, FakeSession

FEED = (Path(__file__).parent / "fixtures" / "feed.xml").read_bytes()
NOW = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
TAGGER = TickerTagger(config.load("universe")["stocks"])
FEED_CFG = {"name": "Test Wire", "url": "https://wire.example.com/rss"}


def by_title(items):
    return {i["title"]: i for i in items}


def test_parse_feed_cleans_tags_and_timestamps():
    items = by_title(parse_feed(FEED, FEED_CFG, TAGGER, NOW))
    apple = items["Apple (NASDAQ: AAPL) beats estimates & raises guidance"]
    assert apple["url"] == "https://news.example.com/apple-beats?id=7"     # tracking removed
    assert apple["snippet"] == "Apple reported record revenue."               # HTML stripped
    assert apple["tickers"] == ["AAPL"]
    assert apple["published_at"] == datetime(2026, 11, 2, 21, 35, tzinfo=timezone.utc)
    # No-look-ahead safety: missing or future dates fall back to when we collected the item.
    assert items["Item without a date"]["published_at"] == NOW
    assert items["Item dated in the future"]["published_at"] == NOW


def test_tagging_uses_whole_words_only():
    oil = by_title(parse_feed(FEED, FEED_CFG, TAGGER, NOW))["Pineapple prices rise; Exxon and Chevron watch oil"]
    assert oil["tickers"] == ["CVX", "XOM"]
    assert TAGGER.tag("Visa rules for students tighten") == []      # word "Visa" alone is ambiguous
    assert TAGGER.tag("Shares of $V rose") == ["V"]
    assert TAGGER.tag("Johnson & Johnson settles") == ["JNJ"]


def test_newsroom_feeds_tag_their_company():
    items = parse_feed(FEED, {**FEED_CFG, "tickers": ["MSFT"]}, TAGGER, NOW)
    assert all(i["tickers"] == ["MSFT"] for i in items)


def test_robots_rules():
    sess = FakeSession({
        "https://blocked.example.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /"),
        "https://norobots.example.com/robots.txt": FakeResponse(404),
        "https://down.example.com/robots.txt": FakeResponse(503),
    })
    rc = RobotsChecker(sess)
    assert not rc.allowed("https://blocked.example.com/rss")
    assert rc.allowed("https://norobots.example.com/rss")      # 4xx = no rules
    assert not rc.allowed("https://down.example.com/rss")      # 5xx = play safe


def test_collect_news_dedupes_respects_robots_and_skips_failed_hosts():
    sess = FakeSession({
        "https://wire.example.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow:"),
        "https://wire.example.com/rss": FakeResponse(200, FEED),
        "https://nope.example.com/robots.txt": FakeResponse(200, "User-agent: *\nDisallow: /"),
        "https://flaky.example.com/robots.txt": FakeResponse(404),
        "https://flaky.example.com/a": FakeResponse(403),
    })
    feeds = [FEED_CFG,
             {"name": "Blocked", "url": "https://nope.example.com/rss"},
             {"name": "Flaky A", "url": "https://flaky.example.com/a"},
             {"name": "Flaky B", "url": "https://flaky.example.com/b"}]
    db = MemoryDB()
    s = collect_news(db, sess, feeds, TAGGER, NOW)
    assert s["new_articles"] == 4 and s["skipped_robots"] == ["Blocked"]
    assert "HTTP 403" in s["errors"]["Flaky A"] and "skipped" in s["errors"]["Flaky B"]
    assert "https://nope.example.com/rss" not in sess.calls
    assert "https://flaky.example.com/b" not in sess.calls

    again = collect_news(db, sess, feeds, TAGGER, NOW)
    assert again["new_articles"] == 0 and len(db.select("articles")) == 4
    assert [s["domain"] for s in db.select("sources")] == ["news.example.com"]
