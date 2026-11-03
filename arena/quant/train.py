"""Monthly retraining: walk-forward evaluation, then fit the model the pipeline uses.

    python -m arena.quant.train            # reads prices from Supabase (service key)

Writes models/quant_model.json and docs/quant_report.md. The GitHub workflow commits them.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from arena import config
from arena.config import ROOT
from arena.quant.features import FEATURES, build_panel, compute
from arena.quant.model import train_final, walk_forward


def load_prices(db, tickers: list[str]) -> list[dict]:
    rows = []
    for t in tickers:   # one ticker at a time keeps each paged query small and ordered
        rows += db.select("prices_daily", {"ticker": t}, columns="ticker,date,close,adj_close,volume", order="date.asc")
    return rows


def _f(x, pct=False, digits=3):
    if x is None:
        return "n/a"
    return f"{x * 100:+.2f}%" if pct else f"{x:+.{digits}f}"


def report(ev: dict, model: dict, cfg: dict) -> str:
    ic, base = ev["ic_monthly"]["mean"], ev["baseline_momentum_ic"]
    t = ev["ic_monthly"]["t"]
    beats_zero = ic is not None and t is not None and ic > 0 and t > 2
    beats_mom = ic is not None and base is not None and ic > base
    pos_months = "n/a" if ev["positive_months"] is None else f"{ev['positive_months']:.0%}"
    if beats_zero and beats_mom:
        verdict = "The model's ranking signal is positive and statistically meaningful, and it beats the simple momentum baseline."
    elif beats_zero:
        verdict = "The model has a positive, statistically meaningful signal, but it does NOT beat the simple momentum baseline."
    elif ic is not None and ic > 0:
        verdict = "The model's signal is positive but too weak to be distinguished from luck (t-stat below 2). Treat its scores as a weak hint at most."
    else:
        verdict = "The model does NOT beat a naive baseline. Its scores should carry little or no weight."
    lines = [
        "# Quant model report", "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by the monthly retraining workflow._", "",
        "## Honest summary", "", verdict, "",
        "What it predicts: each stock's return minus SPY's over the next "
        f"{cfg['horizon_days']} trading days, from price history only. It is one input for the Analyst and never trades by itself.", "",
        "## Walk-forward results", "",
        f"Tested {ev['months_tested']} months ({ev['first_month']} to {ev['last_month']}). Each month the model was trained only on data "
        f"whose outcome was known before that month (with a {cfg['walk_forward']['purge_days']}-day gap), then tested on the month.", "",
        "| Measure | Model | Naive baseline |", "|---|---|---|",
        f"| Rank correlation with outcome (IC), monthly average | {_f(ic)} | 0 (no skill) |",
        f"| IC t-statistic (monthly) | {_f(t, digits=2)} | – |",
        f"| Daily IC: model vs. simple 60-day momentum ranking | {_f(ev['ic_daily_mean'])} | {_f(base)} |",
        f"| Months with positive IC | {pos_months} | 50% |",
        f"| Top fifth minus bottom fifth, 20-day excess return | {_f(ev['top_minus_bottom_monthly']['mean'], pct=True)} | 0% |", "",
        "IC is the rank correlation between predicted and actual excess returns across the ~30 stocks; 0.02-0.05 is typical for useful "
        "real-world signals, and with only 30 stocks the numbers are noisy.", "",
        "## IC by year", "", "| Year | IC |", "|---|---|",
        *[f"| {y} | {_f(v)} |" for y, v in ev["by_year"].items()], "",
        "## Current model", "",
        f"Trained through {model['trained_through']} on {model['samples']:,} samples (ridge, lambda {model['ridge_lambda']}).", "",
        "| Feature | Weight |", "|---|---|",
        *[f"| {f} | {w:+.5f} |" for f, w in zip(FEATURES, model["weights"])], "",
        "## Limits", "",
        "- Past relationships between price patterns and future returns are weak and change over time.",
        "- Only ~30 large stocks: small sample, results are noisy.",
        "- Survivorship bias: today's large companies were chosen with hindsight, which flatters any backtest.",
        "- No transaction costs are included in these numbers.", "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Retrain the quant model")
    p.parse_args(argv)
    from arena.db.client import SupabaseDB
    cfg = config.load("quant")
    u = config.load("universe")
    tickers = [s["ticker"] for s in u["stocks"]]
    sectors = [s["sector"] for s in u["stocks"]]
    rows = load_prices(SupabaseDB.from_env(), tickers + [u["benchmark"]])
    dates, px, vol = build_panel(rows, tickers, u["benchmark"])
    X, y = compute(px, vol, sectors, cfg["horizon_days"])
    ev = walk_forward(dates, X, y, cfg)
    model = train_final(dates, X, y, cfg)
    model["evaluation"] = {k: ev[k] for k in ("ic_monthly", "baseline_momentum_ic", "months_tested", "positive_months")}
    (ROOT / cfg["model_path"]).parent.mkdir(parents=True, exist_ok=True)
    (ROOT / cfg["model_path"]).write_text(json.dumps(model, indent=1))
    (ROOT / cfg["report_path"]).write_text(report(ev, model, cfg))
    print(json.dumps(model["evaluation"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
