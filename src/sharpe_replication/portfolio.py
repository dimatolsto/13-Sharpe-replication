from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig


def construct_signal_weights(signals: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Construct 50/50 long-short target weights from active z-scored EDGE values."""
    required = {"date", "security_id", "z_edge"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Signals missing columns: {sorted(missing)}")

    if "eligible" in signals.columns:
        source = signals.loc[signals["eligible"].fillna(False).astype(bool)]
    else:
        source = signals
    df = source[["date", "security_id", "z_edge"]].copy()
    df["signal_weight"] = 0.0

    for idx in df.groupby("date").groups.values():
        block = df.loc[idx]
        long_idx = block.index[block["z_edge"] > 0]
        short_idx = block.index[block["z_edge"] < 0]
        if len(long_idx):
            denom = df.loc[long_idx, "z_edge"].abs().sum()
            if denom > 0:
                df.loc[long_idx, "signal_weight"] = (
                    cfg.long_exposure * df.loc[long_idx, "z_edge"].abs() / denom
                )
        if len(short_idx):
            denom = df.loc[short_idx, "z_edge"].abs().sum()
            if denom > 0:
                df.loc[short_idx, "signal_weight"] = (
                    -cfg.short_exposure * df.loc[short_idx, "z_edge"].abs() / denom
                )

    return df


def align_weights_to_returns(
    weights: pd.DataFrame,
    panel: pd.DataFrame,
    return_lag_sessions: int,
) -> pd.DataFrame:
    """Align signal weights to returns using a calendar-session lag.

    If `return_lag_sessions=1`, a weight formed at close t earns the return ending at the next
    observed trading date (paper-style/same-close interpretation).

    If `return_lag_sessions=2`, a weight formed at close t is assumed executed at close t+1 and
    first earns the return from t+1 to t+2. This is the certified daily-data convention.
    """
    if return_lag_sessions < 1:
        raise ValueError("return_lag_sessions must be >= 1")

    calendar = pd.Index(pd.to_datetime(panel["date"]).drop_duplicates().sort_values(), name="date")
    pos = pd.Series(np.arange(len(calendar)), index=calendar)
    target_pos = weights["date"].map(pos) + return_lag_sessions
    valid = target_pos < len(calendar)
    aligned = weights.loc[valid].copy()
    aligned["return_date"] = calendar.take(target_pos.loc[valid].astype(int).to_numpy()).to_numpy()

    returns = panel[["date", "security_id", "total_return"]].rename(columns={"date": "return_date"})
    out = aligned.merge(returns, on=["return_date", "security_id"], how="left", validate="many_to_one")
    return out


def daily_portfolio_returns(
    aligned: pd.DataFrame,
    transaction_cost_bp: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate security-level ledger and daily portfolio returns.

    Turnover uses 0.5 * sum(abs(w_t - w_{t-1})) on target signal weights by execution date proxy.
    Missing security returns are never silently treated as zero; they are flagged and excluded from
    certified output by audit code.
    """
    ledger = aligned.copy()
    ledger["missing_weighted_return"] = ledger["total_return"].isna() & ledger["signal_weight"].ne(0.0)
    ledger["gross_contribution"] = ledger["signal_weight"] * ledger["total_return"]

    # Compute daily gross return from available contributions.
    daily = (
        ledger.groupby("return_date", as_index=False)
        .agg(
            gross_return=("gross_contribution", "sum"),
            missing_returns=("missing_weighted_return", "sum"),
            n_positions=("signal_weight", lambda x: int((x != 0).sum())),
            gross_exposure=("signal_weight", lambda x: float(x.abs().sum())),
            net_exposure=("signal_weight", "sum"),
        )
        .sort_values("return_date")
    )
    daily.loc[daily["missing_returns"].gt(0), "gross_return"] = np.nan

    # Build turnover from signal weights by their return_date. This captures target portfolio change.
    wide = ledger.pivot_table(
        index="return_date", columns="security_id", values="signal_weight", aggfunc="sum", fill_value=0.0
    ).sort_index()
    turnover = 0.5 * wide.diff().abs().sum(axis=1)
    if len(turnover):
        turnover.iloc[0] = 0.5 * wide.iloc[0].abs().sum()
    daily = daily.merge(turnover.rename("turnover"), left_on="return_date", right_index=True)
    daily["cost"] = daily["turnover"] * transaction_cost_bp * 1e-4
    daily["net_return"] = daily["gross_return"] - daily["cost"]
    return ledger, daily
