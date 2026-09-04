from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from .config import ExperimentConfig, StrategyConfig, load_experiment
from .data_model import normalize_panel
from .metrics import performance_metrics
from .phase3_attribution import SURVIVORSHIP_LABEL, _json_default, _sha256_file
from .portfolio import construct_signal_weights, daily_portfolio_returns
from .signals import compute_signals

PHASE3B_LABEL = "PHASE 3B REPRODUCTION GAP FORENSICS"
INVALID_LABEL = "INVALID LOOK-AHEAD / ALIGNMENT DIAGNOSTIC - NOT A STRATEGY"
INVALID_REGIME_LABEL = "INVALID CONTEMPORANEOUS REGIME DIAGNOSTIC"
INVALID_REVERSAL_LABEL = "INVALID CURRENT-DAY REVERSAL DIAGNOSTIC"

PAPER_TRAIN_SHARPES = {
    "2005-2010": 19.42,
    "2010-2015": 27.79,
    "2015-2020": 16.63,
}

PAPER_TEST_SHARPES = {
    "2010": 16.89,
    "2015": 22.87,
    "2020": 5.11,
}

PHASE3_BASELINE_SHARPES = {
    ("R0 unscaled", "unscaled"): 0.8384885885105041,
    ("R1 unscaled", "unscaled"): 0.6298441785871275,
    ("R2 unscaled", "unscaled"): 0.4022709667262486,
    ("R3 unscaled", "unscaled"): 0.018720493438701944,
}

WINDOWS = [
    {
        "window_pair": "2005-2010",
        "window": "train_2005_2010",
        "kind": "train",
        "start": pd.Timestamp("2005-01-01"),
        "end": pd.Timestamp("2010-01-01"),
        "paper_sharpe": PAPER_TRAIN_SHARPES["2005-2010"],
    },
    {
        "window_pair": "2005-2010",
        "window": "test_2010",
        "kind": "test",
        "start": pd.Timestamp("2010-01-01"),
        "end": pd.Timestamp("2011-01-01"),
        "paper_sharpe": PAPER_TEST_SHARPES["2010"],
    },
    {
        "window_pair": "2010-2015",
        "window": "train_2010_2015",
        "kind": "train",
        "start": pd.Timestamp("2010-01-01"),
        "end": pd.Timestamp("2015-01-01"),
        "paper_sharpe": PAPER_TRAIN_SHARPES["2010-2015"],
    },
    {
        "window_pair": "2010-2015",
        "window": "test_2015",
        "kind": "test",
        "start": pd.Timestamp("2015-01-01"),
        "end": pd.Timestamp("2016-01-01"),
        "paper_sharpe": PAPER_TEST_SHARPES["2015"],
    },
    {
        "window_pair": "2015-2020",
        "window": "train_2015_2020",
        "kind": "train",
        "start": pd.Timestamp("2015-01-01"),
        "end": pd.Timestamp("2020-01-01"),
        "paper_sharpe": PAPER_TRAIN_SHARPES["2015-2020"],
    },
    {
        "window_pair": "2015-2020",
        "window": "test_2020",
        "kind": "test",
        "start": pd.Timestamp("2020-01-01"),
        "end": pd.Timestamp("2021-01-01"),
        "paper_sharpe": PAPER_TEST_SHARPES["2020"],
    },
]

SELECTED_TEST_DATES = [
    (pd.Timestamp("2010-01-01"), pd.Timestamp("2011-01-01")),
    (pd.Timestamp("2015-01-01"), pd.Timestamp("2016-01-01")),
    (pd.Timestamp("2020-01-01"), pd.Timestamp("2021-01-01")),
]

ALL_TRAIN_DATES = [
    (pd.Timestamp("2005-01-01"), pd.Timestamp("2010-01-01")),
    (pd.Timestamp("2010-01-01"), pd.Timestamp("2015-01-01")),
    (pd.Timestamp("2015-01-01"), pd.Timestamp("2020-01-01")),
]

RegimeMode = Literal["exclude_current", "include_current"]
ReversalMode = Literal["prior_compounded", "endpoint_prior", "include_current_compounded"]
EdgeStandardizationMode = Literal[
    "active_edge_only",
    "base_z_then_regime",
    "edge_z_include_inactive_zero",
    "active_base_only",
]


@dataclass
class VariantRun:
    variant_id: str
    features: pd.DataFrame
    signals: pd.DataFrame
    weights: pd.DataFrame
    aligned: pd.DataFrame
    ledger: pd.DataFrame
    daily: pd.DataFrame
    is_valid_strategy: bool
    diagnostic_label: str


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    elif path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        raise ValueError(f"Unsupported output extension: {path}")


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=s.index, dtype=float)
    return (s - s.mean()) / std


