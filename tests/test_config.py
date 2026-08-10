from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sharpe_replication.config import (
    ExperimentConfig,
    StrategySpec,
    load_assumptions,
    load_experiment,
    load_strategy,
    load_strategy_spec,
)

ROOT = Path(__file__).resolve().parents[1]


def test_strategy_and_assumptions_specs_load_strictly():
    cfg = load_strategy(ROOT / "spec/paper_strategy.yaml")
    spec = load_strategy_spec(ROOT / "spec/paper_strategy.yaml")
    assumptions = load_assumptions(ROOT / "spec/assumptions.yaml")

    assert cfg.drift_window == 63
    assert cfg.rebalance_frequency == "daily"
    assert spec.conventions.corrected_first_return_after_signal_sessions == 2
    assert assumptions.certification.fail_if_future_membership_leakage_detected is True


def test_all_experiments_load_with_expected_names_and_scaling_modes():
    expected = {
        "P0": ("paper_reproduction", "paper_walk_forward"),
        "P1": ("safe_daily_timing", "paper_walk_forward"),
        "P2": ("safe_timing_and_corporate_actions", "paper_walk_forward"),
        "P3": ("add_point_in_time_universe", "paper_walk_forward"),
        "P4": ("continuous_historical_clean", "none"),
        "P5": ("post_publication_forward", "none"),
    }
    for exp_id, (name, scaling_mode) in expected.items():
        matches = list((ROOT / "experiments").glob(f"{exp_id}_*.yaml"))
        assert len(matches) == 1
        exp = load_experiment(matches[0])
        assert exp.id == exp_id
        assert exp.name == name
        assert exp.scaling is not None
        assert exp.scaling.mode == scaling_mode


def test_paper_walk_forward_intervals_are_explicit_half_open_dates():
    exp = load_experiment(ROOT / "experiments/P0_paper_reproduction.yaml")
    assert exp.scaling is not None
    assert [(w.train_start, w.train_end, w.test_start, w.test_end) for w in exp.scaling.windows] == [
        (date(2005, 1, 1), date(2010, 1, 1), date(2010, 1, 1), date(2011, 1, 1)),
        (date(2010, 1, 1), date(2015, 1, 1), date(2015, 1, 1), date(2016, 1, 1)),
        (date(2015, 1, 1), date(2020, 1, 1), date(2020, 1, 1), date(2021, 1, 1)),
    ]


def test_unknown_strategy_field_fails_validation():
    with (ROOT / "spec/paper_strategy.yaml").open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["strategy"]["not_a_real_parameter"] = 123

    with pytest.raises(ValidationError):
        StrategySpec.model_validate(data)


def test_unknown_experiment_field_fails_validation():
    data = load_experiment(ROOT / "experiments/P4_continuous_oos.yaml").model_dump()
    data["ignored_field"] = True

    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(data)


def test_certified_experiments_require_corrected_return_lag():
    with pytest.raises(ValidationError):
        ExperimentConfig(
            id="P_BAD",
            name="bad",
            universe_mode="point_in_time",
            period_mode="continuous",
            start_date=date(2020, 1, 1),
            return_lag_sessions=1,
            raw_signal_price_required=True,
            point_in_time_membership_required=True,
            transaction_cost_bp=0.6,
            paper_scaling=False,
            certified=True,
        )
