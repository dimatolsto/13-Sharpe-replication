from __future__ import annotations

from datetime import date

import pandas as pd

from .normalize import normalize_security_master
from .schema import Severity, ValidationReport


def validate_security_master(
    security_master: pd.DataFrame | None,
    known_security_ids: set[str] | None = None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    if security_master is None:
        report.add(
            Severity.WARNING,
            "security_master_missing",
            "Security-master / identifier-history dataset is absent",
            dataset="security_master",
        )
        return report
    try:
        master = normalize_security_master(security_master)
    except (KeyError, TypeError, ValueError) as exc:
        report.add(
            Severity.ERROR,
            "security_master_schema_invalid",
            str(exc),
            dataset="security_master",
        )
        return report

    if known_security_ids is not None:
        unknown = sorted(set(master["security_id"]) - known_security_ids)
        if unknown:
            report.add(
                Severity.WARNING,
                "security_master_unknown_security_id",
                "Security master contains IDs not present in supplied panel or membership",
                dataset="security_master",
                row_count=len(unknown),
                details={"security_ids": unknown[:20]},
            )

    bad_order = master["effective_end"].notna() & (master["effective_end"] < master["effective_start"])
    if bad_order.any():
        report.add(
            Severity.ERROR,
            "security_master_end_before_start",
            "effective_end precedes effective_start",
            dataset="security_master",
            row_count=int(bad_order.sum()),
        )

    overlap_count = 0
    for _, block in master.sort_values(["security_id", "effective_start"]).groupby("security_id"):
        previous_end: pd.Timestamp | None = None
        for row in block.itertuples(index=False):
            end = row.effective_end if pd.notna(row.effective_end) else pd.Timestamp.max.normalize()
            if previous_end is not None and row.effective_start <= previous_end:
                overlap_count += 1
            previous_end = max(previous_end, end) if previous_end is not None else end
    if overlap_count:
        report.add(
            Severity.ERROR,
            "overlapping_security_master_interval",
            "Overlapping effective intervals for the same security_id",
            dataset="security_master",
            row_count=overlap_count,
        )

    ticker_overlap_count = 0
    for ticker, block in master.sort_values(["ticker", "effective_start"]).groupby("ticker"):
        intervals = []
        for row in block.itertuples(index=False):
            end = row.effective_end if pd.notna(row.effective_end) else pd.Timestamp.max.normalize()
            intervals.append((row.effective_start, end, row.security_id))
        for idx, (start, end, security_id) in enumerate(intervals):
            for other_start, other_end, other_security_id in intervals[idx + 1 :]:
                if security_id != other_security_id and other_start <= end and start <= other_end:
                    ticker_overlap_count += 1
                    break
        if ticker_overlap_count:
            break
    if ticker_overlap_count:
        report.add(
            Severity.WARNING,
            "ambiguous_ticker_overlap",
            "The same ticker maps to multiple security_id values over overlapping dates",
            dataset="security_master",
            row_count=ticker_overlap_count,
        )

    if "external_id" not in master.columns or master["external_id"].fillna("").astype(str).str.strip().eq("").any():
        report.add(
            Severity.WARNING,
            "stable_identifier_unverified",
            "At least one security-master row lacks a provider-native or external stable identifier",
            dataset="security_master",
        )

    ticker_changes = int((master.groupby("security_id")["ticker"].nunique() > 1).sum())
    reused_tickers = int((master.groupby("ticker")["security_id"].nunique() > 1).sum())
    report.sections["security_master"] = {
        "rows": len(master),
        "unique_security_ids": int(master["security_id"].nunique()),
        "ticker_changes": ticker_changes,
        "reused_tickers": reused_tickers,
    }
    return report


def security_id_for_ticker(
    security_master: pd.DataFrame,
    ticker: str,
    as_of: date | str | pd.Timestamp,
) -> str | None:
    master = normalize_security_master(security_master)
    ts = pd.Timestamp(as_of).normalize()
    block = master[
        master["ticker"].eq(ticker)
        & (master["effective_start"] <= ts)
        & (master["effective_end"].isna() | (master["effective_end"] >= ts))
    ]
    ids = sorted(set(block["security_id"]))
    if len(ids) > 1:
        raise ValueError(f"Ticker {ticker!r} is ambiguous on {ts.date()}: {ids}")
    return ids[0] if ids else None
