"""Risk rules (config/risk.yaml). Code, not AI, and they can veto or resize any AI decision."""
import math
from dataclasses import asdict, dataclass

from arena.sim.portfolio import Portfolio


@dataclass
class RiskResult:
    status: str              # approved | resized | vetoed | hold
    side: str | None         # buy | sell | None
    shares: int
    reason: str
    target_weight: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def update_brake(pf: Portfolio, equity: float, cfg: dict) -> bool:
    """Drawdown brake with hysteresis: on beyond drawdown_brake_pct, off once recovered to
    within drawdown_release_pct of the peak."""
    pf.peak_equity = max(pf.peak_equity or equity, equity)
    dd = 1 - equity / pf.peak_equity
    if dd >= cfg["drawdown_brake_pct"]:
        pf.brake_active = True
    elif dd <= cfg["drawdown_release_pct"]:
        pf.brake_active = False
    return pf.brake_active


def stop_loss_sells(pf: Portfolio, closes: dict[str, float], cfg: dict) -> list[tuple[str, int, str]]:
    out = []
    for t, p in pf.positions.items():
        px = closes.get(t)
        if px is not None and px / p.avg_cost - 1 <= cfg["stop_loss_pct"]:
            out.append((t, p.shares, f"stop-loss: {px / p.avg_cost - 1:.1%} from entry"))
    return out


def evaluate(verdict: dict, pf: Portfolio, closes: dict[str, float], cfg: dict,
             min_conviction: float, new_positions_today: int) -> RiskResult:
    """Turn a judge verdict into a sized order, or veto it. Sizes use the latest close as the
    price estimate; the fill at tomorrow's open may differ slightly."""
    t, action = verdict["ticker"], verdict["action"]
    px = closes.get(t)
    if action == "hold":
        return RiskResult("hold", None, 0, "judge chose hold")
    if verdict["conviction"] < min_conviction:
        return RiskResult("hold", None, 0,
                          f"conviction {verdict['conviction']:.2f} below team minimum {min_conviction:.2f}")
    if not px:
        return RiskResult("vetoed", None, 0, f"no recent price for {t}")

    equity = pf.equity(closes)
    held = pf.positions.get(t)
    held_shares = held.shares if held else 0
    target_w = max(0.0, min(float(verdict["target_weight"]), 1.0))
    notes = []

    if action == "sell":
        if not held_shares:
            return RiskResult("vetoed", None, 0, f"no {t} position to sell (long-only, no shorting)")
        keep = math.floor(target_w * equity / px) if target_w > 0 else 0
        n = held_shares - min(keep, held_shares)
        if n <= 0:
            return RiskResult("hold", None, 0, "already at or below target weight")
        return RiskResult("approved", "sell", n, f"sell {n} of {held_shares} shares", target_w)

    # buy / add
    if pf.brake_active:
        return RiskResult("vetoed", None, 0, "drawdown brake on: only reducing positions allowed")
    if not held_shares:
        if len(pf.positions) >= cfg["max_positions"]:
            return RiskResult("vetoed", None, 0, f"already at max {cfg['max_positions']} positions")
        if new_positions_today >= cfg["max_new_positions_per_day"]:
            return RiskResult("vetoed", None, 0, f"max {cfg['max_new_positions_per_day']} new positions per day")
    if target_w > cfg["max_weight_per_stock"]:
        notes.append(f"weight {target_w:.0%} capped at {cfg['max_weight_per_stock']:.0%}")
        target_w = cfg["max_weight_per_stock"]
    want = math.floor(target_w * equity / px) - held_shares
    if want <= 0:
        return RiskResult("hold", None, 0, "already at or above target weight")
    est_cost = px * (1 + cfg["slippage_pct"]) * (1 + cfg["fee_pct"])
    spendable = pf.cash - cfg["min_cash_buffer"] * equity
    affordable = math.floor(max(spendable, 0) / est_cost)
    n = min(want, affordable)
    if n <= 0:
        return RiskResult("vetoed", None, 0, f"cash buffer: keep {cfg['min_cash_buffer']:.0%} of equity in cash")
    if n < want:
        notes.append(f"reduced from {want} to {n} shares to keep the cash buffer")
    status = "resized" if notes else "approved"
    return RiskResult(status, "buy", n, "; ".join(notes) or f"buy {n} shares", target_w)
