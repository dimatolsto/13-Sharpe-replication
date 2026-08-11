from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

import pandas as pd

from .normalize import normalize_membership
from .trading_calendar import TradingCalendar, trading_calendar_metadata


@dataclass
class ReconstructionResult:
    membership: pd.DataFrame
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "membership_rows": len(self.membership),
            "unique_security_ids": int(self.membership["security_id"].nunique()) if len(self.membership) else 0,
            "diagnostics": self.diagnostics,
        }


def _normalize_events(events: pd.DataFrame) -> pd.DataFrame:
    required = {"effective_date", "security_id", "action"}
    missing = required - set(events.columns)
    if missing:
        raise ValueError(f"Change events missing required columns: {sorted(missing)}")
    out = events.copy()
    out["effective_date"] = pd.to_datetime(out["effective_date"]).dt.normalize()
    out["security_id"] = out["security_id"].astype(str)
    out["action"] = out["action"].astype(str).str.lower().str.strip()
    aliases = {
        "add": "addition",
        "added": "addition",
        "addition": "addition",
        "delete": "removal",
        "deleted": "removal",
        "deletion": "removal",
        "remove": "removal",
        "removed": "removal",
        "removal": "removal",
    }
    out["action"] = out["action"].map(aliases).fillna(out["action"])
    invalid = sorted(set(out["action"]) - {"addition", "removal"})
    if invalid:
        raise ValueError(f"Unsupported membership actions: {invalid}")
    if "announcement_date" in out.columns:
        out["announcement_date"] = pd.to_datetime(out["announcement_date"]).dt.normalize()
    if "effective_session" in out.columns:
        out["effective_session"] = out["effective_session"].astype(str).str.upper()
    if "timing_uncertain" not in out.columns:
        out["timing_uncertain"] = out.get("effective_session", pd.Series("", index=out.index)).eq("UNKNOWN")
    else:
        out["timing_uncertain"] = out["timing_uncertain"].fillna(False).astype(bool)
    return out.sort_values(["effective_date", "action", "security_id"]).reset_index(drop=True)


def _apply_events(state: set[str], block: pd.DataFrame, diagnostics: dict[str, Any]) -> set[str]:
    updated = set(state)
    for row in block.itertuples(index=False):
        security_id = str(row.security_id)
        if row.action == "addition":
            if security_id in updated:
                diagnostics["duplicate_additions"].append(
                    {"date": row.effective_date.date().isoformat(), "security_id": security_id}
                )
            updated.add(security_id)
        elif row.action == "removal":
            if security_id not in updated:
                diagnostics["removals_without_active_member"].append(
                    {"date": row.effective_date.date().isoformat(), "security_id": security_id}
                )
            updated.discard(security_id)
    return updated


def _dedupe_diagnostic_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    deduped = []
    for record in records:
        key = tuple(sorted((str(k), str(v)) for k, v in record.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _state_on(
    events: pd.DataFrame,
    anchor_members: set[str],
    anchor_date: pd.Timestamp,
    as_of: pd.Timestamp,
    diagnostics: dict[str, Any],
) -> set[str]:
    state = set(anchor_members)
    if as_of >= anchor_date:
        future = events[(events["effective_date"] > anchor_date) & (events["effective_date"] <= as_of)]
        for _, block in future.groupby("effective_date", sort=True):
            state = _apply_events(state, block, diagnostics)
        return state

    reverse = events[(events["effective_date"] > as_of) & (events["effective_date"] <= anchor_date)].copy()
    reverse["action"] = reverse["action"].map({"addition": "removal", "removal": "addition"})
    for _, block in reverse.sort_values(["effective_date"], ascending=False).groupby("effective_date", sort=False):
        state = _apply_events(state, block, diagnostics)
    return state


def reconstruct_membership_from_change_events(
    events: pd.DataFrame,
    *,
    anchor_date: date | str | pd.Timestamp,
    anchor_members: set[str] | list[str],
    start_date: date | str | pd.Timestamp,
    end_date: date | str | pd.Timestamp,
    source: str = "change_events",
    calendar: TradingCalendar | None = None,
) -> ReconstructionResult:
    """Reconstruct inclusive membership spells from effective-date add/remove events.

    Events are interpreted as effective at the start of `effective_date`. If `calendar` is supplied,
    the prior interval ends on the previous trading session; otherwise the Phase 2A calendar-day
    fallback is preserved.
    """

    normalized_events = _normalize_events(events)
    anchor_ts = pd.Timestamp(anchor_date).normalize()
    start_ts = pd.Timestamp(start_date).normalize()
    end_ts = pd.Timestamp(end_date).normalize()
    if end_ts < start_ts:
        raise ValueError("end_date precedes start_date")

    diagnostics: dict[str, Any] = {
        "anchor_date": anchor_ts.date().isoformat(),
        "start_date": start_ts.date().isoformat(),
        "end_date": end_ts.date().isoformat(),
        "effective_date_interpretation": (
            "effective at start of date; removals end on previous XNYS session"
            if calendar is not None
            else "effective at start of date; removals end on previous calendar day"
        ),
        "trading_calendar": trading_calendar_metadata() if calendar is not None else None,
        "event_count": len(normalized_events),
        "timing_uncertain_event_count": int(normalized_events["timing_uncertain"].sum()),
        "duplicate_additions": [],
        "removals_without_active_member": [],
    }

    event_dates = set(normalized_events["effective_date"])
    boundaries = sorted({start_ts, end_ts + pd.Timedelta(days=1), *(d for d in event_dates if start_ts <= d <= end_ts)})
    intervals: list[tuple[pd.Timestamp, pd.Timestamp, set[str]]] = []
    anchor_set = {str(member) for member in anchor_members}
    for left, right in pairwise(boundaries):
        interval_end = calendar.previous_session(right) if calendar is not None else right - pd.Timedelta(days=1)
        if interval_end < start_ts or left > end_ts:
            continue
        state = _state_on(normalized_events, anchor_set, anchor_ts, left, diagnostics)
        intervals.append((left, min(interval_end, end_ts), state))

    spells: dict[str, list[list[pd.Timestamp]]] = {}
    for start, end, state in intervals:
        for security_id in sorted(state):
            blocks = spells.setdefault(security_id, [])
            next_contiguous_date = (
                calendar.next_session(blocks[-1][1], include_current=False)
                if blocks and calendar is not None
                else (blocks[-1][1] + pd.Timedelta(days=1) if blocks else None)
            )
            if blocks and next_contiguous_date is not None and start <= next_contiguous_date:
                blocks[-1][1] = end
            else:
                blocks.append([start, end])

    rows = [
        {
            "security_id": security_id,
            "membership_start": start,
            "membership_end": end,
            "source": source,
            "timing_uncertain": bool(normalized_events["timing_uncertain"].any()),
        }
        for security_id, blocks in sorted(spells.items())
        for start, end in blocks
    ]
    membership = normalize_membership(pd.DataFrame(rows)) if rows else pd.DataFrame(
        columns=["security_id", "membership_start", "membership_end", "source", "source_security_id"]
    )
    diagnostics["membership_rows"] = len(membership)
    diagnostics["unique_security_ids"] = int(membership["security_id"].nunique()) if len(membership) else 0
    diagnostics["duplicate_additions"] = _dedupe_diagnostic_records(diagnostics["duplicate_additions"])
    diagnostics["removals_without_active_member"] = _dedupe_diagnostic_records(
        diagnostics["removals_without_active_member"]
    )
    return ReconstructionResult(membership=membership, diagnostics=diagnostics)
