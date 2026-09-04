from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sharpe_replication.config import StrategyConfig
from sharpe_replication.phase3b_reproduction_gap import (
    INVALID_LABEL,
    compute_forensic_cross_sectional_signals,
    compute_forensic_time_series_features,
    invalid_lag0_report,
    paper_fingerprint_rows,
    return_alignment_label,
    return_alignment_scan,
    turnover_definition_audit,
)
from sharpe_replication.portfolio import construct_signal_weights


def _panel(returns: list[float], *, securities: int = 1, start: str = "2010-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=len(returns))
    rows = []
    for sid in range(securities):
        price = 10.0 + sid
        adj = 10.0 + sid
        for day, ret in zip(dates, returns, strict=True):
            price *= 1.0 + ret
            adj *= 1.0 + ret
            rows.append(
                {
                    "date": day,
                    "security_id": f"S{sid}",
                    "ticker": f"S{sid}",
                    "raw_close": price + sid,
                    "yahoo_close": price + sid,
                    "yahoo_adj_close": adj,
                    "total_return": ret,
                    "yahoo_adj_close_pct_change": ret,
                }
            )
    return pd.DataFrame(rows)


def test_phase3b_regime_window_excludes_current_return():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.6)
    panel = _panel([0.01, -0.01, 0.01, -0.01])

    valid = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    leaked = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="include_current",
        reversal_mode="prior_compounded",
    )

    assert valid.iloc[-1].up_fraction == pytest.approx(2 / 3)
    assert valid.iloc[-1].regime == 1.0
    assert leaked.iloc[-1].up_fraction == pytest.approx(1 / 3)
    assert leaked.iloc[-1].regime == 0.0


def test_phase3b_regime_threshold_is_strict_greater_than_sixty_percent():
    cfg = StrategyConfig(drift_window=5, reversal_lookback=2, up_fraction_threshold=0.6)
    panel = _panel([0.01, 0.01, 0.01, -0.01, -0.01, 0.02])

    out = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )

    assert out.iloc[-1].up_fraction == pytest.approx(0.6)
    assert out.iloc[-1].regime == 0.0


def test_phase3b_reversal_endpoint_and_compounded_prior_windows_match_boundaries():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.0)
    panel = _panel([0.00, 0.10, 0.20, 0.30, -0.10])

    compounded = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    endpoint = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="endpoint_prior",
    )
    invalid_current = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="include_current_compounded",
    )

    assert compounded.iloc[3].ret_10 == pytest.approx((1.10 * 1.20) - 1.0)
    assert endpoint.iloc[3].ret_10 == pytest.approx(compounded.iloc[3].ret_10)
    assert invalid_current.iloc[3].ret_10 == pytest.approx((1.20 * 1.30) - 1.0)


def test_phase3b_lag0_diagnostic_is_explicitly_invalid():
    assert "INVALID" in return_alignment_label(0)

    weights = pd.DataFrame(
        {
            "date": pd.bdate_range("2010-01-01", periods=4),
            "security_id": ["S0"] * 4,
            "signal_weight": [1.0, 1.0, 1.0, 1.0],
        }
    )
    report = invalid_lag0_report(weights, _panel([0.01, 0.02, -0.01, 0.03]))

    assert report["variant_id"] == "R_INVALID_LAG0"
    assert report["is_valid_strategy"] is False
    assert report["diagnostic_label"] == INVALID_LABEL


