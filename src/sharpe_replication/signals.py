from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .data_model import normalize_panel


def _zscore(s: pd.Series) -> pd.Series:
    std = s.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=s.index, dtype=float)
    return (s - s.mean()) / std


def compute_signals(panel: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Compute the frozen paper signals from historical information available at each close.

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
    df["value_score"] = df.groupby("date")["inverse_price"].rank(pct=True, method="average")
    df["reversal_z"] = df.groupby("date")["reversal_raw"].transform(_zscore)
    df["base"] = cfg.value_weight * df["value_score"] + cfg.reversal_weight * df["reversal_z"]
    df["edge"] = df["base"] * df["regime"]

    active = df["regime"].eq(1.0) & df["edge"].notna()
    df["z_edge"] = np.nan
    if active.any():
        df.loc[active, "z_edge"] = (
            df.loc[active]
            .groupby("date")["edge"]
            .transform(_zscore)
            .astype(float)
        )
    return df