def _window_mask(dates: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    return (pd.to_datetime(dates) >= start) & (pd.to_datetime(dates) < end)


def _selected_test_mask(dates: pd.Series) -> pd.Series:
    mask = pd.Series(False, index=dates.index)
    for start, end in SELECTED_TEST_DATES:
        mask |= _window_mask(dates, start, end)
    return mask


def _all_train_mask(dates: pd.Series) -> pd.Series:
    mask = pd.Series(False, index=dates.index)
    for start, end in ALL_TRAIN_DATES:
        mask |= _window_mask(dates, start, end)
    return mask


def _summarize_returns(daily: pd.DataFrame) -> dict[str, Any]:
    if daily.empty:
        return {
            "observations": 0,
            "sharpe": float("nan"),
            "annualized_return": float("nan"),
            "annualized_volatility": float("nan"),
            "max_drawdown": float("nan"),
            "terminal_wealth": float("nan"),
            "cumulative_return": float("nan"),
            "mean_daily_return": float("nan"),
            "median_daily_return": float("nan"),
            "winning_day_fraction": float("nan"),
            "daily_std": float("nan"),
            "avg_one_way_turnover": float("nan"),
            "avg_gross_traded_notional": float("nan"),
        }
    metrics = performance_metrics(daily["net_return"])
    return {
        "observations": len(daily),
        "sharpe": metrics["sharpe"],
        "annualized_return": metrics["cagr"],
        "annualized_volatility": metrics["ann_vol"],
        "max_drawdown": metrics["max_drawdown"],
        "terminal_wealth": metrics["wealth"],
        "cumulative_return": metrics["wealth"] - 1.0,
        "mean_daily_return": float(daily["net_return"].mean()),
        "median_daily_return": float(daily["net_return"].median()),
        "winning_day_fraction": float((daily["net_return"] > 0).mean()),
        "daily_std": float(daily["net_return"].std(ddof=1)),
        "avg_one_way_turnover": float(daily["turnover"].mean()),
        "avg_gross_traded_notional": float((2.0 * daily["turnover"]).mean()),
    }


def compute_forensic_time_series_features(
    panel: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    regime_mode: RegimeMode,
    reversal_mode: ReversalMode,
) -> pd.DataFrame:
    """Compute Phase 3B feature variants without mutating production experiment specs."""

    df = normalize_panel(panel)
    g = df.groupby("security_id", group_keys=False)

    if reversal_mode == "include_current_compounded":
        reversal_source = g["total_return"].transform(lambda s: s)
    else:
        reversal_source = g["total_return"].transform(lambda s: s.shift(1))

    if reversal_mode in {"prior_compounded", "include_current_compounded"}:
        df["ret_10"] = reversal_source.groupby(df["security_id"], group_keys=False).transform(
            lambda s: (1.0 + s)
            .rolling(cfg.reversal_lookback, min_periods=cfg.reversal_lookback)
            .apply(np.prod, raw=True)
            - 1.0
        )
    elif reversal_mode == "endpoint_prior":
        price_col = "yahoo_adj_close" if "yahoo_adj_close" in df.columns else "raw_close"
        shifted = g[price_col].shift(1)
        lagged = g[price_col].shift(cfg.reversal_lookback + 1)
        df["ret_10"] = shifted / lagged - 1.0
    else:
        raise ValueError(f"Unsupported reversal_mode={reversal_mode}")

    regime_source = (
        g["total_return"].transform(lambda s: s)
        if regime_mode == "include_current"
        else g["total_return"].transform(lambda s: s.shift(1))
    )
    df["up_fraction"] = regime_source.groupby(df["security_id"], group_keys=False).transform(
        lambda s: s.gt(0).rolling(cfg.drift_window, min_periods=cfg.drift_window).mean()
    )
    df["regime"] = (df["up_fraction"] > cfg.up_fraction_threshold).astype(float)
    df.loc[df["up_fraction"].isna(), "regime"] = np.nan
    df["reversal_raw"] = -df["ret_10"]
    df["inverse_price"] = 1.0 / df["raw_close"]
    df["regime_mode"] = regime_mode
    df["reversal_mode"] = reversal_mode
    return df


def compute_forensic_cross_sectional_signals(
    features: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    edge_mode: EdgeStandardizationMode = "active_edge_only",
    regime_offset_sessions: int = 0,
) -> pd.DataFrame:
    """Build VALUE/REVERSAL/BASE/EDGE with selectable diagnostic standardization order."""

    df = features.copy()
    if "eligible" not in df.columns:
        df["eligible"] = True
    eligible = df["eligible"].fillna(False).astype(bool)

    if regime_offset_sessions:
        df["regime_unshifted"] = df["regime"]
        df["up_fraction_unshifted"] = df["up_fraction"]
        df["regime"] = df.groupby("security_id", group_keys=False)["regime"].shift(-regime_offset_sessions)
        df["up_fraction"] = df.groupby("security_id", group_keys=False)["up_fraction"].shift(
            -regime_offset_sessions
        )

    df["value_score"] = np.nan
    valid_value = eligible & df["inverse_price"].notna()
    if valid_value.any():
        df.loc[valid_value, "value_score"] = df.loc[valid_value].groupby("date")["inverse_price"].rank(
            pct=True,
            method="average",
        )

    df["reversal_z"] = np.nan
    valid_reversal = eligible & df["reversal_raw"].notna()
    if valid_reversal.any():
        df.loc[valid_reversal, "reversal_z"] = (
            df.loc[valid_reversal].groupby("date")["reversal_raw"].transform(_zscore).astype(float)
        )

    df["base"] = np.nan
    valid_base = eligible & df["value_score"].notna() & df["reversal_z"].notna()
    df.loc[valid_base, "base"] = (
        cfg.value_weight * df.loc[valid_base, "value_score"]
        + cfg.reversal_weight * df.loc[valid_base, "reversal_z"]
    )
    active = valid_base & df["regime"].eq(1.0)
    df["edge"] = np.nan
    df.loc[valid_base, "edge"] = df.loc[valid_base, "base"] * df.loc[valid_base, "regime"]
    df["z_edge"] = np.nan

    if edge_mode == "active_edge_only":
        if active.any():
            df.loc[active, "z_edge"] = df.loc[active].groupby("date")["edge"].transform(_zscore).astype(float)
    elif edge_mode == "base_z_then_regime":
        df["base_z_all"] = np.nan
        if valid_base.any():
            df.loc[valid_base, "base_z_all"] = (
                df.loc[valid_base].groupby("date")["base"].transform(_zscore).astype(float)
            )
        df.loc[active, "z_edge"] = df.loc[active, "base_z_all"]
    elif edge_mode == "edge_z_include_inactive_zero":
        df["edge_with_inactive_zero"] = np.nan
        df.loc[valid_base, "edge_with_inactive_zero"] = df.loc[valid_base, "edge"].fillna(0.0)
        df.loc[valid_base, "edge_with_inactive_zero"] = df.loc[valid_base, "edge_with_inactive_zero"].fillna(0.0)
        df.loc[valid_base, "z_edge"] = (
            df.loc[valid_base]
            .groupby("date")["edge_with_inactive_zero"]
            .transform(_zscore)
            .astype(float)
        )
    elif edge_mode == "active_base_only":
        if active.any():
            df.loc[active, "z_edge"] = df.loc[active].groupby("date")["base"].transform(_zscore).astype(float)
    else:
        raise ValueError(f"Unsupported edge_mode={edge_mode}")

    df["edge_standardization_mode"] = edge_mode
    df["regime_offset_sessions"] = regime_offset_sessions
    return df


def align_weights_to_returns_forensic(
    weights: pd.DataFrame,
    panel: pd.DataFrame,
    return_offset_sessions: int,
) -> pd.DataFrame:
    """Align fixed target weights to a requested return-date offset for leakage diagnostics."""

    calendar = pd.Index(pd.to_datetime(panel["date"]).drop_duplicates().sort_values(), name="date")
    pos = pd.Series(np.arange(len(calendar)), index=calendar)
    target_pos = weights["date"].map(pos) + return_offset_sessions
    valid = target_pos.notna() & (target_pos >= 0) & (target_pos < len(calendar))
    aligned = weights.loc[valid].copy()
    aligned["return_date"] = calendar.take(target_pos.loc[valid].astype(int).to_numpy()).to_numpy()
    returns = panel[["date", "security_id", "total_return"]].rename(columns={"date": "return_date"})
    return aligned.merge(returns, on=["return_date", "security_id"], how="left", validate="many_to_one")


def return_alignment_label(offset: int) -> str:
    if offset == 0:
        return "lag0_contemporaneous_close_t_return_INVALID"
    if offset < 0:
        return "past_return_matched_to_future_weight_INVALID_ALIGNMENT"
    if offset == 1:
        return "phase3_R0_same_close_execution_suspect"
    if offset == 2:
        return "corrected_daily_t_plus_1_execution"
    return f"delayed_causal_offset_{offset}"


def _run_variant(
    panel: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    variant_id: str,
    regime_mode: RegimeMode,
    reversal_mode: ReversalMode,
    edge_mode: EdgeStandardizationMode = "active_edge_only",
    regime_offset_sessions: int = 0,
    return_offset_sessions: int = 1,
    is_valid_strategy: bool = True,
    diagnostic_label: str = "",
) -> VariantRun:
    features = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode=regime_mode,
        reversal_mode=reversal_mode,
    )
    return _run_variant_from_features(
        panel,
        cfg,
        features=features,
        variant_id=variant_id,
        edge_mode=edge_mode,
        regime_offset_sessions=regime_offset_sessions,
        return_offset_sessions=return_offset_sessions,
        is_valid_strategy=is_valid_strategy,
        diagnostic_label=diagnostic_label,
    )


