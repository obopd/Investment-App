"""Daily quant scores for the Analyst, from the model the monthly workflow trained.
Uses only prices up to the decision day."""
import json
from datetime import date, timedelta

import numpy as np

from arena.config import ROOT
from arena.quant.features import build_panel, compute
from arena.quant.model import score


def load_model(cfg: dict) -> dict | None:
    path = ROOT / cfg["model_path"]
    return json.loads(path.read_text()) if path.exists() else None


def quant_scores(db, run_date: date, stocks: list[str], sectors: list[str], benchmark: str,
                 cfg: dict) -> dict | None:
    model = load_model(cfg)
    if not model:
        return None
    rows = db.select("prices_daily", {"date": ("gte", run_date - timedelta(days=420)),
                                      "ticker": ("in", stocks + [benchmark])},
                     columns="ticker,date,close,adj_close,volume", order="date.asc,ticker.asc")
    rows = [r for r in rows if str(r["date"])[:10] <= run_date.isoformat()]   # no look-ahead
    dates, px, vol = build_panel(rows, stocks, benchmark)
    if len(dates) < 210:
        return None   # needs ~200 trading days for the longest moving average
    X, _ = compute(px, vol, sectors, model["horizon_days"])
    pred = score(model, X[-1])
    order = [i for i in np.argsort(-np.nan_to_num(pred, nan=-np.inf)) if not np.isnan(pred[i])]
    out = {stocks[i]: {"pred": float(pred[i]), "rank": r + 1} for r, i in enumerate(order)}
    return {"as_of": dates[-1], "scores": out, "n": len(order),
            "evaluation": model.get("evaluation", {}), "trained_through": model.get("trained_through")}
