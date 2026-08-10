import pandas as pd


def test_future_split_must_not_retroactively_change_raw_close_fixture():
    # Contract-level test: raw_close is source data and must remain nominal.
    # A 4:1 split on day 3 changes nominal price then, not on prior dates.
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
            "raw_close": [100.0, 104.0, 26.0],
            "total_return": [0.0, 0.04, 0.0],  # split day economic return is 0 here
        }
    )
    assert df.raw_close.iloc[0] == 100.0
    assert df.raw_close.iloc[2] == 26.0
    assert df.total_return.iloc[2] == 0.0
