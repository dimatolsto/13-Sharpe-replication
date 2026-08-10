from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data_model import apply_point_in_time_eligibility, normalize_panel


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=s.index, dtype=float)
    return (s - s.mean()) / std


def compute_time_series_features(panel: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Compute per-security features using only each security's own history through date t.

    `total_return[t]` is the return ending on date t. A 10-day reversal signal uses the compounded
    return over the trailing 10 return observations ending on t. The regime up-fraction includes
    t and the preceding `drift_window - 1` daily returns.
    """
    df = normalize_panel(panel)
    g = df.groupby("security_id", group_keys=False)

    # Trailing compounded return over exactly reversal_lookback observations.
    df["ret_10"] = g["total_return"].transform(
        lambda s: (1.0 + s).rolling(cfg.reversal_lookback, min_periods=cfg.reversal_lookback).apply(
            np.prod, raw=True
        )
        - 1.0
    )
    df["reversal_raw"] = -df["ret_10"]

    df["up_fraction"] = g["total_return"].transform(
        lambda s: s.gt(0)
        .rolling(cfg.drift_window, min_periods=cfg.drift_window)
        .mean()
    )
    df["regime"] = (df["up_fraction"] > cfg.up_fraction_threshold).astype(float)

    # Important: raw_close is deliberately not back-adjusted.
    df["inverse_price"] = 1.0 / df["raw_close"]
    return df


def compute_cross_sectional_signals(features: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Compute cross-sectional ranks/z-scores only across eligible names on each signal date."""
    df = features.copy()
    if "eligible" not in df.columns:
        df["eligible"] = True
    eligible = df["eligible"].fillna(False).astype(bool)

    df["value_score"] = np.nan
    if eligible.any():
        df.loc[eligible, "value_score"] = df.loc[eligible].groupby("date")["inverse_price"].rank(
            pct=True, method="average"
        )

    df["reversal_z"] = np.nan
    if eligible.any():
        df.loc[eligible, "reversal_z"] = (
            df.loc[eligible].groupby("date")["reversal_raw"].transform(_zscore).astype(float)
        )

    df["base"] = np.nan
    df.loc[eligible, "base"] = (
        cfg.value_weight * df.loc[eligible, "value_score"]
        + cfg.reversal_weight * df.loc[eligible, "reversal_z"]
    )
    df["edge"] = np.nan
    df.loc[eligible, "edge"] = df.loc[eligible, "base"] * df.loc[eligible, "regime"]

    active = eligible & df["regime"].eq(1.0) & df["edge"].notna()
    df["z_edge"] = np.nan
    if active.any():
        df.loc[active, "z_edge"] = (
            df.loc[active]
            .groupby("date")["edge"]
            .transform(_zscore)
            .astype(float)
        )
    return df


def compute_signals(
    panel: pd.DataFrame,
    cfg: StrategyConfig,
    membership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Compute frozen paper signals from close-t information.

    Time-series features are always computed from all available history for each security. If
    membership is supplied, only securities eligible on signal date t enter cross-sectional ranks,
    z-scores, EDGE standardization, and downstream portfolio construction.
    """
    features = compute_time_series_features(panel, cfg)
    if membership is not None:
        features = apply_point_in_time_eligibility(features, membership)
    else:
        features["eligible"] = True
    return compute_cross_sectional_signals(features, cfg)
