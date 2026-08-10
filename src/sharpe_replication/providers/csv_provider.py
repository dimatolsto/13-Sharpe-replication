from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .base import MarketDataProvider, ProviderCapabilities


class CSVPanelProvider(MarketDataProvider):
    """Provider for a normalized local CSV/Parquet panel.

    This is intentionally boring: authoritative replication should be runnable from immutable local
    snapshots rather than requiring live vendor calls during the backtest.
    """

    def __init__(self, path: str | Path, capabilities: ProviderCapabilities):
        self.path = Path(path)
        self._capabilities = capabilities

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def fetch_daily_panel(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        if self.path.suffix.lower() == ".parquet":
            df = pd.read_parquet(self.path)
        else:
            df = pd.read_csv(self.path)
        df["date"] = pd.to_datetime(df["date"])
        mask = df["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        if symbols:
            mask &= df["ticker"].isin(symbols)
        return df.loc[mask].copy()
