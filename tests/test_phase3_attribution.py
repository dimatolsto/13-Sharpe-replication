from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from sharpe_replication.config import (
    ClosedDateWindow,
    ExperimentConfig,
    ScalingConfig,
    ScalingWindow,
    StrategyConfig,
    load_experiment,
)
from sharpe_replication.metrics import max_drawdown
from sharpe_replication.phase3_attribution import (
    SURVIVORSHIP_LABEL,
    _run_experiment,
    _yearly_metrics,
    augment_security_ledger,
    choose_phase3_anchor,
    ledger_reconciliation,
    panel_for_value_price,
    prepare_phase3_panel,
)
from sharpe_replication.portfolio import align_weights_to_returns, daily_portfolio_returns
from sharpe_replication.signals import compute_signals


def _snapshot_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "snapshot_date": ["2025-12-22", "2025-12-22", "2025-12-22"],
            "membership_session": ["2025-12-22"] * 3,
            "snapshot_date_convention": ["date_only"] * 3,
            "source_id": ["riazarbi_ishares"] * 3,
            "source_name": ["iShares fixture"] * 3,
            "source_tier": ["SECONDARY_SNAPSHOT"] * 3,
            "source_security_name": ["Acme Inc", "Berkshire Hathaway Inc Class B", "Missing Co"],
            "source_ticker": ["A", "BRKB", "MISS"],
            "isin": ["US0000000001", "US0000000002", "US0000000003"],
            "cik": ["1", "2", "3"],
            "figi": ["", "", ""],
            "other_external_id": ["", "", ""],
            "mapped_security_id": ["wiki:A:ACME", "wiki:BRK.B:BERKSHIRE", ""],
            "mapping_status": ["mapped_exact", "mapped_exact", "unmapped"],
            "mapping_evidence": ["fixture", "fixture", "fixture"],
            "source_url": ["fixture"] * 3,
        }
    )


def _source_panel() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=6)
    rows = []
    for j, day in enumerate(dates):
        rows.append(
            {
                "date": day,
                "security_id": "old:A",
                "ticker": "A",
                "source_symbol": "A",
                "yahoo_close": [50.0, 51.0, 26.0, 27.0, 28.0, 29.0][j],
                "adjusted_close": [25.0, 25.5, 26.0, 27.0, 28.0, 29.0][j],
                "total_return": 99.0,
                "split_factor": 2.0 if j == 2 else 0.0,
                "dividend_cash": 0.0,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "volume": 100,
                "source": "fixture",
            }
        )
        rows.append({**rows[-1], "security_id": "duplicate:A"})
    for j, day in enumerate(dates[2:]):
        rows.append(
            {
                "date": day,
                "security_id": "old:BRKB",
                "ticker": "BRKB",
                "source_symbol": "BRK-B",
                "yahoo_close": 300.0 + j,
                "adjusted_close": 300.0 + j * 2,
                "total_return": 99.0,
                "split_factor": 0.0,
                "dividend_cash": 0.0,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "volume": 100,
                "source": "fixture",
            }
        )
    return pd.DataFrame(rows)


def _strategy() -> StrategyConfig:
    return StrategyConfig(drift_window=4, reversal_lookback=2, up_fraction_threshold=0.25)


def _phase3_exp(
    *,
    exp_id: str = "R1",
    lag: int = 2,
    value_price: str = "yahoo_close",
    paper_scaling: bool = False,
) -> ExperimentConfig:
    scaling = (
        ScalingConfig(
            mode="paper_walk_forward",
            windows=[
                ScalingWindow(
                    train_start=date(2020, 1, 1),
                    train_end=date(2020, 3, 1),
                    test_start=date(2020, 3, 1),
                    test_end=date(2020, 4, 30),
                )
            ],
        )
        if paper_scaling
        else ScalingConfig(mode="none")
    )
    return ExperimentConfig(
        id=exp_id,
        name=exp_id,
        universe_mode="current_constituents",
        period_mode="paper_three_windows" if paper_scaling else "continuous",
        start_date=None if paper_scaling else date(2020, 1, 1),
        end_date=None if paper_scaling else date(2020, 4, 30),
        paper_test_windows=[
            ClosedDateWindow(start=date(2020, 3, 1), end=date(2020, 4, 29))
        ]
        if paper_scaling
        else None,
        return_lag_sessions=lag,
        raw_signal_price_required=value_price == "reconstructed_nominal_close",
        point_in_time_membership_required=False,
        transaction_cost_bp=0.60,
        paper_scaling=paper_scaling,
        scaling=scaling,
        phase3_role=exp_id if exp_id in {"R0", "R1", "R2", "R3"} else "R1",
        value_price_column=value_price,  # type: ignore[arg-type]
        total_return_source="yahoo_adj_close_pct_change",
        survivorship_biased=True,
        survivorship_bias_label=SURVIVORSHIP_LABEL,
    )


