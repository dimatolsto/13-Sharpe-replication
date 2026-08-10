import numpy as np
import pandas as pd

from sharpe_replication.backtest import run_backtest
from sharpe_replication.config import ExperimentConfig, StrategyConfig


def test_end_to_end_corrected_backtest_passes_audit():
    dates = pd.bdate_range("2020-01-01", periods=120)
    rows = []
    securities = [f"S{i}" for i in range(8)]
    for i, sid in enumerate(securities):
        price = 20.0 + 10 * i
        for j, d in enumerate(dates):
            # >60% positive days but with cross-sectional variation.
            positive = (j + i) % 5 != 0
            ret = (0.001 + i * 0.0001) if positive else -(0.0015 + i * 0.00005)
            price = price * (1 + ret)
            rows.append(
                {
                    "date": d,
                    "security_id": sid,
                    "ticker": sid,
                    "raw_close": price,
                    "total_return": ret,
                }
            )
    panel = pd.DataFrame(rows)
    membership = pd.DataFrame(
        {
            "security_id": securities,
            "membership_start": [dates[0]] * len(securities),
            "membership_end": [pd.NaT] * len(securities),
        }
    )
    exp = ExperimentConfig(
        id="PTEST",
        name="integration",
        universe_mode="point_in_time",
        period_mode="continuous",
        start_date=str(dates[70].date()),
        end_date=str(dates[-1].date()),
        return_lag_sessions=2,
        raw_signal_price_required=True,
        point_in_time_membership_required=True,
        transaction_cost_bp=0.6,
        paper_scaling=False,
        certified=True,
    )
    result = run_backtest(panel, StrategyConfig(), exp, membership)
    assert len(result.daily) > 0
    assert result.audit["status"] == "PASS"
    assert np.isfinite(result.metrics["ann_vol"])
