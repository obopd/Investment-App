"""Daily entry point. Run after US market close:

    python -m arena.pipeline                 # today (New York date)
    python -m arena.pipeline --date 2026-11-02
    python -m arena.pipeline --dry-run       # in-memory database, mocked AI, nothing saved

Phase 1 steps: seed stocks -> prices -> news -> filings. Later phases add budget check, order
fills, fact cards, trust, equity, post-mortems and team decisions (see CLAUDE.md Section 6).
Every step is safe to re-run: writes are upserts or skip-if-exists.
"""
import argparse
import json
import sys
import traceback
from datetime import date, datetime

from arena import config
from arena.data.common import utcnow
from arena.data.filings import collect_filings, require_user_agent
from arena.data.news import collect_news
from arena.data.prices import TiingoProvider, collect_prices, history_start_from_config
from arena.data.tagging import TickerTagger
from arena.db.client import MemoryDB, SupabaseDB
from arena.http import make_session
from arena.lookahead import NEW_YORK
from arena.market_calendar import is_trading_day


def seed_stocks(db) -> dict:
    u = config.load("universe")
    rows = [{"ticker": s["ticker"], "name": s["name"], "sector": s["sector"], "active": True}
            for s in u["stocks"]]
    rows.append({"ticker": u["benchmark"], "name": "SPDR S&P 500 ETF (benchmark)",
                 "sector": "Benchmark", "active": True})
    db.upsert("stocks", rows, on_conflict="ticker")
    # Stocks removed from the config are deactivated, never deleted (history stays valid).
    keep = {r["ticker"] for r in rows}
    for s in db.select("stocks", {"active": True}, columns="ticker"):
        if s["ticker"] not in keep:
            db.update("stocks", {"ticker": s["ticker"]}, {"active": False})
    return {"stocks": len(rows)}


def run(run_date: date, db, *, dry_run: bool = False, force: bool = False,
        session=None, price_provider=None, now: datetime | None = None) -> dict:
    """Run one day. Dependencies are injectable so tests can run fully offline."""
    now = now or utcnow()
    if not is_trading_day(run_date) and not force:
        db.insert("runs", [{"run_date": run_date, "status": "skipped", "dry_run": dry_run,
                            "finished_at": now, "summary": {"reason": "market closed"}}])
        return {"status": "skipped", "reason": "market closed"}

    run_row = db.insert("runs", [{"run_date": run_date, "status": "running", "dry_run": dry_run,
                                  "started_at": now}], returning=True)[0]
    session = session or make_session()
    summary: dict = {}
    errors: dict = {}

    def step(name, fn):
        try:
            summary[name] = fn()
            if isinstance(summary[name], dict) and summary[name].get("errors"):
                errors[name] = "some items failed (see summary)"
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:500]
            summary[name] = {"failed": True}
            traceback.print_exc()

    universe = config.load("universe")
    stock_tickers = [s["ticker"] for s in universe["stocks"]]

    step("stocks", lambda: seed_stocks(db))

    def prices():
        cfg = config.load("prices")
        provider = price_provider or TiingoProvider.from_env(session, cfg.get("request_delay_seconds", 1.5))
        return collect_prices(db, provider, config.universe_tickers(), run_date,
                              history_start_from_config(cfg))
    step("prices", prices)

    def news():
        cfg = config.load("news")
        return collect_news(db, session, cfg["feeds"], TickerTagger(universe["stocks"]), now,
                            cfg.get("max_items_per_feed", 50), cfg.get("snippet_chars", 500))
    step("news", news)

    def filings():
        cfg = config.load("filings")
        session.headers["User-Agent"] = require_user_agent()  # SEC requires contact info
        return collect_filings(db, session, stock_tickers, now, cfg["forms"], cfg["lookback_days"],
                               cfg["lookback_days_first_run"], cfg.get("request_delay_seconds", 0.2))
    step("filings", filings)

    collection_steps = ("prices", "news", "filings")
    hard_failures = [s for s in collection_steps if summary.get(s, {}).get("failed")]
    if len(hard_failures) == len(collection_steps):
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    db.update("runs", {"id": run_row["id"]}, {
        "status": status, "finished_at": utcnow(), "summary": summary,
        "error": json.dumps(errors)[:2000] if errors else None})
    return {"status": status, "summary": summary, "errors": errors}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="AI Trading Arena daily pipeline")
    p.add_argument("--date", help="run as if it were this New York date (YYYY-MM-DD)")
    p.add_argument("--dry-run", action="store_true", help="in-memory DB and mocked AI; saves nothing")
    p.add_argument("--force", action="store_true", help="run even if the market is closed")
    args = p.parse_args(argv)

    run_date = date.fromisoformat(args.date) if args.date else datetime.now(NEW_YORK).date()
    db = MemoryDB() if args.dry_run else SupabaseDB.from_env()
    result = run(run_date, db, dry_run=args.dry_run, force=args.force)
    print(json.dumps({"date": run_date.isoformat(), **result}, indent=2, default=str))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