def _signal_panel(test_multiplier: float = 1.0) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-01", periods=90)
    rows = []
    for i in range(8):
        price = 20.0 + i * 3.0
        adj = 20.0 + i * 3.0
        for j, day in enumerate(dates):
            ret = (0.0002 + i * 0.00001) if (j + i) % 3 else -(0.0001 + i * 0.00001)
            if day >= pd.Timestamp("2020-03-01"):
                ret *= test_multiplier
            price *= 1.0 + ret
            adj *= 1.0 + ret
            rows.append(
                {
                    "date": day,
                    "security_id": f"phase3:S{i}",
                    "ticker": f"S{i}",
                    "raw_close": price,
                    "total_return": ret,
                    "yahoo_close": price,
                    "yahoo_adj_close": adj,
                    "yahoo_adj_close_pct_change": ret,
                    "reconstructed_nominal_close": price * (2 if i == 0 else 1),
                    "survivorship_bias_label": SURVIVORSHIP_LABEL,
                }
            )
    return pd.DataFrame(rows)


def test_phase3_anchor_prefers_cached_secondary_snapshot_and_freezes_label():
    anchor = choose_phase3_anchor(_snapshot_rows(), fallback_anchor=None)
    assert len(anchor) == 3
    assert anchor["snapshot_date"].dt.date.astype(str).unique().tolist() == ["2025-12-22"]
    assert anchor.loc[anchor["source_ticker"].eq("BRKB"), "provider_symbol"].iloc[0] == "BRK-B"
    assert set(anchor["survivorship_bias_label"]) == {SURVIVORSHIP_LABEL}
    assert anchor["distance_from_publication_days"].iloc[0] == 34


def test_prepare_phase3_panel_dedupes_symbol_history_and_does_not_fabricate_prelisting_rows():
    anchor = choose_phase3_anchor(_snapshot_rows(), fallback_anchor=None)
    prepared = prepare_phase3_panel(_source_panel(), anchor)
    panel = prepared.panel

    assert not panel.duplicated(["date", "security_id"]).any()
    assert prepared.summary["duplicate_source_symbol_date_rows_removed"] == 6
    brk = panel[panel["security_id"].eq("phase3:BRK-B")]
    assert brk["date"].min() == pd.Timestamp("2024-01-04")
    missing = prepared.anchor.loc[prepared.anchor["provider_symbol"].eq("MISS")].iloc[0]
    assert missing["market_data_available"] is False or not bool(missing["market_data_available"])
    assert set(panel["survivorship_bias_label"]) == {SURVIVORSHIP_LABEL}


def test_yahoo_close_adj_close_and_reconstructed_nominal_close_remain_distinct():
    anchor = choose_phase3_anchor(_snapshot_rows(), fallback_anchor=None)
    prepared = prepare_phase3_panel(_source_panel(), anchor)
    panel = prepared.panel[prepared.panel["security_id"].eq("phase3:A")].sort_values("date")

    assert panel["yahoo_close"].iloc[0] == 50.0
    assert panel["yahoo_adj_close"].iloc[0] == 25.0
    assert panel["reconstructed_nominal_close"].iloc[0] == 100.0
    assert panel["reconstructed_nominal_close"].tolist() == [100.0, 102.0, 26.0, 27.0, 28.0, 29.0]
    assert panel["total_return"].iloc[1] == (25.5 / 25.0) - 1.0

    brk = prepared.panel[prepared.panel["security_id"].eq("phase3:BRK-B")].sort_values("date")
    assert brk["reconstructed_nominal_close"].tolist() == [300.0, 301.0, 302.0, 303.0]


def test_value_price_role_changes_only_raw_close_not_return_series():
    anchor = choose_phase3_anchor(_snapshot_rows(), fallback_anchor=None)
    prepared = prepare_phase3_panel(_source_panel(), anchor)
    yahoo = panel_for_value_price(prepared.panel, "yahoo_close")
    nominal = panel_for_value_price(prepared.panel, "reconstructed_nominal_close")

    assert not yahoo["raw_close"].equals(nominal["raw_close"])
    pd.testing.assert_series_equal(yahoo["total_return"], nominal["total_return"], check_names=False)
    assert set(yahoo["raw_close_source"]) == {"yahoo_close"}
    assert set(nominal["raw_close_source"]) == {"reconstructed_nominal_close"}


