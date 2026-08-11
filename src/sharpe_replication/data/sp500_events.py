from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

from .io import read_table
from .trading_calendar import TradingCalendar, first_membership_session, first_nonmember_session

EVENT_REQUIRED = [
    "event_id",
    "announcement_date",
    "effective_date",
    "effective_session",
    "action",
    "company_name",
    "ticker_as_reported",
    "replacement_group_id",
    "paired_event_id",
    "source_tier",
    "source_url",
    "archive_url",
    "source_retrieved_at_utc",
    "source_sha256",
    "verification_status",
    "evidence_notes",
    "source_security_name",
    "source_symbol",
    "wikipedia_row_id",
    "wikipedia_reference_urls",
]

EFFECTIVE_SESSIONS = {"BEFORE_OPEN", "AFTER_CLOSE", "DATE_ONLY", "UNKNOWN"}
ACTIONS = {"ADD", "REMOVE"}
SOURCE_TIERS = {"SP_PRIMARY", "SP_ARCHIVE", "WIKIPEDIA_SEED", "CONTEMPORANEOUS_FALLBACK"}
VERIFICATION_STATUSES = {
    "VERIFIED_PRIMARY",
    "VERIFIED_ARCHIVED_PRIMARY",
    "VERIFIED_SECONDARY",
    "UNVERIFIED",
}


