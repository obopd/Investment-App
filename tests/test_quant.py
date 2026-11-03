import json
from datetime import date, timedelta

import numpy as np
import pytest

from arena import config
from arena.db.client import MemoryDB
from arena.quant import model as qmodel
from arena.quant.features import FEATURES, build_panel, compute
from arena.quant.score import quant_scores

CFG = {**config.load("quant"), "walk_forward": {"first_test_year": 2003, "test_months": 1, "purge_days": 20}}
N_STOCKS = 12


def synthetic(days=1600, signal=0.0, seed=0):
    """Random-walk prices. With signal > 0, the next 20 days' excess return follows the past
    20-day move (momentum), so a model should find skill; with 0 it should find none."""
    rng = np.random.default_rng(seed)
    start = date(2000, 1, 3)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    rets = rng.normal(0, 0.01, (days, N_STOCKS + 1))
    if signal:
        for t in range(40, days):
            past = rets[t - 20:t, :-1].sum(axis=0)
            rets[t, :-1] += signal * (past - past.mean()) / 20
    px = 100 * np.exp(np.cumsum(rets, axis=0))
    vol = rng.uniform(1e6, 2e6, (days, N_STOCKS + 1))
    return dates, px, vol, ["S" + str(i % 3) for i in range(N_STOCKS)]


def test_features_never_use_future_prices():
    dates, px, vol, sectors = synthetic(400)
    X1, y1 = compute(px, vol, sectors)
    px2 = px.copy()
    px2[300:] *= 3.0                                   # change only the future
    X2, _ = compute(px2, vol, sectors)
    np.testing.assert_array_equal(np.nan_to_num(X1[:300]), np.nan_to_num(X2[:300]))
    assert np.isnan(y1[-20:]).all() and not np.isnan(y1[250]).all()   # targets need 20 future days
    assert X1.shape[2] == len(FEATURES)


def test_walk_forward_never_trains_on_the_test_period(monkeypatch):
    dates, px, vol, sectors = synthetic(1200)
    X, y = compute(px, vol, sectors)
    seen = []
    real_fit = qmodel.fit_ridge
    real_flatten = qmodel._flatten

    def spy_flatten(Z, y_, rows):
        seen.append(rows.stop)
        return real_flatten(Z, y_, rows)
    monkeypatch.setattr(qmodel, "_flatten", spy_flatten)
    monkeypatch.setattr(qmodel, "fit_ridge", real_fit)
    qmodel.walk_forward(dates, X, y, CFG)
    months = sorted({d[:7] for d in dates if int(d[:4]) >= 2003})
    starts = {m: next(i for i, d in enumerate(dates) if d[:7] == m) for m in months}
    assert seen
    for stop in seen:
        # each training slice ends at least `purge` rows before some test month's first day
        assert any(stop == s - CFG["walk_forward"]["purge_days"] for s in starts.values())


def test_model_finds_real_signal_and_not_fake_signal():
    d, px, vol, sec = synthetic(2200, signal=0.6, seed=1)
    X, y = compute(px, vol, sec)
    good = qmodel.walk_forward(d, X, y, CFG)
    d, px, vol, sec = synthetic(2200, signal=0.0, seed=2)
    X, y = compute(px, vol, sec)
    noise = qmodel.walk_forward(d, X, y, CFG)
    # Judge by t-statistics: with ~12 stocks a single month's IC is very noisy.
    assert good["ic_monthly"]["mean"] > 0.1 and good["ic_monthly"]["t"] > 3
    assert abs(noise["ic_monthly"]["t"]) < 3
    assert good["months_tested"] > 30


def test_daily_scores_use_only_data_up_to_the_decision_day(tmp_path, monkeypatch):
    dates, px, vol, sectors = synthetic(500)
    X, y = compute(px, vol, sectors)
    m = qmodel.train_final(dates, X, y, CFG)
    (tmp_path / "m.json").write_text(json.dumps(m))
    monkeypatch.setattr("arena.quant.score.ROOT", tmp_path)
    tickers = [f"T{i}" for i in range(N_STOCKS)]
    db = MemoryDB()
    db.insert("prices_daily", [{"ticker": (tickers + ["SPY"])[j], "date": dates[i], "close": px[i, j],
                                "adj_close": px[i, j], "volume": vol[i, j]}
                               for i in range(len(dates)) for j in range(N_STOCKS + 1)])
    run = date.fromisoformat(dates[450])
    q = quant_scores(db, run, tickers, sectors, "SPY", {**CFG, "model_path": "m.json"})
    assert q["as_of"] == dates[450] and q["n"] == N_STOCKS
    assert sorted(s["rank"] for s in q["scores"].values()) == list(range(1, N_STOCKS + 1))
    assert quant_scores(db, run, tickers, sectors, "SPY", {**CFG, "model_path": "missing.json"}) is None


def test_build_panel_aligns_on_benchmark_days():
    rows = [{"ticker": "SPY", "date": "2026-01-02", "adj_close": 1}, {"ticker": "SPY", "date": "2026-01-05", "adj_close": 2},
            {"ticker": "A", "date": "2026-01-05", "adj_close": 5}, {"ticker": "A", "date": "2026-01-03", "adj_close": 9}]
    dates, px, _ = build_panel(rows, ["A"], "SPY")
    assert dates == ["2026-01-02", "2026-01-05"]
    assert np.isnan(px[0, 0]) and px[1, 0] == 5 and px[1, 1] == 2
