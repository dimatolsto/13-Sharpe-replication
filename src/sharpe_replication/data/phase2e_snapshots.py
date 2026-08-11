from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .io import read_table, write_json
from .membership import members_on
from .membership_reconstruction import reconstruct_membership_from_change_events
from .normalize import normalize_membership
from .phase2d_membership import (
    build_identity_lineages,
    count_anomaly_intervals,
    count_inflation_causes,
    identity_ambiguities,
    membership_count_timeseries,
    reconstruction_conservation_audit,
    reentry_review,
)
from .sp500_events import (
    event_ledger_to_change_events,
    normalize_event_ledger,
    normalize_reported_symbol,
    parse_wikipedia_current_constituents,
    provisional_security_id_from_symbol_name,
)
from .trading_calendar import TradingCalendar

PHASE2E_PARSER_VERSION = "phase2e-snapshot-triangulation-v1"
MERGE_RESOLUTION_TYPES = {
    "SAME_SECURITY_TICKER_RENAME",
    "SAME_SECURITY_NAME_CHANGE",
    "PROVIDER_SYMBOL_NORMALIZATION",
    "REENTRY_SAME_SECURITY",
    "DUPLICATE_PROVISIONAL_ID",
}
APPROVED_REVIEWER_STATUSES = {"APPROVED", "EVIDENCE_BACKED", "VERIFIED"}
SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "membership_session",
    "snapshot_date_convention",
    "source_id",
    "source_name",
    "source_tier",
    "source_security_name",
    "source_ticker",
    "isin",
    "cik",
    "figi",
    "other_external_id",
    "mapped_security_id",
    "mapping_status",
    "mapping_evidence",
    "source_url",
    "revision_id",
    "revision_timestamp",
    "parser_version",
    "row_status",
    "notes",
]


def _now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean_text(value: Any) -> str:
    text = "" if value is None or pd.isna(value) else str(value)
    return re.sub(r"\s+", " ", text).strip()


def _ticker_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_reported_symbol(value))


def _name_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", _clean_text(value).upper()).strip("-")[:48]


def _date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _optional_date(value: Any, default: pd.Timestamp | None = None) -> pd.Timestamp | None:
    text = _clean_text(value)
    if not text:
        return default
    try:
        return pd.Timestamp(text).normalize()
    except (TypeError, ValueError):
        return default


def _safe_stem(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:96].strip("_")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{safe}_{digest}"


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)


def parse_wikipedia_revision_snapshot(
    html: str,
    *,
    revision_id: str,
    revision_timestamp: str,
    source_url: str,
) -> pd.DataFrame:
    """Parse a historical Wikipedia constituent-table revision, not its changes table."""

    parsed = parse_wikipedia_current_constituents(html, source_url=source_url)
    out = parsed.rename(columns={"ticker": "source_ticker", "company_name": "source_security_name"}).copy()
    out["snapshot_date"] = pd.Timestamp(revision_timestamp).normalize()
    out["source_id"] = "wikipedia_revision"
    out["source_name"] = "Wikipedia S&P 500 constituent table revision"
    out["source_tier"] = "SECONDARY_SNAPSHOT"
    out["isin"] = ""
    out["figi"] = ""
    out["other_external_id"] = ""
    out["revision_id"] = str(revision_id)
    out["revision_timestamp"] = str(revision_timestamp)
    out["parser_version"] = PHASE2E_PARSER_VERSION
    out["row_status"] = "active_constituent"
    out["notes"] = "Historical Wikipedia constituent table; secondary snapshot, not authority."
    return out[
        [
            "snapshot_date",
            "source_id",
            "source_name",
            "source_tier",
            "source_security_name",
            "source_ticker",
            "isin",
            "cik",
            "figi",
            "other_external_id",
            "source_url",
            "revision_id",
            "revision_timestamp",
            "parser_version",
            "row_status",
            "notes",
        ]
    ].reset_index(drop=True)


def snapshot_sources_catalog() -> pd.DataFrame:
    rows = [
        {
            "source_id": "wikipedia_revision",
            "source_name": "Wikipedia constituent-table revisions",
            "source_tier": "SECONDARY_SNAPSHOT",
            "canonical_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            "date_coverage": "article revision history; parser implemented, live revision crawl not run by default",
            "snapshots_acquired": 0,
            "identifiers_available": "ticker;company;revision_id;revision_timestamp;CIK in later table schemas",
            "known_limitations": "Wikipedia is a secondary source; selected-change table is not used as authority.",
            "evaluation_status": "parser_available",
        },
        {
            "source_id": "riazarbi_ishares",
            "source_name": "riazarbi/sp500-scraper iShares IVV holdings snapshots",
            "source_tier": "SECONDARY_SNAPSHOT",
            "canonical_url": "https://github.com/riazarbi/sp500-scraper/tree/main/ishares/sp500/csv",
            "date_coverage": "observed public repository coverage from 2006-10-31 onward",
            "snapshots_acquired": 0,
            "identifiers_available": "ticker;company;CUSIP;ISIN;SEDOL",
            "known_limitations": "ETF holdings proxy; cash/non-equity rows filtered; not official S&P membership.",
            "evaluation_status": "bounded_csv_fetch_available",
        },
        {
            "source_id": "fja05680_sp500",
            "source_name": "fja05680/sp500 historical ticker membership intervals",
            "source_tier": "SECONDARY_SNAPSHOT",
            "canonical_url": "https://github.com/fja05680/sp500",
            "date_coverage": "claimed 1996-present ticker intervals",
            "snapshots_acquired": 0,
            "identifiers_available": "ticker;start_date;end_date",
            "known_limitations": "Ticker-only, secondary, partly derived from Wikipedia and book seed; no company/security IDs.",
            "evaluation_status": "bounded_csv_fetch_available",
        },
        {
            "source_id": "riazarbi_wikipedia",
            "source_name": "riazarbi/sp500-scraper Wikipedia snapshots",
            "source_tier": "SECONDARY_SNAPSHOT",
            "canonical_url": "https://github.com/riazarbi/sp500-scraper/tree/main/wikipedia/sp500/csv",
            "date_coverage": "observed CSV folder from 2022-11-08 onward; repo README describes earlier revision traversal",
            "snapshots_acquired": 0,
            "identifiers_available": "ticker;company;CIK in later schemas",
            "known_limitations": "Wikipedia-derived secondary snapshots; parser/schema changed across history.",
            "evaluation_status": "bounded_csv_fetch_available",
        },
        {
            "source_id": "riazarbi_tidyquant",
            "source_name": "riazarbi/sp500-scraper tidyquant snapshots",
            "source_tier": "SECONDARY_SNAPSHOT",
            "canonical_url": "https://github.com/riazarbi/sp500-scraper/tree/main/tidyquant/sp500/csv",
            "date_coverage": "observed from 2022-11-08 onward",
            "snapshots_acquired": 0,
            "identifiers_available": "ticker;company;CUSIP;ISIN;SEDOL",
            "known_limitations": "R-package secondary source; current/index-source semantics require review.",
            "evaluation_status": "bounded_csv_fetch_available",
        },
        {
            "source_id": "sp_primary_snapshot",
            "source_name": "S&P Dow Jones Indices official constituent snapshots",
            "source_tier": "SP_PRIMARY",
            "canonical_url": "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
            "date_coverage": "current public page and event notices; no public full-history snapshot feed located",
            "snapshots_acquired": 0,
            "identifiers_available": "current constituent names/tickers where public page is accessible",
            "known_limitations": "Official historical full constituent snapshots were not found in this bounded pass.",
            "evaluation_status": "not_acquired_no_public_history_feed_found",
        },
    ]
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class SnapshotCandidate:
    source_id: str
    source_name: str
    source_tier: str
    snapshot_date: str
    url: str
    parser: str
    date_convention: str
    known_limitations: str


