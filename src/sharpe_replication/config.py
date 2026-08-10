from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class StrategyConfig(BaseModel):
    drift_window: int = 63
    up_fraction_threshold: float = 0.60
    reversal_lookback: int = 10
    value_weight: float = 0.70
    reversal_weight: float = 0.30
    long_exposure: float = 0.50
    short_exposure: float = 0.50
    gross_exposure: float = 1.00
    paper_transaction_cost_bp: float = 0.60
    annual_vol_target: float = 0.12
    max_drawdown_target: float = 0.15
    risk_free_rate: float = 0.0

    @model_validator(mode="after")
    def validate_frozen_arithmetic(self) -> "StrategyConfig":
        if abs(self.value_weight + self.reversal_weight - 1.0) > 1e-12:
            raise ValueError("value_weight + reversal_weight must equal 1")
        if abs(self.long_exposure + self.short_exposure - self.gross_exposure) > 1e-12:
            raise ValueError("long + short exposure must equal gross exposure")
        if self.drift_window <= self.reversal_lookback:
            raise ValueError("drift window must exceed reversal lookback")
        return self


class ExperimentConfig(BaseModel):
    id: str
    name: str
    universe_mode: str
    period_mode: str
    return_lag_sessions: int = Field(ge=1)
    raw_signal_price_required: bool
    point_in_time_membership_required: bool
    transaction_cost_bp: float = Field(ge=0)
    paper_scaling: bool
    certified: bool = False
    start_date: str | None = None
    end_date: str | None = None
    paper_test_windows: list[list[str]] | None = None
    primary_historical_result: bool = False
    primary_forward_result: bool = False

    @model_validator(mode="after")
    def validate_certified_timing(self) -> "ExperimentConfig":
        if self.certified and self.return_lag_sessions < 2:
            raise ValueError("Certified corrected runs must use return_lag_sessions >= 2")
        if self.point_in_time_membership_required and self.universe_mode != "point_in_time":
            raise ValueError("PIT-required experiment must use point_in_time universe")
        return self


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def load_strategy(path: str | Path = "spec/paper_strategy.yaml") -> StrategyConfig:
    data = _load_yaml(path)
    return StrategyConfig.model_validate(data["strategy"])


def load_experiment(path: str | Path) -> ExperimentConfig:
    return ExperimentConfig.model_validate(_load_yaml(path))
