import pandas as pd
import pytest

from sharpe_replication.metrics import max_drawdown, paper_scale


def test_max_drawdown_includes_initial_wealth_before_first_return():
    assert max_drawdown(pd.Series([-0.10])) == pytest.approx(-0.10)


def test_max_drawdown_keeps_initial_loss_after_partial_recovery():
    assert max_drawdown(pd.Series([-0.10, 0.05])) == pytest.approx(-0.10)


def test_paper_scale_allows_leverage_above_one():
    returns = pd.Series([0.001, -0.001] * 20)
    assert paper_scale(returns, vol_target=0.12, max_dd_target=0.15) > 1.0