def default_snapshot_candidates(end_date: str = "2026-08-11") -> list[SnapshotCandidate]:
    end = _date(end_date)
    candidates = [
        SnapshotCandidate(
            source_id="fja05680_sp500",
            source_name="fja05680/sp500 historical ticker membership intervals",
            source_tier="SECONDARY_SNAPSHOT",
            snapshot_date=end.date().isoformat(),
            url="https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv",
            parser="fja_intervals",
            date_convention="AS_OF_OR_PREVIOUS_SESSION",
            known_limitations="Ticker-only secondary dataset; not official S&P membership.",
        )
    ]
    for yyyymmdd in [
        "20061031",
        "20061130",
        "20061229",
        "20081231",
        "20101231",
        "20120330",
        "20151231",
        "20180329",
        "20200331",
        "20220331",
        "20240930",
        "20251222",
        "20260731",
    ]:
        candidates.append(
            SnapshotCandidate(
                source_id="riazarbi_ishares",
                source_name="riazarbi/sp500-scraper iShares IVV holdings snapshots",
                source_tier="SECONDARY_SNAPSHOT",
                snapshot_date=f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}",
                url=f"https://raw.githubusercontent.com/riazarbi/sp500-scraper/main/ishares/sp500/csv/{yyyymmdd}.csv",
                parser="ishares_csv",
                date_convention="AS_OF_OR_PREVIOUS_SESSION",
                known_limitations="ETF holdings proxy; filtered to equity rows only.",
            )
        )
    for yyyymmdd in ["20221108", "20241231", "20260811"]:
        candidates.append(
            SnapshotCandidate(
                source_id="riazarbi_wikipedia",
                source_name="riazarbi/sp500-scraper Wikipedia snapshots",
                source_tier="SECONDARY_SNAPSHOT",
                snapshot_date=f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}",
                url=f"https://raw.githubusercontent.com/riazarbi/sp500-scraper/main/wikipedia/sp500/csv/{yyyymmdd}.csv",
                parser="generic_snapshot_csv",
                date_convention="AS_OF_OR_PREVIOUS_SESSION",
                known_limitations="Wikipedia-derived secondary constituent table.",
            )
        )
    for yyyymmdd in ["20221108", "20241230"]:
        candidates.append(
            SnapshotCandidate(
                source_id="riazarbi_tidyquant",
                source_name="riazarbi/sp500-scraper tidyquant snapshots",
                source_tier="SECONDARY_SNAPSHOT",
                snapshot_date=f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}",
                url=f"https://raw.githubusercontent.com/riazarbi/sp500-scraper/main/tidyquant/sp500/csv/{yyyymmdd}.csv",
                parser="generic_snapshot_csv",
                date_convention="AS_OF_OR_PREVIOUS_SESSION",
                known_limitations="Tidyquant/R-package secondary source.",
            )
        )
    return [candidate for candidate in candidates if _date(candidate.snapshot_date) <= end]


def map_snapshot_date_to_membership_session(
    snapshot_date: str | pd.Timestamp,
    *,
    convention: str,
    calendar: TradingCalendar | None = None,
) -> pd.Timestamp:
    cal = calendar or TradingCalendar.xnys()
    session = str(convention).upper()
    if session == "BEFORE_OPEN":
        return cal.next_session(snapshot_date, include_current=True)
    if session in {"AFTER_CLOSE", "AS_OF_OR_PREVIOUS_SESSION", "DATE_ONLY"}:
        return cal.previous_session(snapshot_date, include_current=True)
    if session == "UNKNOWN":
        return cal.previous_session(snapshot_date, include_current=True)
    raise ValueError(f"Unsupported snapshot date convention: {convention}")


def _empty_snapshot_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=SNAPSHOT_COLUMNS)


def _raw_cache_paths(cache_dir: Path, candidate: SnapshotCandidate) -> tuple[Path, Path]:
    stem = _safe_stem(f"{candidate.source_id}_{candidate.snapshot_date}_{candidate.url}")
    return cache_dir / f"{stem}.raw", cache_dir / f"{stem}.json"


def _fetch_candidate(
    candidate: SnapshotCandidate,
    *,
    cache_dir: Path,
    force: bool,
    timeout: float,
    downloads_remaining: int,
) -> tuple[bytes | None, dict[str, Any], int]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_path, meta_path = _raw_cache_paths(cache_dir, candidate)
    if raw_path.exists() and meta_path.exists() and not force:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["cache_status"] = "cached"
        return raw_path.read_bytes(), meta, downloads_remaining
    meta = {
        "source_id": candidate.source_id,
        "source_name": candidate.source_name,
        "source_tier": candidate.source_tier,
        "snapshot_date": candidate.snapshot_date,
        "canonical_url": candidate.url,
        "retrieval_timestamp": _now_utc(),
        "archive_url": "",
        "cache_path": str(raw_path),
        "metadata_path": str(meta_path),
        "parser": candidate.parser,
        "parser_version": PHASE2E_PARSER_VERSION,
        "sha256": "",
        "bytes": 0,
        "http_status": "",
        "cache_status": "not_requested",
        "error": "not requested in bounded acquisition run",
    }
    if downloads_remaining <= 0:
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        return None, meta, downloads_remaining
    import httpx

    downloads_remaining -= 1
    meta["cache_status"] = "fetched"
    meta["error"] = ""
    try:
        response = httpx.get(
            candidate.url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "sharpe-replication/0.1 phase2e-snapshot-triangulation"},
        )
        meta["http_status"] = int(response.status_code)
        response.raise_for_status()
        content = response.content
        raw_path.write_bytes(content)
        meta["sha256"] = hashlib.sha256(content).hexdigest()
        meta["bytes"] = len(content)
        meta["final_url"] = str(response.url)
        meta["content_type"] = response.headers.get("content-type", "")
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        return content, meta, downloads_remaining
    except (httpx.HTTPError, OSError, ValueError) as exc:  # pragma: no cover - live network path
        meta["error"] = str(exc)
        meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
        return None, meta, downloads_remaining


def _standardize_csv_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
        for column in out.columns
    ]
    return out


