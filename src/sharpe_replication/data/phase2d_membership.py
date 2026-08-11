from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from .identity import stable_security_id_from_event
from .io import read_table, write_json
from .membership import members_on
from .normalize import normalize_membership
from .sp500_events import (
    gap_register_from_events,
    normalize_event_ledger,
    normalize_reported_symbol,
    provisional_security_id_from_symbol_name,
    write_event_ledger,
)
from .trading_calendar import TradingCalendar, trading_calendar_metadata

QUEUE_STATUSES = {
    "pending",
    "primary_verified",
    "archived_primary_verified",
    "secondary_verified",
    "unresolved",
    "needs_identity_review",
    "needs_timing_review",
}

SOURCE_TIER_PRIORITY = {
    "SP_PRIMARY": 0,
    "SP_ARCHIVE": 1,
    "CONTEMPORANEOUS_FALLBACK": 2,
    "IRRELEVANT": 3,
}

FALLBACK_SOURCE_HOSTS = {
    "reuters.com",
    "www.reuters.com",
    "prnewswire.com",
    "www.prnewswire.com",
    "marketscreener.com",
    "www.marketscreener.com",
    "marketwatch.com",
    "www.marketwatch.com",
    "businesswire.com",
    "www.businesswire.com",
    "advfn.com",
    "br.advfn.com",
    "nasdaq.com",
    "www.nasdaq.com",
    "nyse.com",
    "www.nyse.com",
    "sec.gov",
    "www.sec.gov",
}


def _now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean_text(value: Any) -> str:
    text = unescape("" if value is None else str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_file_stem(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    parsed = urlparse(value)
    host = re.sub(r"[^A-Za-z0-9._-]+", "_", parsed.netloc)[:64] or "source"
    return f"{host}_{digest}"


def _normal_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value).upper())


def _symbol_key(value: Any) -> str:
    return normalize_reported_symbol(value).replace(".", "").replace("-", "")


def _security_parts(security_id: str) -> tuple[str, str]:
    parts = str(security_id).split(":", 2)
    if len(parts) == 3 and parts[0] == "wiki":
        return parts[1], parts[2]
    return "", str(security_id)


def _event_security_id(row: Any) -> str:
    symbol = getattr(row, "source_symbol", "") or getattr(row, "ticker_as_reported", "")
    name = getattr(row, "source_security_name", "") or getattr(row, "company_name", "")
    return provisional_security_id_from_symbol_name(symbol, name)


def classify_reference_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    full = url.lower()
    if host == "web.archive.org" and (
        "standardandpoors.com" in full
        or "spglobal.com" in full
        or "spdji.com" in full
    ):
        return "SP_ARCHIVE"
    if host.endswith(("press.spglobal.com", "spglobal.com", "spdji.com")):
        return "SP_PRIMARY"
    if "standardandpoors.com" in host:
        return "SP_PRIMARY"
    if host in FALLBACK_SOURCE_HOSTS or any(host.endswith(f".{known}") for known in FALLBACK_SOURCE_HOSTS):
        return "CONTEMPORANEOUS_FALLBACK"
    return "IRRELEVANT"


@dataclass
class CitationReference:
    citation_id: str
    reference_number: str
    url: str
    source_tier: str
    reference_text: str


class _CitationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[CitationReference] = []
        self._current_id: str | None = None
        self._current_number = ""
        self._current_text: list[str] = []
        self._current_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value for key, value in attrs}
        if tag == "li" and str(attrs_dict.get("id", "")).startswith("cite_note"):
            self._current_id = str(attrs_dict.get("id", ""))
            self._current_number = str(attrs_dict.get("data-mw-footnote-number", ""))
            self._current_text = []
            self._current_urls = []
            return
        if self._current_id is None:
            return
        if tag == "a":
            href = attrs_dict.get("href")
            if href and href.startswith(("http://", "https://", "//")):
                url = f"https:{href}" if href.startswith("//") else href
                self._current_urls.append(unescape(url))

    def handle_data(self, data: str) -> None:
        if self._current_id is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "li" or self._current_id is None:
            return
        text = _clean_text(" ".join(self._current_text))[:1000]
        seen: set[str] = set()
        for url in self._current_urls:
            if url in seen:
                continue
            seen.add(url)
            self.rows.append(
                CitationReference(
                    citation_id=self._current_id,
                    reference_number=self._current_number,
                    url=url,
                    source_tier=classify_reference_url(url),
                    reference_text=text,
                )
            )
        self._current_id = None
        self._current_number = ""
        self._current_text = []
        self._current_urls = []


def extract_wikipedia_citation_references(html: str) -> pd.DataFrame:
    parser = _CitationParser()
    parser.feed(html)
    rows = [row.__dict__ for row in parser.rows]
    if not rows:
        return pd.DataFrame(columns=["citation_id", "reference_number", "url", "source_tier", "reference_text"])
    out = pd.DataFrame(rows).drop_duplicates(["citation_id", "url"]).reset_index(drop=True)
    return out.sort_values(["citation_id", "source_tier", "url"]).reset_index(drop=True)


def _citation_ids_from_event_refs(refs: Any) -> list[str]:
    ids: list[str] = []
    for raw in str(refs or "").split(";"):
        token = raw.strip()
        if not token.startswith("#cite_note-"):
            continue
        ids.append(token[1:])
    return ids


def _candidate_urls_for_event(row: Any, citations_by_id: dict[str, pd.DataFrame]) -> list[str]:
    urls: list[tuple[int, str]] = []
    for citation_id in _citation_ids_from_event_refs(getattr(row, "wikipedia_reference_urls", "")):
        refs = citations_by_id.get(citation_id)
        if refs is None:
            continue
        for ref in refs.itertuples(index=False):
            priority = SOURCE_TIER_PRIORITY.get(ref.source_tier, 99)
            urls.append((priority, ref.url))
    deduped: dict[str, int] = {}
    for priority, url in urls:
        deduped[url] = min(priority, deduped.get(url, priority))
    return [url for url, _ in sorted(deduped.items(), key=lambda item: (item[1], item[0]))]


