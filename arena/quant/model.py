"""Ridge regression on cross-sectionally standardised features, evaluated walk-forward.

Walk-forward only: for each test month, train on samples whose 20-day target was fully known
before the month started (purged), then predict the month. Time-series data is never shuffled.
"""
import math
from datetime import date

import numpy as np

from arena.quant.features import FEATURES, cross_sectional_z


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> tuple[np.ndarray, float]:
    """X: [n, k] standardised; returns weights and intercept."""
    mu = y.mean()
    A = X.T @ X + lam * np.eye(X.shape[1])
    w = np.linalg.solve(A, X.T @ (y - mu))
    return w, float(mu)


def _flatten(Z, y, rows):
    Xs, ys = Z[rows].reshape(-1, Z.shape[2]), y[rows].reshape(-1)
    ok = ~np.isnan(Xs).any(axis=1) & ~np.isnan(ys)
    return Xs[ok], ys[ok]


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < 8:
        return None
    ra = np.argsort(np.argsort(a[ok])).astype(float)
    rb = np.argsort(np.argsort(b[ok])).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def walk_forward(dates: list[str], X: np.ndarray, y: np.ndarray, cfg: dict) -> dict:
    Z = cross_sectional_z(X)
    lam, purge = cfg["ridge_lambda"], cfg["walk_forward"]["purge_days"]
    first_year = cfg["walk_forward"]["first_test_year"]
    months = sorted({d[:7] for d in dates if int(d[:4]) >= first_year})
    mom_idx = FEATURES.index("mom_60")
    ic_model, ic_mom, spreads, monthly = [], [], [], []
    last_known = max(i for i in range(len(dates)) if not np.all(np.isnan(y[i])))
    for m in months:
        test = [i for i, d in enumerate(dates) if d[:7] == m and i <= last_known]
        if not test:
            continue
        train_end = test[0] - purge          # targets of these rows end before the test month
        if train_end < 250:
            continue
        Xtr, ytr = _flatten(Z, y, slice(0, train_end))
        if len(ytr) < 2000:
            continue
        w, b = fit_ridge(Xtr, ytr, lam)
        month_ics = []
        for i in test:
            pred = np.where(np.isnan(Z[i]).any(axis=1), np.nan, Z[i] @ w + b)
            ic = spearman(pred, y[i])
            ic_b = spearman(Z[i][:, mom_idx], y[i])
            if ic is not None:
                ic_model.append(ic)
                month_ics.append(ic)
            if ic_b is not None:
                ic_mom.append(ic_b)
        # Top-minus-bottom fifth, sampled once per month so 20-day returns don't overlap.
        i = test[0]
        pred = np.where(np.isnan(Z[i]).any(axis=1), np.nan, Z[i] @ w + b)
        ok = ~np.isnan(pred) & ~np.isnan(y[i])
        if ok.sum() >= 10:
            order = np.argsort(pred[ok])
            q = max(len(order) // 5, 1)
            spreads.append(float(y[i][ok][order[-q:]].mean() - y[i][ok][order[:q]].mean()))
        if month_ics:
            monthly.append((m, float(np.mean(month_ics))))

    def stats(xs):
        if not xs:
            return {"mean": None, "t": None, "n": 0}
        a = np.array(xs)
        return {"mean": float(a.mean()), "n": len(a),
                "t": float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))) if len(a) > 2 and a.std() > 0 else None}

    # Daily ICs overlap (20-day targets), so the honest t-stat uses monthly averages.
    mic = [x for _, x in monthly]
    return {"ic_daily_mean": stats(ic_model)["mean"], "ic_monthly": stats(mic),
            "baseline_momentum_ic": stats(ic_mom)["mean"], "baseline_zero_ic": 0.0,
            "top_minus_bottom_monthly": stats(spreads),
            "positive_months": (sum(1 for x in mic if x > 0) / len(mic)) if mic else None,
            "months_tested": len(monthly), "first_month": monthly[0][0] if monthly else None,
            "last_month": monthly[-1][0] if monthly else None,
            "by_year": _by_year(monthly)}


def _by_year(monthly):
    years = {}
    for m, ic in monthly:
        years.setdefault(m[:4], []).append(ic)
    return {y: round(float(np.mean(v)), 4) for y, v in sorted(years.items())}


def train_final(dates: list[str], X: np.ndarray, y: np.ndarray, cfg: dict) -> dict:
    """Fit on everything whose target is known; this is the model the pipeline uses."""
    Z = cross_sectional_z(X)
    Xtr, ytr = _flatten(Z, y, slice(0, len(dates)))
    w, b = fit_ridge(Xtr, ytr, cfg["ridge_lambda"])
    known = [d for i, d in enumerate(dates) if not np.all(np.isnan(y[i]))]
    return {"features": FEATURES, "weights": [float(x) for x in w], "intercept": b,
            "ridge_lambda": cfg["ridge_lambda"], "horizon_days": cfg["horizon_days"],
            "trained_through": known[-1] if known else None, "samples": int(len(ytr)),
            "trained_on": date.today().isoformat()}


def score(model: dict, X_today: np.ndarray) -> np.ndarray:
    """X_today: [ticker, feature] for one date. Returns predicted 20-day excess log return."""
    Z = cross_sectional_z(X_today[None, :, :])[0]
    w = np.array(model["weights"])
    return np.where(np.isnan(Z).any(axis=1), np.nan, Z @ w + model["intercept"])
