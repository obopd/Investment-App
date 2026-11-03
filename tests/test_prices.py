from datetime import date

from arena.data.prices import TiingoProvider, collect_prices
from arena.db.client import MemoryDB
from fakes import FakePriceProvider, FakeResponse, FakeSession


def bar(d, close, adj=None):
    return {"ticker": "AAPL", "date": d, "open": close, "high": close, "low": close,
            "close": close, "adj_close": adj if adj is not None else close, "volume": 100}


START = date(2000, 1, 1)


def test_first_run_loads_full_history_then_increments():
    db = MemoryDB()
    p = FakePriceProvider({"AAPL": [bar("2026-10-30", 10), bar("2026-11-02", 11)]})
    s = collect_prices(db, p, ["AAPL"], date(2026, 11, 2), START)
    assert s["rows"] == 2 and p.calls[0][1] == START

    p.bars["AAPL"].append(bar("2026-11-03", 12))
    collect_prices(db, p, ["AAPL"], date(2026, 11, 3), START)
    assert p.calls[-1][1] == date(2026, 11, 2)          # one-day overlap, not full history
    assert len(db.select("prices_daily")) == 3


def test_rerun_same_day_is_idempotent():
    db = MemoryDB()
    p = FakePriceProvider({"AAPL": [bar("2026-11-02", 11)]})
    for _ in range(3):
        collect_prices(db, p, ["AAPL"], date(2026, 11, 2), START)
    assert len(db.select("prices_daily")) == 1


def test_dividend_adjustment_triggers_full_refetch():
    db = MemoryDB()
    p = FakePriceProvider({"AAPL": [bar("2026-10-30", 10), bar("2026-11-02", 11)]})
    collect_prices(db, p, ["AAPL"], date(2026, 11, 2), START)
    # Provider now reports adjusted history scaled by a dividend.
    p.bars["AAPL"] = [bar("2026-10-30", 10, 9.9), bar("2026-11-02", 11, 10.89), bar("2026-11-03", 12)]
    s = collect_prices(db, p, ["AAPL"], date(2026, 11, 3), START)
    assert s["refetched"] == ["AAPL"]
    oct30 = db.select("prices_daily", {"date": "2026-10-30"})[0]
    assert oct30["adj_close"] == 9.9


def test_one_failing_ticker_does_not_stop_others():
    class Flaky(FakePriceProvider):
        def fetch(self, ticker, start, end):
            if ticker == "BAD":
                raise RuntimeError("HTTP 404")
            return super().fetch(ticker, start, end)
    db = MemoryDB()
    s = collect_prices(db, Flaky({"AAPL": [bar("2026-11-02", 11)]}), ["BAD", "AAPL"], date(2026, 11, 2), START)
    assert "BAD" in s["errors"] and s["rows"] == 1


def test_missing_bar_for_run_date_is_reported():
    db = MemoryDB()
    s = collect_prices(db, FakePriceProvider({"AAPL": [bar("2026-10-30", 10)]}), ["AAPL"], date(2026, 11, 2), START)
    assert s["no_bar_for_end_date"] == ["AAPL"]


def test_tiingo_parsing_and_token_kept_out_of_url():
    seen = {}

    def handler(url, params):
        seen["url"], seen["params"] = url, params
        return FakeResponse(200, '[{"date":"2026-11-02T00:00:00.000Z","open":1,"high":2,"low":0.5,'
                                 '"close":1.5,"adjClose":1.4,"volume":1000.0}]')
    sess = FakeSession({"https://api.tiingo.com/": handler})
    rows = TiingoProvider("SECRET", sess, delay=0).fetch("AAPL", date(2026, 11, 2), date(2026, 11, 2))
    assert rows == [{"ticker": "AAPL", "date": "2026-11-02", "open": 1, "high": 2, "low": 0.5,
                     "close": 1.5, "adj_close": 1.4, "volume": 1000}]
    assert "SECRET" not in seen["url"] and "SECRET" not in str(seen["params"])
