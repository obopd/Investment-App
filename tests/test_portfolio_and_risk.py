from datetime import date

import pytest

from arena import config
from arena.risk.engine import evaluate, stop_loss_sells, update_brake
from arena.sim.portfolio import Portfolio, Position, apply_fill, fill_order

CFG = dict(config.load("risk"))
D = date(2026, 11, 3)


def test_buy_fill_applies_slippage_and_fee():
    pf = Portfolio(cash=10_000)
    f = fill_order("buy", "AAPL", 10, 100.0, pf, fee_pct=0.0005, slippage_pct=0.0005)
    assert f.fill_price == pytest.approx(100.05)
    assert f.fees == pytest.approx(10 * 100.05 * 0.0005)
    assert f.slippage == pytest.approx(0.5)
    apply_fill(pf, f, D)
    assert pf.cash == pytest.approx(10_000 - 1000.5 - f.fees)
    assert pf.positions["AAPL"].avg_cost == pytest.approx((1000.5 + f.fees) / 10)  # fees in cost basis


def test_buy_larger_than_cash_is_reduced_to_whole_shares():
    pf = Portfolio(cash=1_000)
    f = fill_order("buy", "AAPL", 50, 100.0, pf, 0.0005, 0.0005)
    assert f.shares == 9 and f.reduced
    assert fill_order("buy", "AAPL", 5, 2_000.0, pf, 0.0005, 0.0005) is None


def test_sell_capped_at_holdings_and_closed_lot_pnl():
    pf = Portfolio(cash=0, positions={"AAPL": Position(10, 100.0, date(2026, 10, 1))})
    f = fill_order("sell", "AAPL", 25, 110.0, pf, 0.0005, 0.0005)
    assert f.shares == 10 and f.fill_price == pytest.approx(109.945)
    lot = apply_fill(pf, f, D)
    proceeds = 10 * 109.945 - f.fees
    assert pf.cash == pytest.approx(proceeds) and "AAPL" not in pf.positions
    assert lot.pnl == pytest.approx(proceeds - 1000) and lot.entry_date == date(2026, 10, 1)
    assert fill_order("sell", "MSFT", 5, 100.0, pf, 0.0005, 0.0005) is None   # no shorting


def verdict(action="buy", conviction=0.8, w=0.10, t="AAPL"):
    return {"ticker": t, "action": action, "conviction": conviction, "target_weight": w}


def test_weight_is_capped_and_sized_from_equity():
    pf = Portfolio(cash=100_000)
    r = evaluate(verdict(w=0.5), pf, {"AAPL": 100.0}, CFG, 0.6, 0)
    assert r.status == "resized" and r.shares == 150      # 15% of $100k at $100


def test_low_conviction_becomes_hold():
    r = evaluate(verdict(conviction=0.4), Portfolio(cash=100_000), {"AAPL": 100.0}, CFG, 0.6, 0)
    assert r.status == "hold" and r.side is None


def test_max_positions_and_new_positions_per_day():
    full = Portfolio(cash=100_000, positions={f"T{i}": Position(1, 10, D) for i in range(CFG["max_positions"])})
    assert evaluate(verdict(), full, {"AAPL": 100.0}, CFG, 0.6, 0).status == "vetoed"
    r = evaluate(verdict(), Portfolio(cash=100_000), {"AAPL": 100.0}, CFG, 0.6, CFG["max_new_positions_per_day"])
    assert r.status == "vetoed" and "new positions" in r.reason


def test_cash_buffer_resizes_buy():
    # $10k cash in $100k equity; buffer 5% = $5k -> only ~$5k can be spent.
    pf = Portfolio(cash=10_000, positions={"MSFT": Position(900, 100.0, D)})
    r = evaluate(verdict(w=0.15), pf, {"AAPL": 100.0, "MSFT": 100.0}, CFG, 0.6, 0)
    assert r.status == "resized" and r.shares == 49


def test_sell_without_position_is_vetoed_and_sell_to_target_weight():
    assert evaluate(verdict("sell", w=0), Portfolio(cash=1000), {"AAPL": 100.0}, CFG, 0.6, 0).status == "vetoed"
    pf = Portfolio(cash=90_000, positions={"AAPL": Position(100, 100.0, D)})
    r = evaluate(verdict("sell", w=0.05), pf, {"AAPL": 100.0}, CFG, 0.6, 0)
    assert r.side == "sell" and r.shares == 50


def test_drawdown_brake_blocks_buys_allows_sells_and_releases():
    pf = Portfolio(cash=0, positions={"AAPL": Position(100, 100.0, D)}, peak_equity=10_000)
    assert update_brake(pf, 8_400, CFG) is True                         # -16%
    assert evaluate(verdict(), pf, {"AAPL": 84.0}, CFG, 0.6, 0).status == "vetoed"
    assert evaluate(verdict("sell", w=0), pf, {"AAPL": 84.0}, CFG, 0.6, 0).side == "sell"
    assert update_brake(pf, 8_800, CFG) is True                         # -12%: still on (hysteresis)
    assert update_brake(pf, 9_100, CFG) is False                        # -9%: released


def test_stop_loss_triggers_at_threshold():
    pf = Portfolio(cash=0, positions={"AAPL": Position(10, 100.0, D), "MSFT": Position(5, 100.0, D)})
    sells = stop_loss_sells(pf, {"AAPL": 87.9, "MSFT": 90.0}, CFG)
    assert [(t, n) for t, n, _ in sells] == [("AAPL", 10)]
