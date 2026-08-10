import pandas as pd

from sharpe_replication.portfolio import align_weights_to_returns


def _panel():
    dates = pd.bdate_range("2024-01-01", periods=5)
    return pd.DataFrame(
        {
            "date": dates,
            "security_id": ["A"] * 5,
            "ticker": ["A"] * 5,
            "raw_close": [10, 11, 12, 13, 14],
            "total_return": [0.0, 0.10, 0.0909, 0.0833, 0.0769],
        }
    )


def test_paper_style_lag_earns_next_close_to_close_return():
    panel = _panel()
    weights = pd.DataFrame(
        {"date": [panel.date.iloc[0]], "security_id": ["A"], "signal_weight": [1.0]}
    )
    out = align_weights_to_returns(weights, panel, return_lag_sessions=1)
    assert out.return_date.iloc[0] == panel.date.iloc[1]


def test_corrected_daily_lag_skips_one_full_bar_before_first_earned_return():
    panel = _panel()
    weights = pd.DataFrame(
        {"date": [panel.date.iloc[0]], "security_id": ["A"], "signal_weight": [1.0]}
    )
    out = align_weights_to_returns(weights, panel, return_lag_sessions=2)
    assert out.return_date.iloc[0] == panel.date.iloc[2]
