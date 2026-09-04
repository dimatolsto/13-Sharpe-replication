from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from .audit import audit_experiment
from .backtest import BacktestResult, ReturnSeriesResult
from .config import ExperimentConfig, StrategyConfig, load_experiment
from .data.sp500_events import yahoo_symbol_from_reported
from .data.validation import reconstruct_nominal_close_from_splits
from .metrics import TRADING_DAYS, block_bootstrap_sharpe_ci, performance_metrics
from .portfolio import align_weights_to_returns, construct_signal_weights, daily_portfolio_returns
from .signals import compute_cross_sectional_signals, compute_signals, compute_time_series_features

SURVIVORSHIP_LABEL = "SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE"
PHASE3_TRANSFORM_VERSION = "phase3-paper-attribution-v1"
PAPER_PUBLICATION_DATE = pd.Timestamp("2025-11-18")
BOOTSTRAP_BLOCK_LENGTH = 10
BOOTSTRAP_REPETITIONS = 2000
BOOTSTRAP_SEED = 13

PAPER_BENCHMARKS = {
    "2010": {"paper_sharpe": 16.89, "paper_return": 2.067, "paper_scale": 0.841},
    "2015": {"paper_sharpe": 22.87, "paper_return": 2.072, "paper_scale": 1.327},
    "2020": {"paper_sharpe": 5.11, "paper_return": 0.620, "paper_scale": 1.569},
}

PHASE3_EXPERIMENT_PATHS = [
    Path("experiments/R0_phase3_paper_like_reproduction.yaml"),
    Path("experiments/R1_phase3_timing_fix.yaml"),
    Path("experiments/R2_phase3_nominal_price_fix.yaml"),
    Path("experiments/R3_phase3_continuous_survivorship_biased.yaml"),
]

ValuePriceColumn = Literal["yahoo_close", "reconstructed_nominal_close"]


@dataclass
class PreparedPanel:
    panel: pd.DataFrame
    anchor: pd.DataFrame
    summary: dict[str, Any]


@dataclass
class Phase3ExperimentRun:
    experiment: ExperimentConfig
    result: BacktestResult
    signal_diagnostics: pd.DataFrame
    value_scores: pd.DataFrame
    nonzero_weights: pd.DataFrame
    ledger_reconciliation: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        v = float(value)
        return None if math.isnan(v) else v
    if isinstance(value, np.ndarray):
        return value.tolist()
    if pd.isna(value):
        return None
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        raise ValueError(f"Unsupported table output extension: {path}")


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty() -> bool | None:
    try:
        return bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input extension: {path}")


def _phase3_symbol(value: Any) -> str:
    symbol = yahoo_symbol_from_reported(value)
    compact_class_overrides = {
        "BFB": "BF-B",
        "BRKB": "BRK-B",
    }
    return compact_class_overrides.get(symbol, symbol)


def _name_key(value: Any) -> str:
    text = str(value or "").upper()
    out = "".join(ch if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:48] or "UNKNOWN"


def _date_distance_days(snapshot_date: pd.Timestamp) -> int:
    return int(abs((snapshot_date.normalize() - PAPER_PUBLICATION_DATE).days))


def choose_phase3_anchor(
    snapshot_rows: pd.DataFrame | None,
    fallback_anchor: pd.DataFrame | None,
) -> pd.DataFrame:
    """Choose one frozen current-constituent paper-like anchor from existing cached artifacts."""

    selected: pd.DataFrame | None = None
    selected_reason = ""
    if snapshot_rows is not None and len(snapshot_rows):
        rows = snapshot_rows.copy()
        rows["snapshot_date"] = pd.to_datetime(rows["snapshot_date"], errors="coerce").dt.normalize()
        rows = rows[rows["snapshot_date"].notna()].copy()
        rows["distance_days"] = rows["snapshot_date"].map(_date_distance_days)

        wiki = rows[rows["source_id"].astype(str).str.contains("wikipedia", case=False, na=False)]
        if len(wiki) and wiki["distance_days"].min() <= 60:
            source_id, snap_date = (
                wiki.sort_values(["distance_days", "snapshot_date"])
                .loc[:, ["source_id", "snapshot_date"]]
                .iloc[0]
            )
            selected = rows[(rows["source_id"].eq(source_id)) & (rows["snapshot_date"].eq(snap_date))]
            selected_reason = "nearest_cached_wikipedia_revision_within_60_days"
        if selected is None:
            source_id, snap_date = (
                rows.sort_values(["distance_days", "snapshot_date"])
                .loc[:, ["source_id", "snapshot_date"]]
                .iloc[0]
            )
            selected = rows[(rows["source_id"].eq(source_id)) & (rows["snapshot_date"].eq(snap_date))]
            selected_reason = "nearest_cached_secondary_snapshot"

    if selected is None:
        if fallback_anchor is None or fallback_anchor.empty:
            raise ValueError("No Phase 3 anchor source is available")
        selected = fallback_anchor.copy()
        if "snapshot_date" not in selected.columns:
            selected["snapshot_date"] = pd.Timestamp("2026-08-11")
        selected["source_id"] = "wikipedia_current_anchor_2026_08_11"
        selected["source_name"] = "Wikipedia current constituents anchor"
        selected["source_tier"] = "SECONDARY_SNAPSHOT"
        selected["source_security_name"] = selected.get("security", selected.get("source_security_name", ""))
        selected["source_ticker"] = selected.get("symbol", selected.get("source_symbol", selected.get("ticker", "")))
        selected_reason = "fallback_current_wikipedia_anchor"

    required = {"source_ticker", "source_security_name", "snapshot_date", "source_id", "source_tier"}
    missing = required - set(selected.columns)
    if missing:
        raise ValueError(f"Anchor source missing columns: {sorted(missing)}")

    anchor = selected.copy()
    anchor["snapshot_date"] = pd.to_datetime(anchor["snapshot_date"]).dt.normalize()
    anchor["source_ticker"] = anchor["source_ticker"].astype(str).str.strip()
    anchor["provider_symbol"] = anchor["source_ticker"].map(_phase3_symbol)
    anchor["phase3_security_id"] = "phase3:" + anchor["provider_symbol"]
    anchor["paper_publication_date"] = PAPER_PUBLICATION_DATE.date().isoformat()
    anchor["distance_from_publication_days"] = anchor["snapshot_date"].map(_date_distance_days)
    anchor["anchor_selection_reason"] = selected_reason
    anchor["survivorship_bias_label"] = SURVIVORSHIP_LABEL
    anchor["known_limitation"] = (
        "Fixed current-constituent snapshot used historically; ETF/secondary snapshot semantics "
        "and provider-symbol history are not point-in-time membership evidence."
    )
    anchor["anchor_duplicate_provider_symbol"] = anchor.duplicated("provider_symbol", keep=False)

    anchor = anchor.sort_values(["provider_symbol", "source_security_name"]).drop_duplicates(
        "provider_symbol", keep="first"
    )
    keep = [
        "phase3_security_id",
        "provider_symbol",
        "source_ticker",
        "source_security_name",
        "source_id",
        "source_name",
        "source_tier",
        "snapshot_date",
        "paper_publication_date",
        "distance_from_publication_days",
        "isin",
        "cik",
        "figi",
        "other_external_id",
        "mapped_security_id",
        "mapping_status",
        "mapping_evidence",
        "source_url",
        "anchor_selection_reason",
        "anchor_duplicate_provider_symbol",
        "survivorship_bias_label",
        "known_limitation",
    ]
    for column in keep:
        if column not in anchor.columns:
            anchor[column] = pd.NA
    return anchor[keep].reset_index(drop=True)


