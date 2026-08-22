from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from sharpe_replication.cli import app
from sharpe_replication.config import StrategyConfig, load_experiment
from sharpe_replication.phase4_regime_edge import (
    HORIZONS,
    PRIMARY_END,
    PRIMARY_HORIZON,
    PRIMARY_START,
    PRIMARY_YEARS,
    Phase4Dataset,
    add_future_return_targets,
    daily_interaction_regression,
    delayed_target_column,
    eligible_analysis_frame,
    horizon_summary,
    matched_random_masks,
    prepare_phase4_signals,
    primary_sample,
    random_mask_summary,
    regime_prevalence,
    transitions,
    upfraction_binning,
    write_phase4_reports,
    yearly_completeness,
)

ROOT = Path(__file__).resolve().parents[1]


def _phase4_panel(
    returns: list[float],
    *,
    securities: int = 3,
    start: str = "2010-01-01",
) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(returns))
    rows = []
    for sid in range(securities):
        close = 10.0 + sid
        adj = 10.0 + sid
        for day, ret in zip(dates, returns, strict=True):
            close *= 1.0 + ret
            adj *= 1.0 + ret
            rows.append(
                {
                    "date": day,
                    "security_id": f"S{sid:02d}",
                    "ticker": f"S{sid:02d}",
                    "yahoo_close": close + sid,
                    "yahoo_adj_close": adj,
                    "yahoo_adj_close_pct_change": ret,
                }
            )
    return pd.DataFrame(rows)


def _analysis_frame(dates: pd.DatetimeIndex | list[pd.Timestamp], *, securities: int = 40) -> pd.DataFrame:
    rows = []
    for date_value in dates:
        for sid in range(securities):
            regime = int(sid >= securities // 2)
            base = float(sid)
            rows.append(
                {
                    "date": pd.Timestamp(date_value),
                    "security_id": f"S{sid:02d}",
                    "ticker": f"S{sid:02d}",
                    "eligible": True,
                    "base": base,
                    "value_score": base / securities,
                    "reversal_z": (sid - securities / 2) / securities,
                    "regime": regime,
                    "up_fraction": 0.40 + 0.01 * sid,
                    "future_return_1d": 0.001 * (sid % 7 - 3),
                    "future_return_2d": 0.002 * (sid % 7 - 3),
                    "future_return_5d": 0.005 * (sid % 7 - 3),
                    "future_return_10d": 0.010 * (sid % 7 - 3),
                    "future_return_1d_delayed": -0.001 * (sid % 5 - 2),
                }
            )
    return pd.DataFrame(rows)


def test_prepare_phase4_signals_uses_previous_window_and_excludes_current_return():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.6)
    panel = _phase4_panel([0.01, 0.01, -0.01, -0.01])

    out = prepare_phase4_signals(panel, cfg)
    last = out[out["date"].eq(pd.Timestamp("2010-01-06"))]

    assert last["up_fraction"].nunique() == 1
    assert last["up_fraction"].iloc[0] == pytest.approx(2 / 3)
    assert last["regime"].unique().tolist() == [1.0]


def test_prepare_phase4_signals_applies_strict_regime_threshold():
    cfg = StrategyConfig(drift_window=5, reversal_lookback=2, up_fraction_threshold=0.6)
    panel = _phase4_panel([0.01, 0.01, 0.01, -0.01, -0.01, 0.02])

    out = prepare_phase4_signals(panel, cfg)
    last = out[out["date"].eq(pd.Timestamp("2010-01-08"))]

    assert last["up_fraction"].nunique() == 1
    assert last["up_fraction"].iloc[0] == pytest.approx(0.6)
    assert last["regime"].unique().tolist() == [0.0]


def test_prepare_phase4_signals_keeps_phase3b_paper_spec_columns_and_label():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.6)
    out = prepare_phase4_signals(_phase4_panel([0.01, -0.01, 0.02, 0.03]), cfg)

    assert {"value_score", "reversal_z", "base", "regime", "up_fraction"} <= set(out.columns)
    assert out["regime_mode"].dropna().unique().tolist() == ["exclude_current"]
    assert out["reversal_mode"].dropna().unique().tolist() == ["prior_compounded"]
    assert out["edge_standardization_mode"].dropna().unique().tolist() == ["active_edge_only"]
    assert out["phase4_signal_definition"].str.contains("strict UpFraction > 0.60").all()


def test_add_future_return_targets_are_causal_for_each_horizon():
    panel = _phase4_panel([0.00, 0.10, 0.20, 0.30, 0.40, 0.50], securities=1)
    signals = panel[["date", "security_id", "ticker"]].copy()

    out = add_future_return_targets(signals, panel, horizons=(1, 2, 5))

    first = out.iloc[0]
    assert first["future_return_1d"] == pytest.approx(0.10)
    assert first["future_return_2d"] == pytest.approx((1.10 * 1.20) - 1.0)
    assert first["future_return_5d"] == pytest.approx((1.10 * 1.20 * 1.30 * 1.40 * 1.50) - 1.0)


