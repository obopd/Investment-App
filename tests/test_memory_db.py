from arena.db.client import MemoryDB


def test_upsert_merges_or_ignores_duplicates():
    db = MemoryDB()
    db.upsert("prices_daily", [{"ticker": "A", "date": "2026-11-02", "close": 1}], "ticker,date")
    db.upsert("prices_daily", [{"ticker": "A", "date": "2026-11-02", "close": 2}], "ticker,date")
    assert [r["close"] for r in db.select("prices_daily")] == [2]

    new = db.upsert("articles", [{"url": "u1"}, {"url": "u2"}], "url", ignore_duplicates=True, returning=True)
    again = db.upsert("articles", [{"url": "u1"}, {"url": "u3"}], "url", ignore_duplicates=True, returning=True)
    assert len(new) == 2 and [r["url"] for r in again] == ["u3"]


def test_select_filters_order_limit_columns():
    db = MemoryDB()
    db.insert("t", [{"k": "a", "n": 1}, {"k": "a", "n": 3}, {"k": "b", "n": 2}])
    assert db.select("t", {"k": "a"}, columns="n", order="n.desc", limit=1) == [{"n": 3}]
    assert [r["n"] for r in db.select("t", {"n": ("gte", 2)}, order="n.asc")] == [2, 3]
