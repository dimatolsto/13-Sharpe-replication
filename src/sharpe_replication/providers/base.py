from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


def _coerce_status(value: CapabilityStatus | bool | str) -> CapabilityStatus:
    if isinstance(value, CapabilityStatus):
        return value
    if isinstance(value, bool):
        return CapabilityStatus.SUPPORTED if value else CapabilityStatus.UNSUPPORTED
    return CapabilityStatus(value)


@dataclass(frozen=True)
class ProviderCapabilities:
    historical_raw_close: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    adjusted_close: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    dividends: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    splits: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    delisted_securities: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    ticker_history: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    point_in_time_index_membership: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    stable_security_ids: CapabilityStatus | bool | str = CapabilityStatus.UNVERIFIED
    verified: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in [
            "historical_raw_close",
            "adjusted_close",
            "dividends",
            "splits",
            "delisted_securities",
            "ticker_history",
            "point_in_time_index_membership",
            "stable_security_ids",
        ]:
            object.__setattr__(self, field_name, _coerce_status(getattr(self, field_name)))

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "historical_raw_close": self.historical_raw_close.value,
            "adjusted_close": self.adjusted_close.value,
            "dividends": self.dividends.value,
            "splits": self.splits.value,
            "delisted_securities": self.delisted_securities.value,
            "ticker_history": self.ticker_history.value,
            "point_in_time_index_membership": self.point_in_time_index_membership.value,
            "stable_security_ids": self.stable_security_ids.value,
            "verified": self.verified,
            "notes": self.notes,
        }


class MarketDataProvider(ABC):
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def fetch_daily_panel(self, symbols: list[str], start: date, end: date) -> pd.DataFrame: ...