def test_r0_lag_one_and_r1_r2_lag_two_weight_dates_are_explicit():
    panel = _signal_panel()
    weights = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-01-01")],
            "security_id": ["phase3:S0"],
            "signal_weight": [1.0],
        }
    )
    lag1 = align_weights_to_returns(weights, panel, return_lag_sessions=1)
    lag2 = align_weights_to_returns(weights, panel, return_lag_sessions=2)
    ledger1, _ = daily_portfolio_returns(lag1, transaction_cost_bp=0.0)
    ledger2, _ = daily_portfolio_returns(lag2, transaction_cost_bp=0.0)

    aug1 = augment_security_ledger(ledger1, panel, _phase3_exp(exp_id="R0", lag=1))
    aug2 = augment_security_ledger(ledger2, panel, _phase3_exp(exp_id="R1", lag=2))
    assert aug1["weight_date"].iloc[0] == pd.Timestamp("2020-01-01")
    assert aug1["earned_return_date"].iloc[0] == pd.Timestamp("2020-01-02")
    assert aug2["weight_date"].iloc[0] == pd.Timestamp("2020-01-02")
    assert aug2["earned_return_date"].iloc[0] == pd.Timestamp("2020-01-03")


def test_changing_timing_alone_does_not_change_signal_values():
    panel = _signal_panel()
    strategy = _strategy()
    r0_signals = compute_signals(panel, strategy)
    r1_signals = compute_signals(panel, strategy)
    pd.testing.assert_series_equal(r0_signals["z_edge"], r1_signals["z_edge"])


def test_phase3_paper_scaling_is_train_only_and_recomputes_scaled_costs():
    strategy = _strategy()
    exp = _phase3_exp(exp_id="R1", lag=2, paper_scaling=True)
    result, _, _, _ = _run_experiment(_signal_panel(test_multiplier=1.0), strategy, exp)
    changed_test, _, _, _ = _run_experiment(_signal_panel(test_multiplier=25.0), strategy, exp)

    assert result.scaled is not None
    assert result.scaling_windows[0]["scale_factor"] == changed_test.scaling_windows[0]["scale_factor"]
    assert result.scaling_windows[0]["scale_factor"] > 1.0
    assert np.allclose(
        result.scaled.ledger["signal_weight"],
        result.scaled.ledger["unscaled_signal_weight"] * result.scaling_windows[0]["scale_factor"],
    )
    assert np.allclose(result.scaled.daily["cost"], result.scaled.daily["turnover"] * 0.60 * 1e-4)


def test_phase3_security_ledger_sums_to_daily_portfolio_return():
    panel = _signal_panel()
    strategy = _strategy()
    exp = _phase3_exp(exp_id="R3", lag=2, value_price="reconstructed_nominal_close")
    result, _, _, _ = _run_experiment(panel_for_value_price(panel, "reconstructed_nominal_close"), strategy, exp)
    ledger = augment_security_ledger(result.unscaled.ledger, panel, exp)
    recon = ledger_reconciliation(ledger, result.unscaled.daily, exp.id, scaled=False)
    assert recon["max_abs_gross_error"] < 1e-12
    assert recon["max_abs_net_error"] < 1e-12


def test_missing_returns_are_not_filled_with_zero_when_weighted():
    aligned = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
            "return_date": pd.to_datetime(["2024-01-03", "2024-01-03"]),
            "security_id": ["weighted", "zero_weight"],
            "signal_weight": [0.5, 0.0],
            "total_return": [np.nan, np.nan],
        }
    )
    ledger, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.0)
    assert ledger["missing_weighted_return"].tolist() == [True, False]
    assert daily["missing_returns"].iloc[0] == 1
    assert pd.isna(daily["gross_return"].iloc[0])


def test_first_day_drawdown_includes_initial_wealth_one():
    assert max_drawdown(pd.Series([-0.10, 0.05])) == pytest.approx(-0.10)


def test_r3_config_and_yearly_report_cover_continuous_periods_not_selected_windows_only():
    exp = load_experiment("experiments/R3_phase3_continuous_survivorship_biased.yaml")
    assert exp.period_mode == "continuous"
    assert exp.start_date == date(2010, 1, 1)
    assert exp.end_date == date(2024, 12, 31)
    assert exp.paper_test_windows is None

    daily = pd.DataFrame(
        {
            "return_date": pd.to_datetime([f"{year}-06-30" for year in range(2010, 2025)]),
            "net_return": [0.001] * 15,
            "gross_return": [0.001] * 15,
            "turnover": [0.1] * 15,
            "gross_exposure": [1.0] * 15,
            "net_exposure": [0.0] * 15,
        }
    )
    yearly = _yearly_metrics(daily)
    assert yearly["year"].tolist() == list(range(2010, 2025))


def test_phase3_experiment_outputs_declare_survivorship_bias():
    exp = load_experiment("experiments/R0_phase3_paper_like_reproduction.yaml")
    assert exp.survivorship_biased is True
    assert exp.survivorship_bias_label == SURVIVORSHIP_LABEL
    assert exp.value_price_column == "yahoo_close"
    assert exp.total_return_source == "yahoo_adj_close_pct_change"
