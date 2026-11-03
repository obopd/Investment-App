"""Performance metrics, computed by code from daily equity."""
import math

TRADING_DAYS = 252


def daily_returns(equity: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(equity, equity[1:]) if a]


def total_return(equity: list[float]) -> float | None:
    return equity[-1] / equity[0] - 1 if len(equity) >= 1 and equity[0] else None


def sharpe(equity: list[float]) -> float | None:
    """Annualized, risk-free rate taken as 0. None until there are enough days to mean anything."""
    r = daily_returns(equity)
    if len(r) < 5:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    if var <= 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough fall, as a negative fraction (e.g. -0.12)."""
    peak, worst = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return worst