def _source_panel_for_anchor(source_panel: pd.DataFrame, anchor: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    panel = source_panel.copy()
    if "source_symbol" not in panel.columns:
        panel["source_symbol"] = panel["ticker"]
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["provider_symbol"] = panel["source_symbol"].astype(str).map(_phase3_symbol)
    panel = panel[panel["provider_symbol"].isin(set(anchor["provider_symbol"]))].copy()

    for column in ["yahoo_close", "adjusted_close", "split_factor", "dividend_cash"]:
        if column in panel.columns:
            panel[column] = pd.to_numeric(panel[column], errors="coerce")

    before = len(panel)
    panel["_complete_price_row"] = panel["yahoo_close"].notna() & panel["adjusted_close"].notna()
    panel = panel.sort_values(
        ["provider_symbol", "date", "_complete_price_row", "security_id"],
        ascending=[True, True, False, True],
    )
    duplicate_rows = int(panel.duplicated(["provider_symbol", "date"]).sum())
    duplicate_symbols = int(
        panel.loc[panel.duplicated(["provider_symbol", "date"], keep=False), "provider_symbol"].nunique()
    )
    panel = panel.drop_duplicates(["provider_symbol", "date"], keep="first").drop(columns=["_complete_price_row"])
    summary = {
        "source_rows_for_anchor_before_symbol_date_dedupe": before,
        "source_rows_for_anchor_after_symbol_date_dedupe": len(panel),
        "duplicate_source_symbol_date_rows_removed": duplicate_rows,
        "duplicate_source_symbols_affected": duplicate_symbols,
    }
    return panel, summary


def _split_actions_from_panel(panel: pd.DataFrame) -> pd.DataFrame:
    if "split_factor" not in panel.columns:
        return pd.DataFrame(
            columns=["security_id", "date", "event_type", "split_factor", "dividend_cash", "source", "ticker"]
        )
    actions = panel[pd.to_numeric(panel["split_factor"], errors="coerce").fillna(0.0) > 0].copy()
    if actions.empty:
        return pd.DataFrame(
            columns=["security_id", "date", "event_type", "split_factor", "dividend_cash", "source", "ticker"]
        )
    actions["event_type"] = "split"
    actions["source"] = "phase3_yahoo_source_panel_split_factor"
    return actions[
        ["security_id", "date", "event_type", "split_factor", "dividend_cash", "source", "ticker"]
    ].reset_index(drop=True)


def prepare_phase3_panel(source_panel: pd.DataFrame, anchor: pd.DataFrame) -> PreparedPanel:
    """Build the frozen Phase 3 source panel without filling pre-listing or missing observations."""

    filtered, dedupe_summary = _source_panel_for_anchor(source_panel, anchor)
    id_map = anchor[
        [
            "phase3_security_id",
            "provider_symbol",
            "source_ticker",
            "source_security_name",
            "isin",
            "mapping_status",
        ]
    ].copy()
    panel = filtered.merge(id_map, on="provider_symbol", how="inner", validate="many_to_one")
    panel = panel.rename(columns={"adjusted_close": "yahoo_adj_close"})
    panel["security_id"] = panel["phase3_security_id"]
    panel["ticker"] = panel["source_ticker"]
    panel["raw_close"] = panel["yahoo_close"]
    panel["total_return"] = panel.groupby("security_id", sort=False)["yahoo_adj_close"].pct_change(fill_method=None)
    panel["yahoo_adj_close_pct_change"] = panel["total_return"]
    panel["raw_close_source"] = "phase3_value_price_placeholder"
    panel["total_return_source"] = "yahoo_adj_close_pct_change"
    panel["survivorship_bias_label"] = SURVIVORSHIP_LABEL

    for column in ["raw_close", "yahoo_close", "yahoo_adj_close", "total_return"]:
        panel[column] = pd.to_numeric(panel[column], errors="coerce")

    actions = _split_actions_from_panel(panel)
    reconstructed = reconstruct_nominal_close_from_splits(
        panel,
        actions,
        close_column="yahoo_close",
        output_column="reconstructed_nominal_close",
    )
    reconstructed_values = reconstructed[
        ["date", "security_id", "reconstructed_nominal_close", "split_adjustment_multiplier"]
    ]
    panel = panel.drop(
        columns=["reconstructed_nominal_close", "split_adjustment_multiplier"],
        errors="ignore",
    ).merge(
        reconstructed_values,
        on=["date", "security_id"],
        how="left",
        validate="one_to_one",
    )

    first_dates = panel.groupby("security_id")["date"].min().rename("first_market_data_date")
    last_dates = panel.groupby("security_id")["date"].max().rename("last_market_data_date")
    anchor = anchor.merge(first_dates, left_on="phase3_security_id", right_index=True, how="left")
    anchor = anchor.merge(last_dates, left_on="phase3_security_id", right_index=True, how="left")
    anchor["market_data_available"] = anchor["first_market_data_date"].notna()

    panel = panel.sort_values(["security_id", "date"]).reset_index(drop=True)
    columns = [
        "date",
        "security_id",
        "ticker",
        "raw_close",
        "total_return",
        "yahoo_close",
        "yahoo_adj_close",
        "yahoo_adj_close_pct_change",
        "reconstructed_nominal_close",
        "split_adjustment_multiplier",
        "open",
        "high",
        "low",
        "volume",
        "dividend_cash",
        "split_factor",
        "source_symbol",
        "provider_symbol",
        "source",
        "source_security_name",
        "isin",
        "mapping_status",
        "raw_close_source",
        "total_return_source",
        "survivorship_bias_label",
    ]
    for column in columns:
        if column not in panel.columns:
            panel[column] = pd.NA

    missing_anchor = int((~anchor["market_data_available"]).sum())
    summary = {
        **dedupe_summary,
        "anchor_constituents": len(anchor),
        "anchor_constituents_with_market_data": int(anchor["market_data_available"].sum()),
        "anchor_constituents_without_market_data": missing_anchor,
        "market_data_first_date": panel["date"].min().date().isoformat() if len(panel) else None,
        "market_data_last_date": panel["date"].max().date().isoformat() if len(panel) else None,
        "panel_rows": len(panel),
        "panel_security_ids": int(panel["security_id"].nunique()),
        "yahoo_close_missing_rows": int(panel["yahoo_close"].isna().sum()),
        "yahoo_adj_close_missing_rows": int(panel["yahoo_adj_close"].isna().sum()),
        "total_return_missing_rows": int(panel["total_return"].isna().sum()),
        "reconstructed_nominal_transform_version": PHASE3_TRANSFORM_VERSION,
    }
    return PreparedPanel(panel=panel[columns], anchor=anchor, summary=summary)


def panel_for_value_price(panel: pd.DataFrame, value_price_column: ValuePriceColumn) -> pd.DataFrame:
    if value_price_column not in {"yahoo_close", "reconstructed_nominal_close"}:
        raise ValueError(f"Unsupported Phase 3 value price column: {value_price_column}")
    out = panel.copy()
    out["raw_close"] = pd.to_numeric(out[value_price_column], errors="coerce")
    out["raw_close_source"] = value_price_column
    out["total_return"] = pd.to_numeric(out["yahoo_adj_close_pct_change"], errors="coerce")
    out["total_return_source"] = "yahoo_adj_close_pct_change"
    return out


def _filter_aligned_to_experiment(aligned: pd.DataFrame, exp: ExperimentConfig) -> pd.DataFrame:
    df = aligned
    if exp.period_mode == "continuous":
        if exp.start_date:
            df = df[df["return_date"] >= pd.Timestamp(exp.start_date)]
        if exp.end_date:
            df = df[df["return_date"] <= pd.Timestamp(exp.end_date)]
        return df
    if exp.period_mode == "paper_three_windows":
        mask = pd.Series(False, index=df.index)
        if exp.scaling and exp.scaling.mode == "paper_walk_forward":
            for window in exp.scaling.windows:
                mask |= (df["return_date"] >= pd.Timestamp(window.test_start)) & (
                    df["return_date"] < pd.Timestamp(window.test_end)
                )
        elif exp.paper_test_windows:
            for window in exp.paper_test_windows:
                mask |= df["return_date"].between(pd.Timestamp(window.start), pd.Timestamp(window.end))
        else:
            raise ValueError("paper_three_windows requires paper windows")
        return df[mask]
    raise ValueError(f"Unsupported period_mode={exp.period_mode}")


def _window_mask(dates: pd.Series, start: date, end: date, *, half_open: bool = True) -> pd.Series:
    lo = pd.Timestamp(start)
    hi = pd.Timestamp(end)
    if half_open:
        return (dates >= lo) & (dates < hi)
    return dates.between(lo, hi)


def _build_return_series(
    aligned: pd.DataFrame,
    strategy: StrategyConfig,
    exp: ExperimentConfig,
    *,
    audit: bool,
) -> ReturnSeriesResult:
    ledger, daily = daily_portfolio_returns(aligned, exp.transaction_cost_bp)
    metrics = performance_metrics(daily["net_return"], strategy.risk_free_rate)
    ci = block_bootstrap_sharpe_ci(
        daily["net_return"],
        block_size=BOOTSTRAP_BLOCK_LENGTH,
        n_boot=BOOTSTRAP_REPETITIONS,
        seed=BOOTSTRAP_SEED,
    )
    yearly = _yearly_metrics(daily)
    report = audit_experiment(exp, ledger, daily) if audit else None
    return ReturnSeriesResult(
        ledger=ledger,
        daily=daily,
        yearly=yearly,
        metrics=metrics,
        sharpe_ci_95=ci,
        audit=report,
    )


def _paper_scale_details(training_returns: pd.Series, strategy: StrategyConfig) -> dict[str, float]:
    from .metrics import paper_scale_details

    return paper_scale_details(
        training_returns=training_returns,
        vol_target=strategy.annual_vol_target,
        max_dd_target=strategy.max_drawdown_target,
    )


def _apply_paper_scaling(
    full_aligned: pd.DataFrame,
    full_unscaled_daily: pd.DataFrame,
    strategy: StrategyConfig,
    exp: ExperimentConfig,
) -> tuple[ReturnSeriesResult | None, list[dict[str, Any]]]:
    scaling = exp.scaling
    if scaling is None or scaling.mode == "none":
        return None, []
    if scaling.mode != "paper_walk_forward":
        raise ValueError(f"Unsupported Phase 3 scaling mode: {scaling.mode}")

    parts: list[pd.DataFrame] = []
    reports: list[dict[str, Any]] = []
    for window in scaling.windows:
        training = full_unscaled_daily.loc[
            _window_mask(full_unscaled_daily["return_date"], window.train_start, window.train_end),
            "net_return",
        ]
        if training.empty:
            raise ValueError(f"Missing training returns for scaling window {window.train_start}")
        details = _paper_scale_details(training, strategy)
        report = {
            "training_start": window.train_start.isoformat(),
            "training_end": window.train_end.isoformat(),
            "test_start": window.test_start.isoformat(),
            "test_end": window.test_end.isoformat(),
            **details,
        }
        reports.append(report)

        test_mask = _window_mask(full_aligned["return_date"], window.test_start, window.test_end)
        scaled = full_aligned.loc[test_mask].copy()
        scaled["unscaled_signal_weight"] = scaled["signal_weight"]
        scaled["scale_factor"] = report["scale_factor"]
        scaled["signal_weight"] = scaled["signal_weight"] * report["scale_factor"]
        parts.append(scaled)

    scaled_aligned = pd.concat(parts, ignore_index=True) if parts else full_aligned.iloc[0:0].copy()
    return _build_return_series(scaled_aligned, strategy, exp, audit=False), reports


def _yearly_metrics(daily: pd.DataFrame, return_col: str = "net_return") -> pd.DataFrame:
    df = daily.copy()
    df["year"] = pd.to_datetime(df["return_date"]).dt.year
    rows: list[dict[str, Any]] = []
    for year, block in df.groupby("year"):
        metrics = performance_metrics(block[return_col])
        rows.append(
            {
                "year": int(year),
                "observations": len(block),
                "sharpe": metrics["sharpe"],
                "cagr": metrics["cagr"],
                "annualized_return": metrics["cagr"],
                "annualized_volatility": metrics["ann_vol"],
                "max_drawdown": metrics["max_drawdown"],
                "cumulative_return": metrics["wealth"] - 1.0,
                "terminal_wealth": metrics["wealth"],
                "avg_turnover": float(block["turnover"].mean()),
                "avg_gross_exposure": float(block["gross_exposure"].mean()),
                "avg_net_exposure": float(block["net_exposure"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _date_windows(exp: ExperimentConfig) -> list[dict[str, Any]]:
    if exp.period_mode == "continuous":
        return [
            {
                "window": "2010-2024",
                "start": exp.start_date,
                "end": exp.end_date,
                "half_open": False,
            }
        ]
    if exp.scaling and exp.scaling.mode == "paper_walk_forward":
        return [
            {
                "window": str(w.test_start.year),
                "start": w.test_start,
                "end": w.test_end,
                "half_open": True,
            }
            for w in exp.scaling.windows
        ]
    if exp.paper_test_windows:
        return [
            {"window": str(w.start.year), "start": w.start, "end": w.end, "half_open": False}
            for w in exp.paper_test_windows
        ]
    return []


def _slice_daily(daily: pd.DataFrame, window: dict[str, Any]) -> pd.DataFrame:
    mask = _window_mask(
        daily["return_date"],
        window["start"],
        window["end"],
        half_open=bool(window["half_open"]),
    )
    return daily.loc[mask].copy()


def _summarize_daily(daily: pd.DataFrame, diagnostics: pd.DataFrame | None = None) -> dict[str, Any]:
    metrics = performance_metrics(daily["net_return"])
    gross_metrics = performance_metrics(daily["gross_return"])
    out = {
        "observations": len(daily),
        "sharpe": metrics["sharpe"],
        "cagr": metrics["cagr"],
        "annualized_return": metrics["cagr"],
        "annualized_volatility": metrics["ann_vol"],
        "max_drawdown": metrics["max_drawdown"],
        "cumulative_return": metrics["wealth"] - 1.0,
        "terminal_wealth": metrics["wealth"],
        "gross_annualized_return": gross_metrics["cagr"],
        "net_annualized_return": metrics["cagr"],
        "avg_gross_exposure": float(daily["gross_exposure"].mean()) if len(daily) else float("nan"),
        "avg_net_exposure": float(daily["net_exposure"].mean()) if len(daily) else float("nan"),
        "avg_turnover": float(daily["turnover"].mean()) if len(daily) else float("nan"),
        "transaction_cost_drag_annualized": float(daily["cost"].mean() * TRADING_DAYS) if len(daily) else float("nan"),
        "missing_returns": int(daily["missing_returns"].sum()) if "missing_returns" in daily else 0,
    }
    if diagnostics is not None and len(diagnostics):
        diag = diagnostics[diagnostics["return_date"].isin(set(daily["return_date"]))]
        for column in ["eligible_securities", "active_regime_securities", "long_count", "short_count"]:
            out[f"{column}_min"] = int(diag[column].min()) if len(diag) else 0
            out[f"{column}_median"] = float(diag[column].median()) if len(diag) else float("nan")
            out[f"{column}_max"] = int(diag[column].max()) if len(diag) else 0
    return out


def _signal_diagnostics(
    signals: pd.DataFrame,
    weights: pd.DataFrame,
    aligned: pd.DataFrame,
    exp: ExperimentConfig,
) -> pd.DataFrame:
    eligible = signals["eligible"].fillna(False).astype(bool) & signals["raw_close"].notna()
    counts = (
        signals.assign(
            eligible_for_value=eligible,
            active_regime=eligible & signals["regime"].eq(1.0) & signals["z_edge"].notna(),
        )
        .groupby("date", as_index=False)
        .agg(
            eligible_securities=("eligible_for_value", "sum"),
            active_regime_securities=("active_regime", "sum"),
        )
    )
    wcounts = (
        weights.groupby("date", as_index=False)
        .agg(
            long_count=("signal_weight", lambda s: int((s > 0).sum())),
            short_count=("signal_weight", lambda s: int((s < 0).sum())),
        )
    )
    mapping = aligned[["date", "return_date"]].drop_duplicates()
    out = mapping.merge(counts, on="date", how="left").merge(wcounts, on="date", how="left")
    out = out.rename(columns={"date": "signal_date"})
    out["experiment"] = exp.id
    out["timing_lag_sessions"] = exp.return_lag_sessions
    for column in ["eligible_securities", "active_regime_securities", "long_count", "short_count"]:
        out[column] = out[column].fillna(0).astype(int)
    return out.sort_values(["return_date", "signal_date"]).reset_index(drop=True)


def _run_experiment(
    panel: pd.DataFrame,
    strategy: StrategyConfig,
    exp: ExperimentConfig,
) -> tuple[BacktestResult, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    signals = compute_signals(panel, strategy, membership=None)
    weights = construct_signal_weights(signals, strategy)
    full_aligned = align_weights_to_returns(weights, panel, exp.return_lag_sessions)
    full_unscaled = _build_return_series(full_aligned, strategy, exp, audit=False)
    aligned = _filter_aligned_to_experiment(full_aligned, exp)
    unscaled = _build_return_series(aligned, strategy, exp, audit=True)
    scaled, scaling_windows = _apply_paper_scaling(full_aligned, full_unscaled.daily, strategy, exp)
    result = BacktestResult(
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
    return result, signals, weights, aligned


def augment_security_ledger(
    ledger: pd.DataFrame,
    panel: pd.DataFrame,
    exp: ExperimentConfig,
) -> pd.DataFrame:
    out = ledger.copy()
    calendar = pd.Index(pd.to_datetime(panel["date"]).drop_duplicates().sort_values())
    position = pd.Series(np.arange(len(calendar)), index=calendar)
    signal_pos = out["date"].map(position)
    weight_pos = signal_pos + max(exp.return_lag_sessions - 1, 0)
    valid_weight = weight_pos.notna() & (weight_pos >= 0) & (weight_pos < len(calendar))
    out["signal_date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["weight_date"] = pd.NaT
    out.loc[valid_weight, "weight_date"] = calendar.take(weight_pos.loc[valid_weight].astype(int).to_numpy()).to_numpy()
    out["earned_return_date"] = pd.to_datetime(out["return_date"]).dt.normalize()
    out["weight"] = out["signal_weight"]
    out["security_return"] = out["total_return"]
    out["gross_contribution"] = out["weight"] * out["security_return"]

    ticker_map = panel[["security_id", "ticker"]].drop_duplicates("security_id")
    out = out.drop(columns=["ticker"], errors="ignore").merge(ticker_map, on="security_id", how="left")

    wide = out.pivot_table(
        index="earned_return_date",
        columns="security_id",
        values="weight",
        aggfunc="sum",
        fill_value=0.0,
    ).sort_index()
    turnover = 0.5 * wide.diff().abs()
    if len(turnover):
        turnover.iloc[0] = 0.5 * wide.iloc[0].abs()
    turnover_long = (
        turnover.stack()
        .rename("turnover_contribution")
        .reset_index()
        .rename(columns={"level_1": "security_id"})
    )
    out = out.merge(turnover_long, on=["earned_return_date", "security_id"], how="left")
    out["turnover_contribution"] = out["turnover_contribution"].fillna(0.0)
    out["cost_contribution"] = out["turnover_contribution"] * exp.transaction_cost_bp * 1e-4
    out["net_contribution"] = out["gross_contribution"] - out["cost_contribution"]
    out["experiment"] = exp.id
    out["survivorship_bias_label"] = SURVIVORSHIP_LABEL
    return out[
        [
            "experiment",
            "date",
            "security_id",
            "ticker",
            "signal_date",
            "weight_date",
            "weight",
            "earned_return_date",
            "security_return",
            "gross_contribution",
            "turnover_contribution",
            "cost_contribution",
            "net_contribution",
            "signal_weight",
            "return_date",
            "total_return",
            "survivorship_bias_label",
            *[c for c in out.columns if c not in {
                "experiment",
                "date",
                "security_id",
                "ticker",
                "signal_date",
                "weight_date",
                "weight",
                "earned_return_date",
                "security_return",
                "gross_contribution",
                "turnover_contribution",
                "cost_contribution",
                "net_contribution",
                "signal_weight",
                "return_date",
                "total_return",
                "survivorship_bias_label",
            }],
        ]
    ]


def ledger_reconciliation(ledger: pd.DataFrame, daily: pd.DataFrame, exp_id: str, scaled: bool) -> dict[str, Any]:
    grouped = (
        ledger.groupby("earned_return_date", as_index=False)
        .agg(
            ledger_gross_return=("gross_contribution", "sum"),
            ledger_turnover=("turnover_contribution", "sum"),
            ledger_cost=("cost_contribution", "sum"),
            ledger_net_return=("net_contribution", "sum"),
        )
        .rename(columns={"earned_return_date": "return_date"})
    )
    joined = daily[["return_date", "gross_return", "turnover", "cost", "net_return"]].merge(
        grouped,
        on="return_date",
        how="left",
        validate="one_to_one",
    )
    joined["gross_error"] = joined["ledger_gross_return"] - joined["gross_return"]
    joined["turnover_error"] = joined["ledger_turnover"] - joined["turnover"]
    joined["cost_error"] = joined["ledger_cost"] - joined["cost"]
    joined["net_error"] = joined["ledger_net_return"] - joined["net_return"]
    return {
        "experiment": exp_id,
        "scaled": scaled,
        "max_abs_gross_error": float(joined["gross_error"].abs().max()) if len(joined) else 0.0,
        "max_abs_turnover_error": float(joined["turnover_error"].abs().max()) if len(joined) else 0.0,
        "max_abs_cost_error": float(joined["cost_error"].abs().max()) if len(joined) else 0.0,
        "max_abs_net_error": float(joined["net_error"].abs().max()) if len(joined) else 0.0,
        "rows": len(joined),
    }


def _window_result_rows(
    exp: ExperimentConfig,
    result: BacktestResult,
    diagnostics: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    series = [("unscaled", result.unscaled)]
    if result.scaled is not None:
        series.append(("paper_scaled", result.scaled))
    for label, ret in series:
        for window in _date_windows(exp):
            daily = _slice_daily(ret.daily, window)
            row = {
                "experiment": exp.id,
                "series": label,
                "window": window["window"],
                "survivorship_bias_label": SURVIVORSHIP_LABEL,
                "universe": "fixed_current_constituent_anchor",
                "value_price": exp.value_price_column,
                "total_return_source": exp.total_return_source,
                "timing_lag_sessions": exp.return_lag_sessions,
                "paper_scaling": label == "paper_scaled",
                **_summarize_daily(daily, diagnostics),
            }
            if label == "paper_scaled":
                scale = next(
                    (
                        s["scale_factor"]
                        for s in result.scaling_windows
                        if str(pd.Timestamp(s["test_start"]).year) == window["window"]
                    ),
                    float("nan"),
                )
                row["scale_factor"] = scale
            else:
                row["scale_factor"] = 1.0
            rows.append(row)
    return rows


def _combined_result_row(
    exp: ExperimentConfig,
    series_label: str,
    ret: ReturnSeriesResult,
    diagnostics: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "experiment": f"{exp.id} {series_label}".strip(),
        "experiment_id": exp.id,
        "series": series_label,
        "universe": SURVIVORSHIP_LABEL,
        "value_price": exp.value_price_column,
        "timing_lag_sessions": exp.return_lag_sessions,
        "scaling": series_label == "paper_scaled",
        **_summarize_daily(ret.daily, diagnostics),
    }


def _scaling_audit_rows(exp: ExperimentConfig, result: BacktestResult) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in result.scaling_windows:
        year = str(pd.Timestamp(item["test_start"]).year)
        paper_scale = PAPER_BENCHMARKS.get(year, {}).get("paper_scale")
        scale = item["scale_factor"]
        rows.append(
            {
                "experiment": exp.id,
                "window": year,
                "training_start": item["training_start"],
                "training_end": item["training_end"],
                "test_start": item["test_start"],
                "test_end": item["test_end"],
                "training_annualized_volatility": item["training_annualized_volatility"],
                "training_max_drawdown": item["training_max_drawdown"],
                "volatility_scale": item["volatility_scale"],
                "drawdown_scale": item["drawdown_scale"],
                "chosen_scale": scale,
                "paper_reported_scale": paper_scale,
                "scale_difference": None if paper_scale is None else scale - paper_scale,
            }
        )
    return rows


def _timing_audit(
    runs: dict[str, Phase3ExperimentRun],
    panels: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    sample_dates = [
        pd.Timestamp("2009-12-29"),
        pd.Timestamp("2009-12-30"),
        pd.Timestamp("2014-12-30"),
        pd.Timestamp("2019-12-30"),
        pd.Timestamp("2020-01-02"),
    ]
    for exp_id in ["R0", "R1", "R2"]:
        run = runs[exp_id]
        panel = panels[exp_id]
        calendar = pd.Index(pd.to_datetime(panel["date"]).drop_duplicates().sort_values())
        for signal_date in sample_dates:
            if signal_date not in calendar:
                continue
            signal_pos = int(calendar.get_loc(signal_date))
            return_pos = signal_pos + run.experiment.return_lag_sessions
            weight_pos = signal_pos + max(run.experiment.return_lag_sessions - 1, 0)
            if return_pos >= len(calendar):
                continue
            rows.append(
                {
                    "experiment": exp_id,
                    "signal_date": signal_date.date().isoformat(),
                    "close_used_for_signal": signal_date.date().isoformat(),
                    "weight_date": calendar[weight_pos].date().isoformat(),
                    "earned_return_date": calendar[return_pos].date().isoformat(),
                    "return_lag_sessions": run.experiment.return_lag_sessions,
                    "timing_interpretation": (
                        "paper_like_signal_close_t_earns_next_close_to_close"
                        if run.experiment.return_lag_sessions == 1
                        else "corrected_execute_close_t_plus_1_first_earns_t_plus_1_to_t_plus_2"
                    ),
                }
            )
    return pd.DataFrame(rows)


def _value_rank_change_summary(r1_scores: pd.DataFrame, r2_scores: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    left = r1_scores.rename(columns={"value_score": "r1_value_score"})
    right = r2_scores.rename(columns={"value_score": "r2_value_score"})
    joined = left.merge(right, on=["date", "security_id"], how="inner")
    joined = joined[joined["r1_value_score"].notna() & joined["r2_value_score"].notna()].copy()
    joined["abs_value_rank_delta"] = (joined["r2_value_score"] - joined["r1_value_score"]).abs()
    if joined.empty:
        summary = {
            "observations": 0,
            "mean_abs_rank_delta": float("nan"),
            "median_abs_rank_delta": float("nan"),
            "p95_abs_rank_delta": float("nan"),
            "share_abs_rank_delta_gt_5pct": float("nan"),
            "share_abs_rank_delta_gt_10pct": float("nan"),
        }
    else:
        summary = {
            "observations": len(joined),
            "mean_abs_rank_delta": float(joined["abs_value_rank_delta"].mean()),
            "median_abs_rank_delta": float(joined["abs_value_rank_delta"].median()),
            "p95_abs_rank_delta": float(joined["abs_value_rank_delta"].quantile(0.95)),
            "share_abs_rank_delta_gt_5pct": float((joined["abs_value_rank_delta"] > 0.05).mean()),
            "share_abs_rank_delta_gt_10pct": float((joined["abs_value_rank_delta"] > 0.10).mean()),
        }
    return joined, summary


def _portfolio_membership_change_summary(r1_weights: pd.DataFrame, r2_weights: pd.DataFrame) -> dict[str, Any]:
    def _side_sets(weights: pd.DataFrame, side: Literal["long", "short"]) -> pd.DataFrame:
        if side == "long":
            src = weights[weights["signal_weight"] > 0]
        else:
            src = weights[weights["signal_weight"] < 0]
        return src.groupby("date")["security_id"].agg(lambda x: frozenset(x)).rename(side)

    merged = pd.concat(
        [
            _side_sets(r1_weights, "long").rename("r1_long"),
            _side_sets(r2_weights, "long").rename("r2_long"),
            _side_sets(r1_weights, "short").rename("r1_short"),
            _side_sets(r2_weights, "short").rename("r2_short"),
        ],
        axis=1,
    ).dropna()

    def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
        union = len(a | b)
        return float(len(a & b) / union) if union else float("nan")

    if merged.empty:
        return {
            "dates": 0,
            "mean_long_jaccard": float("nan"),
            "mean_short_jaccard": float("nan"),
        }
    return {
        "dates": len(merged),
        "mean_long_jaccard": float(np.mean([_jaccard(a, b) for a, b in zip(merged.r1_long, merged.r2_long, strict=False)])),
        "mean_short_jaccard": float(
            np.mean([_jaccard(a, b) for a, b in zip(merged.r1_short, merged.r2_short, strict=False)])
        ),
    }


def _paper_comparison_rows(paper_window_results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for window, paper in PAPER_BENCHMARKS.items():
        row: dict[str, Any] = {
            "window": window,
            **paper,
        }
        for exp_id in ["R0", "R1", "R2"]:
            block = paper_window_results[
                (paper_window_results["experiment"].eq(exp_id))
                & (paper_window_results["series"].eq("paper_scaled"))
                & (paper_window_results["window"].eq(window))
            ]
            if block.empty:
                block = paper_window_results[
                    (paper_window_results["experiment"].eq(exp_id))
                    & (paper_window_results["series"].eq("unscaled"))
                    & (paper_window_results["window"].eq(window))
                ]
            prefix = exp_id.lower()
            if block.empty:
                row[f"{prefix}_sharpe"] = np.nan
                row[f"{prefix}_return"] = np.nan
                row[f"{prefix}_scale"] = np.nan
            else:
                item = block.iloc[0]
                row[f"{prefix}_sharpe"] = item["sharpe"]
                row[f"{prefix}_return"] = item["cumulative_return"]
                row[f"{prefix}_scale"] = item["scale_factor"]
        rows.append(row)
    return pd.DataFrame(rows)


def _paper_combined_wealth(paper_window_results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for exp_id in ["R0", "R1", "R2"]:
        for series in ["unscaled", "paper_scaled"]:
            block = paper_window_results[
                paper_window_results["experiment"].eq(exp_id) & paper_window_results["series"].eq(series)
            ]
            if block.empty:
                continue
            product_wealth = float(block["terminal_wealth"].prod())
            combined_return = product_wealth - 1.0
            rows.append(
                {
                    "experiment": exp_id,
                    "series": series,
                    "window_count": len(block),
                    "product_of_window_terminal_wealth": product_wealth,
                    "combined_cumulative_return": combined_return,
                    "survivorship_bias_label": SURVIVORSHIP_LABEL,
                }
            )
    return pd.DataFrame(rows)


def _extreme_days(daily: pd.DataFrame, exp_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    top_pos = daily.nlargest(10, "net_return").copy()
    top_pos["extreme_side"] = "positive"
    top_neg = daily.nsmallest(10, "net_return").copy()
    top_neg["extreme_side"] = "negative"
    out = pd.concat([top_pos, top_neg], ignore_index=True)
    out["experiment"] = exp_id
    total_pnl = float(daily["net_return"].sum())
    top5 = float(top_pos.head(5)["net_return"].sum())
    summary = {
        "top5_positive_day_net_return_sum": top5,
        "sum_daily_net_returns": total_pnl,
        "top5_positive_share_of_summed_pnl": top5 / total_pnl if total_pnl else float("nan"),
    }
    return out, summary


def _security_concentration(ledger: pd.DataFrame, exp_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    contrib = (
        ledger.groupby(["security_id", "ticker"], as_index=False)
        .agg(cumulative_net_contribution=("net_contribution", "sum"))
        .sort_values("cumulative_net_contribution", ascending=False)
    )
    top = contrib.head(10).assign(contribution_side="top")
    bottom = contrib.tail(10).sort_values("cumulative_net_contribution").assign(contribution_side="bottom")
    out = pd.concat([top, bottom], ignore_index=True)
    out["experiment"] = exp_id
    total = float(contrib["cumulative_net_contribution"].sum())
    summary = {
        "total_summed_security_net_contribution": total,
        "top10_share_of_summed_pnl": float(contrib.head(10)["cumulative_net_contribution"].sum() / total)
        if total
        else float("nan"),
        "top25_share_of_summed_pnl": float(contrib.head(25)["cumulative_net_contribution"].sum() / total)
        if total
        else float("nan"),
    }
    return out, summary


def _bootstrap_summary(runs: dict[str, Phase3ExperimentRun]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "block_length": BOOTSTRAP_BLOCK_LENGTH,
        "repetitions": BOOTSTRAP_REPETITIONS,
        "seed": BOOTSTRAP_SEED,
        "survivorship_bias_label": SURVIVORSHIP_LABEL,
    }
    for exp_id in ["R2", "R3"]:
        returns = runs[exp_id].result.unscaled.daily["net_return"]
        ci = block_bootstrap_sharpe_ci(
            returns,
            block_size=BOOTSTRAP_BLOCK_LENGTH,
            n_boot=BOOTSTRAP_REPETITIONS,
            seed=BOOTSTRAP_SEED,
        )
        out[exp_id] = {
            "sharpe": performance_metrics(returns)["sharpe"],
            "sharpe_ci_95": list(ci),
        }
    return out


def _variant_z_scores(features: pd.DataFrame, cfg: StrategyConfig, variant: str) -> pd.DataFrame:
    base = compute_cross_sectional_signals(features, cfg)
    out = base.copy()
    eligible = out["eligible"].fillna(False).astype(bool)
    out["z_edge"] = np.nan
    if variant == "full_edge":
        return base
    if variant == "value_only":
        source = out["value_score"]
        active = eligible & source.notna()
        out.loc[active, "z_edge"] = out.loc[active].groupby("date")["value_score"].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else np.nan
        )
    elif variant == "reversal_only":
        source = out["reversal_z"]
        active = eligible & source.notna()
        out.loc[active, "z_edge"] = out.loc[active].groupby("date")["reversal_z"].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else np.nan
        )
    elif variant == "base":
        source = out["base"]
        active = eligible & source.notna()
        out.loc[active, "z_edge"] = out.loc[active].groupby("date")["base"].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else np.nan
        )
    elif variant == "value_regime":
        out["value_regime"] = out["value_score"] * out["regime"]
        active = eligible & out["regime"].eq(1.0) & out["value_regime"].notna()
        out.loc[active, "z_edge"] = out.loc[active].groupby("date")["value_regime"].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else np.nan
        )
    elif variant == "reversal_regime":
        out["reversal_regime"] = out["reversal_z"] * out["regime"]
        active = eligible & out["regime"].eq(1.0) & out["reversal_regime"].notna()
        out.loc[active, "z_edge"] = out.loc[active].groupby("date")["reversal_regime"].transform(
            lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) else np.nan
        )
    else:
        raise ValueError(f"Unknown signal attribution variant: {variant}")
    return out


def _signal_attribution(panel: pd.DataFrame, strategy: StrategyConfig, exp: ExperimentConfig) -> pd.DataFrame:
    features = compute_time_series_features(panel, strategy)
    features["eligible"] = True
    rows: list[dict[str, Any]] = []
    for variant in ["value_only", "reversal_only", "base", "value_regime", "reversal_regime", "full_edge"]:
        signals = _variant_z_scores(features, strategy, variant)
        weights = construct_signal_weights(signals, strategy)
        aligned = align_weights_to_returns(weights, panel, exp.return_lag_sessions)
        aligned = _filter_aligned_to_experiment(aligned, exp)
        ret = _build_return_series(aligned, strategy, exp, audit=False)
        rows.append(
            {
                "experiment": exp.id,
                "variant": variant,
                "survivorship_bias_label": SURVIVORSHIP_LABEL,
                **_summarize_daily(ret.daily),
            }
        )
    return pd.DataFrame(rows)


def _manifest(
    out_dir: Path,
    source_panel_path: Path,
    snapshot_rows_path: Path,
    fallback_anchor_path: Path,
    experiment_paths: list[Path],
    anchor: pd.DataFrame,
    prepared_summary: dict[str, Any],
    input_panel_path: Path,
) -> dict[str, Any]:
    first = anchor["snapshot_date"].iloc[0] if len(anchor) else pd.NaT
    return {
        "generated_at_utc": _now_iso(),
        "git_sha": _git_head(),
        "git_dirty_at_manifest": _git_dirty(),
        "survivorship_bias_label": SURVIVORSHIP_LABEL,
        "paper_publication_date": PAPER_PUBLICATION_DATE.date().isoformat(),
        "anchor_universe": {
            "anchor_date": first.date().isoformat() if not pd.isna(first) else None,
            "source": anchor["source_id"].iloc[0] if len(anchor) else None,
            "source_tier": anchor["source_tier"].iloc[0] if len(anchor) else None,
            "constituent_count": len(anchor),
            "distance_from_publication_days": int(anchor["distance_from_publication_days"].iloc[0])
            if len(anchor)
            else None,
            "known_limitation": anchor["known_limitation"].iloc[0] if len(anchor) else None,
        },
        "input_artifacts": {
            "source_panel": str(source_panel_path),
            "source_panel_sha256": _sha256_file(source_panel_path),
            "snapshot_rows": str(snapshot_rows_path),
            "snapshot_rows_sha256": _sha256_file(snapshot_rows_path),
            "fallback_anchor": str(fallback_anchor_path),
            "fallback_anchor_sha256": _sha256_file(fallback_anchor_path),
            "phase3_input_panel": str(input_panel_path),
            "phase3_input_panel_sha256": _sha256_file(input_panel_path),
        },
        "experiment_specs": {
            str(path): {
                "sha256": _sha256_file(path),
                "copied_to": str(out_dir / "experiment_specs" / path.name),
            }
            for path in experiment_paths
        },
        "yahoo_acquisition": {
            "yfinance_version": "1.5.1",
            "source_panel_role": "Yahoo Close and Adj Close retained separately from Phase 2C cache",
            "full_yahoo_redownload": False,
        },
        "price_roles": {
            "yahoo_close": "R0/R1 VALUE input; not certified nominal raw close",
            "yahoo_adj_close": "candidate total-return source via pct_change",
            "reconstructed_nominal_close": (
                "R2/R3 VALUE input; FORENSIC_CANDIDATE_NOMINAL_CLOSE; independent "
                "cross-source validation pending"
            ),
        },
        "prepared_panel_summary": prepared_summary,
        "output_dir": str(out_dir),
    }


def write_phase3_reports(
    *,
    source_panel_path: Path = Path("reports/generated/phase2c/yahoo_source_panel.parquet"),
    snapshot_rows_path: Path = Path("reports/generated/phase2e/snapshot_rows_mapped.csv"),
    fallback_anchor_path: Path = Path("reports/generated/phase2c/wikipedia_current_anchor.csv"),
    experiment_paths: list[Path] | None = None,
    strategy: StrategyConfig,
    out_dir: Path = Path("reports/generated/phase3"),
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    experiment_paths = experiment_paths or PHASE3_EXPERIMENT_PATHS
    specs_dir = out_dir / "experiment_specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    for path in experiment_paths:
        shutil.copy2(path, specs_dir / path.name)

    snapshot_rows = _read_table(snapshot_rows_path) if snapshot_rows_path.exists() else None
    fallback_anchor = _read_table(fallback_anchor_path) if fallback_anchor_path.exists() else None
    anchor = choose_phase3_anchor(snapshot_rows, fallback_anchor)
    source_panel = _read_table(source_panel_path)
    prepared = prepare_phase3_panel(source_panel, anchor)
    _write_table(out_dir / "anchor_universe.csv", prepared.anchor)
    input_panel_path = out_dir / "phase3_input_panel.parquet"
    _write_table(input_panel_path, prepared.panel)
    manifest = _manifest(
        out_dir,
        source_panel_path,
        snapshot_rows_path,
        fallback_anchor_path,
        experiment_paths,
        prepared.anchor,
        prepared.summary,
        input_panel_path,
    )
    _write_json(out_dir / "input_manifest.json", manifest)

    runs: dict[str, Phase3ExperimentRun] = {}
    panels: dict[str, pd.DataFrame] = {}
    paper_window_rows: list[dict[str, Any]] = []
    waterfall_rows: list[dict[str, Any]] = []
    scaling_rows: list[dict[str, Any]] = []
    diagnostics_frames: list[pd.DataFrame] = []
    ledger_reconciliation_rows: list[dict[str, Any]] = []

    for spec_path in experiment_paths:
        exp = load_experiment(spec_path)
        if not exp.phase3_role:
            raise ValueError(f"{spec_path} is not a Phase 3 experiment")
        panel = panel_for_value_price(prepared.panel, exp.value_price_column)  # type: ignore[arg-type]
        panels[exp.id] = panel
        result, signals, weights, aligned = _run_experiment(panel, strategy, exp)
        diagnostics = _signal_diagnostics(signals, weights, _filter_aligned_to_experiment(aligned, exp), exp)
        diagnostics_frames.append(diagnostics)

        value_signal_dates = set(_filter_aligned_to_experiment(aligned, exp)["date"])
        value_scores = signals.loc[
            signals["date"].isin(value_signal_dates),
            ["date", "security_id", "value_score"],
        ].copy()
        nonzero_weights = weights.loc[
            weights["date"].isin(value_signal_dates) & weights["signal_weight"].ne(0),
            ["date", "security_id", "signal_weight"],
        ].copy()

        unscaled_ledger = augment_security_ledger(result.unscaled.ledger, panel, exp)
        _write_table(out_dir / f"{exp.id}_security_ledger.parquet", unscaled_ledger)
        _write_table(out_dir / f"{exp.id}_daily.parquet", result.unscaled.daily)
        recon = ledger_reconciliation(unscaled_ledger, result.unscaled.daily, exp.id, scaled=False)
        ledger_reconciliation_rows.append(recon)
        if result.scaled is not None:
            scaled_ledger = augment_security_ledger(result.scaled.ledger, panel, exp)
            _write_table(out_dir / f"{exp.id}_paper_scaled_security_ledger.parquet", scaled_ledger)
            _write_table(out_dir / f"{exp.id}_paper_scaled_daily.parquet", result.scaled.daily)
            ledger_reconciliation_rows.append(
                ledger_reconciliation(scaled_ledger, result.scaled.daily, exp.id, scaled=True)
            )

        paper_window_rows.extend(_window_result_rows(exp, result, diagnostics))
        waterfall_rows.append(_combined_result_row(exp, "unscaled", result.unscaled, diagnostics))
        if result.scaled is not None:
            waterfall_rows.append(_combined_result_row(exp, "paper_scaled", result.scaled, diagnostics))
        scaling_rows.extend(_scaling_audit_rows(exp, result))

        runs[exp.id] = Phase3ExperimentRun(
            experiment=exp,
            result=result,
            signal_diagnostics=diagnostics,
            value_scores=value_scores,
            nonzero_weights=nonzero_weights,
            ledger_reconciliation=recon,
        )

    diagnostics_all = pd.concat(diagnostics_frames, ignore_index=True) if diagnostics_frames else pd.DataFrame()
    paper_window_results = pd.DataFrame(paper_window_rows)
    attribution_waterfall = pd.DataFrame(waterfall_rows)
    scaling_audit = pd.DataFrame(scaling_rows)
    ledger_recon = pd.DataFrame(ledger_reconciliation_rows)
    yearly_results = runs["R3"].result.unscaled.yearly.copy()
    yearly_results["experiment"] = "R3"
    yearly_results["survivorship_bias_label"] = SURVIVORSHIP_LABEL

    timing_audit = _timing_audit(runs, panels)
    value_rank_changes, value_rank_summary = _value_rank_change_summary(
        runs["R1"].value_scores,
        runs["R2"].value_scores,
    )
    membership_change_summary = _portfolio_membership_change_summary(
        runs["R1"].nonzero_weights,
        runs["R2"].nonzero_weights,
    )
    paper_comparison = _paper_comparison_rows(paper_window_results)
    combined_wealth = _paper_combined_wealth(paper_window_results)
    extreme_days, extreme_summary = _extreme_days(runs["R3"].result.unscaled.daily, "R3")
    r3_ledger = pd.read_parquet(out_dir / "R3_security_ledger.parquet")
    security_concentration, concentration_summary = _security_concentration(r3_ledger, "R3")
    bootstrap = _bootstrap_summary(runs)
    signal_attribution = _signal_attribution(panels["R3"], strategy, runs["R3"].experiment)

    _write_table(out_dir / "paper_window_results.csv", paper_window_results)
    _write_table(out_dir / "yearly_results.csv", yearly_results)
    _write_table(out_dir / "attribution_waterfall.csv", attribution_waterfall)
    _write_table(out_dir / "paper_comparison.csv", paper_comparison)
    _write_table(out_dir / "scaling_audit.csv", scaling_audit)
    _write_table(out_dir / "timing_audit.csv", timing_audit)
    _write_table(out_dir / "ledger_reconciliation.csv", ledger_recon)
    _write_table(out_dir / "daily_eligible_name_diagnostics.csv", diagnostics_all)
    _write_table(out_dir / "value_rank_changes.csv", value_rank_changes)
    _write_table(out_dir / "paper_window_wealth_accounting.csv", combined_wealth)
    _write_table(out_dir / "extreme_days.csv", extreme_days)
    _write_table(out_dir / "security_concentration.csv", security_concentration)
    _write_table(out_dir / "signal_attribution.csv", signal_attribution)
    _write_json(out_dir / "bootstrap_results.json", bootstrap)

    summary = {
        "generated_at_utc": _now_iso(),
        "survivorship_bias_label": SURVIVORSHIP_LABEL,
        "strategy_performance_run": True,
        "phase3_only_no_pit_membership_reconstruction": True,
        "full_yahoo_redownload": False,
        "anchor": manifest["anchor_universe"],
        "data": {
            **prepared.summary,
            "nominal_split_audit_status": (
                "FORENSIC_CANDIDATE_NOMINAL_CLOSE; Phase 2C found 603/616 split events "
                "nominal-consistent after split de-adjustment; independent validation pending"
            ),
            "total_return_source": "yahoo_adj_close_pct_change",
        },
        "bootstrap": bootstrap,
        "value_rank_change_summary": value_rank_summary,
        "portfolio_membership_change_summary": membership_change_summary,
        "extreme_day_summary": extreme_summary,
        "security_concentration_summary": concentration_summary,
        "ledger_reconciliation": ledger_recon.to_dict(orient="records"),
        "r0_r1_r2_r3_summary": attribution_waterfall.to_dict(orient="records"),
        "paper_window_results": paper_window_results.to_dict(orient="records"),
        "yearly_results": yearly_results.to_dict(orient="records"),
        "paper_comparison": paper_comparison.to_dict(orient="records"),
        "paper_window_wealth_accounting": combined_wealth.to_dict(orient="records"),
        "signal_attribution": signal_attribution.to_dict(orient="records"),
        "confirmations": {
            "no_strategy_parameter_optimization": True,
            "same_anchor_used_across_R0_R1_R2_R3": True,
            "no_full_yahoo_redownload": True,
            "point_in_time_membership_work_deferred": True,
            "survivorship_bias_intentional": True,
        },
    }
    _write_json(out_dir / "phase3_summary.json", summary)
    return summary
