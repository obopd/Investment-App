import math

import pytest

from arena.metrics import max_drawdown, sharpe, total_return


def test_total_return_and_drawdown():
    eq = [100, 110, 99, 120, 90, 95]
    assert total_return(eq) == pytest.approx(-0.05)
    assert max_drawdown(eq) == pytest.approx(90 / 120 - 1)
    assert max_drawdown([100, 101, 102]) == 0


def test_sharpe_matches_formula_and_needs_enough_data():
    eq = [100, 101, 100.5, 102, 101.8, 103, 104]
    r = [b / a - 1 for a, b in zip(eq, eq[1:])]
    m = sum(r) / len(r)
    sd = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))
    assert sharpe(eq) == pytest.approx(m / sd * math.sqrt(252))
    assert sharpe([100, 101, 102]) is None
    assert sharpe([100] * 10) is None