def _run_variant_from_features(
    panel: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    features: pd.DataFrame,
    variant_id: str,
    edge_mode: EdgeStandardizationMode = "active_edge_only",
    regime_offset_sessions: int = 0,
    return_offset_sessions: int = 1,
    is_valid_strategy: bool = True,
    diagnostic_label: str = "",
) -> VariantRun:
    features = features.copy()
    features["eligible"] = True
    signals = compute_forensic_cross_sectional_signals(
        features,
        cfg,
        edge_mode=edge_mode,
        regime_offset_sessions=regime_offset_sessions,
    )
    weights = construct_signal_weights(signals, cfg)
    aligned = align_weights_to_returns_forensic(weights, panel, return_offset_sessions)
    ledger, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.60)
    return VariantRun(
        variant_id=variant_id,
        features=features,
        signals=signals,
        weights=weights,
        aligned=aligned,
        ledger=ledger,
        daily=daily,
        is_valid_strategy=is_valid_strategy,
        diagnostic_label=diagnostic_label,
    )


def _run_legacy_phase3_r0(panel: pd.DataFrame, cfg: StrategyConfig, exp: ExperimentConfig) -> VariantRun:
    signals = compute_signals(panel, cfg)
    weights = construct_signal_weights(signals, cfg)
    aligned = align_weights_to_returns_forensic(weights, panel, exp.return_lag_sessions)
    ledger, daily = daily_portfolio_returns(aligned, exp.transaction_cost_bp)
    features = signals[
        [
            "date",
            "security_id",
            "ticker",
            "raw_close",
            "total_return",
            "ret_10",
            "reversal_raw",
            "up_fraction",
            "regime",
            "inverse_price",
            "eligible",
        ]
    ].copy()
    return VariantRun(
        variant_id="R0_PHASE3_LEGACY_CURRENT_DAY_FEATURES",
        features=features,
        signals=signals,
        weights=weights,
        aligned=aligned,
        ledger=ledger,
        daily=daily,
        is_valid_strategy=False,
        diagnostic_label=INVALID_LABEL,
    )


def _window_daily(run: VariantRun, window: dict[str, Any]) -> pd.DataFrame:
    aligned = run.aligned.loc[_window_mask(run.aligned["return_date"], window["start"], window["end"])].copy()
    _, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.60)
    return daily


def _daily_for_periods(run: VariantRun, periods: list[tuple[pd.Timestamp, pd.Timestamp]]) -> pd.DataFrame:
    mask = pd.Series(False, index=run.aligned.index)
    for start, end in periods:
        mask |= _window_mask(run.aligned["return_date"], start, end)
    _, daily = daily_portfolio_returns(run.aligned.loc[mask].copy(), transaction_cost_bp=0.60)
    return daily


def _signal_dates_for_return_window(run: VariantRun, window: dict[str, Any]) -> pd.Series:
    block = run.aligned.loc[_window_mask(run.aligned["return_date"], window["start"], window["end"])]
    return pd.Series(pd.to_datetime(block["date"].drop_duplicates()).sort_values())


def _position_stats_for_dates(weights: pd.DataFrame, signal_dates: pd.Series) -> dict[str, Any]:
    if signal_dates.empty:
        return {
            "avg_long_positions": float("nan"),
            "avg_short_positions": float("nan"),
            "avg_positioned_names": float("nan"),
            "median_abs_position_weight": float("nan"),
            "largest_abs_position_weight": float("nan"),
            "top_decile_weight_concentration": float("nan"),
            "avg_long_weight_sum": float("nan"),
            "avg_short_weight_sum": float("nan"),
            "avg_gross_weight_sum": float("nan"),
            "avg_net_weight_sum": float("nan"),
        }
    block = weights[weights["date"].isin(set(signal_dates))].copy()
    block["abs_weight"] = block["signal_weight"].abs()
    nonzero = block[block["signal_weight"].ne(0.0)].copy()
    per_date = block.groupby("date").agg(
        long_positions=("signal_weight", lambda s: int((s > 0).sum())),
        short_positions=("signal_weight", lambda s: int((s < 0).sum())),
        long_weight_sum=("signal_weight", lambda s: float(s[s > 0].sum())),
        short_weight_sum=("signal_weight", lambda s: float(s[s < 0].sum())),
        gross_weight_sum=("signal_weight", lambda s: float(s.abs().sum())),
        net_weight_sum=("signal_weight", "sum"),
    )
    conc_values = []
    for _, day in nonzero.groupby("date"):
        gross = day["abs_weight"].sum()
        if gross <= 0:
            continue
        n_top = max(1, math.ceil(len(day) * 0.10))
        conc_values.append(float(day.nlargest(n_top, "abs_weight")["abs_weight"].sum() / gross))
    return {
        "avg_long_positions": float(per_date["long_positions"].mean()),
        "avg_short_positions": float(per_date["short_positions"].mean()),
        "avg_positioned_names": float((per_date["long_positions"] + per_date["short_positions"]).mean()),
        "median_abs_position_weight": float(nonzero["abs_weight"].median()) if len(nonzero) else float("nan"),
        "largest_abs_position_weight": float(nonzero["abs_weight"].max()) if len(nonzero) else float("nan"),
        "top_decile_weight_concentration": float(np.mean(conc_values)) if conc_values else float("nan"),
        "avg_long_weight_sum": float(per_date["long_weight_sum"].mean()),
        "avg_short_weight_sum": float(per_date["short_weight_sum"].mean()),
        "avg_gross_weight_sum": float(per_date["gross_weight_sum"].mean()),
        "avg_net_weight_sum": float(per_date["net_weight_sum"].mean()),
    }