def build_verification_queue(events: pd.DataFrame, citations: pd.DataFrame) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    citation_groups = {key: block for key, block in citations.groupby("citation_id")} if len(citations) else {}
    rows: list[dict[str, Any]] = []
    for event in ledger.itertuples(index=False):
        candidate_urls = _candidate_urls_for_event(event, citation_groups)
        source_url = ""
        archive_url = ""
        for url in candidate_urls:
            tier = classify_reference_url(url)
            if tier == "SP_ARCHIVE" and not archive_url:
                archive_url = url
            if not source_url and tier != "IRRELEVANT":
                source_url = url
        status = "pending" if source_url else "unresolved"
        notes = (
            "Candidate source URLs extracted from Wikipedia citation footnotes; not verified until fetched."
            if source_url
            else "No non-Wikipedia source URL extracted from seed citation footnotes."
        )
        rows.append(
            {
                "event_id": event.event_id,
                "effective_date_seed": event.effective_date.date().isoformat(),
                "added_ticker": event.ticker_as_reported if event.action == "ADD" else "",
                "removed_ticker": event.ticker_as_reported if event.action == "REMOVE" else "",
                "action": event.action,
                "company_name": event.company_name,
                "replacement_group_id": event.replacement_group_id,
                "status": status,
                "attempts": 0,
                "last_attempt_at": "",
                "source_url": source_url,
                "archive_url": archive_url,
                "candidate_urls": ";".join(candidate_urls),
                "notes": notes,
            }
        )
    return pd.DataFrame(rows).sort_values(["effective_date_seed", "replacement_group_id", "action"]).reset_index(drop=True)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(content: bytes | str) -> str:
    raw = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
    parser = _TextExtractor()
    parser.feed(raw)
    return _clean_text(" ".join(parser.parts))


def _date_from_url(url: str) -> pd.Timestamp | None:
    match = re.search(r"/(\d{4})-(\d{2})-(\d{2})[-_/]", url)
    if not match:
        return None
    try:
        return pd.Timestamp("-".join(match.groups())).normalize()
    except ValueError:
        return None


def _date_variants(value: pd.Timestamp) -> set[str]:
    date = pd.Timestamp(value).normalize()
    month = date.strftime("%B")
    abbr = date.strftime("%b")
    day = int(date.day)
    yy = date.strftime("%y")
    variants = {
        f"{month} {day}, {date.year}",
        f"{abbr} {day}, {date.year}",
        f"{day}-{abbr}-{yy}",
        f"{date.year}-{date.month:02d}-{date.day:02d}",
        f"{date.month}/{date.day}/{date.year}",
    }
    if abbr == "Sep":
        variants.add(f"Sept {day}, {date.year}")
    return {variant.upper() for variant in variants}


def _has_date(text_upper: str, value: pd.Timestamp) -> bool:
    return any(variant in text_upper for variant in _date_variants(value))


def _has_entity(text_upper: str, ticker: str, company: str) -> bool:
    symbol = normalize_reported_symbol(ticker)
    company_key = _normal_key(company)
    text_key = _normal_key(text_upper)
    if len(symbol) >= 3 and re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", text_upper):
        return True
    if company_key and company_key in text_key:
        return True
    tokens = [token for token in re.findall(r"[A-Z0-9]+", str(company).upper()) if len(token) > 3]
    if len(tokens) >= 2 and all(token in text_upper for token in tokens[:2]):
        return True
    return bool(symbol and len(symbol) >= 2 and symbol in text_upper and tokens and tokens[0] in text_upper)


def extract_effective_session_from_text(text: str) -> str:
    upper = text.upper()
    if re.search(r"PRIOR TO THE (OPEN|OPENING)", upper):
        return "BEFORE_OPEN"
    if "BEFORE THE OPEN" in upper:
        return "BEFORE_OPEN"
    if re.search(r"AFTER THE (CLOSE|CLOSING)", upper):
        return "AFTER_CLOSE"
    if "AFTER CLOSE OF TRADING" in upper:
        return "AFTER_CLOSE"
    if "MARKET CLOSE" in upper:
        return "AFTER_CLOSE"
    return "DATE_ONLY"


def source_text_supports_event(text: str, event: Any) -> bool:
    upper = text.upper()
    if "S&P 500" not in upper and "S & P 500" not in upper and "S AND P 500" not in upper:
        return False
    if not _has_date(upper, event.effective_date):
        return False
    return _has_entity(upper, event.ticker_as_reported, event.company_name)


def _fetch_url(url: str, cache_dir: Path, *, timeout: float, force: bool) -> tuple[dict[str, Any], bytes | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_file_stem(url)
    raw_path = cache_dir / f"{stem}.raw"
    meta_path = cache_dir / f"{stem}.json"
    if raw_path.exists() and meta_path.exists() and not force:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["cache_status"] = "cached"
        return meta, raw_path.read_bytes()

    meta: dict[str, Any] = {
        "original_url": url,
        "retrieved_at_utc": _now_utc(),
        "cache_path": str(raw_path),
        "metadata_path": str(meta_path),
        "source_tier": classify_reference_url(url),
        "cache_status": "fetched",
    }
    import httpx

    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "sharpe-replication/0.1 phase2d-membership-audit"},
        )
        meta["http_status"] = int(response.status_code)
        meta["final_url"] = str(response.url)
        meta["content_type"] = response.headers.get("content-type", "")
        response.raise_for_status()
        content = response.content
        raw_path.write_bytes(content)
        meta["sha256"] = hashlib.sha256(content).hexdigest()
        meta["bytes"] = len(content)
        meta["error"] = ""
    except (httpx.HTTPError, OSError, ValueError) as exc:  # pragma: no cover - live network error path
        content = None
        meta["error"] = str(exc)
        meta["sha256"] = ""
        meta["bytes"] = 0
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return meta, content


