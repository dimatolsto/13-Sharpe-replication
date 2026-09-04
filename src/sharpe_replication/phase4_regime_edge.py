from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .metrics import TRADING_DAYS, performance_metrics
from .phase3_attribution import _json_default, _sha256_file
from .phase3b_reproduction_gap import (
    compute_forensic_cross_sectional_signals,
    compute_forensic_time_series_features,
    verify_phase3_manifest,
)
from .portfolio import align_weights_to_returns, construct_signal_weights, daily_portfolio_returns

PHASE4_LABEL = "PHASE 4 REGIME EDGE INCREMENTAL PREDICTIVE VALUE"
PHASE4_TRANSFORM_VERSION = "phase4-regime-edge-v1"
PRIMARY_START = pd.Timestamp("2010-01-01")
PRIMARY_END = pd.Timestamp("2024-12-31")
PRIMARY_YEARS = tuple(range(2010, 2025))
HORIZONS = (1, 2, 5, 10)
PRIMARY_HORIZON = 1
MIN_GROUP_SIZE = 20
MIN_INTERACTION_GROUP_SIZE = 20
HAC_LAG = 20
BOOTSTRAP_REPETITIONS = 2000
BOOTSTRAP_BLOCK_LENGTH = 20
BOOTSTRAP_SEED = 13
RANDOM_MASK_REPETITIONS = 1000
RANDOM_MASK_SEED = 13
REGIME_MIN_POSITIVE_DAYS = 38

Timing = Literal["information", "delayed"]


@dataclass(frozen=True)
class Phase4Dataset:
    panel: pd.DataFrame
    signals: pd.DataFrame
    manifest: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _as_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return float("nan")
    return float(value)


def _safe_mean(series: pd.Series) -> float:
    return _as_float(series.mean()) if len(series) else float("nan")


def _safe_std(series: pd.Series) -> float:
    return _as_float(series.std(ddof=1)) if len(series) > 1 else float("nan")


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=series.index, dtype=float)
    return (series - series.mean()) / std


