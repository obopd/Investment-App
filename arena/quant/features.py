"""Features and targets from daily prices, computed by code.

All features at date D use only prices up to and including D. The target is the stock's log
return minus SPY's over the next `horizon` trading days, so it is only known `horizon` days later.
"""
import warnings

import numpy as np

# Early dates and missing tickers are all-NaN slices; that's expected, not worth a warning.
warnings.filterwarnings("ignore", message="Mean of empty slice")
warnings.filterwarnings("ignore", message="Degrees of freedom <= 0")
warnings.filterwarnings("ignore", category=RuntimeWarning, module=__name__)

FEATURES = ["mom_5", "mom_20", "mom_60", "mom_120", "vol_20", "vol_60", "volume_chg",
            "drawdown_60", "vs_ma20", "vs_ma50", "vs_ma200", "sector_rel_20", "vs_spy_20"]


def _shift(a: np.ndarray, n: int) -> np.ndarray:
    """Value n rows earlier (n > 0) or later (n < 0), NaN where unavailable."""
    out = np.full_like(a, np.nan)
    if n > 0:
        out[n:] = a[:-n]
    elif n < 0:
        out[:n] = a[-n:]
    else:
        out[:] = a
    return out


def _rolling(a: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full_like(a, np.nan)
    for i in range(n - 1, len(a)):
        w = a[i - n + 1:i + 1]
        out[i] = fn(w, axis=0)
    return out


def build_panel(rows: list[dict], tickers: list[str], benchmark: str):
    """rows: price rows (ticker, date, adj_close/close, volume). Returns dates and 2-D arrays
    [date, ticker] aligned on the benchmark's trading days."""
    dates = sorted({str(r["date"])[:10] for r in rows if r["ticker"] == benchmark})
    di = {d: i for i, d in enumerate(dates)}
    cols = tickers + [benchmark]
    ci = {t: j for j, t in enumerate(cols)}
    px = np.full((len(dates), len(cols)), np.nan)
    vol = np.full((len(dates), len(cols)), np.nan)
    for r in rows:
        d, t = str(r["date"])[:10], r["ticker"]
        if d in di and t in ci:
            p = r.get("adj_close") or r.get("close")
            px[di[d], ci[t]] = p if p else np.nan
            vol[di[d], ci[t]] = r.get("volume") or np.nan
    return dates, px, vol


def compute(px: np.ndarray, vol: np.ndarray, sectors: list[str], horizon: int = 20):
    """px/vol: [date, ticker+benchmark] with the benchmark in the last column.
    Returns X [date, ticker, feature] and y [date, ticker] (NaN where unknown)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        logp = np.log(px)
        stocks, spy = logp[:, :-1], logp[:, -1:]
        ret1 = stocks - _shift(stocks, 1)
        feats = {
            "mom_5": stocks - _shift(stocks, 5),
            "mom_20": stocks - _shift(stocks, 20),
            "mom_60": stocks - _shift(stocks, 60),
            "mom_120": stocks - _shift(stocks, 120),
            "vol_20": _rolling(ret1, 20, np.std),
            "vol_60": _rolling(ret1, 60, np.std),
            "volume_chg": np.log(_rolling(vol[:, :-1], 5, np.mean) / _rolling(vol[:, :-1], 20, np.mean)),
            "drawdown_60": stocks - _rolling(stocks, 60, np.max),
            "vs_ma20": stocks - np.log(_rolling(px[:, :-1], 20, np.mean)),
            "vs_ma50": stocks - np.log(_rolling(px[:, :-1], 50, np.mean)),
            "vs_ma200": stocks - np.log(_rolling(px[:, :-1], 200, np.mean)),
        }
        mom20 = feats["mom_20"]
        sector_rel = np.full_like(mom20, np.nan)
        for s in set(sectors):
            idx = [j for j, x in enumerate(sectors) if x == s]
            sector_rel[:, idx] = mom20[:, idx] - np.nanmean(mom20[:, idx], axis=1, keepdims=True) if len(idx) > 1 else 0.0
        feats["sector_rel_20"] = sector_rel
        feats["vs_spy_20"] = mom20 - (spy - _shift(spy, 20))
        X = np.stack([feats[f] for f in FEATURES], axis=2)
        fwd = _shift(stocks, -horizon) - stocks
        y = fwd - (_shift(spy, -horizon) - spy)
    return X, y


def cross_sectional_z(X: np.ndarray) -> np.ndarray:
    """Standardise each feature across stocks on each date (removes market-wide level shifts)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.nanmean(X, axis=1, keepdims=True)
        s = np.nanstd(X, axis=1, keepdims=True)
        Z = (X - m) / np.where(s > 0, s, np.nan)
    return np.clip(Z, -4, 4)
