from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .audit import audit_experiment
from .config import ExperimentConfig, StrategyConfig
from .data_model import apply_point_in_time_membership, normalize_panel
from .metrics import block_bootstrap_sharpe_ci, performance_metrics, yearly_metrics
from .portfolio import align_weights_to_returns, construct_signal_weights, daily_portfolio_returns
from .signals import compute_signals


@dataclass
class BacktestResult:
    ledger: pd.DataFrame
    daily: pd.DataFrame
    yearly: pd.DataFrame
    metrics: dict[str, float]
    sharpe_ci_95: tuple[float, float]
    audit: dict


def _filter_period(panel: pd.DataFrame, exp: ExperimentConfig) -> pd.DataFrame:
    df = panel
    if exp.period_mode == "continuous":
        if exp.start_date:
            df = df[df["date"] >= pd.Timestamp(exp.start_date)]
        if exp.end_date:
            df = df[df["date"] <= pd.Timestamp(exp.end_date)]
        return df
    if exp.period_mode == "paper_three_windows":
        if not exp.paper_test_windows:
            raise ValueError("paper_three_windows requires explicit one-year window dates")
        windows = exp.paper_test_windows
        mask = pd.Series(False, index=df.index)
        for start, end in windows:
            mask |= df["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        return df[mask]
    raise ValueError(f"Unknown period_mode={exp.period_mode}")


def run_backtest(
    panel: pd.DataFrame,
    strategy: StrategyConfig,
    experiment: ExperimentConfig,
    membership: pd.DataFrame | None = None,
) -> BacktestResult:
    panel = normalize_panel(panel)
    if experiment.point_in_time_membership_required:
        if membership is None:
            raise ValueError("Point-in-time membership data is required for this experiment")
        panel = apply_point_in_time_membership(panel, membership)

    # Signals need lookback history before the requested OOS period, so filter only after signals.
    signals = compute_signals(panel, strategy)
    weights = construct_signal_weights(signals, strategy)
    aligned = align_weights_to_returns(weights, panel, experiment.return_lag_sessions)

    if experiment.period_mode == "continuous":
        if experiment.start_date:
            aligned = aligned[aligned["return_date"] >= pd.Timestamp(experiment.start_date)]
        if experiment.end_date:
            aligned = aligned[aligned["return_date"] <= pd.Timestamp(experiment.end_date)]
    elif experiment.period_mode == "paper_three_windows":
        windows = experiment.paper_test_windows or []
        mask = pd.Series(False, index=aligned.index)
        for start, end in windows:
            mask |= aligned["return_date"].between(pd.Timestamp(start), pd.Timestamp(end))
        aligned = aligned[mask]

    ledger, daily = daily_portfolio_returns(aligned, experiment.transaction_cost_bp)
    metrics = performance_metrics(daily["net_return"], strategy.risk_free_rate)
    ci = block_bootstrap_sharpe_ci(daily["net_return"])
    yearly = yearly_metrics(daily)
    audit = audit_experiment(experiment, ledger, daily)
    return BacktestResult(ledger=ledger, daily=daily, yearly=yearly, metrics=metrics, sharpe_ci_95=ci, audit=audit)
