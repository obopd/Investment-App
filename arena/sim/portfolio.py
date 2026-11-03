"""Portfolio state and order fills. Pure functions; the database layer is in executor.py.

Rules: long-only, whole shares, fill at the next trading day's open, slippage makes every
fill a little worse than the open, a fee is charged on every trade, buys larger than the
cash on hand are reduced.
"""
import math
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Position:
    shares: int
    avg_cost: float          # per share, including buy slippage and fees
    opened_at: date


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    peak_equity: float | None = None
    brake_active: bool = False

    def value(self, prices: dict[str, float]) -> float:
        return sum(p.shares * prices.get(t, p.avg_cost) for t, p in self.positions.items())

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.value(prices)


@dataclass
class Fill:
    ticker: str
    side: str
    shares: int
    fill_price: float        # open adjusted for slippage
    fees: float
    slippage: float          # total dollars lost to slippage
    reduced: bool = False    # True if the order was cut down to fit cash or holdings


def fill_order(side: str, ticker: str, shares: int, open_price: float, pf: Portfolio,
               fee_pct: float, slippage_pct: float) -> Fill | None:
    if open_price is None or open_price <= 0 or shares <= 0:
        return None
    if side == "buy":
        px = open_price * (1 + slippage_pct)
        affordable = math.floor(pf.cash / (px * (1 + fee_pct)))
        n = min(shares, max(affordable, 0))
    else:
        px = open_price * (1 - slippage_pct)
        held = pf.positions.get(ticker).shares if ticker in pf.positions else 0
        n = min(shares, held)
    if n <= 0:
        return None
    return Fill(ticker, side, n, px, n * px * fee_pct, n * open_price * slippage_pct, reduced=n < shares)


@dataclass
class ClosedLot:
    ticker: str
    shares: int
    entry_date: date
    entry_price: float
    exit_price: float        # net of slippage and sell fee, per share
    pnl: float
    pnl_pct: float


def apply_fill(pf: Portfolio, f: Fill, when: date) -> ClosedLot | None:
    """Update cash and positions. Every sell produces a closed lot for the shares sold."""
    if f.side == "buy":
        cost = f.shares * f.fill_price + f.fees
        pf.cash -= cost
        pos = pf.positions.get(f.ticker)
        if pos:
            total = pos.shares + f.shares
            pos.avg_cost = (pos.avg_cost * pos.shares + cost) / total
            pos.shares = total
        else:
            pf.positions[f.ticker] = Position(f.shares, cost / f.shares, when)
        return None
    pos = pf.positions[f.ticker]
    proceeds = f.shares * f.fill_price - f.fees
    pf.cash += proceeds
    basis = pos.avg_cost * f.shares
    lot = ClosedLot(f.ticker, f.shares, pos.opened_at, pos.avg_cost, proceeds / f.shares,
                    proceeds - basis, proceeds / basis - 1)
    pos.shares -= f.shares
    if pos.shares == 0:
        del pf.positions[f.ticker]
    return lot
