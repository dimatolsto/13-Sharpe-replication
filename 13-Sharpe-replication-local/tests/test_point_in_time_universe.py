import pandas as pd

from sharpe_replication.data_model import apply_point_in_time_membership


def test_membership_start_end_are_respected_and_end_is_inclusive():
    panel = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"] * 2),
            "security_id": ["A"] * 3 + ["B"] * 3,
            "ticker": ["A"] * 3 + ["B"] * 3,
            "raw_close": [10, 11, 12, 20, 21, 22],
            "total_return": [0.0, 0.1, 0.09, 0.0, 0.05, 0.04],
        }
    )
    membership = pd.DataFrame(
        {
            "security_id": ["A", "B"],
            "membership_start": ["2020-01-02", "2020-01-01"],
            "membership_end": ["2020-01-03", "2020-01-02"],
        }
    )
    out = apply_point_in_time_membership(panel, membership)
    got = set(zip(out.security_id, out.date.dt.strftime("%Y-%m-%d")))
    assert got == {
        ("A", "2020-01-02"),
        ("A", "2020-01-03"),
        ("B", "2020-01-01"),
        ("B", "2020-01-02"),
    }
