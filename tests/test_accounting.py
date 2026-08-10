import pandas as pd

from sharpe_replication.audit import audit_experiment
from sharpe_replication.config import ExperimentConfig
from sharpe_replication.portfolio import daily_portfolio_returns


def test_security_ledger_reconciles_to_daily_return():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "return_date": pd.to_datetime(["2024-01-03", "2024-01-03"]),
            "security_id": ["A", "B"],
            "signal_weight": [0.5, -0.5],
            "total_return": [0.02, -0.01],
        }
    )
    ledger, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.0)
    assert abs(daily.gross_return.iloc[0] - 0.015) < 1e-12
    exp = ExperimentConfig(
        id="PTEST",
        name="test",
        universe_mode="point_in_time",
        period_mode="continuous",
        return_lag_sessions=2,
        raw_signal_price_required=True,
        point_in_time_membership_required=True,
        transaction_cost_bp=0,
        paper_scaling=False,
        certified=True,
    )
    report = audit_experiment(exp, ledger, daily)
    assert report["status"] == "PASS"
