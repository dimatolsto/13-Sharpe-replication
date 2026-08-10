from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from .audit import audit_experiment
from .config import ExperimentConfig, ScalingWindow, StrategyConfig
from .data_model import normalize_panel
from .metrics import (
    block_bootstrap_sharpe_ci,
    paper_scale_details,
    performance_metrics,
    yearly_metrics,
)
from .portfolio import align_weights_to_returns, construct_signal_weights, daily_portfolio_returns
from .signals import compute_signals


@dataclass
class ReturnSeriesResult:
    ledger: pd.DataFrame
    daily: pd.DataFrame
    yearly: pd.DataFrame
    metrics: dict[str, float]
    sharpe_ci_95: tuple[float, float]
    audit: dict | None = None


@dataclass
class BacktestResult:
    ledger: pd.DataFrame
    daily: pd.DataFrame
    yearly: pd.DataFrame
    metrics: dict[str, float]
    sharpe_ci_95: tuple[float, float]
    audit: dict
    unscaled: ReturnSeriesResult
    scaled: ReturnSeriesResult | None = None
    scaling_windows: list[dict[str, Any]] = field(default_factory=list)


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(d)


def _date_label(d: date) -> str:
    return d.isoformat()


def _paper_test_windows(exp: ExperimentConfig) -> list[tuple[pd.Timestamp, pd.Timestamp, bool]]:
    if exp.scaling and exp.scaling.mode == "paper_walk_forward":
        return [(_ts(w.test_start), _ts(w.test_end), False) for w in exp.scaling.windows]
    if not exp.paper_test_windows:
        raise ValueError("paper_three_windows requires explicit one-year window dates")
    return [(_ts(w.start), _ts(w.end), True) for w in exp.paper_test_windows]


def _filter_aligned_to_experiment(aligned: pd.DataFrame, exp: ExperimentConfig) -> pd.DataFrame:
    df = aligned
    if exp.period_mode == "continuous":
        if exp.start_date:
            df = df[df["return_date"] >= _ts(exp.start_date)]
        if exp.end_date:
            df = df[df["return_date"] <= _ts(exp.end_date)]
        return df
    if exp.period_mode == "paper_three_windows":
        mask = pd.Series(False, index=df.index)
        for start, end, end_inclusive in _paper_test_windows(exp):
            if end_inclusive:
                mask |= df["return_date"].between(start, end)
            else:
                mask |= (df["return_date"] >= start) & (df["return_date"] < end)
        return df[mask]
    raise ValueError(f"Unknown period_mode={exp.period_mode}")


def _build_return_series(
    aligned: pd.DataFrame,
    strategy: StrategyConfig,
    experiment: ExperimentConfig,
    audit: bool,
) -> ReturnSeriesResult:
    ledger, daily = daily_portfolio_returns(aligned, experiment.transaction_cost_bp)
    metrics = performance_metrics(daily["net_return"], strategy.risk_free_rate)
    ci = block_bootstrap_sharpe_ci(daily["net_return"])
    yearly = yearly_metrics(daily)
    report = audit_experiment(experiment, ledger, daily) if audit else None
    return ReturnSeriesResult(
        ledger=ledger,
        daily=daily,
        yearly=yearly,
        metrics=metrics,
        sharpe_ci_95=ci,
        audit=report,
    )


def _window_mask(series: pd.Series, start: date, end: date) -> pd.Series:
    return (series >= _ts(start)) & (series < _ts(end))


def _scaling_report(
    window: ScalingWindow,
    training_returns: pd.Series,
    strategy: StrategyConfig,
) -> dict[str, Any]:
    details = paper_scale_details(
        training_returns=training_returns,
        vol_target=strategy.annual_vol_target,
        max_dd_target=strategy.max_drawdown_target,
    )
    return {
        "training_start": _date_label(window.train_start),
        "training_end": _date_label(window.train_end),
        "test_start": _date_label(window.test_start),
        "test_end": _date_label(window.test_end),
        **details,
    }


def _apply_scaling(
    aligned: pd.DataFrame,
    full_unscaled_daily: pd.DataFrame,
    strategy: StrategyConfig,
    experiment: ExperimentConfig,
) -> tuple[ReturnSeriesResult | None, list[dict[str, Any]]]:
    scaling = experiment.scaling
    if scaling is None or scaling.mode == "none":
        return None, []
    if scaling.mode != "paper_walk_forward":
        raise ValueError(f"Scaling mode {scaling.mode!r} is not implemented")

    scaled_aligned_parts: list[pd.DataFrame] = []
    reports: list[dict[str, Any]] = []
    for window in scaling.windows:
        train_mask = _window_mask(
            full_unscaled_daily["return_date"],
            window.train_start,
            window.train_end,
        )
        training_returns = full_unscaled_daily.loc[train_mask, "net_return"]
        if training_returns.empty:
            raise ValueError(
                "Cannot compute paper scaling without training returns for "
                f"{window.train_start} to {window.train_end}"
            )

        report = _scaling_report(window, training_returns, strategy)
        reports.append(report)

        test_mask = _window_mask(aligned["return_date"], window.test_start, window.test_end)
        scaled_window = aligned.loc[test_mask].copy()
        scaled_window["unscaled_signal_weight"] = scaled_window["signal_weight"]
        scaled_window["scale_factor"] = report["scale_factor"]
        scaled_window["signal_weight"] = scaled_window["signal_weight"] * report["scale_factor"]
        scaled_aligned_parts.append(scaled_window)

    if scaled_aligned_parts:
        scaled_aligned = pd.concat(scaled_aligned_parts, ignore_index=True)
    else:
        scaled_aligned = aligned.iloc[0:0].copy()
    scaled = _build_return_series(scaled_aligned, strategy, experiment, audit=False)
    return scaled, reports


def run_backtest(
    panel: pd.DataFrame,
    strategy: StrategyConfig,
    experiment: ExperimentConfig,
    membership: pd.DataFrame | None = None,
) -> BacktestResult:
    panel = normalize_panel(panel)
    signal_membership = None
    if experiment.point_in_time_membership_required:
        if membership is None:
            raise ValueError("Point-in-time membership data is required for this experiment")
        signal_membership = membership

    # Signals need lookback history before the requested OOS period, so filter only after signal and
    # return alignment. PIT membership is an eligibility mask on the signal date, not a prefilter.
    signals = compute_signals(panel, strategy, signal_membership)
    weights = construct_signal_weights(signals, strategy)
    full_aligned = align_weights_to_returns(weights, panel, experiment.return_lag_sessions)

    full_unscaled = _build_return_series(full_aligned, strategy, experiment, audit=False)
    aligned = _filter_aligned_to_experiment(full_aligned, experiment)
    unscaled = _build_return_series(aligned, strategy, experiment, audit=True)
    scaled, scaling_windows = _apply_scaling(full_aligned, full_unscaled.daily, strategy, experiment)

    return BacktestResult(
        ledger=unscaled.ledger,
        daily=unscaled.daily,
        yearly=unscaled.yearly,
        metrics=unscaled.metrics,
        sharpe_ci_95=unscaled.sharpe_ci_95,
        audit=unscaled.audit or {},
        unscaled=unscaled,
        scaled=scaled,
        scaling_windows=scaling_windows,
    )