def test_delayed_target_column_earns_t_plus_one_to_t_plus_two_return():
    frame = _phase4_panel([0.00, 0.10, 0.20, 0.30], securities=1)

    out = delayed_target_column(frame)

    assert out.iloc[0]["future_return_1d_delayed"] == pytest.approx(0.20)
    assert pd.isna(out.iloc[-2]["future_return_1d_delayed"])


def test_future_targets_do_not_jump_over_missing_calendar_sessions():
    panel = _phase4_panel([0.00, 0.10, 0.20, 0.30], securities=2)
    missing_date = panel["date"].drop_duplicates().iloc[1]
    panel = panel[~(panel["security_id"].eq("S00") & panel["date"].eq(missing_date))]
    signals = panel[["date", "security_id", "ticker"]].copy()

    informational = add_future_return_targets(signals, panel, horizons=(1,))
    delayed = delayed_target_column(panel)
    first_s0 = informational[informational["security_id"].eq("S00")].iloc[0]
    first_s0_delayed = delayed[delayed["security_id"].eq("S00")].iloc[0]

    assert pd.isna(first_s0["future_return_1d"])
    assert pd.isna(first_s0_delayed["future_return_1d_delayed"])


def test_daily_interaction_regression_uses_same_date_cross_section():
    date_value = pd.Timestamp("2020-01-02")
    frame = _analysis_frame([date_value], securities=40)
    frame["future_return_1d"] = (
        1.0
        + 2.0 * frame["base"]
        + 3.0 * frame["regime"]
        + 4.0 * frame["base"] * frame["regime"]
    )

    out = daily_interaction_regression(frame, "future_return_1d").iloc[0]

    assert bool(out["skipped"]) is False
    assert out["date"] == date_value
    assert out["beta_signal"] == pytest.approx(2.0)
    assert out["beta_regime_or_upfraction"] == pytest.approx(3.0)
    assert out["beta_interaction"] == pytest.approx(4.0)


def test_matched_random_masks_preserve_exact_active_count_per_date_and_are_seed_deterministic():
    frame = _analysis_frame(pd.bdate_range("2020-01-02", periods=3), securities=40)

    first = matched_random_masks(frame, "future_return_1d", repetitions=5, seed=123)
    second = matched_random_masks(frame, "future_return_1d", repetitions=5, seed=123)
    summary = random_mask_summary({"mean_base_ic": 0.0}, first)

    pd.testing.assert_frame_equal(first, second)
    assert first["replication"].tolist() == [0, 1, 2, 3, 4]
    assert first["seed"].tolist() == [123] * 5
    assert summary["matching"] == "exact same REGIME=1 active-security count per date"
    assert summary["repetitions"] == 5


def test_upfraction_bins_cover_each_row_exactly_once():
    frame = _analysis_frame([pd.Timestamp("2020-01-02")], securities=7)
    frame["up_fraction"] = [0.44, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

    out = upfraction_binning(frame, "future_return_1d")

    assert out["stock_day_count"].tolist() == [1, 1, 1, 1, 1, 1, 1]
    assert int(out["stock_day_count"].sum()) == len(frame)


def test_primary_sample_includes_all_and_only_2010_to_2024_rows():
    frame = pd.DataFrame(
        {
            "date": [
                pd.Timestamp("2009-12-31"),
                PRIMARY_START,
                pd.Timestamp("2018-06-01"),
                PRIMARY_END,
                pd.Timestamp("2025-01-01"),
            ]
        }
    )

    out = primary_sample(frame)

    assert out["date"].tolist() == [PRIMARY_START, pd.Timestamp("2018-06-01"), PRIMARY_END]


def test_yearly_completeness_reports_every_primary_year():
    dates = [pd.Timestamp(year=year, month=1, day=4) for year in PRIMARY_YEARS]
    frame = _analysis_frame(dates, securities=40)
    ic_daily = pd.DataFrame(
        {
            "date": np.repeat(dates, 2),
            "regime": [0, 1] * len(dates),
            "spearman_ic": [0.01, -0.01] * len(dates),
        }
    )
    spread_daily = pd.DataFrame(
        {
            "date": np.repeat(dates, 2),
            "regime": [0, 1] * len(dates),
            "spread": [0.02, -0.02] * len(dates),
        }
    )
    interactions = pd.DataFrame({"date": dates, "beta_interaction": np.zeros(len(dates))})

    out = yearly_completeness(frame, ic_daily, spread_daily, interactions)

    assert out["year"].tolist() == list(PRIMARY_YEARS)


def test_transitions_report_entry_and_exit_event_windows():
    dates = pd.bdate_range("2020-01-02", periods=4)
    frame = _analysis_frame(dates, securities=40)
    frame.loc[frame["security_id"].eq("S39"), "regime"] = [0, 1, 1, 0]

    entries, exits = transitions(frame, "future_return_1d", window=1)

    assert entries["event_type"].dropna().unique().tolist() == ["ENTRY"]
    assert exits["event_type"].dropna().unique().tolist() == ["EXIT"]
    assert set(entries["event_day"]) == {-1, 0, 1}
    assert set(exits["event_day"]) == {-1, 0}


def test_horizon_summary_marks_only_fixed_primary_horizon():
    dates = pd.bdate_range("2020-01-02", periods=2)
    signals = _analysis_frame(dates, securities=40)

    out = horizon_summary(signals)

    assert out["horizon_days"].tolist() == list(HORIZONS)
    assert out.loc[out["primary"], "horizon_days"].tolist() == [PRIMARY_HORIZON]


def test_eligible_analysis_frame_rejects_missing_required_columns():
    with pytest.raises(ValueError, match="Analysis frame missing columns"):
        eligible_analysis_frame(pd.DataFrame({"date": [pd.Timestamp("2020-01-02")]}), "future_return_1d")


def test_phase3b_and_phase4_production_specs_keep_corrected_inputs_unchanged():
    phase3 = load_experiment(ROOT / "experiments/R3_phase3_continuous_survivorship_biased.yaml")
    phase4 = load_experiment(ROOT / "experiments/P4_continuous_oos.yaml")

    assert phase3.return_lag_sessions == 2
    assert phase3.value_price_column == "reconstructed_nominal_close"
    assert phase3.total_return_source == "yahoo_adj_close_pct_change"
    assert phase4.return_lag_sessions == 2
    assert phase4.universe_mode == "point_in_time"
    assert phase4.primary_historical_result is True
    assert phase4.start_date == date(2009, 1, 1)
    assert phase4.end_date == date(2024, 12, 31)


def test_invalid_leakage_modes_are_not_experiment_specs():
    experiment_text = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("experiments/*.yaml"))

    assert "include_current" not in experiment_text
    assert "R_INVALID" not in experiment_text
    assert "LAG0" not in experiment_text


