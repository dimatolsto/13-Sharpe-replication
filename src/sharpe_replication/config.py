from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrategyConfig(StrictBaseModel):
    drift_window: int = 63
    up_fraction_threshold: float = 0.60
    reversal_lookback: int = 10
    value_weight: float = 0.70
    reversal_weight: float = 0.30
    long_exposure: float = 0.50
    short_exposure: float = 0.50
    gross_exposure: float = 1.00
    rebalance_frequency: Literal["daily"] = "daily"
    paper_transaction_cost_bp: float = 0.60
    annual_vol_target: float = 0.12
    max_drawdown_target: float = 0.15
    risk_free_rate: float = 0.0

    @model_validator(mode="after")
    def validate_frozen_arithmetic(self) -> StrategyConfig:
        if abs(self.value_weight + self.reversal_weight - 1.0) > 1e-12:
            raise ValueError("value_weight + reversal_weight must equal 1")
        if abs(self.long_exposure + self.short_exposure - self.gross_exposure) > 1e-12:
            raise ValueError("long + short exposure must equal gross exposure")
        if self.drift_window <= self.reversal_lookback:
            raise ValueError("drift window must exceed reversal lookback")
        return self


class StrategyConventions(StrictBaseModel):
    value_feature: Literal["inverse_raw_nominal_close_percentile"]
    reversal_feature: Literal["negative_trailing_total_return_zscore"]
    gate: Literal["up_fraction_strictly_greater_than_threshold"]
    active_edge_standardization: Literal["cross_sectional_zscore_after_gate"]
    long_short_rule: Literal["sign_of_standardized_active_edge"]
    side_weighting: Literal["proportional_to_absolute_zscore_within_side"]
    corrected_execution_lag_sessions: int = Field(ge=1)
    corrected_first_return_after_signal_sessions: int = Field(ge=1)
    membership_end_inclusive: bool


class StrategySpec(StrictBaseModel):
    version: int
    frozen: bool
    strategy: StrategyConfig
    conventions: StrategyConventions


class HardRequirements(StrictBaseModel):
    daily_data_only: bool
    no_same_close_earnings_in_corrected_runs: bool
    point_in_time_membership_for_P3_plus: bool
    raw_nominal_close_for_value_signal: bool
    corporate_action_correct_return_for_pnl: bool
    stable_security_identifier_required: bool
    no_parameter_optimization: bool


class CertificationRequirements(StrictBaseModel):
    fail_if_raw_close_semantics_unverified: bool
    fail_if_membership_source_not_point_in_time: bool
    fail_if_accounting_reconciliation_fails: bool
    fail_if_future_membership_leakage_detected: bool
    fail_if_corrected_run_uses_return_lag_less_than_2: bool


class AssumptionsSpec(StrictBaseModel):
    version: int
    hard_requirements: HardRequirements
    certification: CertificationRequirements


class ClosedDateWindow(StrictBaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> ClosedDateWindow:
        if self.end < self.start:
            raise ValueError("window end must be on or after start")
        return self


class ScalingWindow(StrictBaseModel):
    train_start: date
    train_end: date
    test_start: date
    test_end: date

    @model_validator(mode="after")
    def validate_half_open_windows(self) -> ScalingWindow:
        if self.train_end <= self.train_start:
            raise ValueError("train_end must be after train_start")
        if self.test_end <= self.test_start:
            raise ValueError("test_end must be after test_start")
        return self


class ScalingConfig(StrictBaseModel):
    mode: Literal["none", "paper_walk_forward", "rolling_5y_1y_extension"] = "none"
    windows: list[ScalingWindow] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_windows(self) -> ScalingConfig:
        if self.mode == "none" and self.windows:
            raise ValueError("scaling mode 'none' cannot define windows")
        if self.mode != "none" and not self.windows:
            raise ValueError(f"scaling mode {self.mode!r} requires explicit windows")
        return self


class ExperimentConfig(StrictBaseModel):
    id: str
    name: str
    universe_mode: Literal["current_constituents", "point_in_time"]
    period_mode: Literal["paper_three_windows", "continuous"]
    return_lag_sessions: int = Field(ge=1)
    raw_signal_price_required: bool
    point_in_time_membership_required: bool
    transaction_cost_bp: float = Field(ge=0)
    paper_scaling: bool
    certified: bool = False
    start_date: date | None = None
    end_date: date | None = None
    paper_test_windows: list[ClosedDateWindow] | None = None
    scaling: ScalingConfig | None = None
    primary_historical_result: bool = False
    primary_forward_result: bool = False

    @model_validator(mode="after")
    def validate_experiment(self) -> ExperimentConfig:
        if self.certified and self.return_lag_sessions < 2:
            raise ValueError("Certified corrected runs must use return_lag_sessions >= 2")
        if self.point_in_time_membership_required and self.universe_mode != "point_in_time":
            raise ValueError("PIT-required experiment must use point_in_time universe")
        if self.period_mode == "paper_three_windows" and not self.paper_test_windows:
            raise ValueError("paper_three_windows requires explicit paper_test_windows")
        if self.period_mode == "continuous" and self.paper_test_windows:
            raise ValueError("continuous experiments cannot define paper_test_windows")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.scaling is None:
            if self.paper_scaling:
                raise ValueError("paper_scaling=true requires explicit scaling config")
            self.scaling = ScalingConfig()
        if self.paper_scaling and self.scaling.mode == "none":
            raise ValueError("paper_scaling=true requires non-none scaling mode")
        if not self.paper_scaling and self.scaling.mode != "none":
            raise ValueError("paper_scaling=false requires scaling mode 'none'")
        if self.scaling.mode == "paper_walk_forward" and self.period_mode != "paper_three_windows":
            raise ValueError("paper_walk_forward scaling applies only to paper_three_windows")
        return self


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TypeError(f"Expected mapping in {path}")
    return data


def load_strategy_spec(path: str | Path = "spec/paper_strategy.yaml") -> StrategySpec:
    return StrategySpec.model_validate(_load_yaml(path))


def load_strategy(path: str | Path = "spec/paper_strategy.yaml") -> StrategyConfig:
    return load_strategy_spec(path).strategy


def load_experiment(path: str | Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(_load_yaml(path))


def load_assumptions(path: str | Path = "spec/assumptions.yaml") -> AssumptionsSpec:
    return AssumptionsSpec.model_validate(_load_yaml(path))
