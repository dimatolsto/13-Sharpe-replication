from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .normalize import normalize_security_master
from .sp500_events import normalize_reported_symbol, yahoo_symbol_from_reported


@dataclass(frozen=True)
class YahooAlias:
    security_id: str
    provider_symbol: str
    effective_start: pd.Timestamp
    effective_end: pd.Timestamp | None = None
    source: str = "security_master"

    def to_dict(self) -> dict[str, Any]:
        return {
            "security_id": self.security_id,
            "provider": "yahoo",
            "provider_symbol": self.provider_symbol,
            "effective_start": self.effective_start,
            "effective_end": self.effective_end,
            "source": self.source,
        }


def stable_security_id_from_event(symbol: str, company_name: str, source: str = "wiki") -> str:
    """Build a provisional ID that avoids ticker-only identity.

    This is not a certified permanent identifier. It deliberately includes a normalized company-name
    component so ticker reuse does not collapse unrelated issuers by default.
    """

    ticker = normalize_reported_symbol(symbol) or "UNKNOWN"
    name = re.sub(r"[^A-Z0-9]+", "-", str(company_name).upper()).strip("-")[:48] or "UNKNOWN"
    return f"{source}:{ticker}:{name}"


def build_security_master_from_event_ledger(events: pd.DataFrame, *, source: str = "open_reconstruction") -> pd.DataFrame:
    rows = []
    for row in events.itertuples(index=False):
        symbol = normalize_reported_symbol(row.ticker_as_reported)
        if not symbol:
            continue
        security_id = stable_security_id_from_event(symbol, row.company_name)
        rows.append(
            {
                "security_id": security_id,
                "ticker": symbol,
                "effective_start": row.effective_date,
                "effective_end": pd.NaT,
                "source": source,
                "source_security_id": row.event_id,
                "external_id": "",
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=["security_id", "ticker", "effective_start", "effective_end", "source", "source_security_id"]
        )
    return normalize_security_master(pd.DataFrame(rows))


def yahoo_aliases_from_security_master(security_master: pd.DataFrame) -> pd.DataFrame:
    master = normalize_security_master(security_master)
    rows = []
    for row in master.itertuples(index=False):
        rows.append(
            YahooAlias(
                security_id=row.security_id,
                provider_symbol=yahoo_symbol_from_reported(row.ticker),
                effective_start=row.effective_start,
                effective_end=row.effective_end if pd.notna(row.effective_end) else None,
            ).to_dict()
        )
    return pd.DataFrame(rows).sort_values(["security_id", "effective_start", "provider_symbol"]).reset_index(drop=True)


def detect_identity_ambiguities(security_master: pd.DataFrame) -> dict[str, Any]:
    master = normalize_security_master(security_master)
    ticker_changes = master.groupby("security_id")["ticker"].nunique()
    ticker_reuse = master.groupby("ticker")["security_id"].nunique()
    return {
        "security_ids": int(master["security_id"].nunique()),
        "ticker_aliases": len(master),
        "ticker_changes": int((ticker_changes > 1).sum()),
        "ticker_reuse_cases": int((ticker_reuse > 1).sum()),
        "unresolved_identity_mappings": int(master["external_id"].fillna("").eq("").sum())
        if "external_id" in master.columns
        else len(master),
    }
