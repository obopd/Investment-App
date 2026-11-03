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


def universe_tickers(include_benchmark: bool = True) -> list[str]:
    u = load("universe")
    tickers = [s["ticker"] for s in u["stocks"]]
    if include_benchmark:
        tickers.append(u["benchmark"])
    return tickers
