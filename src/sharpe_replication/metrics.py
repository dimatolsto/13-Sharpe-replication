from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(returns: pd.Series) -> float:
    r = returns.fillna(0.0).astype(float)
    if len(r) == 0:
        return float("nan")
    wealth = pd.concat(
        [pd.Series([1.0], index=[-1], dtype=float), (1.0 + r).cumprod()],
        ignore_index=True,
    )
    peak = wealth.cummax()
    dd = wealth / peak - 1.0
    return float(dd.min())


def performance_metrics(returns: pd.Series, risk_free_rate: float = 0.0) -> dict[str, float]:
    r = returns.dropna().astype(float)
    if len(r) == 0:
        return {k: float("nan") for k in ["sharpe", "cagr", "ann_vol", "max_drawdown", "wealth"]}
    mean_daily = r.mean() - risk_free_rate / TRADING_DAYS
    vol_daily = r.std(ddof=1)
    sharpe = math.sqrt(TRADING_DAYS) * mean_daily / vol_daily if vol_daily > 0 else float("nan")
    wealth = float((1.0 + r).prod())
    years = len(r) / TRADING_DAYS
    cagr = wealth ** (1.0 / years) - 1.0 if years > 0 and wealth > 0 else float("nan")
    return {
        "sharpe": float(sharpe),
        "cagr": float(cagr),
        "ann_vol": float(vol_daily * math.sqrt(TRADING_DAYS)),
        "max_drawdown": max_drawdown(r),
        "wealth": wealth,
    }


def paper_scale_details(
    training_returns: pd.Series,
    vol_target: float,
    max_dd_target: float,
) -> dict[str, float]:
    metrics = performance_metrics(training_returns)
    vol = metrics["ann_vol"]
    max_dd = metrics["max_drawdown"]
    dd_abs = abs(max_dd)
    if not np.isfinite(vol) or vol <= 0:
        raise ValueError("Cannot compute paper scaling with zero or invalid training volatility")
    if not np.isfinite(dd_abs) or dd_abs <= 0:
        raise ValueError("Cannot compute paper scaling with zero or invalid training max drawdown")
    volatility_scale = vol_target / vol
    drawdown_scale = max_dd_target / dd_abs
    return {
        "training_annualized_volatility": float(vol),
        "training_max_drawdown": float(max_dd),
        "volatility_scale": float(volatility_scale),
        "drawdown_scale": float(drawdown_scale),
        "scale_factor": float(min(volatility_scale, drawdown_scale)),
    }


def paper_scale(training_returns: pd.Series, vol_target: float, max_dd_target: float) -> float:
    return paper_scale_details(training_returns, vol_target, max_dd_target)["scale_factor"]


def yearly_metrics(daily: pd.DataFrame, return_col: str = "net_return") -> pd.DataFrame:
    df = daily.copy()
    df["year"] = pd.to_datetime(df["return_date"]).dt.year
    rows = []
    for year, block in df.groupby("year"):
        m = performance_metrics(block[return_col])
        rows.append(
            {
                "year": int(year),
                "n_days": len(block),
                **m,
                "avg_turnover": float(block["turnover"].mean()),
                "avg_gross_exposure": float(block["gross_exposure"].mean()),
                "avg_net_exposure": float(block["net_exposure"].mean()),
            }
        )
    return pd.DataFrame(rows)


def block_bootstrap_sharpe_ci(
    returns: pd.Series,
    block_size: int = 10,
    n_boot: int = 2000,
    seed: int = 13,
    alpha: float = 0.05,
) -> tuple[float, float]:
    r = returns.dropna().to_numpy(dtype=float)
    n = len(r)
    if n < max(20, block_size * 2):
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    starts = np.arange(0, n - block_size + 1)
    vals = []
    for _ in range(n_boot):
        sample = []
        while len(sample) < n:
            s = int(rng.choice(starts))
            sample.extend(r[s : s + block_size])
        x = np.asarray(sample[:n])
        sd = x.std(ddof=1)
        if sd > 0:
            vals.append(math.sqrt(TRADING_DAYS) * x.mean() / sd)
    if not vals:
        return (float("nan"), float("nan"))
    return (float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2)))