def _ols_coefficients(x: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    valid = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if int(valid.sum()) < x.shape[1]:
        return None
    x_valid = x[valid]
    y_valid = y[valid]
    if np.linalg.matrix_rank(x_valid) < x_valid.shape[1]:
        return None
    beta, *_ = np.linalg.lstsq(x_valid, y_valid, rcond=None)
    return beta


def _simple_slope(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if int(valid.sum()) < 3:
        return float("nan")
    xv = x.loc[valid].to_numpy(dtype=float)
    yv = y.loc[valid].to_numpy(dtype=float)
    var = float(np.var(xv, ddof=0))
    if not np.isfinite(var) or var == 0:
        return float("nan")
    return float(np.cov(xv, yv, ddof=0)[0, 1] / var)


def _spearman_corr(x: pd.Series, y: pd.Series) -> float:
    valid = x.notna() & y.notna()
    if int(valid.sum()) < 3:
        return float("nan")
    xr = x.loc[valid].rank(method="average")
    yr = y.loc[valid].rank(method="average")
    corr = xr.corr(yr, method="pearson")
    return _as_float(corr)


def _top_bottom_spread(block: pd.DataFrame, signal_col: str, target_col: str) -> float:
    valid = block[[signal_col, target_col]].dropna()
    if len(valid) < MIN_GROUP_SIZE:
        return float("nan")
    ranks = valid[signal_col].rank(pct=True, method="first")
    top = valid.loc[ranks > 0.8, target_col]
    bottom = valid.loc[ranks <= 0.2, target_col]
    if top.empty or bottom.empty:
        return float("nan")
    return float(top.mean() - bottom.mean())


def _sharpe_from_daily(values: pd.Series) -> float:
    x = values.dropna().astype(float)
    std = x.std(ddof=1)
    if len(x) < 2 or not np.isfinite(std) or std == 0:
        return float("nan")
    return float(math.sqrt(TRADING_DAYS) * x.mean() / std)


def _phase3_panel_from_manifest(phase3_dir: Path) -> pd.DataFrame:
    manifest_path = phase3_dir / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return pd.read_parquet(manifest["input_artifacts"]["phase3_input_panel"])


def prepare_phase4_signals(panel: pd.DataFrame, strategy: StrategyConfig) -> pd.DataFrame:
    """Prepare frozen Phase 3B paper-spec VALUE, REVERSAL, BASE, and REGIME signals."""

    required = {"yahoo_close", "yahoo_adj_close_pct_change"}
    missing = required - set(panel.columns)
    if missing:
        raise ValueError(f"Phase 4 requires Phase 3 panel columns: {sorted(missing)}")

    source = panel.copy()
    source["raw_close"] = source["yahoo_close"]
    source["total_return"] = source["yahoo_adj_close_pct_change"]
    source["eligible"] = True
    features = compute_forensic_time_series_features(
        source,
        strategy,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    features["eligible"] = True
    signals = compute_forensic_cross_sectional_signals(features, strategy)
    signals["phase4_signal_definition"] = (
        "Phase3B paper-spec: Yahoo Close VALUE, prior-compounded REVERSAL, "
        "63 prior returns, strict UpFraction > 0.60"
    )
    return signals


def add_future_return_targets(
    signals: pd.DataFrame,
    panel: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = HORIZONS,
) -> pd.DataFrame:
    """Add causal close-to-close future-return targets for fixed horizons."""

    returns = panel[["date", "security_id", "yahoo_adj_close_pct_change"]].copy()
    returns["date"] = pd.to_datetime(returns["date"])
    returns = returns.sort_values(["security_id", "date"])
    g = returns.groupby("security_id", group_keys=False)["yahoo_adj_close_pct_change"]
    calendar = pd.Index(returns["date"].drop_duplicates().sort_values())
    calendar_position = pd.Series(np.arange(len(calendar)), index=calendar)
    current_position = returns["date"].map(calendar_position)

    out = signals.copy()
    out["date"] = pd.to_datetime(out["date"])
    target_cols = ["date", "security_id"]
    for horizon in horizons:
        # Product of returns t+1 through t+horizon, observed only after signal date t.
        forward = g.transform(
            lambda s, h=horizon: (1.0 + s)
            .shift(-1)
            .rolling(h, min_periods=h)
            .apply(np.prod, raw=True)
            .shift(-(h - 1))
            - 1.0
        )
        future_date = returns.groupby("security_id", group_keys=False)["date"].shift(-horizon)
        exact_calendar_horizon = future_date.map(calendar_position).eq(current_position + horizon)
        forward = forward.where(exact_calendar_horizon)
        returns[f"future_return_{horizon}d"] = forward
        target_cols.append(f"future_return_{horizon}d")

    return out.merge(returns[target_cols], on=["date", "security_id"], how="left", validate="one_to_one")


def delayed_target_column(frame: pd.DataFrame) -> pd.DataFrame:
    """Add corrected one-day delayed-execution target: signal t earns t+1 -> t+2."""

    out = frame.sort_values(["security_id", "date"]).copy()
    return_col = "yahoo_adj_close_pct_change" if "yahoo_adj_close_pct_change" in out.columns else "total_return"
    out["future_return_1d_delayed"] = out.groupby("security_id", group_keys=False)[
        return_col
    ].shift(-2)
    calendar = pd.Index(pd.to_datetime(out["date"]).drop_duplicates().sort_values())
    calendar_position = pd.Series(np.arange(len(calendar)), index=calendar)
    current_position = pd.to_datetime(out["date"]).map(calendar_position)
    future_date = out.groupby("security_id", group_keys=False)["date"].shift(-2)
    exact_calendar_horizon = pd.to_datetime(future_date).map(calendar_position).eq(current_position + 2)
    out["future_return_1d_delayed"] = out["future_return_1d_delayed"].where(exact_calendar_horizon)
    return out


def primary_sample(frame: pd.DataFrame) -> pd.DataFrame:
    dates = pd.to_datetime(frame["date"])
    return frame[(dates >= PRIMARY_START) & (dates <= PRIMARY_END)].copy()


def eligible_analysis_frame(signals: pd.DataFrame, target_col: str) -> pd.DataFrame:
    required = {"date", "security_id", "base", "value_score", "reversal_z", "regime", "up_fraction", target_col}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Analysis frame missing columns: {sorted(missing)}")
    eligible = signals.get("eligible", pd.Series(True, index=signals.index)).fillna(False).astype(bool)
    cols = [
        "date",
        "security_id",
        "ticker",
        "base",
        "value_score",
        "reversal_z",
        "regime",
        "up_fraction",
        target_col,
    ]
    if "ticker" not in signals.columns:
        cols.remove("ticker")
    out = signals.loc[eligible, cols].copy()
    out["date"] = pd.to_datetime(out["date"])
    out = out.dropna(subset=["base", "regime", "up_fraction", target_col])
    out["regime"] = out["regime"].astype(int)
    return primary_sample(out)


def conditional_ic_daily(frame: pd.DataFrame, target_col: str, signal_col: str = "base") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (date, regime), block in frame.groupby(["date", "regime"], sort=True):
        if len(block) < MIN_GROUP_SIZE:
            rows.append(
                {
                    "date": date,
                    "regime": int(regime),
                    "n": len(block),
                    "spearman_ic": np.nan,
                    "pearson_ic": np.nan,
                    "slope": np.nan,
                }
            )
            continue
        rows.append(
            {
                "date": date,
                "regime": int(regime),
                "n": len(block),
                "spearman_ic": _spearman_corr(block[signal_col], block[target_col]),
                "pearson_ic": block[signal_col].corr(block[target_col], method="pearson"),
                "slope": _simple_slope(block[signal_col], block[target_col]),
            }
        )
    return pd.DataFrame(rows)


def conditional_ic_summary(daily: pd.DataFrame) -> pd.DataFrame:
    wide = daily.pivot(index="date", columns="regime", values=["spearman_ic", "pearson_ic", "slope"])
    rows: list[dict[str, Any]] = []
    for metric in ["spearman_ic", "pearson_ic", "slope"]:
        r0 = wide[(metric, 0)] if (metric, 0) in wide else pd.Series(dtype=float)
        r1 = wide[(metric, 1)] if (metric, 1) in wide else pd.Series(dtype=float)
        diff = r1 - r0
        summ = hac_summary(diff.rename("diff"), value_col="diff")
        rows.append(
            {
                "metric": metric,
                "regime_0_mean": _safe_mean(r0),
                "regime_1_mean": _safe_mean(r1),
                "difference": _safe_mean(diff),
                "difference_hac_se": summ["se"],
                "difference_t_stat": summ["t_stat"],
                "difference_ci_low": summ["ci_low"],
                "difference_ci_high": summ["ci_high"],
                "paired_days": int(diff.notna().sum()),
            }
        )
    return pd.DataFrame(rows)


def conditional_spreads(frame: pd.DataFrame, target_col: str, signal_col: str = "base") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (date, regime), block in frame.groupby(["date", "regime"], sort=True):
        rows.append(
            {
                "date": date,
                "regime": int(regime),
                "n": len(block),
                "spread": _top_bottom_spread(block, signal_col, target_col),
            }
        )
    return pd.DataFrame(rows)


def conditional_spread_summary(daily: pd.DataFrame) -> pd.DataFrame:
    wide = daily.pivot(index="date", columns="regime", values="spread")
    r0 = wide[0] if 0 in wide else pd.Series(dtype=float)
    r1 = wide[1] if 1 in wide else pd.Series(dtype=float)
    diff = r1 - r0
    summ = hac_summary(diff.rename("diff"), value_col="diff")
    return pd.DataFrame(
        [
            {
                "metric": "top_bottom_spread",
                "regime_0_mean": _safe_mean(r0),
                "regime_1_mean": _safe_mean(r1),
                "difference": _safe_mean(diff),
                "difference_hac_se": summ["se"],
                "difference_t_stat": summ["t_stat"],
                "difference_ci_low": summ["ci_low"],
                "difference_ci_high": summ["ci_high"],
                "paired_days": int(diff.notna().sum()),
            }
        ]
    )


def daily_interaction_regression(
    frame: pd.DataFrame,
    target_col: str,
    *,
    signal_col: str = "base",
    standardized: bool = False,
    continuous_upfraction: bool = False,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date, block in frame.groupby("date", sort=True):
        b = block.dropna(subset=[signal_col, "regime", "up_fraction", target_col]).copy()
        r0 = int(b["regime"].eq(0).sum())
        r1 = int(b["regime"].eq(1).sum())
        insufficient_sample = (
            len(b) < 2 * MIN_INTERACTION_GROUP_SIZE
            if continuous_upfraction
            else r0 < MIN_INTERACTION_GROUP_SIZE or r1 < MIN_INTERACTION_GROUP_SIZE
        )
        if insufficient_sample:
            rows.append(
                {
                    "date": date,
                    "n": len(b),
                    "regime_0_n": r0,
                    "regime_1_n": r1,
                    "skipped": True,
                    "alpha": np.nan,
                    "beta_signal": np.nan,
                    "beta_regime_or_upfraction": np.nan,
                    "beta_interaction": np.nan,
                }
            )
            continue

        signal = _zscore(b[signal_col]) if standardized else b[signal_col].astype(float)
        conditioner = b["up_fraction"].astype(float) if continuous_upfraction else b["regime"].astype(float)
        x = np.column_stack(
            [
                np.ones(len(b)),
                signal.to_numpy(dtype=float),
                conditioner.to_numpy(dtype=float),
                (signal * conditioner).to_numpy(dtype=float),
            ]
        )
        beta = _ols_coefficients(x, b[target_col].to_numpy(dtype=float))
        rows.append(
            {
                "date": date,
                "n": len(b),
                "regime_0_n": r0,
                "regime_1_n": r1,
                "skipped": beta is None,
                "alpha": np.nan if beta is None else float(beta[0]),
                "beta_signal": np.nan if beta is None else float(beta[1]),
                "beta_regime_or_upfraction": np.nan if beta is None else float(beta[2]),
                "beta_interaction": np.nan if beta is None else float(beta[3]),
                "signal_scaling": "date_cross_section_zscore" if standardized else "raw",
                "conditioner": "up_fraction" if continuous_upfraction else "regime",
            }
        )
    return pd.DataFrame(rows)


def hac_summary(
    series_or_frame: pd.Series | pd.DataFrame,
    *,
    value_col: str | None = None,
    lag: int = HAC_LAG,
) -> dict[str, float]:
    values = series_or_frame[value_col] if isinstance(series_or_frame, pd.DataFrame) else series_or_frame
    x = pd.Series(values).dropna().astype(float).to_numpy()
    n = len(x)
    if n < 2:
        return {
            "n": n,
            "mean": float("nan"),
            "se": float("nan"),
            "t_stat": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
            "fraction_positive": float("nan"),
        }
    centered = x - x.mean()
    gamma0 = float(np.dot(centered, centered) / n)
    long_run_var = gamma0
    max_lag = min(lag, n - 1)
    for k in range(1, max_lag + 1):
        gamma = float(np.dot(centered[k:], centered[:-k]) / n)
        weight = 1.0 - k / (max_lag + 1.0)
        long_run_var += 2.0 * weight * gamma
    se = math.sqrt(max(long_run_var, 0.0) / n)
    mean = float(x.mean())
    t_stat = mean / se if se > 0 else float("nan")
    return {
        "n": n,
        "mean": mean,
        "se": se,
        "t_stat": t_stat,
        "ci_low": mean - 1.96 * se if np.isfinite(se) else float("nan"),
        "ci_high": mean + 1.96 * se if np.isfinite(se) else float("nan"),
        "fraction_positive": float((x > 0).mean()),
    }


def block_bootstrap_mean_ci(
    values: pd.Series,
    *,
    repetitions: int = BOOTSTRAP_REPETITIONS,
    block_length: int = BOOTSTRAP_BLOCK_LENGTH,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> dict[str, float]:
    x = values.dropna().astype(float).to_numpy()
    n = len(x)
    if n < max(2, block_length):
        return {"ci_low": float("nan"), "ci_high": float("nan"), "repetitions": repetitions}
    starts = np.arange(0, n - block_length + 1)
    rng = np.random.default_rng(seed)
    means = np.empty(repetitions, dtype=float)
    for i in range(repetitions):
        pieces: list[np.ndarray] = []
        total = 0
        while total < n:
            start = int(rng.choice(starts))
            piece = x[start : start + block_length]
            pieces.append(piece)
            total += len(piece)
        means[i] = np.concatenate(pieces)[:n].mean()
    return {
        "ci_low": float(np.quantile(means, alpha / 2)),
        "ci_high": float(np.quantile(means, 1 - alpha / 2)),
        "repetitions": repetitions,
    }


def interaction_summary(
    daily: pd.DataFrame,
    *,
    bootstrap_repetitions: int = BOOTSTRAP_REPETITIONS,
) -> dict[str, Any]:
    stats = hac_summary(daily, value_col="beta_interaction")
    boot = block_bootstrap_mean_ci(
        daily["beta_interaction"],
        repetitions=bootstrap_repetitions,
        block_length=BOOTSTRAP_BLOCK_LENGTH,
        seed=BOOTSTRAP_SEED,
    )
    return {
        **stats,
        "bootstrap_ci_low": boot["ci_low"],
        "bootstrap_ci_high": boot["ci_high"],
        "bootstrap_repetitions": boot["repetitions"],
        "bootstrap_block_length": BOOTSTRAP_BLOCK_LENGTH,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "hac_lag": HAC_LAG,
        "skipped_dates": int(daily.get("skipped", pd.Series(False)).fillna(False).sum()),
    }


def coefficient_summaries(
    daily: pd.DataFrame,
    *,
    bootstrap_repetitions: int = BOOTSTRAP_REPETITIONS,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for coefficient, col in {
        "alpha": "alpha",
        "beta1_signal": "beta_signal",
        "beta2_regime_or_upfraction": "beta_regime_or_upfraction",
        "beta3_interaction": "beta_interaction",
    }.items():
        stats = hac_summary(daily, value_col=col)
        boot = block_bootstrap_mean_ci(
            daily[col],
            repetitions=bootstrap_repetitions,
            block_length=BOOTSTRAP_BLOCK_LENGTH,
            seed=BOOTSTRAP_SEED,
        )
        rows.append(
            {
                "coefficient": coefficient,
                "mean": stats["mean"],
                "hac_se": stats["se"],
                "t_stat": stats["t_stat"],
                "ci_low": stats["ci_low"],
                "ci_high": stats["ci_high"],
                "bootstrap_ci_low": boot["ci_low"],
                "bootstrap_ci_high": boot["ci_high"],
                "fraction_positive": stats["fraction_positive"],
                "usable_dates": stats["n"],
                "hac_lag": HAC_LAG,
            }
        )
    return pd.DataFrame(rows)


def upfraction_binning(frame: pd.DataFrame, target_col: str, signal_col: str = "base") -> pd.DataFrame:
    bins = [-np.inf, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, np.inf]
    labels = ["<45%", "45%-50%", "50%-55%", "55%-60%", "60%-65%", "65%-70%", ">=70%"]
    work = frame.copy()
    work["upfraction_bin"] = pd.cut(work["up_fraction"], bins=bins, labels=labels, right=False)
    daily_rows: list[dict[str, Any]] = []
    for (date, label), block in work.groupby(["date", "upfraction_bin"], observed=False, sort=True):
        if block.empty:
            continue
        daily_rows.append(
            {
                "date": date,
                "upfraction_bin": str(label),
                "n": len(block),
                "spearman_ic": _spearman_corr(block[signal_col], block[target_col])
                if len(block) >= MIN_GROUP_SIZE
                else np.nan,
                "base_slope": _simple_slope(block[signal_col], block[target_col]),
                "top_bottom_spread": _top_bottom_spread(block, signal_col, target_col),
            }
        )
    daily = pd.DataFrame(daily_rows)
    rows: list[dict[str, Any]] = []
    for label in labels:
        block = work[work["upfraction_bin"].astype(str).eq(label)]
        daily_block = (
            daily[daily["upfraction_bin"].eq(label)]
            if len(daily)
            else pd.DataFrame(columns=["spearman_ic", "base_slope", "top_bottom_spread"])
        )
        rows.append(
            {
                "upfraction_bin": label,
                "stock_day_count": len(block),
                "mean_up_fraction": _safe_mean(block["up_fraction"]),
                "mean_base": _safe_mean(block[signal_col]),
                "mean_daily_spearman_ic": _safe_mean(daily_block["spearman_ic"]),
                "mean_daily_base_slope": _safe_mean(daily_block["base_slope"]),
                "mean_daily_top_bottom_spread": _safe_mean(daily_block["top_bottom_spread"]),
                "usable_days": int(daily_block["spearman_ic"].notna().sum()) if len(daily_block) else 0,
            }
        )
    out = pd.DataFrame(rows)
    valid = out["mean_daily_spearman_ic"].notna()
    if int(valid.sum()) >= 3:
        out["monotonicity_rank_correlation"] = _spearman_corr(
            out.loc[valid, "mean_up_fraction"],
            out.loc[valid, "mean_daily_spearman_ic"],
        )
    else:
        out["monotonicity_rank_correlation"] = np.nan
    return out


def matched_random_masks(
    frame: pd.DataFrame,
    target_col: str,
    *,
    repetitions: int = RANDOM_MASK_REPETITIONS,
    seed: int = RANDOM_MASK_SEED,
    signal_col: str = "base",
) -> pd.DataFrame:
    """Evaluate equally broad deterministic random masks matching regime count per date."""

    by_date: list[tuple[np.ndarray, np.ndarray, int]] = []
    for _, block in frame.groupby("date", sort=True):
        b = block[[signal_col, target_col, "regime"]].dropna()
        active_n = int(b["regime"].eq(1).sum())
        if active_n < MIN_GROUP_SIZE or len(b) - active_n < MIN_GROUP_SIZE:
            continue
        by_date.append((b[signal_col].to_numpy(dtype=float), b[target_col].to_numpy(dtype=float), active_n))

    rng = np.random.default_rng(seed)
    ic_sum = np.zeros(repetitions, dtype=float)
    ic_count = np.zeros(repetitions, dtype=float)
    spread_sum = np.zeros(repetitions, dtype=float)
    spread_count = np.zeros(repetitions, dtype=float)
    slope_diff_sum = np.zeros(repetitions, dtype=float)
    slope_diff_count = np.zeros(repetitions, dtype=float)
    spread_daily: list[list[float]] = [[] for _ in range(repetitions)]

    def masked_slope(signal: np.ndarray, target: np.ndarray, mask: np.ndarray) -> np.ndarray:
        count = mask.sum(axis=0).astype(float)
        sx = (mask * signal[:, None]).sum(axis=0)
        sy = (mask * target[:, None]).sum(axis=0)
        sxx = (mask * (signal * signal)[:, None]).sum(axis=0)
        sxy = (mask * (signal * target)[:, None]).sum(axis=0)
        cov = sxy / count - (sx / count) * (sy / count)
        var = sxx / count - (sx / count) ** 2
        out = np.full(mask.shape[1], np.nan, dtype=float)
        valid = (count >= MIN_GROUP_SIZE) & np.isfinite(var) & (var > 0)
        out[valid] = cov[valid] / var[valid]
        return out

    def masked_corr(rank_x: np.ndarray, rank_y: np.ndarray, mask: np.ndarray) -> np.ndarray:
        count = mask.sum(axis=0).astype(float)
        sx = (mask * rank_x).sum(axis=0)
        sy = (mask * rank_y).sum(axis=0)
        sxx = (mask * rank_x * rank_x).sum(axis=0)
        syy = (mask * rank_y * rank_y).sum(axis=0)
        sxy = (mask * rank_x * rank_y).sum(axis=0)
        cov = sxy / count - (sx / count) * (sy / count)
        vx = sxx / count - (sx / count) ** 2
        vy = syy / count - (sy / count) ** 2
        denom = np.sqrt(vx * vy)
        out = np.full(mask.shape[1], np.nan, dtype=float)
        valid = (count >= MIN_GROUP_SIZE) & np.isfinite(denom) & (denom > 0)
        out[valid] = cov[valid] / denom[valid]
        return out

    for signal, target, active_n in by_date:
        n = len(signal)
        selected = np.zeros((n, repetitions), dtype=bool)
        random_scores = rng.random((n, repetitions))
        chosen = np.argpartition(random_scores, active_n - 1, axis=0)[:active_n, :]
        selected[chosen, np.arange(repetitions)] = True
        complement = ~selected

        signal_order = np.argsort(signal, kind="mergesort")
        target_order = np.argsort(target, kind="mergesort")
        selected_by_signal = selected[signal_order, :]
        rank_x_by_signal = np.cumsum(selected_by_signal, axis=0) * selected_by_signal
        rank_x = np.zeros((n, repetitions), dtype=float)
        rank_x[signal_order, :] = rank_x_by_signal
        selected_by_target = selected[target_order, :]
        rank_y_by_target = np.cumsum(selected_by_target, axis=0) * selected_by_target
        rank_y = np.zeros((n, repetitions), dtype=float)
        rank_y[target_order, :] = rank_y_by_target

        ic = masked_corr(rank_x, rank_y, selected)
        valid_ic = np.isfinite(ic)
        ic_sum[valid_ic] += ic[valid_ic]
        ic_count[valid_ic] += 1.0

        subset_slope = masked_slope(signal, target, selected)
        complement_slope = masked_slope(signal, target, complement)
        slope_diff = subset_slope - complement_slope
        valid_slope = np.isfinite(slope_diff)
        slope_diff_sum[valid_slope] += slope_diff[valid_slope]
        slope_diff_count[valid_slope] += 1.0

        selected_rank_by_signal = rank_x_by_signal
        bottom_cut = max(1, math.floor(0.2 * active_n))
        top_cut = math.floor(0.8 * active_n)
        bottom = selected_by_signal & (selected_rank_by_signal <= bottom_cut)
        top = selected_by_signal & (selected_rank_by_signal > top_cut)
        sorted_target = target[signal_order]
        top_count = top.sum(axis=0)
        bottom_count = bottom.sum(axis=0)
        spread = (top * sorted_target[:, None]).sum(axis=0) / top_count - (
            bottom * sorted_target[:, None]
        ).sum(axis=0) / bottom_count
        valid_spread = np.isfinite(spread)
        spread_sum[valid_spread] += spread[valid_spread]
        spread_count[valid_spread] += 1.0
        for rep in np.flatnonzero(valid_spread):
            spread_daily[int(rep)].append(float(spread[rep]))

    rows: list[dict[str, Any]] = []
    for rep in range(repetitions):
        spread_series = pd.Series(spread_daily[rep], dtype=float)
        rows.append(
            {
                "replication": rep,
                "mean_base_ic": ic_sum[rep] / ic_count[rep] if ic_count[rep] else np.nan,
                "base_spread_mean": spread_sum[rep] / spread_count[rep] if spread_count[rep] else np.nan,
                "base_spread_sharpe": _sharpe_from_daily(spread_series),
                "interaction_like_efficacy_statistic": slope_diff_sum[rep] / slope_diff_count[rep]
                if slope_diff_count[rep]
                else np.nan,
                "usable_dates": int(slope_diff_count[rep]),
                "seed": seed,
            }
        )
    return pd.DataFrame(rows)


def random_mask_summary(actual: dict[str, float], random_distribution: pd.DataFrame) -> dict[str, Any]:
    rows: dict[str, Any] = {
        "repetitions": len(random_distribution),
        "seed": RANDOM_MASK_SEED,
        "matching": "exact same REGIME=1 active-security count per date",
    }
    for actual_key, random_col in {
        "mean_base_ic": "mean_base_ic",
        "base_spread_mean": "base_spread_mean",
        "base_spread_sharpe": "base_spread_sharpe",
        "interaction_like_efficacy_statistic": "interaction_like_efficacy_statistic",
    }.items():
        dist = random_distribution[random_col].dropna().astype(float)
        actual_value = actual.get(actual_key, float("nan"))
        if dist.empty or not np.isfinite(actual_value):
            rows[actual_key] = {"actual": actual_value}
            continue
        ge = int((dist >= actual_value).sum())
        rows[actual_key] = {
            "actual": actual_value,
            "random_mean": float(dist.mean()),
            "random_median": float(dist.median()),
            "random_95th_percentile": float(dist.quantile(0.95)),
            "actual_percentile": float((dist < actual_value).mean()),
            "empirical_one_sided_p": float((1 + ge) / (len(dist) + 1)),
        }
    return rows


def yearly_completeness(
    frame: pd.DataFrame,
    ic_daily: pd.DataFrame,
    spread_daily: pd.DataFrame,
    interactions: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    ic_wide = ic_daily.pivot(index="date", columns="regime", values="spearman_ic")
    sp_wide = spread_daily.pivot(index="date", columns="regime", values="spread")
    for year in PRIMARY_YEARS:
        dates = frame[pd.to_datetime(frame["date"]).dt.year.eq(year)]["date"]
        year_frame = frame[frame["date"].isin(dates)]
        active = year_frame["regime"].eq(1)
        ic_year = ic_wide[pd.to_datetime(ic_wide.index).year == year]
        sp_year = sp_wide[pd.to_datetime(sp_wide.index).year == year]
        int_year = interactions[pd.to_datetime(interactions["date"]).dt.year.eq(year)]
        r0_ic = ic_year[0] if 0 in ic_year else pd.Series(dtype=float)
        r1_ic = ic_year[1] if 1 in ic_year else pd.Series(dtype=float)
        r0_sp = sp_year[0] if 0 in sp_year else pd.Series(dtype=float)
        r1_sp = sp_year[1] if 1 in sp_year else pd.Series(dtype=float)
        rows.append(
            {
                "year": year,
                "eligible_stock_days": len(year_frame),
                "regime_1_stock_days": int(active.sum()),
                "regime_1_fraction": float(active.mean()) if len(year_frame) else np.nan,
                "average_active_names_per_day": _safe_mean(
                    year_frame[active].groupby("date")["security_id"].count()
                ),
                "regime_1_base_ic": _safe_mean(r1_ic),
                "regime_0_base_ic": _safe_mean(r0_ic),
                "ic_difference": _safe_mean(r1_ic - r0_ic),
                "regime_1_base_spread": _safe_mean(r1_sp),
                "regime_0_base_spread": _safe_mean(r0_sp),
                "spread_difference": _safe_mean(r1_sp - r0_sp),
                "interaction_beta3": _safe_mean(int_year["beta_interaction"]),
                "usable_dates": int(year_frame["date"].nunique()),
            }
        )
    return pd.DataFrame(rows)


def regime_prevalence(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, block in [("overall", frame), *[(str(y), frame[pd.to_datetime(frame["date"]).dt.year.eq(y)]) for y in PRIMARY_YEARS]]:
        active = block["regime"].eq(1)
        by_date = block.groupby("date")["security_id"].count()
        active_by_date = block[active].groupby("date")["security_id"].count()
        rows.append(
            {
                "period": label,
                "eligible_stock_days": len(block),
                "regime_1_stock_days": int(active.sum()),
                "regime_1_fraction": float(active.mean()) if len(block) else np.nan,
                "average_eligible_names_per_day": _safe_mean(by_date),
                "median_eligible_names_per_day": _as_float(by_date.median()) if len(by_date) else np.nan,
                "average_active_names_per_day": _safe_mean(active_by_date),
                "median_active_names_per_day": _as_float(active_by_date.median())
                if len(active_by_date)
                else np.nan,
                "minimum_active_names_per_day": _as_float(active_by_date.min())
                if len(active_by_date)
                else np.nan,
                "usable_dates": int(block["date"].nunique()),
            }
        )
    return pd.DataFrame(rows)


def transitions(frame: pd.DataFrame, target_col: str, *, window: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.sort_values(["security_id", "date"]).copy()
    work["prev_regime"] = work.groupby("security_id")["regime"].shift(1)
    work["entry"] = work["prev_regime"].eq(0) & work["regime"].eq(1)
    work["exit"] = work["prev_regime"].eq(1) & work["regime"].eq(0)
    work["ranked_base"] = work.groupby("date")["base"].rank(pct=True, method="average") - 0.5
    work["alignment"] = work["ranked_base"] * work[target_col]

    def collect(kind: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        event_keys = work.loc[work[kind], ["security_id", "date"]].copy()
        event_keys["event_id"] = np.arange(len(event_keys))
        indexed = work.set_index(["security_id", "date"]).sort_index()
        calendars = {
            sid: pd.Index(block["date"].to_numpy())
            for sid, block in work.groupby("security_id", sort=False)
        }
        for row in event_keys.itertuples(index=False):
            cal = calendars[row.security_id]
            pos = cal.get_indexer([row.date])[0]
            for offset in range(-window, window + 1):
                target_pos = pos + offset
                if target_pos < 0 or target_pos >= len(cal):
                    continue
                sample_date = cal[target_pos]
                try:
                    obs = indexed.loc[(row.security_id, sample_date)]
                except KeyError:
                    continue
                rows.append(
                    {
                        "event_id": int(row.event_id),
                        "security_id": row.security_id,
                        "event_date": row.date,
                        "event_day": offset,
                        "alignment": obs["alignment"],
                    }
                )
        if not rows:
            return pd.DataFrame(columns=["event_day", "mean_alignment", "ci_low", "ci_high", "n_events"])
        events = pd.DataFrame(rows)
        out_rows: list[dict[str, Any]] = []
        for event_day, block in events.groupby("event_day", sort=True):
            x = block["alignment"].dropna().astype(float)
            se = x.std(ddof=1) / math.sqrt(len(x)) if len(x) > 1 else np.nan
            out_rows.append(
                {
                    "event_day": int(event_day),
                    "mean_alignment": _safe_mean(x),
                    "ci_low": _safe_mean(x) - 1.96 * se if np.isfinite(se) else np.nan,
                    "ci_high": _safe_mean(x) + 1.96 * se if np.isfinite(se) else np.nan,
                    "n_events": int(x.count()),
                    "event_type": kind.upper(),
                }
            )
        return pd.DataFrame(out_rows)

    return collect("entry"), collect("exit")


def portfolio_comparison(
    signals: pd.DataFrame,
    panel: pd.DataFrame,
    strategy: StrategyConfig,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    base = signals.copy()
    base["date"] = pd.to_datetime(base["date"])
    base = primary_sample(base)

    for name, mask in {
        "BASE_ALL": base["base"].notna(),
        "BASE_REGIME": base["base"].notna() & base["regime"].eq(1.0),
        "BASE_NONREGIME": base["base"].notna() & base["regime"].eq(0.0),
    }.items():
        work = base.copy()
        work["z_edge"] = np.nan
        work.loc[mask, "z_edge"] = work.loc[mask].groupby("date")["base"].transform(_zscore)
        weights = construct_signal_weights(work, strategy)
        aligned = align_weights_to_returns(weights, panel, return_lag_sessions=2)
        _, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.0)
        daily = daily[
            (pd.to_datetime(daily["return_date"]) >= PRIMARY_START)
            & (pd.to_datetime(daily["return_date"]) <= PRIMARY_END)
        ]
        metrics = performance_metrics(daily["net_return"])
        rows.append(
            {
                "portfolio": name,
                "sharpe": metrics["sharpe"],
                "annualized_return": metrics["cagr"],
                "annualized_volatility": metrics["ann_vol"],
                "max_drawdown": metrics["max_drawdown"],
                "turnover": _safe_mean(daily["turnover"]),
                "observations": len(daily),
            }
        )
    return pd.DataFrame(rows)


def component_interactions(frame: pd.DataFrame, target_col: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for signal_col in ["value_score", "reversal_z", "base"]:
        daily = daily_interaction_regression(frame, target_col, signal_col=signal_col)
        summary = interaction_summary(daily)
        rows.append(
            {
                "component": signal_col,
                "beta_interaction_mean": summary["mean"],
                "beta_interaction_hac_se": summary["se"],
                "beta_interaction_t_stat": summary["t_stat"],
                "beta_interaction_ci_low": summary["ci_low"],
                "beta_interaction_ci_high": summary["ci_high"],
                "fraction_positive": summary["fraction_positive"],
                "usable_dates": summary["n"],
            }
        )
    return pd.DataFrame(rows)


def concentration_diagnostics(frame: pd.DataFrame, target_col: str) -> pd.DataFrame:
    active = frame[frame["regime"].eq(1)].copy()
    active["base_rank"] = active.groupby("date")["base"].rank(pct=True, method="average") - 0.5
    active["contribution"] = active["base_rank"] * active[target_col]
    security = (
        active.groupby("security_id", as_index=False)
        .agg(
            contribution=("contribution", "sum"),
            mean_contribution=("contribution", "mean"),
            observations=("contribution", "count"),
        )
        .sort_values("contribution", ascending=False)
    )
    total_abs = float(security["contribution"].abs().sum())
    top_abs = float(security.head(10)["contribution"].abs().sum())
    security["rank"] = np.arange(1, len(security) + 1)
    security["side"] = "middle"
    security.loc[security["rank"].le(10), "side"] = "top_10"
    bottom_index = security.tail(10).index
    security.loc[bottom_index, "side"] = "bottom_10"
    security["fraction_attributable_to_top_10_abs"] = top_abs / total_abs if total_abs > 0 else np.nan

    rank_frame = frame.copy()
    rank_frame["rank_target"] = rank_frame.groupby("date")[target_col].rank(pct=True, method="average")
    rank_interactions = daily_interaction_regression(rank_frame, "rank_target")
    rank_summary = interaction_summary(rank_interactions)
    security["rank_outcome_beta3_mean"] = rank_summary["mean"]
    security["rank_outcome_beta3_t_stat"] = rank_summary["t_stat"]
    return security


def horizon_summary(signals: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        target_col = f"future_return_{horizon}d"
        frame = eligible_analysis_frame(signals, target_col)
        ic = conditional_ic_daily(frame, target_col)
        ic_summary = conditional_ic_summary(ic)
        spreads = conditional_spreads(frame, target_col)
        sp_wide = spreads.pivot(index="date", columns="regime", values="spread")
        spread_diff = sp_wide.get(1, np.nan) - sp_wide.get(0, np.nan)
        interactions = daily_interaction_regression(frame, target_col)
        interaction = interaction_summary(interactions)
        ic_diff = ic_summary.loc[ic_summary["metric"].eq("spearman_ic"), "difference"]
        rows.append(
            {
                "horizon_days": horizon,
                "primary": horizon == PRIMARY_HORIZON,
                "interaction_beta3": interaction["mean"],
                "interaction_beta3_t_stat": interaction["t_stat"],
                "ic_difference": _as_float(ic_diff.iloc[0]) if len(ic_diff) else np.nan,
                "spread_difference": _safe_mean(pd.Series(spread_diff)),
                "usable_dates": interaction["n"],
            }
        )
    return pd.DataFrame(rows)


def summarize_classification(
    interaction: dict[str, Any],
    conditional_summary: pd.DataFrame,
    delayed_interaction: dict[str, Any],
    random_summary_payload: dict[str, Any],
    yearly: pd.DataFrame,
    upfraction_bins: pd.DataFrame,
) -> str:
    ic_diff = conditional_summary.loc[conditional_summary["metric"].eq("spearman_ic"), "difference"]
    ic_positive = len(ic_diff) and float(ic_diff.iloc[0]) > 0
    beta_positive = np.isfinite(interaction["mean"]) and interaction["mean"] > 0
    delayed_positive = np.isfinite(delayed_interaction["mean"]) and delayed_interaction["mean"] > 0
    years_positive = int((yearly["interaction_beta3"] > 0).sum())
    random_ic = random_summary_payload.get("mean_base_ic", {})
    random_unusual = random_ic.get("actual_percentile", 0.0) >= 0.95 and beta_positive
    supportive_bins = _as_float(upfraction_bins["monotonicity_rank_correlation"].dropna().head(1).mean()) > 0
    ci_excludes_zero = interaction["ci_low"] > 0 if np.isfinite(interaction["ci_low"]) else False

    if beta_positive and ci_excludes_zero and ic_positive and delayed_positive and random_unusual:
        if years_positive >= 9 and supportive_bins:
            return "STRONG_POSITIVE"
        return "WEAK_OR_MIXED"
    if not beta_positive and not ic_positive:
        if np.isfinite(interaction["mean"]) and interaction["mean"] < 0:
            return "NEGATIVE_EVIDENCE"
        return "NO_EVIDENCE"
    return "WEAK_OR_MIXED"


def load_phase4_dataset(phase3_dir: Path, strategy: StrategyConfig) -> Phase4Dataset:
    manifest_report = verify_phase3_manifest(phase3_dir)
    panel = _phase3_panel_from_manifest(phase3_dir)
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["raw_close"] = panel["yahoo_close"]
    panel["total_return"] = panel["yahoo_adj_close_pct_change"]
    signals = prepare_phase4_signals(panel, strategy)
    signals = add_future_return_targets(signals, panel)
    signals = delayed_target_column(signals)
    return Phase4Dataset(panel=panel, signals=signals, manifest=manifest_report)


def write_phase4_reports(
    *,
    phase3_dir: Path = Path("reports/generated/phase3"),
    strategy: StrategyConfig,
    out_dir: Path = Path("reports/generated/phase4"),
    random_mask_repetitions: int = RANDOM_MASK_REPETITIONS,
    bootstrap_repetitions: int = BOOTSTRAP_REPETITIONS,
) -> dict[str, Any]:
    """Run Phase 4 and write deterministic regime-edge analysis artifacts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = load_phase4_dataset(phase3_dir, strategy)
    signals = dataset.signals

    primary_frame = eligible_analysis_frame(signals, "future_return_1d")
    delayed_frame = eligible_analysis_frame(signals, "future_return_1d_delayed")

    prevalence = regime_prevalence(primary_frame)
    ic_daily = conditional_ic_daily(primary_frame, "future_return_1d")
    ic_summary = conditional_ic_summary(ic_daily)
    spreads = conditional_spreads(primary_frame, "future_return_1d")
    spread_summary = conditional_spread_summary(spreads)
    interaction_raw = daily_interaction_regression(primary_frame, "future_return_1d")
    interaction_std = daily_interaction_regression(primary_frame, "future_return_1d", standardized=True)
    interaction_daily = pd.concat(
        [interaction_raw.assign(model="raw_base"), interaction_std.assign(model="standardized_base")],
        ignore_index=True,
    )
    interaction_raw_summary = interaction_summary(interaction_raw, bootstrap_repetitions=bootstrap_repetitions)
    interaction_std_summary = interaction_summary(interaction_std, bootstrap_repetitions=bootstrap_repetitions)
    interaction_summary_table = pd.concat(
        [
            coefficient_summaries(
                interaction_raw,
                bootstrap_repetitions=bootstrap_repetitions,
            ).assign(model="raw_base", timing="information"),
            coefficient_summaries(
                interaction_std,
                bootstrap_repetitions=bootstrap_repetitions,
            ).assign(model="standardized_base", timing="information"),
        ],
        ignore_index=True,
    )

    delayed_ic_daily = conditional_ic_daily(delayed_frame, "future_return_1d_delayed")
    delayed_ic_summary = conditional_ic_summary(delayed_ic_daily)
    delayed_spreads = conditional_spreads(delayed_frame, "future_return_1d_delayed")
    delayed_spread_summary = conditional_spread_summary(delayed_spreads)
    delayed_interaction_daily = daily_interaction_regression(delayed_frame, "future_return_1d_delayed")
    delayed_interaction_summary = interaction_summary(
        delayed_interaction_daily,
        bootstrap_repetitions=bootstrap_repetitions,
    )
    interaction_summary_table = pd.concat(
        [
            interaction_summary_table,
            coefficient_summaries(
                delayed_interaction_daily,
                bootstrap_repetitions=bootstrap_repetitions,
            ).assign(model="raw_base", timing="delayed"),
        ],
        ignore_index=True,
    )

    horizons = horizon_summary(signals)
    bins = upfraction_binning(primary_frame, "future_return_1d")
    upfraction_interaction_daily = daily_interaction_regression(
        primary_frame,
        "future_return_1d",
        continuous_upfraction=True,
    )
    upfraction_interaction_summary = pd.DataFrame(
        [interaction_summary(upfraction_interaction_daily, bootstrap_repetitions=bootstrap_repetitions)]
    )

    random_usable_dates = pd.Index(
        interaction_raw.loc[interaction_raw["beta_interaction"].notna(), "date"].unique()
    )
    actual_ic = ic_daily[
        ic_daily["regime"].eq(1) & ic_daily["date"].isin(random_usable_dates)
    ]["spearman_ic"]
    actual_spread = spreads[
        spreads["regime"].eq(1) & spreads["date"].isin(random_usable_dates)
    ]["spread"]
    actual_interaction = interaction_raw[
        interaction_raw["date"].isin(random_usable_dates)
    ]["beta_interaction"]
    actual_stats = {
        "mean_base_ic": _safe_mean(actual_ic),
        "base_spread_mean": _safe_mean(actual_spread),
        "base_spread_sharpe": _sharpe_from_daily(actual_spread),
        "interaction_like_efficacy_statistic": _safe_mean(actual_interaction),
    }
    random_distribution = matched_random_masks(
        primary_frame[primary_frame["date"].isin(random_usable_dates)],
        "future_return_1d",
        repetitions=random_mask_repetitions,
        seed=RANDOM_MASK_SEED,
    )
    random_summary_payload = random_mask_summary(actual_stats, random_distribution)

    yearly = yearly_completeness(primary_frame, ic_daily, spreads, interaction_raw)
    entries, exits = transitions(primary_frame, "future_return_1d")
    portfolios = portfolio_comparison(signals, dataset.panel, strategy)
    components = component_interactions(primary_frame, "future_return_1d")
    concentration = concentration_diagnostics(primary_frame, "future_return_1d")

    classification = summarize_classification(
        interaction_raw_summary,
        ic_summary,
        delayed_interaction_summary,
        random_summary_payload,
        yearly,
        bins,
    )

    manifest = {
        "phase": PHASE4_LABEL,
        "created_at": _now_iso(),
        "git_sha": _git_head(),
        "git_dirty_at_manifest": _git_dirty(),
        "phase4_transform_version": PHASE4_TRANSFORM_VERSION,
        "phase3_manifest_verification": dataset.manifest,
        "phase3_input_panel_sha256": dataset.manifest["input_panel_sha256"],
        "sample": {
            "primary_start": PRIMARY_START.date().isoformat(),
            "primary_end": PRIMARY_END.date().isoformat(),
            "years": list(PRIMARY_YEARS),
            "horizons": list(HORIZONS),
            "primary_horizon": PRIMARY_HORIZON,
        },
        "signal_definition": {
            "value": "inverse-price percentile rank using Phase 3 Yahoo Close VALUE input",
            "reversal": "Phase 3B paper-spec prior compounded trailing 10-day reversal",
            "base": "0.70 * VALUE + 0.30 * REVERSAL",
            "regime": "fraction of positive daily returns over 63 observations immediately preceding t",
            "threshold": "strict > 0.60",
            "minimum_positive_days_out_of_63": REGIME_MIN_POSITIVE_DAYS,
            "current_return_in_upfraction": False,
        },
        "settings": {
            "min_group_size": MIN_GROUP_SIZE,
            "min_interaction_group_size_each_regime": MIN_INTERACTION_GROUP_SIZE,
            "hac_lag": HAC_LAG,
            "bootstrap_repetitions": bootstrap_repetitions,
            "bootstrap_block_length": BOOTSTRAP_BLOCK_LENGTH,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "random_mask_repetitions": random_mask_repetitions,
            "random_mask_seed": RANDOM_MASK_SEED,
        },
        "input_artifacts": {
            "phase3_input_panel": dataset.manifest["input_panel"],
            "phase3_input_panel_sha256": _sha256_file(Path(dataset.manifest["input_panel"])),
        },
    }

    summary: dict[str, Any] = {
        "phase": PHASE4_LABEL,
        "input_manifest": manifest,
        "classification": classification,
        "primary_test": {
            "timing": "NEXT-DAY PREDICTIVE INFORMATION",
            "horizon_days": PRIMARY_HORIZON,
            "interaction_raw_base": interaction_raw_summary,
            "interaction_standardized_base": interaction_std_summary,
            "interaction_coefficients": interaction_summary_table[
                interaction_summary_table["timing"].eq("information")
            ].to_dict(orient="records"),
            "conditional_base_efficacy": ic_summary.to_dict(orient="records"),
            "conditional_spread_efficacy": spread_summary.to_dict(orient="records"),
        },
        "primary_interaction": interaction_raw_summary,
        "corrected_delayed_timing": {
            "timing": "CORRECTED DELAYED EXECUTION",
            "interaction_raw_base": delayed_interaction_summary,
            "interaction_coefficients": interaction_summary_table[
                interaction_summary_table["timing"].eq("delayed")
            ].to_dict(orient="records"),
            "conditional_base_efficacy": delayed_ic_summary.to_dict(orient="records"),
            "conditional_spread_efficacy": delayed_spread_summary.to_dict(orient="records"),
        },
        "random_mask_summary": random_summary_payload,
        "yearly_sign_counts": {
            "years_interaction_positive": int((yearly["interaction_beta3"] > 0).sum()),
            "years_interaction_negative": int((yearly["interaction_beta3"] < 0).sum()),
            "years_ic_advantage_positive": int((yearly["ic_difference"] > 0).sum()),
            "years_ic_advantage_negative": int((yearly["ic_difference"] < 0).sum()),
        },
        "confirmations": {
            "window_63_days_unchanged": strategy.drift_window == 63,
            "threshold_strict_gt_60pct_unchanged": strategy.up_fraction_threshold == 0.60,
            "base_weights_unchanged": strategy.value_weight == 0.70
            and strategy.reversal_weight == 0.30,
            "no_parameter_optimization": True,
            "no_alternative_threshold_selected": True,
            "no_pit_membership_work": True,
            "no_new_full_yahoo_download": True,
            "no_favorable_year_selection": True,
            "no_performance_driven_cleaning": True,
            "all_2010_2024_years_included": set(yearly["year"]) == set(PRIMARY_YEARS),
            "randomization_seed_fixed": RANDOM_MASK_SEED,
            "primary_horizon_fixed": PRIMARY_HORIZON,
            "phase3b_invalid_leakage_variant_used": False,
            "no_bulk_market_data_written": True,
        },
    }

    _write_json(out_dir / "phase4_input_manifest.json", manifest)
    _write_table(out_dir / "regime_prevalence.csv", prevalence)
    _write_table(out_dir / "conditional_ic_daily.csv", ic_daily)
    _write_table(out_dir / "conditional_ic_summary.csv", ic_summary)
    _write_table(out_dir / "conditional_spreads.csv", spreads)
    _write_table(out_dir / "interaction_coefficients_daily.csv", interaction_daily)
    _write_table(out_dir / "interaction_summary.csv", interaction_summary_table)
    _write_json(
        out_dir / "interaction_summary.json",
        {
            "raw_base": interaction_raw_summary,
            "standardized_base": interaction_std_summary,
            "delayed_raw_base": delayed_interaction_summary,
        },
    )
    _write_table(out_dir / "horizon_summary.csv", horizons)
    _write_table(out_dir / "upfraction_bins.csv", bins)
    _write_table(out_dir / "upfraction_interaction.csv", upfraction_interaction_summary)
    _write_table(out_dir / "random_mask_distribution.csv", random_distribution)
    _write_json(out_dir / "random_mask_summary.json", random_summary_payload)
    _write_table(out_dir / "yearly_regime_edge.csv", yearly)
    _write_table(out_dir / "transition_entries.csv", entries)
    _write_table(out_dir / "transition_exits.csv", exits)
    _write_table(out_dir / "portfolio_comparison.csv", portfolios)
    _write_table(out_dir / "component_interactions.csv", components)
    _write_table(out_dir / "concentration_diagnostics.csv", concentration)
    _write_json(out_dir / "phase4_summary.json", summary)
    return summary
