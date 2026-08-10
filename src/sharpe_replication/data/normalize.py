from __future__ import annotations

from typing import Any

import pandas as pd

from .schema import (
    CORPORATE_ACTION_ORDER,
    CORPORATE_ACTION_REQUIRED,
    CORPORATE_ACTION_TYPES,
    DAILY_ORDER,
    DAILY_REQUIRED,
    MEMBERSHIP_ORDER,
    MEMBERSHIP_REQUIRED,
    SECURITY_MASTER_ORDER,
    SECURITY_MASTER_REQUIRED,
)


def _string_column(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip()


def _parse_date_column(s: pd.Series, column: str) -> pd.Series:
    out = pd.to_datetime(s, errors="raise").dt.normalize()
    if out.isna().any() and column != "membership_end" and column != "effective_end":
        raise ValueError(f"{column} contains null dates")
    return out


def _canonical_columns(df: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    ordered = [column for column in order if column in df.columns]
    extras = sorted(column for column in df.columns if column not in ordered)
    return df[ordered + extras]


def _apply_source_mapping(
    table: pd.DataFrame,
    source_to_target: dict[str, str],
    required_targets: list[str],
    allowed_targets: set[str],
    dataset: str,
) -> pd.DataFrame:
    if not source_to_target:
        raise ValueError(f"{dataset} requires an explicit source-to-target column map")
    missing_sources = sorted(set(source_to_target) - set(table.columns))
    if missing_sources:
        raise ValueError(f"{dataset} source columns missing: {missing_sources}")
    unknown_targets = sorted(set(source_to_target.values()) - allowed_targets)
    if unknown_targets:
        raise ValueError(f"{dataset} target columns are not supported: {unknown_targets}")
    duplicated_targets = sorted(
        {target for target in source_to_target.values() if list(source_to_target.values()).count(target) > 1}
    )
    if duplicated_targets:
        raise ValueError(f"{dataset} maps multiple source columns to: {duplicated_targets}")
    missing_targets = sorted(set(required_targets) - set(source_to_target.values()))
    if missing_targets:
        raise ValueError(f"{dataset} mapping missing required targets: {missing_targets}")
    selected = table[list(source_to_target)].rename(columns=source_to_target)
    return selected.copy()


def normalize_daily_panel(
    table: pd.DataFrame,
    source_to_target: dict[str, str] | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    """Normalize a local daily panel from explicit source columns.

    The mapping is source-column -> normalized-column. `raw_close` is required; the normalizer never
    falls back to `adjusted_close` because that would silently contaminate the inverse-price signal.
    """
    allowed = set(DAILY_ORDER)
    if source_to_target is not None:
        df = _apply_source_mapping(table, source_to_target, DAILY_REQUIRED, allowed, "daily_panel")
    else:
        missing = sorted(set(DAILY_REQUIRED) - set(table.columns))
        if missing:
            raise ValueError(f"daily_panel missing required columns: {missing}")
        df = table[[c for c in DAILY_ORDER if c in table.columns]].copy()

    if "raw_close" not in df.columns and "adjusted_close" in df.columns:
        raise ValueError("raw_close cannot be implicitly populated from adjusted_close")

    df["date"] = _parse_date_column(df["date"], "date")
    df["security_id"] = _string_column(df["security_id"])
    df["ticker"] = _string_column(df["ticker"])
    for column in ["raw_close", "total_return", "open", "high", "low", "adjusted_close"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="raise")
    for column in ["volume", "unadjusted_volume"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="raise")
    for column in ["dividend_cash", "split_factor"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if source is not None and "source" not in df.columns:
        df["source"] = source
    return _canonical_columns(df.sort_values(["date", "security_id"]).reset_index(drop=True), DAILY_ORDER)


def normalize_membership(
    table: pd.DataFrame,
    source_to_target: dict[str, str] | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    allowed = set(MEMBERSHIP_ORDER)
    if source_to_target is not None:
        df = _apply_source_mapping(table, source_to_target, MEMBERSHIP_REQUIRED, allowed, "membership")
    else:
        missing = sorted(set(MEMBERSHIP_REQUIRED) - set(table.columns))
        if missing:
            raise ValueError(f"membership missing required columns: {missing}")
        df = table[[c for c in MEMBERSHIP_ORDER if c in table.columns]].copy()
    df["security_id"] = _string_column(df["security_id"])
    df["membership_start"] = _parse_date_column(df["membership_start"], "membership_start")
    df["membership_end"] = pd.to_datetime(df["membership_end"], errors="coerce").dt.normalize()
    if source is not None and "source" not in df.columns:
        df["source"] = source
    return _canonical_columns(
        df.sort_values(["security_id", "membership_start", "membership_end"]).reset_index(drop=True),
        MEMBERSHIP_ORDER,
    )


def normalize_security_master(
    table: pd.DataFrame,
    source_to_target: dict[str, str] | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    allowed = set(SECURITY_MASTER_ORDER)
    if source_to_target is not None:
        df = _apply_source_mapping(
            table,
            source_to_target,
            SECURITY_MASTER_REQUIRED,
            allowed,
            "security_master",
        )
    else:
        missing = sorted(set(SECURITY_MASTER_REQUIRED) - set(table.columns))
        if missing:
            raise ValueError(f"security_master missing required columns: {missing}")
        df = table[[c for c in SECURITY_MASTER_ORDER if c in table.columns]].copy()
    df["security_id"] = _string_column(df["security_id"])
    df["ticker"] = _string_column(df["ticker"])
    df["effective_start"] = _parse_date_column(df["effective_start"], "effective_start")
    df["effective_end"] = pd.to_datetime(df["effective_end"], errors="coerce").dt.normalize()
    if source is not None:
        df["source"] = source
    return _canonical_columns(
        df.sort_values(["security_id", "effective_start", "ticker"]).reset_index(drop=True),
        SECURITY_MASTER_ORDER,
    )


def normalize_corporate_actions(
    table: pd.DataFrame,
    source_to_target: dict[str, str] | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    allowed = set(CORPORATE_ACTION_ORDER)
    if source_to_target is not None:
        df = _apply_source_mapping(
            table,
            source_to_target,
            CORPORATE_ACTION_REQUIRED,
            allowed,
            "corporate_actions",
        )
    else:
        missing = sorted(set(CORPORATE_ACTION_REQUIRED) - set(table.columns))
        if missing:
            raise ValueError(f"corporate_actions missing required columns: {missing}")
        df = table[[c for c in CORPORATE_ACTION_ORDER if c in table.columns]].copy()
    df["security_id"] = _string_column(df["security_id"])
    df["date"] = _parse_date_column(df["date"], "date")
    df["event_type"] = _string_column(df["event_type"]).str.lower().str.replace("/", "_")
    unknown_events = sorted(set(df["event_type"].dropna()) - CORPORATE_ACTION_TYPES)
    if unknown_events:
        raise ValueError(f"Unsupported corporate action event_type values: {unknown_events}")
    for column in ["split_factor", "dividend_cash"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    if source is not None and "source" not in df.columns:
        df["source"] = source
    return _canonical_columns(
        df.sort_values(["date", "security_id", "event_type"]).reset_index(drop=True),
        CORPORATE_ACTION_ORDER,
    )


def normalize_from_mapping(
    table: pd.DataFrame,
    dataset: str,
    source_to_target: dict[str, str],
    source: str | None = None,
) -> pd.DataFrame:
    normalizers: dict[str, Any] = {
        "daily_panel": normalize_daily_panel,
        "membership": normalize_membership,
        "security_master": normalize_security_master,
        "corporate_actions": normalize_corporate_actions,
    }
    if dataset not in normalizers:
        raise ValueError(f"Unknown dataset={dataset!r}")
    return normalizers[dataset](table, source_to_target, source)