def _feature_stats_for_dates(signals: pd.DataFrame, signal_dates: pd.Series) -> dict[str, Any]:
    if signal_dates.empty:
        return {
            "mean_eligible_names_day": float("nan"),
            "median_eligible_names_day": float("nan"),
            "regime_active_stock_day_fraction": float("nan"),
            "avg_active_stocks_day": float("nan"),
            "min_active_stocks_day": float("nan"),
            "median_active_stocks_day": float("nan"),
            "max_active_stocks_day": float("nan"),
            "avg_nonzero_edge_names_day": float("nan"),
        }
    block = signals[signals["date"].isin(set(signal_dates))].copy()
    eligible = block["eligible"].fillna(False).astype(bool) & block["raw_close"].notna()
    block["eligible_for_signal"] = eligible
    block["active_regime"] = eligible & block["regime"].eq(1.0) & block["z_edge"].notna()
    block["nonzero_edge"] = block["z_edge"].fillna(0.0).ne(0.0)
    per_date = block.groupby("date").agg(
        eligible_names=("eligible_for_signal", "sum"),
        active_names=("active_regime", "sum"),
        nonzero_edge_names=("nonzero_edge", "sum"),
    )
    eligible_stock_days = int(per_date["eligible_names"].sum())
    active_stock_days = int(per_date["active_names"].sum())
    return {
        "mean_eligible_names_day": float(per_date["eligible_names"].mean()),
        "median_eligible_names_day": float(per_date["eligible_names"].median()),
        "regime_active_stock_day_fraction": (
            float(active_stock_days / eligible_stock_days) if eligible_stock_days else float("nan")
        ),
        "avg_active_stocks_day": float(per_date["active_names"].mean()),
        "min_active_stocks_day": float(per_date["active_names"].min()),
        "median_active_stocks_day": float(per_date["active_names"].median()),
        "max_active_stocks_day": float(per_date["active_names"].max()),
        "avg_nonzero_edge_names_day": float(per_date["nonzero_edge_names"].mean()),
    }


def paper_fingerprint_rows(run: VariantRun) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for window in WINDOWS:
        daily = _window_daily(run, window)
        signal_dates = _signal_dates_for_return_window(run, window)
        row = {
            "variant_id": run.variant_id,
            "window_pair": window["window_pair"],
            "window": window["window"],
            "kind": window["kind"],
            "start": window["start"].date().isoformat(),
            "end_exclusive": window["end"].date().isoformat(),
            "paper_sharpe": window["paper_sharpe"],
            "is_valid_strategy": run.is_valid_strategy,
            "diagnostic_label": run.diagnostic_label,
            "survivorship_bias_label": SURVIVORSHIP_LABEL,
            **_summarize_returns(daily),
            **_feature_stats_for_dates(run.signals, signal_dates),
            **_position_stats_for_dates(run.weights, signal_dates),
        }
        row["sharpe_difference_vs_paper"] = row["sharpe"] - row["paper_sharpe"]
        rows.append(row)
    return pd.DataFrame(rows)


