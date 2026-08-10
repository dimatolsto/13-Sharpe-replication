import numpy as np
import pandas as pd

from sharpe_replication.config import StrategyConfig
from sharpe_replication.signals import compute_signals


def test_inverse_price_uses_raw_nominal_close():
    dates = pd.bdate_range("2020-01-01", periods=70)
    rows = []
    for sid, price in [("LOW", 10.0), ("HIGH", 100.0)]:
        for d in dates:
            rows.append(
                {
                    "date": d,
                    "security_id": sid,
                    "ticker": sid,
                    "raw_close": price,
                    "total_return": 0.001 if d.day % 3 else -0.001,
                }
            )
    out = compute_signals(pd.DataFrame(rows), StrategyConfig())
    last = out[out.date == dates[-1]].set_index("security_id")
    assert last.loc["LOW", "inverse_price"] > last.loc["HIGH", "inverse_price"]
    assert last.loc["LOW", "value_score"] > last.loc["HIGH", "value_score"]


def test_strict_regime_threshold():
    cfg = StrategyConfig(drift_window=10, reversal_lookback=3, up_fraction_threshold=0.6)
    dates = pd.bdate_range("2020-01-01", periods=10)
    returns = [0.01] * 6 + [-0.01] * 4
    panel = pd.DataFrame(
        {
            "date": dates,
            "security_id": ["A"] * 10,
            "ticker": ["A"] * 10,
            "raw_close": np.linspace(10, 11, 10),
            "total_return": returns,
        }
    )
    out = compute_signals(panel, cfg)
    assert out.iloc[-1].up_fraction == 0.6
    assert out.iloc[-1].regime == 0.0  # strictly greater than 60%