def verify_queue_from_sources(
    events: pd.DataFrame,
    queue: pd.DataFrame,
    *,
    cache_dir: Path,
    max_sources: int = 0,
    sleep_seconds: float = 0.25,
    timeout: float = 20.0,
    force: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ledger = normalize_event_ledger(events).copy()
    queue_out = queue.copy()
    source_urls: list[str] = []
    for raw in queue_out["candidate_urls"].fillna(""):
        for url in str(raw).split(";"):
            if not url:
                continue
            if classify_reference_url(url) != "IRRELEVANT" and url not in source_urls:
                source_urls.append(url)
    source_urls.sort(key=lambda url: (SOURCE_TIER_PRIORITY.get(classify_reference_url(url), 99), url))
    if max_sources > 0:
        source_urls = source_urls[:max_sources]

    artifacts: list[dict[str, Any]] = []
    discrepancies: list[dict[str, Any]] = []
    event_by_id = {row.event_id: row for row in ledger.itertuples(index=False)}
    for index, url in enumerate(source_urls):
        meta, content = _fetch_url(url, cache_dir, timeout=timeout, force=force)
        artifacts.append(meta)
        attempted = queue_out["candidate_urls"].fillna("").str.contains(re.escape(url), regex=True)
        queue_out.loc[attempted, "attempts"] = queue_out.loc[attempted, "attempts"].astype(int) + 1
        queue_out.loc[attempted, "last_attempt_at"] = meta["retrieved_at_utc"]
        if content is None or meta.get("error"):
            if index != len(source_urls) - 1 and sleep_seconds:
                time.sleep(sleep_seconds)
            continue
        text = html_to_text(content)
        tier = classify_reference_url(url)
        supported_ids: list[str] = []
        for row_index, queue_row in queue_out[attempted].iterrows():
            event = event_by_id.get(str(queue_row["event_id"]))
            if event is None or not source_text_supports_event(text, event):
                continue
            supported_ids.append(event.event_id)
            session = extract_effective_session_from_text(text)
            if tier == "SP_PRIMARY":
                queue_status = "primary_verified"
                verification_status = "VERIFIED_PRIMARY"
                source_tier = "SP_PRIMARY"
            elif tier == "SP_ARCHIVE":
                queue_status = "archived_primary_verified"
                verification_status = "VERIFIED_ARCHIVED_PRIMARY"
                source_tier = "SP_ARCHIVE"
            else:
                queue_status = "secondary_verified"
                verification_status = "VERIFIED_SECONDARY"
                source_tier = "CONTEMPORANEOUS_FALLBACK"
            if session == "DATE_ONLY":
                queue_status = "needs_timing_review"
            queue_out.at[row_index, "status"] = queue_status
            queue_out.at[row_index, "source_url"] = url if source_tier != "SP_ARCHIVE" else ""
            queue_out.at[row_index, "archive_url"] = url if source_tier == "SP_ARCHIVE" else queue_out.at[row_index, "archive_url"]
            queue_out.at[row_index, "notes"] = (
                f"Fetched source supports seed event; effective_session_extracted={session}."
            )
            idx = ledger.index[ledger["event_id"].eq(event.event_id)][0]
            if session != str(ledger.at[idx, "effective_session"]):
                discrepancies.append(
                    {
                        "event_id": event.event_id,
                        "field": "effective_session",
                        "seed_value": str(ledger.at[idx, "effective_session"]),
                        "evidence_value": session,
                        "source_tier": source_tier,
                        "source_url": url,
                    }
                )
            ledger.at[idx, "effective_session"] = session
            ledger.at[idx, "source_tier"] = source_tier
            ledger.at[idx, "source_url"] = url if source_tier != "SP_ARCHIVE" else ""
            ledger.at[idx, "archive_url"] = url if source_tier == "SP_ARCHIVE" else ""
            ledger.at[idx, "source_retrieved_at_utc"] = meta.get("retrieved_at_utc", "")
            ledger.at[idx, "source_sha256"] = meta.get("sha256", "")
            ledger.at[idx, "verification_status"] = verification_status
            announcement_date = _date_from_url(url)
            if announcement_date is not None:
                ledger.at[idx, "announcement_date"] = announcement_date
            ledger.at[idx, "evidence_notes"] = "Phase 2D source fetch matched event date and security in retrieved source."
        meta["linked_event_ids"] = ";".join(supported_ids)
        if index != len(source_urls) - 1 and sleep_seconds:
            time.sleep(sleep_seconds)
    return (
        normalize_event_ledger(ledger),
        queue_out.sort_values(["effective_date_seed", "replacement_group_id", "action"]).reset_index(drop=True),
        pd.DataFrame(artifacts),
        pd.DataFrame(discrepancies),
    )


def _group_stats(events: pd.DataFrame) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for group_id, block in events.groupby("replacement_group_id"):
        add_count = int(block["action"].eq("ADD").sum())
        remove_count = int(block["action"].eq("REMOVE").sum())
        verified = not block["verification_status"].eq("UNVERIFIED").any()
        stats[str(group_id)] = {
            "group_additions": add_count,
            "group_removals": remove_count,
            "seed_net_count_change": add_count - remove_count,
            "expected_net_count_change": 0 if verified and add_count == remove_count else pd.NA,
            "event_pair_imbalance": add_count != remove_count,
        }
    return stats


def _duplicate_event_keys(events: pd.DataFrame) -> set[tuple[str, str, str]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    for row in events.itertuples(index=False):
        counts[(row.effective_date.date().isoformat(), row.action, _event_security_id(row))] += 1
    return {key for key, count in counts.items() if count > 1}


def _identity_flags(security_id: str, state: set[str], opposite_group_ids: set[str]) -> list[str]:
    symbol, name = _security_parts(security_id)
    symbol_key = _symbol_key(symbol)
    name_key = _normal_key(name)
    flags: list[str] = []
    for active_id in state:
        active_symbol, active_name = _security_parts(active_id)
        active_symbol_key = _symbol_key(active_symbol)
        if symbol_key and symbol_key == active_symbol_key and active_id != security_id:
            if normalize_reported_symbol(symbol) != normalize_reported_symbol(active_symbol):
                flags.append("TICKER_PUNCTUATION_MISMATCH")
            else:
                flags.append("IDENTITY_ALIAS_MISMATCH")
        if name_key and name_key == _normal_key(active_name) and active_id != security_id:
            flags.append("IDENTITY_ALIAS_MISMATCH")
    if opposite_group_ids:
        flags.append("POSSIBLE_TICKER_RENAME")
    return sorted(set(flags))


def reconstruction_conservation_audit(
    events: pd.DataFrame,
    *,
    anchor_members: set[str] | list[str],
    start_date: str | pd.Timestamp,
    anchor_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    ledger = normalize_event_ledger(events)
    start_ts = pd.Timestamp(start_date).normalize()
    anchor_ts = pd.Timestamp(anchor_date).normalize()
    end_ts = pd.Timestamp(end_date).normalize()
    ledger = ledger[(ledger["effective_date"] >= start_ts) & (ledger["effective_date"] <= anchor_ts)].copy()
    group_stats = _group_stats(ledger)
    duplicate_keys = _duplicate_event_keys(ledger)
    ids_by_group_action: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in ledger.itertuples(index=False):
        ids_by_group_action[(row.replacement_group_id, row.action)].add(_event_security_id(row))

    records: list[dict[str, Any]] = []
    state = {str(member) for member in anchor_members}
    reverse_order = ledger.sort_values(
        ["effective_date", "replacement_group_id", "action", "event_id"],
        ascending=[False, False, False, False],
    )
    for row in reverse_order.itertuples(index=False):
        security_id = _event_security_id(row)
        existed = security_id in state
        count_before = len(state)
        flags: list[str] = []
        if row.action == "ADD":
            operation = "BACKWARD_REMOVE"
            expected_delta = -1
            if existed:
                state.remove(security_id)
            else:
                flags.append("BACKWARD_ADD_NOT_PRESENT")
        else:
            operation = "BACKWARD_ADD"
            expected_delta = 1
            if existed:
                flags.append("BACKWARD_REMOVE_ALREADY_PRESENT")
            else:
                state.add(security_id)
        if (row.effective_date.date().isoformat(), row.action, security_id) in duplicate_keys:
            flags.append("DUPLICATE_EVENT")
        if group_stats[row.replacement_group_id]["event_pair_imbalance"]:
            flags.append("EVENT_PAIR_IMBALANCE")
        opposite_action = "REMOVE" if row.action == "ADD" else "ADD"
        flags.extend(_identity_flags(security_id, state, ids_by_group_action[(row.replacement_group_id, opposite_action)]))
        count_after = len(state)
        actual_delta = count_after - count_before
        records.append(
            {
                "traversal": "BACKWARD",
                "effective_date": row.effective_date.date().isoformat(),
                "event_id": row.event_id,
                "replacement_group_id": row.replacement_group_id,
                "action": row.action,
                "security_id": security_id,
                "company_name": row.company_name,
                "ticker_as_reported": row.ticker_as_reported,
                "count_before": count_before,
                "security_existed_before": existed,
                "operation_applied": operation,
                "expected_count_delta": expected_delta,
                "actual_count_delta": actual_delta,
                "count_excess_delta": actual_delta - expected_delta,
                "count_after": count_after,
                "flags": ";".join(sorted(set(flags))),
            }
        )

    start_state = set(state)
    forward_order = ledger.sort_values(["effective_date", "replacement_group_id", "action", "event_id"])
    for row in forward_order.itertuples(index=False):
        security_id = _event_security_id(row)
        existed = security_id in state
        count_before = len(state)
        flags = []
        if row.action == "ADD":
            operation = "FORWARD_ADD"
            expected_delta = 1
            if existed:
                flags.append("FORWARD_ADD_ALREADY_PRESENT")
            else:
                state.add(security_id)
        else:
            operation = "FORWARD_REMOVE"
            expected_delta = -1
            if existed:
                state.remove(security_id)
            else:
                flags.append("FORWARD_REMOVE_NOT_PRESENT")
        if (row.effective_date.date().isoformat(), row.action, security_id) in duplicate_keys:
            flags.append("DUPLICATE_EVENT")
        if group_stats[row.replacement_group_id]["event_pair_imbalance"]:
            flags.append("EVENT_PAIR_IMBALANCE")
        opposite_action = "REMOVE" if row.action == "ADD" else "ADD"
        flags.extend(_identity_flags(security_id, state, ids_by_group_action[(row.replacement_group_id, opposite_action)]))
        count_after = len(state)
        actual_delta = count_after - count_before
        records.append(
            {
                "traversal": "FORWARD",
                "effective_date": row.effective_date.date().isoformat(),
                "event_id": row.event_id,
                "replacement_group_id": row.replacement_group_id,
                "action": row.action,
                "security_id": security_id,
                "company_name": row.company_name,
                "ticker_as_reported": row.ticker_as_reported,
                "count_before": count_before,
                "security_existed_before": existed,
                "operation_applied": operation,
                "expected_count_delta": expected_delta,
                "actual_count_delta": actual_delta,
                "count_excess_delta": actual_delta - expected_delta,
                "count_after": count_after,
                "flags": ";".join(sorted(set(flags))),
            }
        )
    audit = pd.DataFrame(records)
    backward = audit[audit["traversal"].eq("BACKWARD")]
    forward = audit[audit["traversal"].eq("FORWARD")]
    summary = {
        "anchor_count": len(set(anchor_members)),
        "start_state_count": len(start_state),
        "end_state_count_after_forward": len(state),
        "backward_conservation_failures": int(backward["count_excess_delta"].ne(0).sum()) if len(backward) else 0,
        "forward_conservation_failures": int(forward["count_excess_delta"].ne(0).sum()) if len(forward) else 0,
        "backward_net_excess_delta": int(backward["count_excess_delta"].sum()) if len(backward) else 0,
        "forward_net_excess_delta": int(forward["count_excess_delta"].sum()) if len(forward) else 0,
        "trading_calendar": trading_calendar_metadata(),
        "start_date": start_ts.date().isoformat(),
        "anchor_date": anchor_ts.date().isoformat(),
        "end_date": end_ts.date().isoformat(),
    }
    return audit, summary


def count_inflation_causes(conservation: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    backward = conservation[conservation["traversal"].eq("BACKWARD")].copy()
    failures = backward[backward["count_excess_delta"].ne(0)]
    if failures.empty:
        return pd.DataFrame(
            columns=[
                "cause",
                "events",
                "net_excess_delta",
                "first_date",
                "last_date",
                "example_event_ids",
                "example_security_ids",
            ]
        )
    for row in failures.itertuples(index=False):
        flags = set(str(row.flags).split(";")) if row.flags else set()
        if "TICKER_PUNCTUATION_MISMATCH" in flags:
            cause = "ticker punctuation mismatch"
        elif "IDENTITY_ALIAS_MISMATCH" in flags:
            cause = "identity mismatch"
        elif "DUPLICATE_EVENT" in flags:
            cause = "duplicate event"
        elif "BACKWARD_ADD_NOT_PRESENT" in flags:
            cause = "addition not present during backward inversion"
        elif "BACKWARD_REMOVE_ALREADY_PRESENT" in flags:
            cause = "removal already present during backward inversion"
        elif "EVENT_PAIR_IMBALANCE" in flags:
            cause = "event pair imbalance"
        else:
            cause = "unknown"
        rows.append(
            {
                "cause": cause,
                "effective_date": row.effective_date,
                "event_id": row.event_id,
                "security_id": row.security_id,
                "net_excess_delta": int(row.count_excess_delta),
            }
        )
    frame = pd.DataFrame(rows)
    out_rows = []
    for cause, block in frame.groupby("cause"):
        out_rows.append(
            {
                "cause": cause,
                "events": len(block),
                "net_excess_delta": int(block["net_excess_delta"].sum()),
                "first_date": block["effective_date"].min(),
                "last_date": block["effective_date"].max(),
                "example_event_ids": ";".join(block["event_id"].head(10)),
                "example_security_ids": ";".join(block["security_id"].head(10)),
            }
        )
    return pd.DataFrame(out_rows).sort_values(["net_excess_delta", "events"], ascending=[False, False])


def event_group_model(events: pd.DataFrame) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    rows = []
    for group_id, block in ledger.groupby("replacement_group_id"):
        add_count = int(block["action"].eq("ADD").sum())
        remove_count = int(block["action"].eq("REMOVE").sum())
        verified = not block["verification_status"].eq("UNVERIFIED").any()
        expected = 0 if verified and add_count == remove_count else pd.NA
        rows.append(
            {
                "replacement_group_id": group_id,
                "effective_date_seed_min": block["effective_date"].min().date().isoformat(),
                "effective_date_seed_max": block["effective_date"].max().date().isoformat(),
                "additions": add_count,
                "removals": remove_count,
                "seed_net_count_change": add_count - remove_count,
                "expected_net_count_change": expected,
                "expected_net_count_change_basis": (
                    "verified balanced S&P 500 add/delete group"
                    if pd.notna(expected)
                    else "not asserted: unverified source or unbalanced group"
                ),
                "verification_statuses": ";".join(sorted(set(block["verification_status"]))),
                "event_ids": ";".join(block["event_id"]),
            }
        )
    return pd.DataFrame(rows).sort_values(["effective_date_seed_min", "replacement_group_id"]).reset_index(drop=True)


def membership_count_timeseries(
    membership: pd.DataFrame,
    *,
    start_date: str | pd.Timestamp,
    end_date: str | pd.Timestamp,
    calendar: TradingCalendar | None = None,
) -> pd.DataFrame:
    members = normalize_membership(membership)
    cal = calendar or TradingCalendar.xnys()
    day = cal.next_session(start_date, include_current=True)
    end = pd.Timestamp(end_date).normalize()
    rows = []
    while day <= end:
        active = members_on(members, day)
        rows.append({"date": day.date().isoformat(), "member_count": len(active)})
        day = cal.next_session(day, include_current=False)
    return pd.DataFrame(rows)


def count_anomaly_intervals(counts: pd.DataFrame, *, low: int = 475, high: int = 525) -> pd.DataFrame:
    if counts.empty:
        return pd.DataFrame(columns=["start_date", "end_date", "min_count", "max_count", "sessions", "anomaly_type"])
    frame = counts.copy()
    frame["is_anomaly"] = (frame["member_count"] < low) | (frame["member_count"] > high)
    rows = []
    current: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        if record["is_anomaly"]:
            current.append(record)
        elif current:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    out = []
    for block in rows:
        values = [int(item["member_count"]) for item in block]
        out.append(
            {
                "start_date": block[0]["date"],
                "end_date": block[-1]["date"],
                "min_count": min(values),
                "max_count": max(values),
                "sessions": len(block),
                "anomaly_type": "high_count" if max(values) > high else "low_count",
                "notes": "Materially away from plausible S&P 500 size; diagnostic only, not a forced 500 target.",
            }
        )
    return pd.DataFrame(out)


def membership_delta_audit(events: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    members = normalize_membership(membership)
    cal = TradingCalendar.xnys()
    rows = []
    for group_id, block in ledger.groupby("replacement_group_id"):
        effective = block["effective_date"].min()
        previous = cal.previous_session(effective, include_current=False)
        current = cal.next_session(effective, include_current=True)
        previous_members = members_on(members, previous)
        current_members = members_on(members, current)
        entered = sorted(current_members - previous_members)
        exited = sorted(previous_members - current_members)
        expected_entered = sorted(_event_security_id(row) for row in block[block["action"].eq("ADD")].itertuples(index=False))
        expected_exited = sorted(_event_security_id(row) for row in block[block["action"].eq("REMOVE")].itertuples(index=False))
        rows.append(
            {
                "replacement_group_id": group_id,
                "effective_date_seed": effective.date().isoformat(),
                "previous_xnys_session": previous.date().isoformat(),
                "effective_xnys_session": current.date().isoformat(),
                "expected_entered_count": len(expected_entered),
                "observed_entered_count": len(entered),
                "expected_exited_count": len(expected_exited),
                "observed_exited_count": len(exited),
                "entered": ";".join(entered[:50]),
                "exited": ";".join(exited[:50]),
                "unexpected_entered": ";".join(sorted(set(entered) - set(expected_entered))[:50]),
                "unexpected_exited": ";".join(sorted(set(exited) - set(expected_exited))[:50]),
                "missing_expected_entered": ";".join(sorted(set(expected_entered) - set(entered))[:50]),
                "missing_expected_exited": ";".join(sorted(set(expected_exited) - set(exited))[:50]),
                "delta_mismatch": sorted(entered) != expected_entered or sorted(exited) != expected_exited,
            }
        )
    return pd.DataFrame(rows).sort_values(["effective_date_seed", "replacement_group_id"]).reset_index(drop=True)


def build_identity_lineages(events: pd.DataFrame, anchor: pd.DataFrame) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    if len(anchor):
        for row in anchor.itertuples(index=False):
            security_id = str(getattr(row, "security_id", stable_security_id_from_event(row.ticker, row.company_name)))
            key = (security_id, str(row.ticker), "anchor")
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "security_id": security_id,
                    "canonical_name": row.company_name,
                    "ticker": row.ticker,
                    "ticker_start": row.date_added if pd.notna(row.date_added) else "",
                    "ticker_end": "",
                    "source_security_name": row.company_name,
                    "source_ticker": row.ticker,
                    "CIK": getattr(row, "cik", ""),
                    "FIGI": "",
                    "other_external_id": "",
                    "lineage_event_type": "ORIGINAL",
                    "evidence_source": getattr(row, "source_url", ""),
                    "evidence_date": "",
                    "confidence/status": "CURRENT_ANCHOR_CIK_ENRICHED" if str(getattr(row, "cik", "")).strip() else "PROVISIONAL_CURRENT_ANCHOR",
                    "manual_override": False,
                    "notes": "Current anchor row; CIK is issuer-level enrichment, not tradable-security authority.",
                }
            )
    for row in ledger.itertuples(index=False):
        security_id = _event_security_id(row)
        key = (security_id, row.ticker_as_reported, row.event_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "security_id": security_id,
                "canonical_name": row.company_name,
                "ticker": row.ticker_as_reported,
                "ticker_start": row.effective_date.date().isoformat(),
                "ticker_end": "",
                "source_security_name": row.source_security_name or row.company_name,
                "source_ticker": row.source_symbol or row.ticker_as_reported,
                "CIK": "",
                "FIGI": "",
                "other_external_id": "",
                "lineage_event_type": "UNKNOWN",
                "evidence_source": row.source_url or row.archive_url or row.wikipedia_reference_urls,
                "evidence_date": row.effective_date.date().isoformat(),
                "confidence/status": (
                    "EVENT_VERIFIED_IDENTITY_UNREVIEWED"
                    if row.verification_status != "UNVERIFIED"
                    else "PROVISIONAL_WIKIPEDIA_IDENTITY"
                ),
                "manual_override": False,
                "notes": "Internal provisional tradable-security lineage pending date-aware identity evidence.",
            }
        )
    return pd.DataFrame(rows).sort_values(["security_id", "ticker_start", "ticker"]).reset_index(drop=True)


def identity_ambiguities(lineages: pd.DataFrame, conservation: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if len(lineages):
        ticker_counts = lineages.groupby("ticker")["security_id"].nunique()
        for ticker in ticker_counts[ticker_counts > 1].index:
            block = lineages[lineages["ticker"].eq(ticker)]
            rows.append(
                {
                    "ambiguity_type": "ticker_reuse_or_duplicate_identity",
                    "ticker": ticker,
                    "security_ids": ";".join(sorted(set(block["security_id"]))),
                    "event_ids": "",
                    "blocking_for_P3": True,
                    "notes": "Same ticker maps to multiple internal security_ids; requires date-aware review.",
                }
            )
    if len(conservation):
        for row in conservation[
            conservation["flags"].fillna("").str.contains("IDENTITY_ALIAS_MISMATCH|TICKER_PUNCTUATION_MISMATCH")
        ].itertuples(index=False):
            rows.append(
                {
                    "ambiguity_type": row.flags,
                    "ticker": row.ticker_as_reported,
                    "security_ids": row.security_id,
                    "event_ids": row.event_id,
                    "blocking_for_P3": True,
                    "notes": "Conservation audit found a possible alias/identity mismatch.",
                }
            )
    return pd.DataFrame(rows).drop_duplicates().reset_index(drop=True) if rows else pd.DataFrame(
        columns=["ambiguity_type", "ticker", "security_ids", "event_ids", "blocking_for_P3", "notes"]
    )


def reentry_review(membership: pd.DataFrame, lineages: pd.DataFrame) -> pd.DataFrame:
    members = normalize_membership(membership)
    status_by_id = {}
    if len(lineages):
        for security_id, block in lineages.groupby("security_id"):
            statuses = sorted(set(block["confidence/status"]))
            status_by_id[security_id] = ";".join(statuses)
    rows = []
    for security_id, block in members.groupby("security_id"):
        if len(block) <= 1:
            continue
        rows.append(
            {
                "security_id": security_id,
                "spell_count": len(block),
                "first_start": block["membership_start"].min().date().isoformat(),
                "last_end": (
                    block["membership_end"].max().date().isoformat()
                    if block["membership_end"].notna().any()
                    else ""
                ),
                "identity_assessment": "unresolved" if "PROVISIONAL" in status_by_id.get(security_id, "") else "needs_review",
                "same_security_reentry": "",
                "successor_security": "",
                "ticker_reuse": "",
                "blocking_for_P3": True,
                "notes": "Provisional reentry requires source-backed identity review before certification.",
            }
        )
    return pd.DataFrame(rows)


def year_by_year_completeness(
    events: pd.DataFrame,
    queue: pd.DataFrame,
    *,
    start_year: int,
    end_year: int,
    additional_events: pd.DataFrame | None = None,
) -> pd.DataFrame:
    ledger = normalize_event_ledger(events)
    queue_by_event = queue.set_index("event_id") if len(queue) else pd.DataFrame()
    additional_events = additional_events if additional_events is not None else pd.DataFrame()
    rows = []
    for year in range(start_year, end_year + 1):
        block = ledger[ledger["effective_date"].dt.year.eq(year)]
        event_ids = set(block["event_id"])
        q = queue_by_event.loc[list(event_ids)] if len(queue_by_event) and event_ids else pd.DataFrame()
        addl = additional_events[additional_events.get("effective_date", pd.Series(dtype=str)).astype(str).str.startswith(str(year))]
        addl_secondary = int(addl.get("verification_status", pd.Series(dtype=str)).astype(str).eq("VERIFIED_SECONDARY").sum())
        suspected_missing = bool(year == 2004 and len(block) == 0)
        unresolved = int(block["verification_status"].eq("UNVERIFIED").sum())
        timing_unknown = int(block["effective_session"].eq("UNKNOWN").sum())
        rows.append(
            {
                "year": year,
                "wikipedia_seed_events": int(block["wikipedia_row_id"].astype(str).str.strip().ne("").sum()),
                "additional_events_found": len(addl),
                "primary_verified": int(block["verification_status"].eq("VERIFIED_PRIMARY").sum()),
                "archived_primary_verified": int(block["verification_status"].eq("VERIFIED_ARCHIVED_PRIMARY").sum()),
                "secondary_verified": int(block["verification_status"].eq("VERIFIED_SECONDARY").sum()) + addl_secondary,
                "unresolved": unresolved,
                "timing_unknown": timing_unknown,
                "identity_unresolved": len(block) + len(addl),
                "P3_blocking_events": unresolved + timing_unknown + len(addl) + int(suspected_missing),
                "suspected_missing_events": suspected_missing,
                "queue_pending": int(q["status"].eq("pending").sum()) if len(q) else 0,
            }
        )
    return pd.DataFrame(rows)


def additional_2004_event_candidates() -> pd.DataFrame:
    url = "https://br.advfn.com/noticias/PRNUS/2004/artigo/7217409"
    rows = [
        {
            "event_id": "phase2d:2004-03-31:ADD:ET",
            "announcement_date": "2004-03-25",
            "effective_date": "2004-03-31",
            "effective_session": "AFTER_CLOSE",
            "action": "ADD",
            "company_name": "E*TRADE Financial",
            "ticker_as_reported": "ET",
            "replacement_group_id": "phase2d:2004-03-31:fleetboston-etrade",
            "source_tier": "CONTEMPORANEOUS_FALLBACK",
            "source_url": url,
            "archive_url": "",
            "verification_status": "VERIFIED_SECONDARY",
            "discovered_outside_wikipedia": True,
            "notes": "Standard & Poor's PRNewswire release mirrored by ADVFN; Wikipedia 2004 seed has zero rows.",
        },
        {
            "event_id": "phase2d:2004-03-31:REMOVE:FBF",
            "announcement_date": "2004-03-25",
            "effective_date": "2004-03-31",
            "effective_session": "AFTER_CLOSE",
            "action": "REMOVE",
            "company_name": "FleetBoston Financial",
            "ticker_as_reported": "FBF",
            "replacement_group_id": "phase2d:2004-03-31:fleetboston-etrade",
            "source_tier": "CONTEMPORANEOUS_FALLBACK",
            "source_url": url,
            "archive_url": "",
            "verification_status": "VERIFIED_SECONDARY",
            "discovered_outside_wikipedia": True,
            "notes": "Standard & Poor's PRNewswire release mirrored by ADVFN; Wikipedia 2004 seed has zero rows.",
        },
    ]
    return pd.DataFrame(rows)


def membership_gap_register(
    events: pd.DataFrame,
    queue: pd.DataFrame,
    conservation: pd.DataFrame,
    identities: pd.DataFrame,
    additional_events: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    event_gaps = gap_register_from_events(events)
    for row in event_gaps.itertuples(index=False):
        rows.append(
            {
                "event/date/year": f"{row.date}/{row.year}",
                "security": row.event_security,
                "gap_type": row.problem_type,
                "current_evidence": row.wikipedia_reference,
                "attempted_sources": "",
                "blocking_for_P3": bool(row.blocking_for_P3),
                "next_resolution_action": "Retrieve primary/archived-primary S&P source or contemporaneous fallback.",
                "notes": row.notes,
            }
        )
    for row in queue[queue["status"].isin(["pending", "unresolved", "needs_timing_review"])].itertuples(index=False):
        rows.append(
            {
                "event/date/year": f"{row.effective_date_seed}/{str(row.effective_date_seed)[:4]}",
                "security": f"{row.action} {row.added_ticker or row.removed_ticker} {row.company_name}".strip(),
                "gap_type": f"verification_queue_{row.status}",
                "current_evidence": row.source_url or row.archive_url,
                "attempted_sources": row.candidate_urls,
                "blocking_for_P3": True,
                "next_resolution_action": "Fetch/review queued candidate sources and extract effective-session wording.",
                "notes": row.notes,
            }
        )
    for row in conservation[conservation["traversal"].eq("BACKWARD") & conservation["count_excess_delta"].ne(0)].itertuples(index=False):
        rows.append(
            {
                "event/date/year": f"{row.effective_date}/{str(row.effective_date)[:4]}",
                "security": row.security_id,
                "gap_type": "reconstruction_conservation_failure",
                "current_evidence": row.event_id,
                "attempted_sources": row.flags,
                "blocking_for_P3": True,
                "next_resolution_action": "Resolve date-aware identity/event pairing before certification.",
                "notes": f"Expected count delta {row.expected_count_delta}, actual {row.actual_count_delta}.",
            }
        )
    unresolved_identities = identities[identities["confidence/status"].astype(str).str.contains("PROVISIONAL|UNKNOWN", regex=True)]
    for row in unresolved_identities.head(500).itertuples(index=False):
        rows.append(
            {
                "event/date/year": f"{row.evidence_date}/{str(row.evidence_date)[:4]}",
                "security": row.security_id,
                "gap_type": "identity_lineage_unresolved",
                "current_evidence": row.evidence_source,
                "attempted_sources": "",
                "blocking_for_P3": True,
                "next_resolution_action": "Find date-aware ticker/name/security evidence; do not merge by string similarity.",
                "notes": row.notes,
            }
        )
    for row in additional_events.itertuples(index=False):
        rows.append(
            {
                "event/date/year": f"{row.effective_date}/{str(row.effective_date)[:4]}",
                "security": f"{row.action} {row.ticker_as_reported} {row.company_name}",
                "gap_type": "additional_non_wikipedia_event_needs_primary_confirmation",
                "current_evidence": row.source_url,
                "attempted_sources": row.source_url,
                "blocking_for_P3": True,
                "next_resolution_action": "Locate primary or archived-primary S&P copy and reconcile into canonical ledger.",
                "notes": row.notes,
            }
        )
    out = pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)
    if len(out):
        out["sort_key"] = out["blocking_for_P3"].map({True: 0, False: 1})
        out = out.sort_values(["sort_key", "gap_type", "event/date/year"]).drop(columns=["sort_key"]).reset_index(drop=True)
    return out


def manual_review_queue(gaps: pd.DataFrame) -> pd.DataFrame:
    if gaps.empty:
        return pd.DataFrame(columns=["priority", *gaps.columns])
    out = gaps.copy()
    def priority(row: pd.Series) -> int:
        gap_type = str(row["gap_type"])
        if bool(row["blocking_for_P3"]):
            return 0
        if "conservation" in gap_type:
            return 1
        if "identity" in gap_type:
            return 2
        return 3
    out.insert(0, "priority", out.apply(priority, axis=1))
    return out.sort_values(["priority", "gap_type", "event/date/year"]).reset_index(drop=True)


def yahoo_plan_delta(yahoo_state: pd.DataFrame | None, identities: pd.DataFrame) -> pd.DataFrame:
    if yahoo_state is None or yahoo_state.empty:
        return pd.DataFrame(
            [
                {
                    "metric": "previous_required_historical_ids",
                    "value": 0,
                    "notes": "Yahoo state not supplied.",
                }
            ]
        )
    previous_ids = set(yahoo_state["security_id"].astype(str)) if "security_id" in yahoo_state.columns else set()
    corrected_ids = set(identities["security_id"].astype(str)) if len(identities) else set()
    removed_artifacts = sorted(previous_ids - corrected_ids)
    new_members = sorted(corrected_ids - previous_ids)
    failed = yahoo_state[yahoo_state.get("status", pd.Series(dtype=str)).astype(str).str.contains("failed", case=False, regex=True)]
    still_required = sorted(set(failed["security_id"].astype(str)) & corrected_ids) if len(failed) and "security_id" in failed.columns else []
    no_longer_required = sorted(set(failed["security_id"].astype(str)) - corrected_ids) if len(failed) and "security_id" in failed.columns else []
    return pd.DataFrame(
        [
            {"metric": "previous_required_historical_ids", "value": len(previous_ids), "notes": ""},
            {"metric": "corrected_required_historical_ids", "value": len(corrected_ids), "notes": "No full Yahoo redownload performed."},
            {"metric": "ids_removed_as_reconstruction_identity_artifacts", "value": len(removed_artifacts), "notes": ";".join(removed_artifacts[:25])},
            {"metric": "genuinely_new_historical_members", "value": len(new_members), "notes": ";".join(new_members[:25])},
            {"metric": "old_yahoo_failures_still_required", "value": len(still_required), "notes": ";".join(still_required[:25])},
            {"metric": "old_yahoo_failures_no_longer_relevant", "value": len(no_longer_required), "notes": ";".join(no_longer_required[:25])},
            {"metric": "aliases_changed", "value": 0, "notes": "Phase 2D did not rewrite Yahoo alias plan."},
        ]
    )


def anchor_comparison(anchor: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "anchor_date": "2026-08-11",
                "anchor_source": "Wikipedia current constituent table parsed in Phase 2C",
                "anchor_count": len(anchor),
                "cross_check_source": "S&P primary public constituent list not located in this automated pass",
                "cross_check_count": pd.NA,
                "discrepancies": "Anchor remains provisional; CIK enrichment is issuer-level only.",
                "anchor_remains_provisional": True,
            }
        ]
    )


def historical_snapshot_checks() -> pd.DataFrame:
    rows = []
    for year in [2004, 2005, 2008, 2010, 2015, 2020, 2024]:
        rows.append(
            {
                "year": year,
                "snapshot_source": "",
                "source_tier": "",
                "overlap": pd.NA,
                "missing": "",
                "extra": "",
                "status": "not_completed",
                "notes": "Phase 2D automated pass prioritized event/source verification and conservation audit; snapshot cross-check remains queued.",
            }
        )
    return pd.DataFrame(rows)


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    else:
        frame.to_csv(path, index=False)


def write_phase2d_reports(
    *,
    events_path: Path,
    anchor_path: Path,
    wikipedia_html_path: Path,
    membership_path: Path,
    out_dir: Path,
    source_cache_dir: Path,
    yahoo_state_path: Path | None = None,
    start_date: str = "2004-01-01",
    anchor_date: str = "2026-08-11",
    end_date: str = "2026-08-11",
    max_source_fetches: int = 0,
    source_sleep_seconds: float = 0.25,
    force_sources: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events = normalize_event_ledger(read_table(events_path))
    events = events[
        (events["effective_date"] >= pd.Timestamp(start_date).normalize())
        & (events["effective_date"] <= pd.Timestamp(end_date).normalize())
    ].reset_index(drop=True)
    anchor = read_table(anchor_path)
    membership = normalize_membership(read_table(membership_path))
    html = wikipedia_html_path.read_text(encoding="utf-8")
    citations = extract_wikipedia_citation_references(html)
    queue = build_verification_queue(events, citations)
    verified_events, queue, source_artifacts, discrepancies = verify_queue_from_sources(
        events,
        queue,
        cache_dir=source_cache_dir,
        max_sources=max_source_fetches,
        sleep_seconds=source_sleep_seconds,
        force=force_sources,
    )
    additional_events = additional_2004_event_candidates()
    additional_dates = pd.to_datetime(additional_events["effective_date"], errors="raise").dt.normalize()
    additional_events = additional_events[
        (additional_dates >= pd.Timestamp(start_date).normalize())
        & (additional_dates <= pd.Timestamp(end_date).normalize())
    ].reset_index(drop=True)

    conservation, conservation_summary = reconstruction_conservation_audit(
        verified_events,
        anchor_members=set(anchor["security_id"].astype(str)),
        start_date=start_date,
        anchor_date=anchor_date,
        end_date=end_date,
    )
    causes = count_inflation_causes(conservation)
    groups = event_group_model(verified_events)
    counts = membership_count_timeseries(membership, start_date=start_date, end_date=end_date)
    count_anomalies = count_anomaly_intervals(counts)
    delta = membership_delta_audit(verified_events, membership)
    lineages = build_identity_lineages(verified_events, anchor)
    ambiguities = identity_ambiguities(lineages, conservation)
    reentries = reentry_review(membership, lineages)
    by_year = year_by_year_completeness(
        verified_events,
        queue,
        start_year=pd.Timestamp(start_date).year,
        end_year=pd.Timestamp(end_date).year,
        additional_events=additional_events,
    )
    gaps = membership_gap_register(verified_events, queue, conservation, lineages, additional_events)
    manual = manual_review_queue(gaps)
    yahoo_state = read_table(yahoo_state_path) if yahoo_state_path is not None and yahoo_state_path.exists() else None
    yahoo_delta = yahoo_plan_delta(yahoo_state, lineages)
    anchor_audit = anchor_comparison(anchor)
    snapshots = historical_snapshot_checks()

    write_event_ledger(out_dir / "event_ledger_phase2d.csv", verified_events)
    _write_table(out_dir / "wikipedia_citation_references.csv", citations)
    _write_table(out_dir / "verification_queue.csv", queue)
    _write_table(out_dir / "source_artifacts.csv", source_artifacts)
    _write_table(out_dir / "event_discrepancies.csv", discrepancies)
    _write_table(out_dir / "additional_primary_events.csv", additional_events)
    _write_table(out_dir / "event_groups.csv", groups)
    _write_table(out_dir / "reconstruction_conservation.csv", conservation)
    _write_table(out_dir / "count_inflation_causes.csv", causes)
    _write_table(out_dir / "membership_counts.csv", counts)
    _write_table(out_dir / "membership_count_anomalies.csv", count_anomalies)
    _write_table(out_dir / "membership_delta_audit.csv", delta)
    _write_table(out_dir / "identity_lineages.csv", lineages)
    _write_table(out_dir / "identity_ambiguities.csv", ambiguities)
    _write_table(out_dir / "reentry_review.csv", reentries)
    _write_table(out_dir / "event_reconciliation_by_year.csv", by_year)
    _write_table(out_dir / "membership_gaps.csv", gaps)
    _write_table(out_dir / "manual_review_queue.csv", manual)
    _write_table(out_dir / "yahoo_plan_delta.csv", yahoo_delta)
    _write_table(out_dir / "anchor_comparison.csv", anchor_audit)
    _write_table(out_dir / "historical_snapshot_checks.csv", snapshots)

    primary = int(verified_events["verification_status"].eq("VERIFIED_PRIMARY").sum())
    archive = int(verified_events["verification_status"].eq("VERIFIED_ARCHIVED_PRIMARY").sum())
    secondary = int(verified_events["verification_status"].eq("VERIFIED_SECONDARY").sum()) + int(
        additional_events["verification_status"].eq("VERIFIED_SECONDARY").sum()
    )
    unresolved = int(verified_events["verification_status"].eq("UNVERIFIED").sum())
    timing_counts = verified_events["effective_session"].value_counts().to_dict()
    before_stats = {"min": 503, "median": 543, "max": 574}
    after_stats = {
        "min": int(counts["member_count"].min()) if len(counts) else None,
        "median": int(counts["member_count"].median()) if len(counts) else None,
        "max": int(counts["member_count"].max()) if len(counts) else None,
    }
    p3_blockers = int(gaps["blocking_for_P3"].sum()) if len(gaps) else 0
    membership_certification = "FAIL" if p3_blockers else "PASS"
    if membership_certification == "PASS" and (unresolved or int(timing_counts.get("UNKNOWN", 0))):
        membership_certification = "UNVERIFIED"
    summary = {
        "phase": "2D",
        "generated_at_utc": _now_utc(),
        "inputs": {
            "events": str(events_path),
            "anchor": str(anchor_path),
            "wikipedia_html": str(wikipedia_html_path),
            "membership": str(membership_path),
            "yahoo_state": str(yahoo_state_path) if yahoo_state_path else None,
        },
        "strict_exclusions": {
            "strategy_performance_run": False,
            "p0_p1_p2_p3_p4_p5_run": False,
            "sharpe_calculated": False,
            "full_yahoo_redownload": False,
        },
        "events": {
            "wikipedia_seed_events_2004_present": len(events),
            "primary_verified": primary,
            "archived_primary_verified": archive,
            "secondary_verified": secondary,
            "unresolved": unresolved,
            "additional_non_wikipedia_events_discovered": len(additional_events),
            "timing": {str(k): int(v) for k, v in timing_counts.items()},
            "source_fetches_attempted": len(source_artifacts),
            "source_fetch_errors": int(source_artifacts["error"].astype(str).ne("").sum()) if len(source_artifacts) and "error" in source_artifacts else 0,
        },
        "count_inflation": {
            "before": before_stats,
            "after": after_stats,
            "conservation": conservation_summary,
            "cause_rows": len(causes),
        },
        "membership": {
            "spells": len(membership),
            "unique_historical_security_ids": int(membership["security_id"].nunique()) if len(membership) else 0,
            "count_sessions": len(counts),
            "count_anomaly_intervals": len(count_anomalies),
        },
        "identity": {
            "lineage_rows": len(lineages),
            "unique_internal_security_ids": int(lineages["security_id"].nunique()) if len(lineages) else 0,
            "ambiguity_rows": len(ambiguities),
            "reentry_rows": len(reentries),
        },
        "gaps": {
            "total": len(gaps),
            "p3_blocking": p3_blockers,
            "manual_review_queue": len(manual),
        },
        "membership_certification": membership_certification,
        "p2_status_unchanged_from_phase2c": "FAIL",
        "overall_p3_status": "FAIL" if membership_certification != "PASS" else "FAIL_P2_BLOCKED",
    }
    write_json(out_dir / "phase2d_summary.json", summary)
    return summary
