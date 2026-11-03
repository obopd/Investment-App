"""Daily OHLCV prices. Providers are pluggable because free sources change.

Tiingo terms (free tier, checked 2026-11): personal / non-commercial use, data may not be
redistributed. So raw prices are stored but never exposed to the public dashboard.
"""
import os
import time
from datetime import date

from arena.http import TIMEOUT


class PriceProviderError(RuntimeError):
    pass


class TiingoProvider:
    name = "tiingo"
    URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

    def __init__(self, token: str, session, delay: float = 1.5):
        self.token, self.s, self.delay = token, session, delay

    @classmethod
    def from_env(cls, session, delay: float = 1.5):
        token = os.environ.get("TIINGO_API_KEY", "").strip()
        if not token:
            raise PriceProviderError("TIINGO_API_KEY is not set (GitHub Secrets).")
        return cls(token, session, delay)

    def fetch(self, ticker: str, start: date, end: date) -> list[dict]:
        # Token goes in a header, never the URL, so it can't leak into logs.
        r = self.s.get(self.URL.format(ticker=ticker),
                       params={"startDate": start.isoformat(), "endDate": end.isoformat(),
                               "format": "json"},
                       headers={"Authorization": f"Token {self.token}"}, timeout=TIMEOUT)
        if self.delay:
            time.sleep(self.delay)
        if r.status_code != 200:
            raise PriceProviderError(f"Tiingo {ticker}: HTTP {r.status_code} {r.text[:150]}")
        return [{
            "ticker": ticker,
            "date": row["date"][:10],
            "open": row.get("open"), "high": row.get("high"), "low": row.get("low"),
            "close": row.get("close"), "adj_close": row.get("adjClose"),
            "volume": int(row["volume"]) if row.get("volume") is not None else None,
        } for row in r.json()]


def _adjustment_changed(stored: dict, fresh_rows: list[dict]) -> bool:
    """Dividends and splits rewrite every past adjusted close. If the overlap day disagrees,
    the stored history is stale and must be refetched."""
    same_day = next((r for r in fresh_rows if r["date"] == str(stored["date"])[:10]), None)
    if not same_day or stored.get("adj_close") in (None, 0) or same_day["adj_close"] is None:
        return False
    return abs(same_day["adj_close"] / stored["adj_close"] - 1) > 1e-6


def collect_prices(db, provider, tickers: list[str], end: date, history_start: date) -> dict:
    """Fetch missing bars for each ticker up to `end`. Safe to re-run (upsert on ticker+date)."""
    summary = {"rows": 0, "refetched": [], "errors": {}, "no_bar_for_end_date": []}
    for ticker in tickers:
        try:
            last = db.select("prices_daily", {"ticker": ticker}, columns="date,adj_close",
                             order="date.desc", limit=1)
            # Start from the last stored day (one day overlap) to detect adjustment changes.
            start = date.fromisoformat(str(last[0]["date"])[:10]) if last else history_start
            if start > end:
                continue
            rows = provider.fetch(ticker, start, end)
            if last and _adjustment_changed(last[0], rows):
                rows = provider.fetch(ticker, history_start, end)
                summary["refetched"].append(ticker)
            if rows:
                db.upsert("prices_daily", rows, on_conflict="ticker,date")
                summary["rows"] += len(rows)
            if not any(r["date"] == end.isoformat() for r in rows) and start <= end:
                summary["no_bar_for_end_date"].append(ticker)
        except Exception as e:  # one bad ticker must not stop the others
            summary["errors"][ticker] = str(e)[:200]
    return summary


def history_start_from_config(cfg: dict) -> date:
    return date.fromisoformat(str(cfg.get("history_start", "2000-01-01")))