def parse_ishares_snapshot_csv(
    text: str,
    *,
    candidate: SnapshotCandidate,
    source_url: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    frame = _standardize_csv_columns(raw)
    required = {"symbol", "name", "asset_class"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"iShares snapshot missing required columns: {missing}")
    active = frame["asset_class"].str.upper().eq("EQUITY")
    active &= frame["symbol"].map(_clean_text).ne("")
    active &= ~frame["symbol"].map(_clean_text).isin({"-", "CASH"})
    filtered = frame.loc[~active].copy()
    rows = []
    for row in frame.loc[active].itertuples(index=False):
        row_dict = row._asdict()
        external_ids = []
        for field_name in ["cusip", "sedol"]:
            value = _clean_text(row_dict.get(field_name, ""))
            if value:
                external_ids.append(f"{field_name.upper()}={value}")
        rows.append(
            {
                "snapshot_date": candidate.snapshot_date,
                "source_id": candidate.source_id,
                "source_name": candidate.source_name,
                "source_tier": candidate.source_tier,
                "source_security_name": _clean_text(row_dict.get("name", "")),
                "source_ticker": normalize_reported_symbol(row_dict.get("symbol", "")),
                "isin": _clean_text(row_dict.get("isin", "")),
                "cik": "",
                "figi": "",
                "other_external_id": ";".join(external_ids),
                "source_url": source_url,
                "revision_id": "",
                "revision_timestamp": "",
                "parser_version": PHASE2E_PARSER_VERSION,
                "row_status": "active_constituent",
                "notes": "iShares IVV holding retained after deterministic equity-row filter.",
            }
        )
    filtered_rows = pd.DataFrame(
        [
            {
                "snapshot_date": candidate.snapshot_date,
                "source_id": candidate.source_id,
                "source_ticker": row.symbol,
                "source_security_name": row.name,
                "asset_class": row.asset_class,
                "filter_reason": "non_equity_or_blank_symbol",
            }
            for row in filtered.itertuples(index=False)
        ]
    )
    return pd.DataFrame(rows), filtered_rows


def parse_generic_snapshot_csv(
    text: str,
    *,
    candidate: SnapshotCandidate,
    source_url: str,
) -> pd.DataFrame:
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    frame = _standardize_csv_columns(raw)
    ticker_col = next((column for column in ["symbol", "ticker", "holding_ticker"] if column in frame.columns), None)
    name_col = next((column for column in ["name", "security", "company", "company_name"] if column in frame.columns), None)
    if ticker_col is None or name_col is None:
        raise ValueError("Generic snapshot missing recognizable ticker/name columns")
    rows = []
    for row in frame.itertuples(index=False):
        row_dict = row._asdict()
        ticker = normalize_reported_symbol(row_dict.get(ticker_col, ""))
        name = _clean_text(row_dict.get(name_col, ""))
        if not ticker or not name:
            raise ValueError(f"Generic snapshot row has blank ticker/name: {row_dict}")
        external_ids = []
        for field_name in ["cusip", "sedol"]:
            value = _clean_text(row_dict.get(field_name, ""))
            if value:
                external_ids.append(f"{field_name.upper()}={value}")
        rows.append(
            {
                "snapshot_date": candidate.snapshot_date,
                "source_id": candidate.source_id,
                "source_name": candidate.source_name,
                "source_tier": candidate.source_tier,
                "source_security_name": name,
                "source_ticker": ticker,
                "isin": _clean_text(row_dict.get("isin", "")),
                "cik": _clean_text(row_dict.get("cik", "")),
                "figi": "",
                "other_external_id": ";".join(external_ids),
                "source_url": source_url,
                "revision_id": "",
                "revision_timestamp": "",
                "parser_version": PHASE2E_PARSER_VERSION,
                "row_status": "active_constituent",
                "notes": "Secondary constituent snapshot row.",
            }
        )
    return pd.DataFrame(rows)


def parse_fja_interval_snapshots(
    text: str,
    *,
    candidate: SnapshotCandidate,
    checkpoint_dates: list[str],
) -> pd.DataFrame:
    raw = pd.read_csv(StringIO(text), dtype=str, keep_default_na=False)
    frame = _standardize_csv_columns(raw)
    required = {"ticker", "start_date", "end_date"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"fja05680 interval snapshot missing required columns: {missing}")
    rows = []
    for checkpoint in checkpoint_dates:
        checkpoint_ts = _date(checkpoint)
        for row in frame.itertuples(index=False):
            start = _optional_date(row.start_date, pd.Timestamp("1900-01-01"))
            end = _optional_date(row.end_date, pd.Timestamp("2100-12-31"))
            if start is None or end is None or not (start <= checkpoint_ts <= end):
                continue
            rows.append(
                {
                    "snapshot_date": checkpoint_ts.date().isoformat(),
                    "source_id": candidate.source_id,
                    "source_name": candidate.source_name,
                    "source_tier": candidate.source_tier,
                    "source_security_name": "",
                    "source_ticker": normalize_reported_symbol(row.ticker),
                    "isin": "",
                    "cik": "",
                    "figi": "",
                    "other_external_id": "",
                    "source_url": candidate.url,
                    "revision_id": "",
                    "revision_timestamp": "",
                    "parser_version": PHASE2E_PARSER_VERSION,
                    "row_status": "active_constituent",
                    "notes": "Ticker-only membership row generated from secondary interval dataset.",
                }
            )
    return pd.DataFrame(rows)


def normalize_snapshot_frame(
    snapshot: pd.DataFrame,
    *,
    date_convention: str,
    calendar: TradingCalendar | None = None,
) -> pd.DataFrame:
    if snapshot.empty:
        return _empty_snapshot_frame()
    cal = calendar or TradingCalendar.xnys()
    out = snapshot.copy()
    for column in SNAPSHOT_COLUMNS:
        if column not in out.columns:
            out[column] = ""
    out["snapshot_date"] = pd.to_datetime(out["snapshot_date"], errors="raise").dt.normalize()
    out["membership_session"] = [
        map_snapshot_date_to_membership_session(value, convention=date_convention, calendar=cal)
        for value in out["snapshot_date"]
    ]
    out["snapshot_date_convention"] = date_convention
    for column in [
        "source_id",
        "source_name",
        "source_tier",
        "source_security_name",
        "source_ticker",
        "isin",
        "cik",
        "figi",
        "other_external_id",
        "mapped_security_id",
        "mapping_status",
        "mapping_evidence",
        "source_url",
        "revision_id",
        "revision_timestamp",
        "parser_version",
        "row_status",
        "notes",
    ]:
        out[column] = out[column].astype("string").fillna("").map(_clean_text)
    out["source_ticker"] = out["source_ticker"].map(normalize_reported_symbol)
    return out[SNAPSHOT_COLUMNS].sort_values(["snapshot_date", "source_id", "source_ticker"]).reset_index(drop=True)


def load_identity_resolutions(path: Path | None) -> pd.DataFrame:
    columns = [
        "resolution_id",
        "old_security_id",
        "canonical_security_id",
        "effective_start",
        "effective_end",
        "resolution_type",
        "evidence_url",
        "evidence_source_tier",
        "reason",
        "reviewer_status",
    ]
    if path is None or not path.exists():
        return pd.DataFrame(columns=columns)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#")
    for column in columns:
        if column not in frame.columns:
            raise ValueError(f"Identity resolution config missing column: {column}")
    out = frame[columns].copy()
    for column in columns:
        out[column] = out[column].map(_clean_text)
    return out


def _active_resolution_map(resolutions: pd.DataFrame, when: str | pd.Timestamp) -> dict[str, dict[str, str]]:
    if resolutions.empty:
        return {}
    ts = _date(when)
    mapping: dict[str, dict[str, str]] = {}
    for row in resolutions.itertuples(index=False):
        resolution_type = str(row.resolution_type).upper()
        reviewer_status = str(row.reviewer_status).upper()
        if resolution_type not in MERGE_RESOLUTION_TYPES or reviewer_status not in APPROVED_REVIEWER_STATUSES:
            continue
        start = _optional_date(row.effective_start, pd.Timestamp("1900-01-01"))
        end = _optional_date(row.effective_end, pd.Timestamp("2100-12-31"))
        if start is None or end is None or not (start <= ts <= end):
            continue
        old = str(row.old_security_id)
        canonical = str(row.canonical_security_id)
        if old and canonical:
            mapping[old] = {
                "canonical_security_id": canonical,
                "resolution_id": str(row.resolution_id),
                "resolution_type": resolution_type,
            }
    return mapping


def apply_identity_resolutions_to_security_id(
    security_id: str,
    when: str | pd.Timestamp,
    resolutions: pd.DataFrame,
) -> tuple[str, str]:
    mapping = _active_resolution_map(resolutions, when)
    current = str(security_id)
    seen: set[str] = set()
    evidence: list[str] = []
    while current in mapping and current not in seen:
        seen.add(current)
        item = mapping[current]
        current = item["canonical_security_id"]
        evidence.append(f"{item['resolution_id']}:{item['resolution_type']}")
    return current, ";".join(evidence)


def _resolution_counts(resolutions: pd.DataFrame) -> dict[str, int]:
    if resolutions.empty:
        return {}
    return {
        str(key): int(value)
        for key, value in resolutions["resolution_type"].str.upper().value_counts().sort_index().items()
    }


def _known_security_ids(lineages: pd.DataFrame, membership: pd.DataFrame) -> set[str]:
    ids = set(membership["security_id"].astype(str)) if len(membership) else set()
    if len(lineages) and "security_id" in lineages.columns:
        ids.update(lineages["security_id"].astype(str))
    return ids


def _event_security_id_from_ledger_row(row: Any) -> str:
    return provisional_security_id_from_symbol_name(
        getattr(row, "source_symbol", "") or getattr(row, "ticker_as_reported", ""),
        getattr(row, "source_security_name", "") or getattr(row, "company_name", ""),
    )


def _events_with_resolved_security_ids(events: pd.DataFrame, resolutions: pd.DataFrame) -> pd.DataFrame:
    out = normalize_event_ledger(events).copy()
    resolved_ids: list[str] = []
    evidence_values: list[str] = []
    for row in out.itertuples(index=False):
        security_id = _event_security_id_from_ledger_row(row)
        mapped, evidence = apply_identity_resolutions_to_security_id(security_id, row.effective_date, resolutions)
        resolved_ids.append(mapped)
        evidence_values.append(evidence)
    out["resolved_security_id"] = resolved_ids
    out["identity_resolution_evidence"] = evidence_values
    return out


def _resolved_anchor_members(anchor: pd.DataFrame, resolutions: pd.DataFrame, anchor_date: str) -> list[str]:
    members = []
    for security_id in anchor["security_id"].astype(str):
        mapped, _ = apply_identity_resolutions_to_security_id(security_id, anchor_date, resolutions)
        members.append(mapped)
    return members


def build_snapshot_identity_index(lineages: pd.DataFrame, membership: pd.DataFrame) -> dict[str, set[str]]:
    ids = _known_security_ids(lineages, membership)
    index: dict[str, set[str]] = {}
    for security_id in ids:
        parts = str(security_id).split(":", 2)
        if len(parts) >= 2:
            index.setdefault(_ticker_key(parts[1]), set()).add(str(security_id))
    if len(lineages):
        for row in lineages.itertuples(index=False):
            ticker = getattr(row, "ticker", "")
            security_id = getattr(row, "security_id", "")
            if ticker and security_id:
                index.setdefault(_ticker_key(ticker), set()).add(str(security_id))
    return index


def map_snapshot_identities(
    snapshot: pd.DataFrame,
    *,
    lineages: pd.DataFrame,
    membership: pd.DataFrame,
    resolutions: pd.DataFrame,
) -> pd.DataFrame:
    if snapshot.empty:
        return _empty_snapshot_frame()
    known_ids = _known_security_ids(lineages, membership)
    ticker_index = build_snapshot_identity_index(lineages, membership)
    out = snapshot.copy()
    mapped_ids: list[str] = []
    statuses: list[str] = []
    evidence_values: list[str] = []
    for row in out.itertuples(index=False):
        session = row.membership_session
        ticker = normalize_reported_symbol(getattr(row, "source_ticker", ""))
        name = _clean_text(getattr(row, "source_security_name", ""))
        provisional = provisional_security_id_from_symbol_name(ticker, name) if ticker and name else ""
        if provisional in known_ids:
            mapped, evidence = apply_identity_resolutions_to_security_id(provisional, session, resolutions)
            mapped_ids.append(mapped)
            statuses.append("mapped_exact_provisional_id" if not evidence else "mapped_by_identity_resolution")
            evidence_values.append(evidence or f"ticker_name={ticker}/{name}")
            continue
        candidates = sorted(ticker_index.get(_ticker_key(ticker), set()))
        if len(candidates) == 1:
            mapped, evidence = apply_identity_resolutions_to_security_id(candidates[0], session, resolutions)
            mapped_ids.append(mapped)
            statuses.append("mapped_unique_ticker_candidate" if not evidence else "mapped_by_identity_resolution")
            evidence_values.append(evidence or f"unique_ticker={ticker}")
        elif len(candidates) > 1:
            mapped_ids.append("")
            statuses.append("ambiguous_ticker_candidates")
            evidence_values.append(";".join(candidates[:25]))
        else:
            mapped_ids.append("")
            statuses.append("unmapped_snapshot_row")
            evidence_values.append("no date-aware internal identity candidate")
    out["mapped_security_id"] = mapped_ids
    out["mapping_status"] = statuses
    out["mapping_evidence"] = evidence_values
    return out[SNAPSHOT_COLUMNS].reset_index(drop=True)


def compare_snapshot_to_membership(snapshot: pd.DataFrame, membership: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if snapshot.empty:
        columns = [
            "snapshot_date",
            "membership_session",
            "source_id",
            "source_tier",
            "snapshot_count",
            "reconstructed_count",
            "mapped_snapshot_count",
            "unresolved_identity_rows",
            "overlap_count",
            "only_reconstructed_count",
            "only_snapshot_count",
            "symmetric_difference_count",
            "precision_like",
            "recall_like",
            "only_reconstructed",
            "only_snapshot",
        ]
        return pd.DataFrame(columns=columns), pd.DataFrame()
    members = normalize_membership(membership)
    summary_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    mapped_statuses = {"mapped_exact_provisional_id", "mapped_unique_ticker_candidate", "mapped_by_identity_resolution"}
    for (source_id, snapshot_date, membership_session), block in snapshot.groupby(
        ["source_id", "snapshot_date", "membership_session"],
        sort=True,
    ):
        recon_set = members_on(members, membership_session)
        source_block = block[block["mapping_status"].isin(mapped_statuses)]
        source_set = set(source_block["mapped_security_id"].astype(str)) - {""}
        overlap = recon_set & source_set
        only_recon = sorted(recon_set - source_set)
        only_source = sorted(source_set - recon_set)
        unresolved = int((~block["mapping_status"].isin(mapped_statuses)).sum())
        source_tier = str(block["source_tier"].iloc[0])
        snapshot_count = len(block)
        precision = len(overlap) / len(source_set) if source_set else 0.0
        recall = len(overlap) / len(recon_set) if recon_set else 0.0
        summary_rows.append(
            {
                "snapshot_date": pd.Timestamp(snapshot_date).date().isoformat(),
                "membership_session": pd.Timestamp(membership_session).date().isoformat(),
                "source_id": source_id,
                "source_tier": source_tier,
                "snapshot_count": snapshot_count,
                "reconstructed_count": len(recon_set),
                "mapped_snapshot_count": len(source_set),
                "unresolved_identity_rows": unresolved,
                "overlap_count": len(overlap),
                "only_reconstructed_count": len(only_recon),
                "only_snapshot_count": len(only_source),
                "symmetric_difference_count": len(only_recon) + len(only_source),
                "precision_like": round(precision, 6),
                "recall_like": round(recall, 6),
                "only_reconstructed": ";".join(only_recon[:100]),
                "only_snapshot": ";".join(only_source[:100]),
            }
        )
        snapshot_by_id = source_block.groupby("mapped_security_id").first()
        for security_id in only_recon:
            detail_rows.append(
                {
                    "snapshot_date": pd.Timestamp(snapshot_date).date().isoformat(),
                    "membership_session": pd.Timestamp(membership_session).date().isoformat(),
                    "source_id": source_id,
                    "difference_side": "only_reconstructed",
                    "security_id": security_id,
                    "source_ticker": "",
                    "source_security_name": "",
                    "classification": "RECON_ONLY_SOURCE",
                    "notes": "Present in reconstructed membership and absent from mapped snapshot set.",
                }
            )
        for security_id in only_source:
            source_row = snapshot_by_id.loc[security_id]
            detail_rows.append(
                {
                    "snapshot_date": pd.Timestamp(snapshot_date).date().isoformat(),
                    "membership_session": pd.Timestamp(membership_session).date().isoformat(),
                    "source_id": source_id,
                    "difference_side": "only_snapshot",
                    "security_id": security_id,
                    "source_ticker": source_row.source_ticker,
                    "source_security_name": source_row.source_security_name,
                    "classification": "SNAPSHOT_ONLY_SOURCE",
                    "notes": "Present in mapped snapshot set and absent from reconstructed membership.",
                }
            )
    return pd.DataFrame(summary_rows).sort_values(["membership_session", "source_id"]).reset_index(drop=True), pd.DataFrame(detail_rows)


def snapshot_source_disagreements(snapshot: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty:
        return pd.DataFrame(
            columns=["membership_session", "security_id", "classification", "sources_present", "sources_absent", "reconstructed_present"]
        )
    mapped_statuses = {"mapped_exact_provisional_id", "mapped_unique_ticker_candidate", "mapped_by_identity_resolution"}
    members = normalize_membership(membership)
    rows: list[dict[str, Any]] = []
    for session, session_block in snapshot.groupby("membership_session"):
        source_sets = {
            source_id: set(block[block["mapping_status"].isin(mapped_statuses)]["mapped_security_id"].astype(str)) - {""}
            for source_id, block in session_block.groupby("source_id")
        }
        if len(source_sets) < 2:
            continue
        source_ids = sorted(source_sets)
        union = set().union(*source_sets.values())
        recon_set = members_on(members, session)
        for security_id in sorted(union):
            present = [source_id for source_id in source_ids if security_id in source_sets[source_id]]
            absent = [source_id for source_id in source_ids if security_id not in source_sets[source_id]]
            if present and absent:
                classification = "SOURCE_DISAGREEMENT"
            elif present == source_ids and security_id not in recon_set:
                classification = "SNAPSHOT_ONLY_ALL_SOURCES"
            else:
                continue
            rows.append(
                {
                    "membership_session": pd.Timestamp(session).date().isoformat(),
                    "security_id": security_id,
                    "classification": classification,
                    "sources_present": ";".join(present),
                    "sources_absent": ";".join(absent),
                    "reconstructed_present": security_id in recon_set,
                }
            )
    return pd.DataFrame(rows)


def localize_error_intervals(comparisons: pd.DataFrame, events: pd.DataFrame, *, agree_threshold: int = 25) -> pd.DataFrame:
    if comparisons.empty:
        return pd.DataFrame(
            columns=[
                "source_id",
                "last_agreeing_checkpoint",
                "first_disagreeing_checkpoint",
                "candidate_event_count",
                "candidate_events_between",
                "notes",
            ]
        )
    ledger = normalize_event_ledger(events)
    rows = []
    for source_id, block in comparisons.sort_values("membership_session").groupby("source_id"):
        last_agree: pd.Timestamp | None = None
        for row in block.itertuples(index=False):
            session = _date(row.membership_session)
            if int(row.symmetric_difference_count) <= agree_threshold:
                last_agree = session
                continue
            start = last_agree if last_agree is not None else _date(ledger["effective_date"].min())
            event_window = ledger[(ledger["effective_date"] > start) & (ledger["effective_date"] <= session)]
            rows.append(
                {
                    "source_id": source_id,
                    "last_agreeing_checkpoint": start.date().isoformat() if last_agree is not None else "",
                    "first_disagreeing_checkpoint": session.date().isoformat(),
                    "candidate_event_count": len(event_window),
                    "candidate_events_between": ";".join(event_window["event_id"].astype(str).head(75)),
                    "notes": (
                        f"First checkpoint with symmetric difference above {agree_threshold}; "
                        "use candidate events for targeted source research."
                    ),
                }
            )
            break
    return pd.DataFrame(rows)


def collapse_membership_spells(membership: pd.DataFrame) -> pd.DataFrame:
    if membership.empty:
        return normalize_membership(membership)
    members = normalize_membership(membership).sort_values(["security_id", "membership_start", "membership_end"])
    rows = []
    for security_id, block in members.groupby("security_id", sort=True):
        current_start: pd.Timestamp | None = None
        current_end: pd.Timestamp | None = None
        for row in block.itertuples(index=False):
            start = row.membership_start
            end = row.membership_end if pd.notna(row.membership_end) else pd.NaT
            if current_start is None:
                current_start = start
                current_end = end
                continue
            open_current = pd.isna(current_end)
            open_next = pd.isna(end)
            adjacent = False
            if not open_current:
                adjacent = start <= pd.Timestamp(current_end) + pd.Timedelta(days=1)
            if open_current or adjacent:
                if open_current or open_next:
                    current_end = pd.NaT
                else:
                    current_end = max(pd.Timestamp(current_end), pd.Timestamp(end))
                continue
            rows.append(
                {
                    "security_id": security_id,
                    "membership_start": current_start,
                    "membership_end": current_end,
                    "source": "phase2e_rebuilt",
                }
            )
            current_start = start
            current_end = end
        if current_start is not None:
            rows.append(
                {
                    "security_id": security_id,
                    "membership_start": current_start,
                    "membership_end": current_end,
                    "source": "phase2e_rebuilt",
                }
            )
    return normalize_membership(pd.DataFrame(rows))


def rebuild_membership_from_scratch(
    events: pd.DataFrame,
    anchor: pd.DataFrame,
    *,
    resolutions: pd.DataFrame,
    start_date: str,
    anchor_date: str,
    end_date: str,
) -> pd.DataFrame:
    calendar = TradingCalendar.xnys()
    change_events = event_ledger_to_change_events(
        events,
        calendar=calendar,
        include_unverified=True,
        provisional_security_ids=True,
        start_date=start_date,
        end_date=end_date,
    )
    if len(change_events):
        mapped = []
        evidence = []
        for row in change_events.itertuples(index=False):
            security_id, resolution_evidence = apply_identity_resolutions_to_security_id(
                row.security_id,
                row.effective_date,
                resolutions,
            )
            mapped.append(security_id)
            evidence.append(resolution_evidence)
        change_events = change_events.copy()
        change_events["security_id"] = mapped
        change_events["identity_resolution_evidence"] = evidence
    anchor_members = _resolved_anchor_members(anchor, resolutions, anchor_date)
    result = reconstruct_membership_from_change_events(
        change_events,
        anchor_date=anchor_date,
        anchor_members=anchor_members,
        start_date=start_date,
        end_date=end_date,
        source="phase2e_snapshot_triangulation",
        calendar=calendar,
    )
    return collapse_membership_spells(result.membership)


def _checkpoint_dates(start_date: str, end_date: str) -> list[str]:
    requested = [
        "2004-12-31",
        "2005-12-30",
        "2006-10-31",
        "2008-12-31",
        "2010-12-31",
        "2012-12-31",
        "2015-12-31",
        "2018-12-31",
        "2020-12-31",
        "2022-12-30",
        "2024-12-31",
        "2026-08-11",
    ]
    start = _date(start_date)
    end = _date(end_date)
    return [date for date in requested if start <= _date(date) <= end]


def acquire_snapshot_checkpoints(
    *,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    max_downloads: int,
    sleep_seconds: float,
    timeout: float,
    force: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    candidates = default_snapshot_candidates(end_date=end_date)
    artifacts: list[dict[str, Any]] = []
    checkpoint_rows: list[dict[str, Any]] = []
    filtered_rows: list[pd.DataFrame] = []
    snapshots: list[pd.DataFrame] = []
    remaining = max_downloads
    calendar = TradingCalendar.xnys()
    for index, candidate in enumerate(candidates):
        content, meta, remaining = _fetch_candidate(
            candidate,
            cache_dir=cache_dir,
            force=force,
            timeout=timeout,
            downloads_remaining=remaining,
        )
        artifacts.append(meta)
        parsed = pd.DataFrame()
        parse_status = "not_requested_or_unavailable"
        parse_error = meta.get("error", "")
        if content is not None:
            try:
                text = content.decode("utf-8-sig", errors="replace")
                if candidate.parser == "ishares_csv":
                    parsed, filtered = parse_ishares_snapshot_csv(text, candidate=candidate, source_url=candidate.url)
                    if not filtered.empty:
                        filtered_rows.append(filtered)
                elif candidate.parser == "fja_intervals":
                    parsed = parse_fja_interval_snapshots(
                        text,
                        candidate=candidate,
                        checkpoint_dates=_checkpoint_dates(start_date, end_date),
                    )
                else:
                    parsed = parse_generic_snapshot_csv(text, candidate=candidate, source_url=candidate.url)
                parsed = normalize_snapshot_frame(
                    parsed,
                    date_convention=candidate.date_convention,
                    calendar=calendar,
                )
                snapshots.append(parsed)
                parse_status = "parsed"
                parse_error = ""
            except (csv.Error, KeyError, TypeError, ValueError) as exc:
                parse_status = "parse_failed"
                parse_error = str(exc)
        checkpoint_rows.append(
            {
                "source_id": candidate.source_id,
                "snapshot_date": candidate.snapshot_date,
                "membership_session": (
                    map_snapshot_date_to_membership_session(
                        candidate.snapshot_date,
                        convention=candidate.date_convention,
                        calendar=calendar,
                    )
                    .date()
                    .isoformat()
                ),
                "source_tier": candidate.source_tier,
                "source_url": candidate.url,
                "date_convention": candidate.date_convention,
                "parser": candidate.parser,
                "rows_parsed": len(parsed),
                "acquisition_status": meta.get("cache_status", ""),
                "http_status": meta.get("http_status", ""),
                "sha256": meta.get("sha256", ""),
                "parse_status": parse_status,
                "error": parse_error,
                "known_limitations": candidate.known_limitations,
            }
        )
        if sleep_seconds and index != len(candidates) - 1 and content is not None and meta.get("cache_status") == "fetched":
            time.sleep(sleep_seconds)
    snapshot_frame = (
        pd.concat(snapshots, ignore_index=True)
        if snapshots
        else _empty_snapshot_frame()
    )
    filtered_frame = (
        pd.concat(filtered_rows, ignore_index=True)
        if filtered_rows
        else pd.DataFrame(columns=["snapshot_date", "source_id", "source_ticker", "source_security_name", "asset_class", "filter_reason"])
    )
    return snapshot_sources_catalog(), pd.DataFrame(checkpoint_rows), pd.DataFrame(artifacts), filtered_frame.join(pd.DataFrame()), snapshot_frame


def _snapshot_identifier_enrichment(mapped_snapshot: pd.DataFrame) -> pd.DataFrame:
    if mapped_snapshot.empty:
        return pd.DataFrame(
            columns=["security_id", "source_id", "snapshot_date", "isin", "cik", "other_external_id", "evidence", "notes"]
        )
    mapped = mapped_snapshot[mapped_snapshot["mapped_security_id"].astype(str).ne("")]
    mapped = mapped[
        mapped["isin"].astype(str).ne("")
        | mapped["cik"].astype(str).ne("")
        | mapped["other_external_id"].astype(str).ne("")
    ]
    rows = []
    for row in mapped.itertuples(index=False):
        rows.append(
            {
                "security_id": row.mapped_security_id,
                "source_id": row.source_id,
                "snapshot_date": pd.Timestamp(row.snapshot_date).date().isoformat(),
                "isin": row.isin,
                "cik": row.cik,
                "other_external_id": row.other_external_id,
                "evidence": row.source_url,
                "notes": "External identifier enrichment only; does not establish S&P membership authority.",
            }
        )
    return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)


def _review_conservation_failures(
    conservation: pd.DataFrame,
    comparisons: pd.DataFrame,
    *,
    failure_filter: str,
) -> pd.DataFrame:
    if conservation.empty:
        return pd.DataFrame()
    failures = conservation[
        conservation["traversal"].eq("BACKWARD")
        & conservation["count_excess_delta"].ne(0)
        & conservation["flags"].fillna("").str.contains(failure_filter, regex=True)
    ].copy()
    if "BACKWARD_" in failure_filter and "IDENTITY" not in failure_filter:
        failures = failures[
            ~failures["flags"].fillna("").str.contains("IDENTITY_ALIAS_MISMATCH|TICKER_PUNCTUATION_MISMATCH", regex=True)
        ].copy()
    rows = []
    for row in failures.itertuples(index=False):
        date = _date(row.effective_date)
        before = comparisons[pd.to_datetime(comparisons["membership_session"], errors="coerce") <= date] if len(comparisons) else pd.DataFrame()
        after = comparisons[pd.to_datetime(comparisons["membership_session"], errors="coerce") >= date] if len(comparisons) else pd.DataFrame()
        rows.append(
            {
                "event_id": row.event_id,
                "effective_date": row.effective_date,
                "security_id": row.security_id,
                "ticker_as_reported": row.ticker_as_reported,
                "company_name": row.company_name,
                "flags": row.flags,
                "count_excess_delta": int(row.count_excess_delta),
                "nearest_prior_checkpoint": before["membership_session"].max() if len(before) else "",
                "nearest_after_checkpoint": after["membership_session"].min() if len(after) else "",
                "root_cause_assessment": "identity mismatch candidate" if "IDENTITY" in str(row.flags) else "event/anchor inversion failure",
                "review_status": "EXPLICITLY_UNRESOLVED",
                "evidence_used": "snapshot triangulation did not supply source-backed lineage repair",
                "blocking_for_P3": True,
                "notes": "Requires targeted primary/archive or strong contemporaneous evidence before repair.",
            }
        )
    return pd.DataFrame(rows)


def _reentry_review_phase2e(base_reentry: pd.DataFrame, comparisons: pd.DataFrame) -> pd.DataFrame:
    if base_reentry.empty:
        return pd.DataFrame(
            columns=[
                "security_id",
                "spell_count",
                "identity_assessment",
                "same_security_reentry",
                "artifact_or_ticker_reuse",
                "review_status",
                "snapshot_evidence",
                "blocking_for_P3",
            ]
        )
    rows = []
    for row in base_reentry.itertuples(index=False):
        rows.append(
            {
                "security_id": row.security_id,
                "spell_count": row.spell_count,
                "first_start": row.first_start,
                "last_end": row.last_end,
                "identity_assessment": "unresolved",
                "same_security_reentry": "",
                "artifact_or_ticker_reuse": "",
                "review_status": "EXPLICITLY_UNRESOLVED",
                "snapshot_evidence": f"checked_checkpoint_rows={len(comparisons)}",
                "blocking_for_P3": True,
                "notes": "No same-security reentry preserved without continuity evidence.",
            }
        )
    return pd.DataFrame(rows)


def _membership_gap_register_phase2e(
    *,
    identity_review: pd.DataFrame,
    backward_review: pd.DataFrame,
    reentries: pd.DataFrame,
    comparisons: pd.DataFrame,
    early_period_status: str,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in identity_review.itertuples(index=False):
        rows.append(
            {
                "gap_id": f"identity:{row.event_id}",
                "gap_type": "identity_mismatch_unresolved",
                "date": row.effective_date,
                "security": row.security_id,
                "current_evidence": row.evidence_used,
                "attempted_sources": "historical snapshot checkpoints",
                "blocking_for_P3": True,
                "next_resolution_action": "Target S&P/archive or contemporaneous lineage evidence for this event.",
                "notes": row.notes,
            }
        )
    for row in backward_review.itertuples(index=False):
        rows.append(
            {
                "gap_id": f"backward:{row.event_id}",
                "gap_type": "backward_reconstruction_failure_unresolved",
                "date": row.effective_date,
                "security": row.security_id,
                "current_evidence": row.flags,
                "attempted_sources": "historical snapshot checkpoints",
                "blocking_for_P3": True,
                "next_resolution_action": "Determine whether source event, anchor, or identity lineage is wrong.",
                "notes": row.notes,
            }
        )
    for row in reentries.itertuples(index=False):
        rows.append(
            {
                "gap_id": f"reentry:{row.security_id}",
                "gap_type": "reentry_unresolved",
                "date": row.first_start,
                "security": row.security_id,
                "current_evidence": row.snapshot_evidence,
                "attempted_sources": "historical snapshot checkpoints",
                "blocking_for_P3": True,
                "next_resolution_action": "Use source-backed continuity, ticker-reuse, or successor evidence.",
                "notes": row.notes,
            }
        )
    if early_period_status != "defensible":
        rows.append(
            {
                "gap_id": "early-period:2004-2006",
                "gap_type": "early_period_snapshot_gap",
                "date": "2004-01-01/2006-12-31",
                "security": "S&P 500 universe",
                "current_evidence": "secondary ticker intervals and first iShares snapshot where available",
                "attempted_sources": "fja05680 intervals; iShares proxy from 2006-10-31; Wikipedia revision parser",
                "blocking_for_P3": True,
                "next_resolution_action": "Locate primary/archive S&P snapshots or targeted 2004-2006 announcements.",
                "notes": "Early period remains materially under-supported by independent dated snapshots.",
            }
        )
    worst = comparisons.sort_values("symmetric_difference_count", ascending=False).head(25) if len(comparisons) else pd.DataFrame()
    for row in worst.itertuples(index=False):
        rows.append(
            {
                "gap_id": f"snapshot:{row.source_id}:{row.membership_session}",
                "gap_type": "snapshot_set_difference",
                "date": row.membership_session,
                "security": "checkpoint constituent set",
                "current_evidence": f"symdiff={row.symmetric_difference_count}; recon_only={row.only_reconstructed_count}; source_only={row.only_snapshot_count}",
                "attempted_sources": row.source_id,
                "blocking_for_P3": int(row.symmetric_difference_count) > 25,
                "next_resolution_action": "Localize disagreement to event interval and resolve exact security-level differences.",
                "notes": "Snapshot is diagnostic evidence only; no count forcing.",
            }
        )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates().sort_values(["blocking_for_P3", "gap_type", "date"], ascending=[False, True, True])
    return out.reset_index(drop=True)


def _citation_ids(value: Any) -> list[str]:
    ids: list[str] = []
    for token in str(value or "").split(";"):
        cleaned = token.strip()
        if cleaned.startswith("#cite_note-"):
            ids.append(cleaned[1:])
    return ids


def _targeted_primary_research(
    events: pd.DataFrame,
    *,
    identity_review: pd.DataFrame,
    backward_review: pd.DataFrame,
    phase2d_dir: Path,
) -> pd.DataFrame:
    columns = [
        "event_id",
        "issue_type",
        "security_id",
        "effective_date",
        "source_tier",
        "source_url",
        "cached_artifact",
        "investigation_status",
        "resolution_status",
        "notes",
    ]
    targets: list[dict[str, Any]] = []
    for row in identity_review.itertuples(index=False):
        targets.append(
            {
                "event_id": row.event_id,
                "issue_type": "identity_mismatch",
                "security_id": row.security_id,
                "effective_date": row.effective_date,
            }
        )
    for row in backward_review.itertuples(index=False):
        targets.append(
            {
                "event_id": row.event_id,
                "issue_type": "backward_failure",
                "security_id": row.security_id,
                "effective_date": row.effective_date,
            }
        )
    if not targets:
        return pd.DataFrame(columns=columns)
    citation_path = phase2d_dir / "wikipedia_citation_references.csv"
    artifact_path = phase2d_dir / "source_artifacts.csv"
    citations = read_table(citation_path) if citation_path.exists() else pd.DataFrame()
    artifacts = read_table(artifact_path) if artifact_path.exists() else pd.DataFrame()
    artifact_urls = set()
    if len(artifacts) and "original_url" in artifacts.columns:
        ok = artifacts[artifacts.get("error", pd.Series(dtype=str)).astype(str).eq("")]
        artifact_urls = set(ok["original_url"].astype(str))
    citations_by_id = {key: block for key, block in citations.groupby("citation_id")} if len(citations) else {}
    event_by_id = {row.event_id: row for row in events.itertuples(index=False)}
    rows: list[dict[str, Any]] = []
    for target in targets:
        event = event_by_id.get(str(target["event_id"]))
        if event is None:
            rows.append(
                {
                    **target,
                    "source_tier": "",
                    "source_url": "",
                    "cached_artifact": False,
                    "investigation_status": "event_not_found",
                    "resolution_status": "UNRESOLVED",
                    "notes": "Target event was not present in the normalized event ledger.",
                }
            )
            continue
        candidate_rows = []
        for citation_id in _citation_ids(event.wikipedia_reference_urls):
            block = citations_by_id.get(citation_id)
            if block is not None:
                candidate_rows.extend(block.to_dict("records"))
        primary_candidates = [
            item
            for item in candidate_rows
            if str(item.get("source_tier", "")) in {"SP_PRIMARY", "SP_ARCHIVE"}
        ]
        if not primary_candidates:
            rows.append(
                {
                    **target,
                    "source_tier": "",
                    "source_url": "",
                    "cached_artifact": False,
                    "investigation_status": "no_primary_or_archive_candidate_in_phase2d_citations",
                    "resolution_status": "UNRESOLVED",
                    "notes": "No primary/archive URL was discoverable from the seed citation table.",
                }
            )
            continue
        for item in primary_candidates:
            url = str(item.get("url", ""))
            rows.append(
                {
                    **target,
                    "source_tier": str(item.get("source_tier", "")),
                    "source_url": url,
                    "cached_artifact": url in artifact_urls,
                    "investigation_status": "primary_or_archive_candidate_located",
                    "resolution_status": "UNRESOLVED_NO_LINEAGE_CHANGE",
                    "notes": "Candidate source is useful for targeted review, but was not enough here to approve an identity repair.",
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _manual_review_queue(gaps: pd.DataFrame) -> pd.DataFrame:
    if gaps.empty:
        return pd.DataFrame(columns=["priority", *gaps.columns])
    priorities = {
        "identity_mismatch_unresolved": 0,
        "backward_reconstruction_failure_unresolved": 1,
        "early_period_snapshot_gap": 2,
        "reentry_unresolved": 3,
        "snapshot_set_difference": 4,
    }
    out = gaps.copy()
    out.insert(0, "priority", [priorities.get(str(value), 9) for value in out["gap_type"]])
    return out.sort_values(["priority", "date", "gap_id"]).reset_index(drop=True)


def _count_stats(counts: pd.DataFrame) -> dict[str, int | None]:
    if counts.empty:
        return {"min": None, "median": None, "max": None}
    return {
        "min": int(counts["member_count"].min()),
        "median": int(counts["member_count"].median()),
        "max": int(counts["member_count"].max()),
    }


def _membership_count_progress(before: dict[str, int], after: dict[str, int | None], resolutions: pd.DataFrame) -> pd.DataFrame:
    approved = resolutions[
        resolutions["resolution_type"].str.upper().isin(MERGE_RESOLUTION_TYPES)
        & resolutions["reviewer_status"].str.upper().isin(APPROVED_REVIEWER_STATUSES)
    ] if len(resolutions) else pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "stage": "phase2d_baseline",
                "min_count": before["min"],
                "median_count": before["median"],
                "max_count": before["max"],
                "approved_identity_resolutions": 0,
                "count_delta_explanation": "Phase 2D provisional reconstruction.",
            },
            {
                "stage": "phase2e_after_snapshot_triage",
                "min_count": after["min"],
                "median_count": after["median"],
                "max_count": after["max"],
                "approved_identity_resolutions": len(approved),
                "count_delta_explanation": (
                    "No count changed unless a tracked, evidence-backed identity resolution was approved."
                ),
            },
        ]
    )


def _resolution_attribution(resolutions: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "resolution_id",
        "cause",
        "affected_security_ids",
        "affected_date_range",
        "count_delta",
        "evidence",
    ]
    if resolutions.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for row in resolutions.itertuples(index=False):
        approved = (
            str(row.resolution_type).upper() in MERGE_RESOLUTION_TYPES
            and str(row.reviewer_status).upper() in APPROVED_REVIEWER_STATUSES
        )
        rows.append(
            {
                "resolution_id": row.resolution_id,
                "cause": row.resolution_type,
                "affected_security_ids": f"{row.old_security_id}->{row.canonical_security_id}",
                "affected_date_range": f"{row.effective_start}/{row.effective_end}",
                "count_delta": "" if not approved else "requires rebuild audit",
                "evidence": row.evidence_url,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _phase2d_previous_required_ids(phase2d_dir: Path, fallback: int) -> int:
    summary_path = phase2d_dir / "phase2d_summary.json"
    if not summary_path.exists():
        return fallback
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    value = summary.get("membership", {}).get("unique_historical_security_ids")
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _phase2e_yahoo_plan_delta(
    yahoo_state: pd.DataFrame | None,
    rebuilt_membership: pd.DataFrame,
    *,
    phase2d_dir: Path,
    resolutions: pd.DataFrame,
) -> pd.DataFrame:
    new_ids = set(rebuilt_membership["security_id"].astype(str)) if len(rebuilt_membership) else set()
    previous_required = _phase2d_previous_required_ids(phase2d_dir, len(new_ids))
    if yahoo_state is None or yahoo_state.empty:
        return pd.DataFrame(
            [
                {
                    "metric": "previous_required_historical_ids",
                    "value": previous_required,
                    "notes": "Yahoo state not supplied; previous count came from Phase 2D summary when available.",
                },
                {
                    "metric": "revised_required_historical_ids",
                    "value": len(new_ids),
                    "notes": "No full Yahoo redownload performed.",
                },
            ]
        )
    state_ids = set(yahoo_state["security_id"].astype(str)) if "security_id" in yahoo_state.columns else set()
    failed = yahoo_state[yahoo_state.get("status", pd.Series(dtype=str)).astype(str).str.contains("failed", case=False, regex=True)]
    failed_ids = set(failed["security_id"].astype(str)) if len(failed) and "security_id" in failed.columns else set()
    alias_ids_not_required = sorted(state_ids - new_ids)
    still_required = sorted(failed_ids & new_ids)
    no_longer_required = sorted(failed_ids - new_ids)
    approved_resolutions = resolutions[
        resolutions["resolution_type"].str.upper().isin(MERGE_RESOLUTION_TYPES)
        & resolutions["reviewer_status"].str.upper().isin(APPROVED_REVIEWER_STATUSES)
    ] if len(resolutions) else pd.DataFrame()
    return pd.DataFrame(
        [
            {"metric": "previous_required_historical_ids", "value": previous_required, "notes": "Phase 2D membership union."},
            {"metric": "previous_yahoo_alias_rows", "value": len(state_ids), "notes": "Phase 2C/2D Yahoo acquisition plan aliases."},
            {"metric": "revised_required_historical_ids", "value": len(new_ids), "notes": "Rebuilt Phase 2E membership union; no full Yahoo redownload performed."},
            {
                "metric": "yahoo_alias_ids_not_in_rebuilt_membership",
                "value": len(alias_ids_not_required),
                "notes": ";".join(alias_ids_not_required[:25]),
            },
            {"metric": "genuinely_new_historical_members", "value": len(new_ids - state_ids), "notes": ";".join(sorted(new_ids - state_ids)[:25])},
            {"metric": "old_yahoo_failures_still_required", "value": len(still_required), "notes": ";".join(still_required[:25])},
            {"metric": "old_yahoo_failures_no_longer_relevant", "value": len(no_longer_required), "notes": ";".join(no_longer_required[:25])},
            {"metric": "aliases_changed", "value": len(approved_resolutions), "notes": "Approved identity-resolution rows that would alter aliases."},
        ]
    )


def _early_period_status(checkpoints: pd.DataFrame, comparisons: pd.DataFrame) -> pd.DataFrame:
    early = checkpoints[pd.to_datetime(checkpoints["membership_session"], errors="coerce").dt.year.between(2004, 2006)]
    snapshot_evidence = int(early["rows_parsed"].gt(0).sum()) if len(early) else 0
    status = "UNVERIFIED" if snapshot_evidence else "FAIL"
    notes = (
        "2004-2006 has secondary ticker-interval checkpoints and iShares only from 2006-10-31 where acquired; "
        "primary full constituent snapshots remain unavailable."
    )
    if len(comparisons):
        early_cmp = comparisons[pd.to_datetime(comparisons["membership_session"], errors="coerce").dt.year.between(2004, 2006)]
        if len(early_cmp) and int(early_cmp["symmetric_difference_count"].median()) > 25:
            status = "FAIL"
    return pd.DataFrame(
        [
            {
                "period": "2004-2006",
                "snapshot_evidence_available": snapshot_evidence,
                "primary_or_archived_snapshot_evidence": 0,
                "remaining_gaps": "primary full constituent snapshots; targeted 2004-2006 event evidence",
                "early_period_status": status,
                "notes": notes,
            }
        ]
    )


def write_phase2e_reports(
    *,
    events_path: Path,
    anchor_path: Path,
    phase2d_dir: Path,
    out_dir: Path,
    snapshot_cache_dir: Path,
    identity_resolutions_path: Path | None = None,
    yahoo_state_path: Path | None = None,
    start_date: str = "2004-01-01",
    anchor_date: str = "2026-08-11",
    end_date: str = "2026-08-11",
    max_snapshot_downloads: int = 0,
    snapshot_sleep_seconds: float = 0.25,
    force_snapshots: bool = False,
    snapshot_timeout: float = 20.0,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = normalize_event_ledger(read_table(events_path))
    events = events[
        (events["effective_date"] >= _date(start_date))
        & (events["effective_date"] <= _date(end_date))
    ].reset_index(drop=True)
    anchor = read_table(anchor_path)
    resolutions = load_identity_resolutions(identity_resolutions_path)
    rebuilt_membership = rebuild_membership_from_scratch(
        events,
        anchor,
        resolutions=resolutions,
        start_date=start_date,
        anchor_date=anchor_date,
        end_date=end_date,
    )
    lineages = build_identity_lineages(events, anchor)
    counts = membership_count_timeseries(rebuilt_membership, start_date=start_date, end_date=end_date)
    count_anomalies = count_anomaly_intervals(counts)
    resolved_events = _events_with_resolved_security_ids(events, resolutions)
    conservation, conservation_summary = reconstruction_conservation_audit(
        resolved_events,
        anchor_members=set(_resolved_anchor_members(anchor, resolutions, anchor_date)),
        start_date=start_date,
        anchor_date=anchor_date,
        end_date=end_date,
    )
    causes = count_inflation_causes(conservation)
    sources, checkpoints, artifacts, filtered_rows, raw_snapshots = acquire_snapshot_checkpoints(
        cache_dir=snapshot_cache_dir,
        start_date=start_date,
        end_date=end_date,
        max_downloads=max_snapshot_downloads,
        sleep_seconds=snapshot_sleep_seconds,
        timeout=snapshot_timeout,
        force=force_snapshots,
    )
    mapped_snapshots = map_snapshot_identities(
        raw_snapshots,
        lineages=lineages,
        membership=rebuilt_membership,
        resolutions=resolutions,
    )
    comparisons, difference_details = compare_snapshot_to_membership(mapped_snapshots, rebuilt_membership)
    source_disagreements = snapshot_source_disagreements(mapped_snapshots, rebuilt_membership)
    intervals = localize_error_intervals(comparisons, events)
    identity_review = _review_conservation_failures(conservation, comparisons, failure_filter="IDENTITY_ALIAS_MISMATCH|TICKER_PUNCTUATION_MISMATCH")
    backward_review = _review_conservation_failures(
        conservation,
        comparisons,
        failure_filter="BACKWARD_ADD_NOT_PRESENT|BACKWARD_REMOVE_ALREADY_PRESENT",
    )
    base_reentries = reentry_review(rebuilt_membership, lineages)
    reentries = _reentry_review_phase2e(base_reentries, comparisons)
    targeted_primary = _targeted_primary_research(
        events,
        identity_review=identity_review,
        backward_review=backward_review,
        phase2d_dir=phase2d_dir,
    )
    early_status = _early_period_status(checkpoints, comparisons)
    gaps = _membership_gap_register_phase2e(
        identity_review=identity_review,
        backward_review=backward_review,
        reentries=reentries,
        comparisons=comparisons,
        early_period_status=str(early_status.loc[0, "early_period_status"]).lower(),
    )
    manual = _manual_review_queue(gaps)
    ambiguities = identity_ambiguities(lineages, conservation)
    id_enrichment = _snapshot_identifier_enrichment(mapped_snapshots)
    yahoo_state = read_table(yahoo_state_path) if yahoo_state_path is not None and yahoo_state_path.exists() else None
    yahoo_delta = _phase2e_yahoo_plan_delta(
        yahoo_state,
        rebuilt_membership,
        phase2d_dir=phase2d_dir,
        resolutions=resolutions,
    )
    before_stats = {"min": 503, "median": 543, "max": 574}
    after_stats = _count_stats(counts)
    progress = _membership_count_progress(before_stats, after_stats, resolutions)
    resolution_attribution = _resolution_attribution(resolutions)
    source_counts = (
        comparisons.groupby("source_id")["membership_session"].nunique().to_dict()
        if len(comparisons)
        else (
            checkpoints[checkpoints["rows_parsed"].gt(0)].groupby("source_id")["membership_session"].nunique().to_dict()
            if len(checkpoints)
            else {}
        )
    )
    for source_id, count in source_counts.items():
        sources.loc[sources["source_id"].eq(source_id), "snapshots_acquired"] = int(count)

    _write_table(out_dir / "snapshot_sources.csv", sources)
    _write_table(out_dir / "snapshot_checkpoints.csv", checkpoints)
    _write_table(out_dir / "snapshot_artifacts.csv", artifacts)
    _write_table(out_dir / "snapshot_filtered_rows.csv", filtered_rows)
    _write_table(out_dir / "snapshot_rows_mapped.csv", mapped_snapshots)
    _write_table(out_dir / "snapshot_set_differences.csv", comparisons)
    _write_table(out_dir / "snapshot_set_difference_details.csv", difference_details)
    _write_table(out_dir / "multi_source_disagreements.csv", source_disagreements)
    _write_table(out_dir / "error_intervals.csv", intervals)
    _write_table(out_dir / "identity_mismatch_review.csv", identity_review)
    _write_table(out_dir / "backward_failure_review.csv", backward_review)
    _write_table(out_dir / "reentry_review.csv", reentries)
    _write_table(out_dir / "targeted_primary_research.csv", targeted_primary)
    _write_table(out_dir / "identity_resolutions.csv", resolutions)
    _write_table(out_dir / "identity_resolution_attribution.csv", resolution_attribution)
    _write_table(out_dir / "identity_identifier_enrichment.csv", id_enrichment)
    _write_table(out_dir / "identity_ambiguities.csv", ambiguities)
    _write_table(out_dir / "rebuilt_membership.csv", rebuilt_membership)
    _write_table(out_dir / "membership_counts.csv", counts)
    _write_table(out_dir / "membership_count_anomalies.csv", count_anomalies)
    _write_table(out_dir / "membership_count_progress.csv", progress)
    _write_table(out_dir / "reconstruction_conservation.csv", conservation)
    _write_table(out_dir / "count_inflation_causes.csv", causes)
    _write_table(out_dir / "residual_membership_gaps.csv", gaps)
    _write_table(out_dir / "manual_review_queue.csv", manual)
    _write_table(out_dir / "early_period_status.csv", early_status)
    _write_table(out_dir / "yahoo_plan_delta.csv", yahoo_delta)

    checkpoint_compared = len(comparisons)
    median_source_count = int(comparisons["snapshot_count"].median()) if checkpoint_compared else None
    median_symdiff = int(comparisons["symmetric_difference_count"].median()) if checkpoint_compared else None
    membership_certification = "FAIL"
    if (
        int(conservation_summary["backward_conservation_failures"]) == 0
        and not len(gaps[gaps["blocking_for_P3"].eq(True)])
        and checkpoint_compared
        and (median_symdiff is not None and median_symdiff <= 25)
    ):
        membership_certification = "PASS"
    elif checkpoint_compared and median_symdiff is not None and median_symdiff <= 25:
        membership_certification = "UNVERIFIED"
    summary = {
        "phase": "2E",
        "generated_at_utc": _now_utc(),
        "inputs": {
            "events": str(events_path),
            "anchor": str(anchor_path),
            "phase2d_dir": str(phase2d_dir),
            "identity_resolutions": str(identity_resolutions_path) if identity_resolutions_path else None,
            "yahoo_state": str(yahoo_state_path) if yahoo_state_path else None,
        },
        "strict_exclusions": {
            "strategy_performance_run": False,
            "p0_p1_p2_p3_p4_p5_run": False,
            "sharpe_cagr_wealth_returns_drawdown_calculated": False,
            "full_yahoo_redownload": False,
        },
        "snapshot_sources": {
            "sources_evaluated": len(sources),
            "checkpoints_attempted": len(checkpoints),
            "checkpoints_compared": checkpoint_compared,
            "snapshots_with_rows": int(checkpoints["rows_parsed"].gt(0).sum()) if len(checkpoints) else 0,
            "source_counts": {str(k): int(v) for k, v in source_counts.items()},
            "download_errors": int(
                artifacts[
                    artifacts["cache_status"].astype(str).eq("fetched")
                    & artifacts["error"].astype(str).ne("")
                ].shape[0]
            ) if len(artifacts) and {"cache_status", "error"}.issubset(artifacts.columns) else 0,
        },
        "checkpoint_comparison": {
            "median_reconstructed_count": int(comparisons["reconstructed_count"].median()) if checkpoint_compared else None,
            "median_source_snapshot_count": median_source_count,
            "median_symmetric_difference": median_symdiff,
            "worst_symmetric_difference": int(comparisons["symmetric_difference_count"].max()) if checkpoint_compared else None,
            "source_disagreements": len(source_disagreements),
        },
        "count_inflation": {
            "before": before_stats,
            "after": after_stats,
            "excess_removed": before_stats["median"] - int(after_stats["median"] or 0),
            "cause_rows": len(causes),
        },
        "conservation": conservation_summary,
        "identity_mismatches": {
            "reviewed": len(identity_review),
            "resolved_by_config": int(
                resolutions["resolution_type"].str.upper().isin(MERGE_RESOLUTION_TYPES).sum()
            ) if len(resolutions) else 0,
            "unresolved": int(identity_review["review_status"].eq("EXPLICITLY_UNRESOLVED").sum()) if len(identity_review) else 0,
            "resolution_type_counts": _resolution_counts(resolutions),
        },
        "backward_failures": {
            "reviewed": len(backward_review),
            "resolved": int(backward_review["review_status"].eq("RESOLVED").sum()) if len(backward_review) else 0,
            "unresolved": int(backward_review["review_status"].eq("EXPLICITLY_UNRESOLVED").sum()) if len(backward_review) else 0,
        },
        "reentries": {
            "reviewed": len(reentries),
            "confirmed_same_security": int(reentries["same_security_reentry"].astype(str).eq("True").sum()) if len(reentries) else 0,
            "unresolved": int(reentries["review_status"].eq("EXPLICITLY_UNRESOLVED").sum()) if len(reentries) else 0,
        },
        "primary_research": {
            "targeted_cases_investigated": int(targeted_primary["event_id"].nunique()) if len(targeted_primary) else 0,
            "primary_candidates_located": int(targeted_primary["source_tier"].eq("SP_PRIMARY").sum()) if len(targeted_primary) else 0,
            "archived_primary_candidates_located": int(targeted_primary["source_tier"].eq("SP_ARCHIVE").sum()) if len(targeted_primary) else 0,
            "cached_artifacts_available": int(targeted_primary["cached_artifact"].sum()) if len(targeted_primary) else 0,
            "resolved_using_primary": 0,
            "resolved_using_archived_primary": 0,
            "resolved_using_secondary_or_multisource": 0,
            "unresolved": int(targeted_primary["event_id"].nunique()) if len(targeted_primary) else 0,
        },
        "membership": {
            "period_start": start_date,
            "period_end": end_date,
            "unique_historical_security_ids": int(rebuilt_membership["security_id"].nunique()) if len(rebuilt_membership) else 0,
            "membership_spells": len(rebuilt_membership),
            "count_anomaly_intervals": len(count_anomalies),
            "certification": membership_certification,
        },
        "identity": {
            "internal_ids": int(lineages["security_id"].nunique()) if len(lineages) else 0,
            "alias_rows": len(lineages),
            "external_id_enrichment_rows": len(id_enrichment),
            "tracked_identity_resolutions": len(resolutions),
            "unresolved_identity_cases": len(ambiguities),
        },
        "gaps": {
            "total": len(gaps),
            "p3_blocking": int(gaps["blocking_for_P3"].sum()) if len(gaps) else 0,
            "manual_review_queue": len(manual),
        },
        "early_period_status": early_status.to_dict(orient="records")[0],
        "p2_status_unchanged_from_phase2c": "FAIL",
        "overall_p3_status": "FAIL" if membership_certification != "PASS" else "FAIL_P2_BLOCKED",
    }
    write_json(out_dir / "phase2e_summary.json", summary)
    return summary