def test_phase3b_return_alignment_scan_uses_supplied_weights_without_signal_recompute():
    panel = _panel([0.00, 0.10, -0.20, 0.30], securities=2)
    weights = pd.DataFrame(
        {
            "date": [pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-01")],
            "security_id": ["S0", "S1"],
            "signal_weight": [0.75, -0.25],
        }
    )

    scan = return_alignment_scan(weights, panel, offsets=range(2))
    lag0 = scan[scan["return_offset_sessions"].eq(0)].iloc[0]
    lag1 = scan[scan["return_offset_sessions"].eq(1)].iloc[0]

    assert bool(lag0["is_valid_strategy"]) is False
    assert lag1["date_interpretation"] == "phase3_R0_same_close_execution_suspect"
    assert set(scan["return_offset_sessions"]) == {0, 1}


def test_phase3b_active_edge_standardization_excludes_inactive_zeros():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.5)
    features = pd.DataFrame(
        {
            "date": [pd.Timestamp("2010-01-04")] * 3,
            "security_id": ["A", "B", "C"],
            "ticker": ["A", "B", "C"],
            "raw_close": [10.0, 20.0, 1.0],
            "total_return": [0.0, 0.0, 0.0],
            "inverse_price": [0.10, 0.05, 1.00],
            "reversal_raw": [1.0, -1.0, 10.0],
            "up_fraction": [1.0, 1.0, 0.0],
            "regime": [1.0, 1.0, 0.0],
            "eligible": [True, True, True],
        }
    )

    active = compute_forensic_cross_sectional_signals(features, cfg, edge_mode="active_edge_only")
    inactive_zero = compute_forensic_cross_sectional_signals(
        features,
        cfg,
        edge_mode="edge_z_include_inactive_zero",
    )

    assert pd.isna(active.loc[active["security_id"].eq("C"), "z_edge"].iloc[0])
    assert np.isfinite(inactive_zero.loc[inactive_zero["security_id"].eq("C"), "z_edge"].iloc[0])


def test_phase3b_long_short_normalization_uses_z_edge_signs():
    cfg = StrategyConfig()
    signals = pd.DataFrame(
        {
            "date": [pd.Timestamp("2010-01-04")] * 3,
            "security_id": ["L1", "L2", "S1"],
            "z_edge": [1.0, 3.0, -2.0],
            "eligible": [True, True, True],
        }
    )

    weights = construct_signal_weights(signals, cfg)

    assert weights.loc[weights["signal_weight"].gt(0), "signal_weight"].sum() == pytest.approx(0.5)
    assert weights.loc[weights["signal_weight"].lt(0), "signal_weight"].sum() == pytest.approx(-0.5)
    assert weights["signal_weight"].abs().sum() == pytest.approx(1.0)


def test_phase3b_turnover_audit_reports_one_way_and_gross_traded_conventions():
    fingerprints = pd.DataFrame(
        {
            "variant_id": ["x"],
            "window": ["test_2010"],
            "avg_one_way_turnover": [0.31],
            "avg_gross_traded_notional": [0.62],
        }
    )

    out = turnover_definition_audit(fingerprints).iloc[0]

    assert out["engine_turnover_convention"] == "0.5 * sum(abs(w_t - w_t_minus_1))"
    assert out["gross_traded_convention"] == "sum(abs(w_t - w_t_minus_1))"


def test_phase3b_paper_fingerprint_generation_smoke():
    cfg = StrategyConfig(drift_window=3, reversal_lookback=2, up_fraction_threshold=0.0)
    panel = _panel([0.001] * 20, securities=4, start="2009-12-15")
    features = compute_forensic_time_series_features(
        panel,
        cfg,
        regime_mode="exclude_current",
        reversal_mode="prior_compounded",
    )
    features["eligible"] = True
    signals = compute_forensic_cross_sectional_signals(features, cfg)
    weights = construct_signal_weights(signals, cfg)
    from sharpe_replication.phase3b_reproduction_gap import align_weights_to_returns_forensic
    from sharpe_replication.portfolio import daily_portfolio_returns

    aligned = align_weights_to_returns_forensic(weights, panel, 1)
    ledger, daily = daily_portfolio_returns(aligned, transaction_cost_bp=0.0)
    from sharpe_replication.phase3b_reproduction_gap import VariantRun

    rows = paper_fingerprint_rows(
        VariantRun(
            variant_id="fixture",
            features=features,
            signals=signals,
            weights=weights,
            aligned=aligned,
            ledger=ledger,
            daily=daily,
            is_valid_strategy=True,
            diagnostic_label="",
        )
    )

    assert {"avg_long_positions", "avg_short_positions", "median_daily_return"} <= set(rows.columns)
    assert rows["window"].str.contains("test_2010").any()


def test_phase3b_invalid_variants_are_not_production_experiment_specs():
    assert not list(Path("experiments").glob("*INVALID*"))
    assert not list(Path("experiments").glob("*LAG0*"))
