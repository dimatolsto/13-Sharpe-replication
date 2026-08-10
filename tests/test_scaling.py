from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from sharpe_replication.backtest import run_backtest
from sharpe_replication.config import (
    ClosedDateWindow,
    ExperimentConfig,
    ScalingConfig,
    ScalingWindow,
    StrategyConfig,
)


def _scaling_panel(test_multiplier: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-01", "2020-03-31")
    rows = []
    securities = ["A", "B", "C", "D", "E"]
    for i, sid in enumerate(securities):
        price = 12.0 + i * 7.0
        for j, d in enumerate(dates):
            ret = (0.001 + i * 0.00008) if (j + i) % 4 else -(0.003 + i * 0.00005)
            if d >= pd.Timestamp("2020-01-01"):
                ret *= test_multiplier
            price *= 1.0 + ret
            rows.append(
                {
                    "date": d,
                    "security_id": sid,
                    "ticker": sid,
                    "raw_close": price,
                    "total_return": ret,
                }
            )
    return pd.DataFrame(rows)


def _paper_scaled_experiment(paper_scaling: bool = True) -> ExperimentConfig:
    scaling = (
        ScalingConfig(
            mode="paper_walk_forward",
            windows=[
                ScalingWindow(
                    train_start=date(2019, 6, 1),
                    train_end=date(2020, 1, 1),
                    test_start=date(2020, 1, 1),
                    test_end=date(2020, 3, 1),
                )
            ],
        )
        if paper_scaling
        else ScalingConfig(mode="none")
    )
    return ExperimentConfig(
        id="PTEST",
        name="scaled",
        universe_mode="current_constituents",
        period_mode="paper_three_windows",
        paper_test_windows=[ClosedDateWindow(start=date(2020, 1, 1), end=date(2020, 2, 29))],
        return_lag_sessions=2,
        raw_signal_price_required=True,
        point_in_time_membership_required=False,
        transaction_cost_bp=0.6,
        paper_scaling=paper_scaling,
        scaling=scaling,
        certified=False,
    )


def test_paper_scaling_uses_training_returns_only_and_preserves_unscaled_results():
    strategy = StrategyConfig(drift_window=10, reversal_lookback=3, up_fraction_threshold=0.5)
    scaled_exp = _paper_scaled_experiment(paper_scaling=True)
    unscaled_exp = _paper_scaled_experiment(paper_scaling=False)

    result = run_backtest(_scaling_panel(test_multiplier=1.0), strategy, scaled_exp)
    modified_test_result = run_backtest(_scaling_panel(test_multiplier=25.0), strategy, scaled_exp)
    unscaled_only = run_backtest(_scaling_panel(test_multiplier=1.0), strategy, unscaled_exp)

    assert result.scaled is not None
    assert result.scaling_windows[0]["scale_factor"] == modified_test_result.scaling_windows[0]["scale_factor"]
    pd.testing.assert_frame_equal(
        result.unscaled.daily.reset_index(drop=True),
        unscaled_only.daily.reset_index(drop=True),
    )
    assert result.daily is result.unscaled.daily
    assert result.metrics == result.unscaled.metrics


def test_scaled_weights_turnover_cost_and_returns_reconcile():
    strategy = StrategyConfig(drift_window=10, reversal_lookback=3, up_fraction_threshold=0.5)
    result = run_backtest(_scaling_panel(), strategy, _paper_scaled_experiment())
    assert result.scaled is not None
    factor = result.scaling_windows[0]["scale_factor"]

    ledger = result.scaled.ledger
    daily = result.scaled.daily
    assert np.allclose(ledger["signal_weight"], ledger["unscaled_signal_weight"] * factor)

    reconstructed = (
        ledger.assign(recomputed=lambda x: x["signal_weight"] * x["total_return"])
        .groupby("return_date")["recomputed"]
        .sum()
        .sort_index()
    )
    reported = daily.set_index("return_date")["gross_return"].sort_index()
    assert np.allclose(reconstructed, reported)
    assert np.allclose(daily["cost"], daily["turnover"] * 0.6 * 1e-4)
    assert np.allclose(daily["net_return"], daily["gross_return"] - daily["cost"])