def test_write_phase4_reports_summary_confirms_no_invalid_leakage_mode(monkeypatch, tmp_path):
    dates = [pd.Timestamp(year=year, month=1, day=4) for year in PRIMARY_YEARS]
    signals = _analysis_frame(dates, securities=40)
    panel = signals[["date", "security_id", "ticker"]].copy()
    panel["yahoo_adj_close_pct_change"] = 0.0

    monkeypatch.setattr(
        "sharpe_replication.phase4_regime_edge.load_phase4_dataset",
        lambda phase3_dir, strategy: Phase4Dataset(
            panel=panel,
            signals=signals,
            manifest={
                "input_panel": str(tmp_path / "phase3_input.parquet"),
                "input_panel_sha256": "fixture-sha",
            },
        ),
    )
    monkeypatch.setattr("sharpe_replication.phase4_regime_edge._sha256_file", lambda path: "fixture-sha")
    monkeypatch.setattr(
        "sharpe_replication.phase4_regime_edge.portfolio_comparison",
        lambda signals, panel, strategy: pd.DataFrame({"portfolio": [], "sharpe": []}),
    )
    monkeypatch.setattr(
        "sharpe_replication.phase4_regime_edge.concentration_diagnostics",
        lambda frame, target_col: pd.DataFrame({"security_id": [], "contribution": []}),
    )

    summary = write_phase4_reports(
        phase3_dir=tmp_path,
        strategy=StrategyConfig(),
        out_dir=tmp_path / "phase4",
        random_mask_repetitions=3,
        bootstrap_repetitions=3,
    )

    confirmations = summary["confirmations"]
    assert summary["primary_test"]["horizon_days"] == PRIMARY_HORIZON
    assert confirmations["all_2010_2024_years_included"] is True
    assert confirmations["primary_horizon_fixed"] == PRIMARY_HORIZON
    assert confirmations["phase3b_invalid_leakage_variant_used"] is False
    assert confirmations["window_63_days_unchanged"] is True
    assert confirmations["threshold_strict_gt_60pct_unchanged"] is True
    assert summary["random_mask_summary"]["repetitions"] == 3


def test_regime_prevalence_uses_exhaustive_valid_regime_split():
    frame = _analysis_frame([pd.Timestamp("2020-01-02")], securities=40)

    out = regime_prevalence(frame)
    overall = out[out["period"].eq("overall")].iloc[0]

    assert overall["eligible_stock_days"] == 40
    assert overall["regime_1_stock_days"] == 20
    assert overall["regime_1_fraction"] == pytest.approx(0.5)


def test_phase4_cli_reports_primary_beta3_without_post_run_key_error(monkeypatch):
    monkeypatch.setattr(
        "sharpe_replication.cli.write_phase4_reports",
        lambda **kwargs: {
            "classification": "NO_EVIDENCE",
            "primary_interaction": {"mean": -0.001},
        },
    )

    result = CliRunner().invoke(app, ["phase4-regime-edge"])

    assert result.exit_code == 0
    assert '"primary_beta3": -0.001' in result.stdout
    assert '"primary_beta3_positive": false' in result.stdout
