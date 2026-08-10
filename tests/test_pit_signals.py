from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sharpe_replication.config import StrategyConfig
from sharpe_replication.data_model import apply_point_in_time_eligibility
from sharpe_replication.portfolio import construct_signal_weights
from sharpe_replication.signals import compute_signals


def _pit_panel() -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=8)
    returns = {
        "A": [0.010, 0.010, -0.004, 0.011, 0.010, 0.012, -0.003, 0.011],
        "B": [0.020, 0.018, 0.017, -0.010, 0.016, 0.019, 0.020, -0.006],
        "C": [0.008, -0.003, 0.009, 0.010, 0.009, 0.008, 0.011, -0.002],
    }
    start_prices = {"A": 10.0, "B": 1.0, "C": 20.0}
    rows = []
    for sid, values in returns.items():
        price = start_prices[sid]
        for d, ret in zip(dates, values, strict=True):
            price *= 1.0 + ret
            rows.append(
                {
                    "date": d,
                    "security_id": sid,
                    "ticker": sid,
                    "raw_close": price,
                    "total_return": ret,
                }
            )
    return pd.DataFrame(rows)


def test_pit_signals_keep_pre_membership_history_without_future_member_leakage():
    dates = pd.bdate_range("2020-01-01", periods=8)
    panel = _pit_panel()
    membership = pd.DataFrame(
        {
            "security_id": ["A", "B", "C"],
            "membership_start": [dates[0], dates[5], dates[0]],
            "membership_end": [pd.NaT, pd.NaT, pd.NaT],
        }
    )
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.5)

    signals = compute_signals(panel, cfg, membership)
    weights = construct_signal_weights(signals, cfg)

    before_entry = signals[signals["date"].eq(dates[4])].set_index("security_id")
    assert bool(before_entry.loc["B", "eligible"]) is False
    assert np.isnan(before_entry.loc["B", "value_score"])
    assert before_entry.loc["A", "value_score"] == 1.0
    assert before_entry.loc["C", "value_score"] == 0.5
    assert np.isnan(before_entry.loc["B", "reversal_z"])
    eligible_reversal = before_entry.loc[["A", "C"], "reversal_raw"]
    expected_reversal_z = (eligible_reversal - eligible_reversal.mean()) / eligible_reversal.std(ddof=0)
    assert before_entry.loc["A", "reversal_z"] == pytest.approx(expected_reversal_z.loc["A"])
    assert before_entry.loc["C", "reversal_z"] == pytest.approx(expected_reversal_z.loc["C"])

    entry = signals[signals["date"].eq(dates[5])].set_index("security_id")
    assert bool(entry.loc["B", "eligible"]) is True
    assert np.isfinite(entry.loc["B", "ret_10"])
    assert np.isfinite(entry.loc["B", "up_fraction"])
    assert np.isfinite(entry.loc["B", "value_score"])
    assert np.isfinite(entry.loc["B", "z_edge"])

    entry_weights = weights[weights["date"].eq(dates[5])].set_index("security_id")
    assert entry_weights.loc["B", "signal_weight"] != 0.0
    before_entry_weights = weights[weights["date"].eq(dates[4])].set_index("security_id")
    assert "B" not in before_entry_weights.index


def test_point_in_time_eligibility_supports_multiple_membership_spells():
    dates = pd.bdate_range("2020-01-01", periods=8)
    panel = _pit_panel()
    membership = pd.DataFrame(
        {
            "security_id": ["B", "B"],
            "membership_start": [dates[1], dates[5]],
            "membership_end": [dates[2], dates[6]],
        }
    )

    out = apply_point_in_time_eligibility(panel[panel["security_id"].eq("B")], membership)
    eligible_dates = set(out.loc[out["eligible"], "date"].dt.strftime("%Y-%m-%d"))
    assert eligible_dates == {
        dates[1].strftime("%Y-%m-%d"),
        dates[2].strftime("%Y-%m-%d"),
        dates[5].strftime("%Y-%m-%d"),
        dates[6].strftime("%Y-%m-%d"),
    }