def training_test_matrix(fingerprints: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for variant_id, block in fingerprints.groupby("variant_id"):
        for pair in ["2005-2010", "2010-2015", "2015-2020"]:
            train = block[(block["window_pair"].eq(pair)) & (block["kind"].eq("train"))]
            test = block[(block["window_pair"].eq(pair)) & (block["kind"].eq("test"))]
            if train.empty or test.empty:
                continue
            rows.append(
                {
                    "variant_id": variant_id,
                    "window_pair": pair,
                    "paper_train_sharpe": float(train["paper_sharpe"].iloc[0]),
                    "ours_train_sharpe": float(train["sharpe"].iloc[0]),
                    "train_difference": float(train["sharpe"].iloc[0] - train["paper_sharpe"].iloc[0]),
                    "paper_test_sharpe": float(test["paper_sharpe"].iloc[0]),
                    "ours_test_sharpe": float(test["sharpe"].iloc[0]),
                    "test_difference": float(test["sharpe"].iloc[0] - test["paper_sharpe"].iloc[0]),
                    "is_valid_strategy": bool(train["is_valid_strategy"].iloc[0]),
                    "diagnostic_label": str(train["diagnostic_label"].iloc[0]),
                }
            )
    return pd.DataFrame(rows)


def baseline_reproduction_report(phase3_dir: Path) -> dict[str, Any]:
    summary_path = phase3_dir / "phase3_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing Phase 3 summary: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = summary.get("r0_r1_r2_r3_summary", [])
    checks = []
    for row in rows:
        key = (row.get("experiment"), row.get("series"))
        if key not in PHASE3_BASELINE_SHARPES:
            continue
        expected = PHASE3_BASELINE_SHARPES[key]
        actual = float(row["sharpe"])
        checks.append(
            {
                "experiment": key[0],
                "series": key[1],
                "expected_sharpe": expected,
                "actual_sharpe": actual,
                "abs_error": abs(actual - expected),
                "within_tolerance": abs(actual - expected) <= 1e-12,
            }
        )
    return {
        "phase3_summary": str(summary_path),
        "matched": bool(checks) and all(item["within_tolerance"] for item in checks),
        "checks": checks,
    }


def verify_phase3_manifest(phase3_dir: Path) -> dict[str, Any]:
    manifest_path = phase3_dir / "input_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing Phase 3 input manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_panel = Path(manifest["input_artifacts"]["phase3_input_panel"])
    expected_hash = manifest["input_artifacts"]["phase3_input_panel_sha256"]
    actual_hash = _sha256_file(input_panel)
    if actual_hash != expected_hash:
        raise ValueError(
            "Phase 3 input panel hash mismatch: "
            f"expected {expected_hash}, got {actual_hash}"
        )
    return {
        "manifest": str(manifest_path),
        "input_panel": str(input_panel),
        "input_panel_sha256": actual_hash,
        "anchor": manifest["anchor_universe"],
        "verified": True,
    }


def regime_window_audit(panel: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    df = normalize_panel(panel)
    rows: list[dict[str, Any]] = []
    candidates = [
        ("year_boundary_2010", pd.Timestamp("2010-01-04")),
        ("year_boundary_2015", pd.Timestamp("2015-01-02")),
        ("year_boundary_2020", pd.Timestamp("2020-01-02")),
    ]
    first_late = (
        df.groupby("security_id", as_index=False)["date"].min().sort_values("date", ascending=False).head(1)
    )
    if len(first_late):
        sid = first_late["security_id"].iloc[0]
        dates = df.loc[df["security_id"].eq(sid), "date"].sort_values().reset_index(drop=True)
        if len(dates) > cfg.drift_window + 2:
            candidates.append(("missing_security_start", dates.iloc[cfg.drift_window + 1]))

    securities = df.groupby("security_id")["date"].count().sort_values(ascending=False).head(3).index.tolist()
    for label, signal_date in candidates:
        for sid in securities:
            dates = df.loc[(df["security_id"].eq(sid)) & (df["date"] < signal_date), "date"].sort_values()
            if len(dates) < cfg.drift_window:
                continue
            included = dates.tail(cfg.drift_window)
            rows.append(
                {
                    "case": label,
                    "security_id": sid,
                    "signal_date": signal_date.date().isoformat(),
                    "included_return_count": len(included),
                    "first_included_return_date": included.iloc[0].date().isoformat(),
                    "last_included_return_date": included.iloc[-1].date().isoformat(),
                    "current_return_date": signal_date.date().isoformat(),
                    "current_return_included": False,
                    "production_phase3_legacy_current_return_included": True,
                    "paper_equation": "sum k=1..63 I(return[t-k] > 0)",
                }
            )
    return pd.DataFrame(rows)


def regime_alignment_scan(
    panel: pd.DataFrame,
    cfg: StrategyConfig,
    *,
    offsets: range = range(-5, 6),
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    features = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    for offset in offsets:
        run = _run_variant_from_features(
            panel,
            cfg,
            features=features,
            variant_id=f"REGIME_OFFSET_{offset:+d}",
            regime_offset_sessions=offset,
            return_offset_sessions=1,
            is_valid_strategy=offset <= 0,
            diagnostic_label=INVALID_REGIME_LABEL if offset > 0 else "",
        )
        selected = _daily_for_periods(run, SELECTED_TEST_DATES)
        training = _daily_for_periods(run, ALL_TRAIN_DATES)
        active = run.signals["eligible"].fillna(False).astype(bool) & run.signals["regime"].eq(1.0)
        eligible = run.signals["eligible"].fillna(False).astype(bool) & run.signals["raw_close"].notna()
        rows.append(
            {
                "offset_sessions": offset,
                "sign_convention": "+N uses same security's regime from N future signal sessions; -N uses lagged regime",
                "is_valid_strategy": offset <= 0,
                "diagnostic_label": INVALID_REGIME_LABEL if offset > 0 else "",
                "selected_window_sharpe": performance_metrics(selected["net_return"])["sharpe"],
                "training_sharpe": performance_metrics(training["net_return"])["sharpe"],
                "active_stock_day_fraction": float(active.sum() / eligible.sum()) if eligible.sum() else float("nan"),
                "selected_mean_daily_return": float(selected["net_return"].mean()) if len(selected) else float("nan"),
                "selected_winning_day_fraction": float((selected["net_return"] > 0).mean()) if len(selected) else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def return_alignment_scan(
    weights: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    offsets: range = range(-10, 11),
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    valid_aligned = align_weights_to_returns_forensic(weights, panel, 1)
    _, valid_daily = daily_portfolio_returns(
        valid_aligned.loc[_selected_test_mask(valid_aligned["return_date"])].copy(),
        transaction_cost_bp=0.60,
    )
    valid_selected = valid_daily[["return_date", "net_return"]]
    valid_selected = valid_selected.rename(columns={"net_return": "valid_r0_net_return"})
    for offset in offsets:
        aligned = align_weights_to_returns_forensic(weights, panel, offset)
        _, selected = daily_portfolio_returns(
            aligned.loc[_selected_test_mask(aligned["return_date"])].copy(),
            transaction_cost_bp=0.60,
        )
        _, training = daily_portfolio_returns(
            aligned.loc[_all_train_mask(aligned["return_date"])].copy(),
            transaction_cost_bp=0.60,
        )
        corr_join = selected[["return_date", "net_return"]].merge(valid_selected, on="return_date", how="inner")
        corr = (
            float(corr_join["net_return"].corr(corr_join["valid_r0_net_return"]))
            if len(corr_join) > 2
            else float("nan")
        )
        rows.append(
            {
                "return_offset_sessions": offset,
                "date_interpretation": return_alignment_label(offset),
                "is_valid_strategy": offset >= 2,
                "diagnostic_label": INVALID_LABEL if offset <= 1 else "",
                "selected_window_sharpe": performance_metrics(selected["net_return"])["sharpe"],
                "training_sharpe": performance_metrics(training["net_return"])["sharpe"],
                "mean_daily_return": float(selected["net_return"].mean()) if len(selected) else float("nan"),
                "correlation_with_phase3_r0_return": corr,
            }
        )
    return pd.DataFrame(rows)


def invalid_lag0_report(weights: pd.DataFrame, panel: pd.DataFrame) -> dict[str, Any]:
    aligned = align_weights_to_returns_forensic(weights, panel, 0)
    _, selected = daily_portfolio_returns(
        aligned.loc[_selected_test_mask(aligned["return_date"])].copy(),
        transaction_cost_bp=0.60,
    )
    _, training = daily_portfolio_returns(
        aligned.loc[_all_train_mask(aligned["return_date"])].copy(),
        transaction_cost_bp=0.60,
    )
    rows = []
    for window in WINDOWS:
        _, block = daily_portfolio_returns(
            aligned.loc[_window_mask(aligned["return_date"], window["start"], window["end"])].copy(),
            transaction_cost_bp=0.60,
        )
        rows.append({"window": window["window"], "kind": window["kind"], **_summarize_returns(block)})
    return {
        "variant_id": "R_INVALID_LAG0",
        "definition": "close-t signal/weight information matched to close(t-1)->close(t) return",
        "diagnostic_label": INVALID_LABEL,
        "is_valid_strategy": False,
        "selected_window_sharpe": performance_metrics(selected["net_return"])["sharpe"],
        "training_sharpe": performance_metrics(training["net_return"])["sharpe"],
        "windows": rows,
    }


def reversal_definition_audit(panel: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    feature_frames = {
        mode: compute_forensic_time_series_features(
            panel,
            cfg,
            regime_mode="exclude_current",
            reversal_mode=mode,  # type: ignore[arg-type]
        )[["date", "security_id", "ret_10", "reversal_raw"]].rename(
            columns={"ret_10": f"{mode}_ret_10", "reversal_raw": f"{mode}_reversal_raw"}
        )
        for mode in ["prior_compounded", "endpoint_prior", "include_current_compounded"]
    }
    joined = feature_frames["prior_compounded"]
    for mode in ["endpoint_prior", "include_current_compounded"]:
        joined = joined.merge(feature_frames[mode], on=["date", "security_id"], how="inner", validate="one_to_one")

    rows: list[dict[str, Any]] = []
    pairs = [
        ("endpoint_prior", "prior_compounded", True, ""),
        ("include_current_compounded", "prior_compounded", False, INVALID_REVERSAL_LABEL),
    ]
    for left, right, valid, label in pairs:
        diff = joined[f"{left}_ret_10"] - joined[f"{right}_ret_10"]
        corr = joined[f"{left}_reversal_raw"].corr(joined[f"{right}_reversal_raw"])
        left_features = feature_frames[left].merge(
            compute_forensic_time_series_features(
                panel,
                cfg,
                regime_mode="exclude_current",
                reversal_mode=left,  # type: ignore[arg-type]
            ).drop(columns=["ret_10", "reversal_raw"]),
            on=["date", "security_id"],
            how="inner",
            validate="one_to_one",
        )
        left_features = left_features.rename(
            columns={
                f"{left}_ret_10": "ret_10",
                f"{left}_reversal_raw": "reversal_raw",
            }
        )
        run = _run_variant_from_features(
            panel,
            cfg,
            features=left_features,
            variant_id=f"REVERSAL_{left}",
            is_valid_strategy=valid,
            diagnostic_label=label,
        )
        selected = _daily_for_periods(run, SELECTED_TEST_DATES)
        rows.append(
            {
                "comparison": f"{left}_vs_{right}",
                "left_interpretation": left,
                "right_interpretation": right,
                "is_valid_strategy": valid,
                "diagnostic_label": label,
                "max_abs_return_difference": float(diff.abs().max()),
                "mean_abs_return_difference": float(diff.abs().mean()),
                "raw_reversal_correlation": float(corr),
                "selected_window_r0_diagnostic_sharpe": performance_metrics(selected["net_return"])["sharpe"],
            }
        )
    return pd.DataFrame(rows)


def value_rank_audit(panel: pd.DataFrame) -> pd.DataFrame:
    df = normalize_panel(panel)
    sample_dates = [
        pd.Timestamp("2010-01-04"),
        pd.Timestamp("2015-01-02"),
        pd.Timestamp("2020-01-02"),
    ]
    rows: list[dict[str, Any]] = []
    for sample_date in sample_dates:
        block = df[df["date"].eq(sample_date) & df["raw_close"].notna()].copy()
        if block.empty:
            continue
        block["inverse_price"] = 1.0 / block["raw_close"]
        block["pandas_pct_rank"] = block["inverse_price"].rank(pct=True, method="average")
        n = block["inverse_price"].notna().sum()
        block["zero_one_rank"] = (block["inverse_price"].rank(method="average") - 1.0) / (n - 1.0)
        block["rank_convention_abs_delta"] = (block["pandas_pct_rank"] - block["zero_one_rank"]).abs()
        sample = pd.concat([block.nsmallest(3, "raw_close"), block.nlargest(3, "raw_close")])
        for row in sample.itertuples(index=False):
            rows.append(
                {
                    "date": sample_date.date().isoformat(),
                    "security_id": row.security_id,
                    "ticker": row.ticker,
                    "price": float(row.raw_close),
                    "inverse_price": float(row.inverse_price),
                    "pandas_pct_rank": float(row.pandas_pct_rank),
                    "zero_one_rank": float(row.zero_one_rank),
                    "rank_convention_abs_delta": float(row.rank_convention_abs_delta),
                    "rank_direction": "lower price -> higher inverse price -> higher VALUE score",
                    "daily_max_rank_convention_abs_delta": float(block["rank_convention_abs_delta"].max()),
                }
            )
    return pd.DataFrame(rows)


def base_distribution_audit(signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    cols = ["value_score", "reversal_z", "base"]
    daily = signals.groupby("date")[cols].agg(["mean", "std", "median"])
    for column in cols:
        rows.append(
            {
                "feature": column,
                "overall_mean": float(signals[column].mean()),
                "overall_std": float(signals[column].std(ddof=1)),
                "overall_median": float(signals[column].median()),
                "median_daily_mean": float(daily[(column, "mean")].median()),
                "median_daily_std": float(daily[(column, "std")].median()),
                "median_daily_median": float(daily[(column, "median")].median()),
                "note": "VALUE percentile is mixed with REVERSAL z-score before post-regime EDGE z-score",
            }
        )
    return pd.DataFrame(rows)


def edge_standardization_audit(panel: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    base_run: VariantRun | None = None
    features = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    for mode in [
        "active_edge_only",
        "base_z_then_regime",
        "edge_z_include_inactive_zero",
        "active_base_only",
    ]:
        valid = mode in {"active_edge_only", "active_base_only"}
        run = _run_variant_from_features(
            panel,
            cfg,
            features=features,
            variant_id=f"EDGE_STD_{mode}",
            edge_mode=mode,  # type: ignore[arg-type]
            return_offset_sessions=1,
            is_valid_strategy=valid,
            diagnostic_label="" if valid else "INTERPRETATION DIAGNOSTIC - NOT PRODUCTION",
        )
        if base_run is None:
            base_run = run
            signal_corr = 1.0
        else:
            joined = run.signals[["date", "security_id", "z_edge"]].merge(
                base_run.signals[["date", "security_id", "z_edge"]].rename(columns={"z_edge": "base_z_edge"}),
                on=["date", "security_id"],
                how="inner",
            )
            signal_corr = float(joined["z_edge"].corr(joined["base_z_edge"]))
        selected = _daily_for_periods(run, SELECTED_TEST_DATES)
        signal_dates = pd.Series(
            pd.to_datetime(
                run.aligned.loc[
                    _selected_test_mask(run.aligned["return_date"]),
                    "date",
                ].drop_duplicates()
            )
        )
        rows.append(
            {
                "edge_standardization_mode": mode,
                "is_valid_strategy": valid,
                "diagnostic_label": run.diagnostic_label,
                "production_order_confirmed": mode == "active_edge_only",
                "signal_correlation_with_active_edge_only": signal_corr,
                "selected_window_sharpe": performance_metrics(selected["net_return"])["sharpe"],
                **_feature_stats_for_dates(run.signals, signal_dates),
                **_position_stats_for_dates(run.weights, signal_dates),
            }
        )
    return pd.DataFrame(rows)


def position_cardinality(fingerprints: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "window",
        "mean_eligible_names_day",
        "regime_active_stock_day_fraction",
        "avg_active_stocks_day",
        "avg_nonzero_edge_names_day",
        "avg_positioned_names",
        "avg_long_positions",
        "avg_short_positions",
    ]
    out = fingerprints[cols].copy()
    out["paper_active_stock_day_fraction"] = 0.35
    out["paper_avg_long_positions"] = 187
    out["paper_avg_short_positions"] = 189
    out["paper_implied_position_count"] = 376
    out["paper_35pct_of_500_names"] = 175
    out["contradiction_note"] = (
        "If inactive EDGE names are not traded, 35% active stock-days over ~500 names implies "
        "~175 active names/day, not ~376 long+short positions/day."
    )
    return out


def exposure_regime_audit(run: VariantRun) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for window in WINDOWS:
        daily = _window_daily(run, window)
        signal_dates = _signal_dates_for_return_window(run, window)
        if daily.empty or signal_dates.empty:
            continue
        features = run.signals[run.signals["date"].isin(set(signal_dates))].copy()
        eligible = features["eligible"].fillna(False).astype(bool) & features["raw_close"].notna()
        features["eligible_for_signal"] = eligible
        features["active_regime"] = eligible & features["regime"].eq(1.0) & features["z_edge"].notna()
        active = features.groupby("date").agg(
            active_names=("active_regime", "sum"),
            eligible_names=("eligible_for_signal", "sum"),
        )
        active["active_fraction"] = active["active_names"] / active["eligible_names"]
        mapping = run.aligned[["date", "return_date"]].drop_duplicates()
        active = active.reset_index().merge(mapping, on="date", how="left").drop_duplicates("return_date")
        joined = daily.merge(active[["return_date", "active_fraction"]], on="return_date", how="left")
        rows.append(
            {
                "variant_id": run.variant_id,
                "window": window["window"],
                "avg_active_fraction": float(joined["active_fraction"].mean()),
                "avg_gross_exposure": float(joined["gross_exposure"].mean()),
                "gross_exposure_active_fraction_correlation": float(
                    joined["gross_exposure"].corr(joined["active_fraction"])
                ),
                "normalization_note": (
                    "Long and short sides are normalized to +/-50%, so gross exposure stays near "
                    "100% whenever both sides exist even if active names shrink."
                ),
            }
        )
    return pd.DataFrame(rows)


def winning_day_audit(fingerprints: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "window",
        "winning_day_fraction",
        "median_daily_return",
        "mean_daily_return",
        "daily_std",
        "paper_winning_day_fraction",
        "paper_median_daily_return",
    ]
    out = fingerprints.copy()
    out["paper_winning_day_fraction"] = 0.67
    out["paper_median_daily_return"] = 0.0063
    return out[cols]


def turnover_definition_audit(fingerprints: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "variant_id",
        "window",
        "avg_one_way_turnover",
        "avg_gross_traded_notional",
    ]
    out = fingerprints[cols].copy()
    out["paper_reported_daily_turnover"] = 0.42
    out["engine_turnover_convention"] = "0.5 * sum(abs(w_t - w_t_minus_1))"
    out["gross_traded_convention"] = "sum(abs(w_t - w_t_minus_1))"
    out["paper_possible_convention_match"] = np.where(
        (out["avg_gross_traded_notional"] - 0.42).abs() < (out["avg_one_way_turnover"] - 0.42).abs(),
        "gross_traded_notional",
        "one_way_turnover",
    )
    return out


def _phase3_panel_from_manifest(phase3_dir: Path) -> pd.DataFrame:
    manifest = json.loads((phase3_dir / "input_manifest.json").read_text(encoding="utf-8"))
    return pd.read_parquet(manifest["input_artifacts"]["phase3_input_panel"])


def _classification(summary: dict[str, Any], interpretations: pd.DataFrame) -> str:
    paper_like = 10.0
    best_invalid = interpretations.loc[~interpretations["is_valid_strategy"], "selected_window_sharpe"].max()
    best_valid = interpretations.loc[interpretations["is_valid_strategy"], "selected_window_sharpe"].max()
    if np.isfinite(best_invalid) and best_invalid >= paper_like:
        return "RETURN_ALIGNMENT_LEAKAGE"
    if np.isfinite(best_valid) and best_valid >= paper_like:
        return "DATA_SOURCE_DIFFERENCE"
    if summary.get("position_count_contradiction", {}).get("contradiction_confirmed"):
        return "MULTIPLE: DATA_SOURCE_DIFFERENCE + PAPER_INTERNAL_INCONSISTENCY"
    return "DATA_SOURCE_DIFFERENCE"


def write_phase3b_reports(
    *,
    phase3_dir: Path = Path("reports/generated/phase3"),
    strategy: StrategyConfig,
    out_dir: Path = Path("reports/generated/phase3b"),
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    baseline = baseline_reproduction_report(phase3_dir)
    if not baseline["matched"]:
        raise ValueError(f"Phase 3 baseline does not reproduce within tolerance: {baseline}")
    manifest_report = verify_phase3_manifest(phase3_dir)
    panel = _phase3_panel_from_manifest(phase3_dir)
    r0_exp = load_experiment("experiments/R0_phase3_paper_like_reproduction.yaml")
    panel = panel.copy()
    panel["raw_close"] = panel["yahoo_close"]
    panel["total_return"] = panel["yahoo_adj_close_pct_change"]
    diagnostic_end = pd.Timestamp("2021-02-01")
    panel = panel[pd.to_datetime(panel["date"]) <= diagnostic_end].copy()

    legacy_r0 = _run_legacy_phase3_r0(panel, strategy, r0_exp)
    paper_spec_r0 = _run_variant(
        panel,
        strategy,
        variant_id="R0_PAPER_SPEC_EXCLUDE_CURRENT",
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
        return_offset_sessions=1,
        is_valid_strategy=True,
        diagnostic_label="PAPER EQUATION FEATURE ALIGNMENT; R0 STILL USES PAPER-LIKE LAG 1",
    )
    invalid_regime = _run_variant(
        panel,
        strategy,
        variant_id="R_INVALID_REGIME_INCLUDE_CURRENT",
        regime_mode="include_current",
        reversal_mode="prior_compounded",
        return_offset_sessions=1,
        is_valid_strategy=False,
        diagnostic_label=INVALID_REGIME_LABEL,
    )
    invalid_reversal = _run_variant(
        panel,
        strategy,
        variant_id="R_INVALID_REVERSAL_INCLUDE_CURRENT",
        regime_mode="exclude_current",
        reversal_mode="include_current_compounded",
        return_offset_sessions=1,
        is_valid_strategy=False,
        diagnostic_label=INVALID_REVERSAL_LABEL,
    )

    fingerprint_runs = [legacy_r0, paper_spec_r0, invalid_regime, invalid_reversal]
    fingerprints = pd.concat([paper_fingerprint_rows(run) for run in fingerprint_runs], ignore_index=True)
    matrix = training_test_matrix(fingerprints)
    regime_window = regime_window_audit(panel, strategy)
    regime_scan = regime_alignment_scan(panel, strategy)
    return_scan = return_alignment_scan(legacy_r0.weights, panel)
    lag0 = invalid_lag0_report(legacy_r0.weights, panel)
    reversal_audit = reversal_definition_audit(panel, strategy)
    value_audit = value_rank_audit(panel)
    base_audit = base_distribution_audit(paper_spec_r0.signals)
    edge_audit = edge_standardization_audit(panel, strategy)
    position_audit = position_cardinality(fingerprints)
    exposure_audit = exposure_regime_audit(paper_spec_r0)
    winning_audit = winning_day_audit(fingerprints)
    turnover_audit = turnover_definition_audit(fingerprints)

    selected_rows: list[dict[str, Any]] = []
    for run in fingerprint_runs:
        selected = _daily_for_periods(run, SELECTED_TEST_DATES)
        row = {
            "variant_id": run.variant_id,
            "axis": "core_variant",
            "is_valid_strategy": run.is_valid_strategy,
            "diagnostic_label": run.diagnostic_label,
            **_summarize_returns(selected),
        }
        row["selected_window_sharpe"] = row["sharpe"]
        selected_rows.append(
            row
        )
    best_return_invalid = return_scan.loc[return_scan["diagnostic_label"].ne("")].sort_values(
        "selected_window_sharpe",
        ascending=False,
    )
    if len(best_return_invalid):
        row = best_return_invalid.iloc[0]
        selected_rows.append(
            {
                "variant_id": f"RETURN_ALIGNMENT_OFFSET_{int(row.return_offset_sessions):+d}",
                "axis": "return_alignment_scan",
                "is_valid_strategy": False,
                "diagnostic_label": row.diagnostic_label,
                "sharpe": row.selected_window_sharpe,
                "selected_window_sharpe": row.selected_window_sharpe,
                "annualized_return": np.nan,
                "annualized_volatility": np.nan,
                "max_drawdown": np.nan,
                "terminal_wealth": np.nan,
                "cumulative_return": np.nan,
                "mean_daily_return": row.mean_daily_return,
                "median_daily_return": np.nan,
                "winning_day_fraction": np.nan,
                "daily_std": np.nan,
                "avg_one_way_turnover": np.nan,
                "avg_gross_traded_notional": np.nan,
            }
        )
    interpretations = pd.DataFrame(selected_rows)

    contradiction = {
        "paper_active_stock_day_fraction": 0.35,
        "paper_long_positions": 187,
        "paper_short_positions": 189,
        "paper_implied_positions": 376,
        "paper_35pct_of_500_names": 175,
        "contradiction_confirmed": True,
        "basis": (
            "Under stated non-zero EDGE-only trading, 35% active stock-days over roughly 500 "
            "eligible names implies about 175 active names/day, not about 376 long+short positions."
        ),
    }

    summary: dict[str, Any] = {
        "phase": PHASE3B_LABEL,
        "survivorship_bias_label": SURVIVORSHIP_LABEL,
        "baseline_reproduction": baseline,
        "manifest_verification": manifest_report,
        "diagnostic_panel": {
            "source": "verified Phase 3 input panel",
            "end_date_inclusive": diagnostic_end.date().isoformat(),
            "rows": len(panel),
            "securities": int(panel["security_id"].nunique()),
            "reason": "Phase 3B windows and +/-10-session diagnostics end after the 2020 paper window.",
        },
        "production_phase3_legacy_finding": {
            "regime_current_return_excluded": False,
            "reversal_current_return_excluded": False,
            "detail": (
                "Phase 3 exact R0 used the existing compute_signals path, whose rolling regime and "
                "reversal windows end on signal date t. Phase 3B preserves that exact baseline as "
                "R0_PHASE3_LEGACY_CURRENT_DAY_FEATURES and separately reports paper-spec variants."
            ),
        },
        "position_count_contradiction": contradiction,
        "classification": _classification({"position_count_contradiction": contradiction}, interpretations),
        "best_invalid_return_alignment": (
            best_return_invalid.head(1).to_dict(orient="records")[0] if len(best_return_invalid) else None
        ),
        "best_invalid_regime_alignment": (
            regime_scan.loc[regime_scan["is_valid_strategy"].eq(False)]
            .sort_values("selected_window_sharpe", ascending=False)
            .head(1)
            .to_dict(orient="records")[0]
        ),
        "core_interpretations": interpretations.to_dict(orient="records"),
        "confirmations": {
            "no_parameter_optimization": True,
            "no_pit_membership_work": True,
            "no_full_yahoo_redownload": True,
            "invalid_diagnostics_labeled": True,
            "production_experiment_specs_unchanged": True,
            "no_bulk_market_data_committed": True,
        },
    }

    _write_json(out_dir / "baseline_reproduction.json", baseline)
    _write_table(out_dir / "paper_fingerprints.csv", fingerprints)
    _write_table(out_dir / "training_test_matrix.csv", matrix)
    _write_table(out_dir / "regime_window_audit.csv", regime_window)
    _write_table(out_dir / "regime_alignment_scan.csv", regime_scan)
    _write_table(out_dir / "return_alignment_scan.csv", return_scan)
    _write_json(out_dir / "invalid_lag0.json", lag0)
    _write_table(out_dir / "reversal_definition_audit.csv", reversal_audit)
    _write_table(out_dir / "value_rank_audit.csv", value_audit)
    _write_table(out_dir / "base_distribution_audit.csv", base_audit)
    _write_table(out_dir / "edge_standardization_audit.csv", edge_audit)
    _write_table(out_dir / "position_cardinality.csv", position_audit)
    _write_table(out_dir / "exposure_regime_audit.csv", exposure_audit)
    _write_table(out_dir / "winning_day_audit.csv", winning_audit)
    _write_table(out_dir / "turnover_definition_audit.csv", turnover_audit)
    _write_table(out_dir / "forensic_interpretations.csv", interpretations)
    _write_json(out_dir / "phase3b_summary.json", summary)
    return summary
