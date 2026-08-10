from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class ProviderCapabilities:
    historical_raw_close: bool
    adjusted_close: bool
    dividends: bool
    splits: bool
    delisted_securities: bool
    point_in_time_index_membership: bool
    stable_security_ids: bool
    verified: bool = False
    notes: str = ""


class MarketDataProvider(ABC):
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def fetch_daily_panel(self, symbols: list[str], start: date, end: date) -> pd.DataFrame: ...
