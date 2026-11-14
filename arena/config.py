"""Loads YAML files from /config so settings never live in code."""
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@lru_cache(maxsize=None)
def load(name: str) -> dict:
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def stock_name(ticker: str) -> str:
    """Full company name for display text (e.g. 'WMT' -> 'Walmart')."""
    for s in load("universe")["stocks"]:
        if s["ticker"] == ticker:
            return s["name"]
    return ticker


def focus_tickers(team_cfg: dict) -> set[str]:
    """Stocks a team may buy, from its focus_sectors ('all' or missing = the whole universe)."""
    stocks = load("universe")["stocks"]
    sectors = team_cfg.get("focus_sectors", "all")
    if sectors in (None, "all") or sectors == ["all"]:
        return {s["ticker"] for s in stocks}
    return {s["ticker"] for s in stocks if s["sector"] in set(sectors)}


def universe_tickers(include_benchmark: bool = True) -> list[str]:
    u = load("universe")
    tickers = [s["ticker"] for s in u["stocks"]]
    if include_benchmark:
        tickers.append(u["benchmark"])
    return tickers