@dataclass
class _Cell:
    text: str = ""
    links: list[str] = field(default_factory=list)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[_Cell]]] = []
        self._current_table: list[list[_Cell]] | None = None
        self._current_row: list[_Cell] | None = None
        self._current_cell: _Cell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table":
            self._current_table = []
        elif tag == "tr" and self._current_table is not None:
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = _Cell()
        elif tag == "a" and self._current_cell is not None and attrs_dict.get("href"):
            self._current_cell.links.append(str(attrs_dict["href"]))

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.text += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            self._current_cell.text = _clean_text(self._current_cell.text)
            self._current_row.append(self._current_cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None and self._current_table is not None:
            if any(cell.text or cell.links for cell in self._current_row):
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._current_table is not None:
            self.tables.append(self._current_table)
            self._current_table = None


def _clean_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", unescape("" if value is None else str(value)))
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_reported_symbol(value: Any) -> str:
    text = _clean_text(value).upper()
    text = re.sub(r"^(NYSE|NASDAQ|AMEX|CBOE):", "", text)
    text = text.replace(" ", "")
    return text


def yahoo_symbol_from_reported(value: Any) -> str:
    return normalize_reported_symbol(value).replace(".", "-")


def provisional_security_id_from_symbol_name(symbol: Any, company_name: Any, source: str = "wiki") -> str:
    ticker = normalize_reported_symbol(symbol) or "UNKNOWN"
    name = re.sub(r"[^A-Z0-9]+", "-", str(company_name).upper()).strip("-")[:48] or "UNKNOWN"
    return f"{source}:{ticker}:{name}"


def _date_or_none(value: Any) -> pd.Timestamp | None:
    text = _clean_text(value)
    if not text or text.lower() in {"nan", "none"}:
        return None
    try:
        parsed = pd.to_datetime(text, errors="raise")
    except (TypeError, ValueError):
        return None
    return parsed.normalize()


def _table_looks_like_changes(table: list[list[_Cell]]) -> bool:
    joined_rows = [" ".join(cell.text.lower() for cell in row) for row in table[:5]]
    joined = " ".join(joined_rows)
    return "date" in joined and "added" in joined and "removed" in joined


def _table_looks_like_current_constituents(table: list[list[_Cell]]) -> bool:
    if not table:
        return False
    headers = {cell.text.lower() for cell in table[0]}
    return {"symbol", "security"}.issubset(headers) and any("gics" in value for value in headers)


def _absolute_wikipedia_url(href: str) -> str:
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://en.wikipedia.org{href}"
    return href


def _event_row(
    *,
    source_row_id: str,
    effective_date: pd.Timestamp,
    action: str,
    company_name: str,
    ticker: str,
    replacement_group_id: str,
    source_url: str,
    reference_urls: list[str],
    reason: str,
    event_index: int,
) -> dict[str, Any]:
    symbol = normalize_reported_symbol(ticker)
    name = _clean_text(company_name)
    event_id = f"wikipedia:{source_row_id}:{action}:{event_index}:{symbol or name}"
    return {
        "event_id": event_id,
        "announcement_date": pd.NaT,
        "effective_date": effective_date,
        "effective_session": "UNKNOWN",
        "action": action,
        "company_name": name,
        "ticker_as_reported": symbol,
        "replacement_group_id": replacement_group_id,
        "paired_event_id": "",
        "source_tier": "WIKIPEDIA_SEED",
        "source_url": source_url,
        "archive_url": "",
        "source_retrieved_at_utc": "",
        "source_sha256": "",
        "verification_status": "UNVERIFIED",
        "evidence_notes": reason,
        "source_security_name": name,
        "source_symbol": symbol,
        "wikipedia_row_id": source_row_id,
        "wikipedia_reference_urls": ";".join(dict.fromkeys(reference_urls)),
    }


def parse_wikipedia_change_table(html: str, *, source_url: str) -> pd.DataFrame:
    """Parse Wikipedia's S&P 500 changes table as an unverified event seed.

    The parser deliberately rejects pages where it cannot find the expected Date/Added/Removed
    table shape. Wikipedia rows are not certified evidence; every emitted event is UNVERIFIED.
    """

    parser = _TableParser()
    parser.feed(html)
    candidates = [table for table in parser.tables if _table_looks_like_changes(table)]
    if not candidates:
        raise ValueError("Could not find an S&P 500 changes table with Date/Added/Removed headers")
    table = candidates[0]

    rows: list[dict[str, Any]] = []
    current_date: pd.Timestamp | None = None
    data_row_count = 0
    for raw_index, cells in enumerate(table):
        texts = [cell.text for cell in cells]
        lower = " ".join(text.lower() for text in texts)
        if "date" in lower and ("added" in lower or "removed" in lower):
            continue
        if not texts:
            continue

        parsed_date = _date_or_none(texts[0])
        if parsed_date is not None:
            current_date = parsed_date
            payload = texts[1:]
            cell_links = [link for cell in cells for link in cell.links]
        elif current_date is not None and len(texts) >= 5:
            payload = texts
            cell_links = [link for cell in cells for link in cell.links]
        else:
            continue

        if len(payload) < 5:
            raise ValueError(f"Malformed Wikipedia changes row {raw_index}: {texts}")

        added_ticker, added_company, removed_ticker, removed_company, reason = payload[:5]
        if not any([added_ticker, added_company, removed_ticker, removed_company]):
            raise ValueError(f"Malformed Wikipedia changes row {raw_index}: no add/remove fields")
        data_row_count += 1
        row_id = f"wikipedia-row-{data_row_count:05d}"
        group_id = row_id
        reference_urls = [_absolute_wikipedia_url(link) for link in cell_links]
        event_index = 0
        if _clean_text(added_ticker) or _clean_text(added_company):
            rows.append(
                _event_row(
                    source_row_id=row_id,
                    effective_date=current_date,
                    action="ADD",
                    company_name=added_company,
                    ticker=added_ticker,
                    replacement_group_id=group_id,
                    source_url=source_url,
                    reference_urls=reference_urls,
                    reason=_clean_text(reason),
                    event_index=event_index,
                )
            )
            event_index += 1
        if _clean_text(removed_ticker) or _clean_text(removed_company):
            rows.append(
                _event_row(
                    source_row_id=row_id,
                    effective_date=current_date,
                    action="REMOVE",
                    company_name=removed_company,
                    ticker=removed_ticker,
                    replacement_group_id=group_id,
                    source_url=source_url,
                    reference_urls=reference_urls,
                    reason=_clean_text(reason),
                    event_index=event_index,
                )
            )

    if not rows:
        raise ValueError("Wikipedia changes table contained no parseable add/remove events")
    return normalize_event_ledger(pd.DataFrame(rows))


def parse_wikipedia_current_constituents(html: str, *, source_url: str) -> pd.DataFrame:
    """Parse Wikipedia's current S&P 500 table as a provisional reconstruction anchor."""

    parser = _TableParser()
    parser.feed(html)
    candidates = [table for table in parser.tables if _table_looks_like_current_constituents(table)]
    if not candidates:
        raise ValueError("Could not find a current S&P 500 constituent table with Symbol/Security/GICS headers")
    table = candidates[0]
    headers = [cell.text for cell in table[0]]
    rows: list[dict[str, Any]] = []
    for raw_index, cells in enumerate(table[1:], start=1):
        values = {headers[index]: cells[index] for index in range(min(len(headers), len(cells)))}
        symbol_cell = values.get("Symbol")
        security_cell = values.get("Security")
        if symbol_cell is None or security_cell is None:
            raise ValueError(f"Malformed Wikipedia constituent row {raw_index}: missing Symbol/Security cells")
        symbol = normalize_reported_symbol(symbol_cell.text)
        company = _clean_text(security_cell.text)
        if not symbol or not company:
            raise ValueError(f"Malformed Wikipedia constituent row {raw_index}: blank Symbol/Security value")
        date_added = _date_or_none(values.get("Date added").text if values.get("Date added") else "")
        reference_urls = [
            _absolute_wikipedia_url(link)
            for cell in cells
            for link in cell.links
        ]
        rows.append(
            {
                "ticker": symbol,
                "company_name": company,
                "gics_sector": _clean_text(values.get("GICS Sector").text if values.get("GICS Sector") else ""),
                "gics_sub_industry": _clean_text(
                    values.get("GICS Sub-Industry").text if values.get("GICS Sub-Industry") else ""
                ),
                "headquarters_location": _clean_text(
                    values.get("Headquarters Location").text if values.get("Headquarters Location") else ""
                ),
                "date_added": date_added,
                "cik": _clean_text(values.get("CIK").text if values.get("CIK") else ""),
                "founded": _clean_text(values.get("Founded").text if values.get("Founded") else ""),
                "source_url": source_url,
                "source_tier": "WIKIPEDIA_ANCHOR",
                "verification_status": "UNVERIFIED",
                "wikipedia_row_id": f"wikipedia-current-row-{raw_index:05d}",
                "wikipedia_reference_urls": ";".join(dict.fromkeys(reference_urls)),
            }
        )
    if not rows:
        raise ValueError("Wikipedia current constituent table contained no parseable rows")
    out = pd.DataFrame(rows)
    out["date_added"] = pd.to_datetime(out["date_added"], errors="coerce").dt.normalize()
    return out.sort_values(["ticker", "company_name"]).reset_index(drop=True)


def normalize_event_ledger(events: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(EVENT_REQUIRED) - set(events.columns))
    if missing:
        raise ValueError(f"S&P event ledger missing required columns: {missing}")
    out = events.copy()
    for column in [
        "event_id",
        "effective_session",
        "action",
        "company_name",
        "ticker_as_reported",
        "replacement_group_id",
        "paired_event_id",
        "source_tier",
        "source_url",
        "archive_url",
        "source_retrieved_at_utc",
        "source_sha256",
        "verification_status",
        "evidence_notes",
        "source_security_name",
        "source_symbol",
        "wikipedia_row_id",
        "wikipedia_reference_urls",
    ]:
        out[column] = out[column].astype("string").fillna("").map(_clean_text)
    out["ticker_as_reported"] = out["ticker_as_reported"].map(normalize_reported_symbol)
    out["source_symbol"] = out["source_symbol"].map(normalize_reported_symbol)
    out["effective_session"] = out["effective_session"].str.upper()
    out["action"] = out["action"].str.upper()
    out["source_tier"] = out["source_tier"].str.upper()
    out["verification_status"] = out["verification_status"].str.upper()
    out["announcement_date"] = pd.to_datetime(out["announcement_date"], errors="coerce").dt.normalize()
    out["effective_date"] = pd.to_datetime(out["effective_date"], errors="raise").dt.normalize()
    invalid_sessions = sorted(set(out["effective_session"]) - EFFECTIVE_SESSIONS)
    invalid_actions = sorted(set(out["action"]) - ACTIONS)
    invalid_tiers = sorted(set(out["source_tier"]) - SOURCE_TIERS)
    invalid_verifications = sorted(set(out["verification_status"]) - VERIFICATION_STATUSES)
    if invalid_sessions:
        raise ValueError(f"Unsupported effective_session values: {invalid_sessions}")
    if invalid_actions:
        raise ValueError(f"Unsupported event action values: {invalid_actions}")
    if invalid_tiers:
        raise ValueError(f"Unsupported source_tier values: {invalid_tiers}")
    if invalid_verifications:
        raise ValueError(f"Unsupported verification_status values: {invalid_verifications}")
    return out[EVENT_REQUIRED].sort_values(["effective_date", "replacement_group_id", "action"]).reset_index(drop=True)


def read_event_ledger(path: str | Path) -> pd.DataFrame:
    return normalize_event_ledger(read_table(path))


def write_event_ledger(path: str | Path, events: pd.DataFrame) -> None:
    out = normalize_event_ledger(events).copy()
    for column in ["announcement_date", "effective_date"]:
        out[column] = out[column].dt.date.astype("string")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if Path(path).suffix.lower() == ".parquet":
        out.to_parquet(path, index=False)
    else:
        out.to_csv(path, index=False)


def merge_event_evidence(seed_events: pd.DataFrame, evidence: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Overlay stronger evidence on seed events and return discrepancy rows.

    Evidence rows are keyed by `event_id`. Primary/archived/fallback rows replace the seed factual
    extraction for the same event; mismatched fields are retained as discrepancy records.
    """

    seed = normalize_event_ledger(seed_events)
    ev = normalize_event_ledger(evidence)
    seed_by_id = seed.set_index("event_id", drop=False)
    discrepancies: list[dict[str, Any]] = []
    for row in ev.itertuples(index=False):
        if row.event_id not in seed_by_id.index:
            seed = pd.concat([seed, pd.DataFrame([row._asdict()])], ignore_index=True)
            continue
        idx = seed.index[seed["event_id"].eq(row.event_id)][0]
        for field_name in ["effective_date", "action", "company_name", "ticker_as_reported", "effective_session"]:
            old = seed.at[idx, field_name]
            new = getattr(row, field_name)
            old_value = old.isoformat() if isinstance(old, pd.Timestamp) else str(old)
            new_value = new.isoformat() if isinstance(new, pd.Timestamp) else str(new)
            if old_value and new_value and old_value != new_value:
                discrepancies.append(
                    {
                        "event_id": row.event_id,
                        "field": field_name,
                        "seed_value": old_value,
                        "evidence_value": new_value,
                        "source_tier": row.source_tier,
                    }
                )
        for column in EVENT_REQUIRED:
            seed.at[idx, column] = getattr(row, column)
    return normalize_event_ledger(seed), pd.DataFrame(discrepancies)


def event_completeness_report(events: pd.DataFrame, gaps: pd.DataFrame | None = None) -> dict[str, Any]:
    ledger = normalize_event_ledger(events)
    unresolved = ledger["verification_status"].eq("UNVERIFIED")
    unknown_timing = ledger["effective_session"].eq("UNKNOWN")
    by_year = {}
    for year, block in ledger.groupby(ledger["effective_date"].dt.year):
        by_year[str(int(year))] = {
            "event_count": len(block),
            "verified_count": int((~block["verification_status"].eq("UNVERIFIED")).sum()),
            "unresolved_count": int(block["verification_status"].eq("UNVERIFIED").sum()),
            "discovered_missing_events": 0,
        }
    if gaps is not None and len(gaps) and "year" in gaps.columns:
        for year, count in gaps["year"].value_counts().sort_index().items():
            item = by_year.setdefault(str(int(year)), {"event_count": 0, "verified_count": 0, "unresolved_count": 0})
            item["discovered_missing_events"] = int(count)
    years = sorted(int(year) for year in by_year)
    zero_event_years: list[int] = []
    if years:
        for year in range(min(years), max(years) + 1):
            if by_year.get(str(year), {}).get("event_count", 0) == 0:
                zero_event_years.append(year)
    return {
        "total_events": len(ledger),
        "wikipedia_seed_events": int(ledger["source_tier"].eq("WIKIPEDIA_SEED").sum()),
        "additional_events_discovered_outside_wikipedia": int(ledger["wikipedia_row_id"].eq("").sum()),
        "primary_verified": int(ledger["verification_status"].eq("VERIFIED_PRIMARY").sum()),
        "archived_primary_verified": int(ledger["verification_status"].eq("VERIFIED_ARCHIVED_PRIMARY").sum()),
        "fallback_verified": int(ledger["verification_status"].eq("VERIFIED_SECONDARY").sum()),
        "unresolved": int(unresolved.sum()),
        "unknown_effective_session_events": int(unknown_timing.sum()),
        "coverage_by_year": by_year,
        "years_with_zero_events": zero_event_years,
    }


def gap_register_from_events(events: pd.DataFrame) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    rows = []
    for row in ledger[
        ledger["verification_status"].eq("UNVERIFIED") | ledger["effective_session"].eq("UNKNOWN")
    ].itertuples(index=False):
        unverified = row.verification_status == "UNVERIFIED"
        problem_type = (
            "unverified_membership_event"
            if unverified
            else "unknown_effective_session_timing"
        )
        note = (
            "Wikipedia seed event has not been verified against primary or fallback evidence."
            if unverified
            else "Event evidence does not establish before-open or after-close effective timing."
        )
        rows.append(
            {
                "year": int(row.effective_date.year),
                "date": row.effective_date.date().isoformat(),
                "event_security": f"{row.action} {row.ticker_as_reported} {row.company_name}".strip(),
                "problem_type": problem_type,
                "wikipedia_reference": row.wikipedia_reference_urls,
                "primary_source_found": bool(row.verification_status == "VERIFIED_PRIMARY"),
                "fallback_source": "",
                "effect_on_membership": (
                    "event included only if explicitly requested as provisional"
                    if unverified
                    else "normalized date remains timing-uncertain and is blocking for P3 certification"
                ),
                "blocking_for_P3": True,
                "notes": note,
            }
        )
    return pd.DataFrame(rows)


def event_ledger_to_change_events(
    events: pd.DataFrame,
    *,
    calendar: TradingCalendar | None = None,
    include_unverified: bool = False,
    provisional_security_ids: bool = False,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    if not include_unverified:
        ledger = ledger[~ledger["verification_status"].eq("UNVERIFIED")]
    if start_date is not None:
        ledger = ledger[ledger["effective_date"] >= pd.Timestamp(start_date).normalize()]
    if end_date is not None:
        ledger = ledger[ledger["effective_date"] <= pd.Timestamp(end_date).normalize()]
    cal = calendar or TradingCalendar.xnys()
    rows = []
    for row in ledger.itertuples(index=False):
        security_id = row.source_symbol or row.ticker_as_reported
        if provisional_security_ids:
            security_id = provisional_security_id_from_symbol_name(
                row.source_symbol or row.ticker_as_reported,
                row.source_security_name or row.company_name,
            )
        if row.action == "ADD":
            change_date = first_membership_session(row.effective_date, row.effective_session, cal)
            action = "addition"
        else:
            change_date = first_nonmember_session(row.effective_date, row.effective_session, cal)
            action = "removal"
        rows.append(
            {
                "effective_date": change_date,
                "security_id": security_id,
                "action": action,
                "announcement_date": row.announcement_date,
                "source_event_id": row.event_id,
                "verification_status": row.verification_status,
                "effective_session": row.effective_session,
                "timing_uncertain": row.effective_session == "UNKNOWN",
            }
        )
    return pd.DataFrame(rows)


def parse_wikipedia_file(path: str | Path, *, source_url: str) -> pd.DataFrame:
    return parse_wikipedia_change_table(Path(path).read_text(encoding="utf-8"), source_url=source_url)


def event_ledger_json(events: pd.DataFrame) -> str:
    out = normalize_event_ledger(events).copy()
    for column in ["announcement_date", "effective_date"]:
        out[column] = out[column].dt.date.astype("string")
    return out.to_json(orient="records", indent=2)


def events_from_json(text: str) -> pd.DataFrame:
    return normalize_event_ledger(pd.read_json(StringIO(text)))
