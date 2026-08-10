from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from .normalize import normalize_membership
from .schema import Severity, ValidationReport


def members_on(membership: pd.DataFrame, as_of: date | str | pd.Timestamp) -> set[str]:
    """Return members active on `as_of` using inclusive membership_end semantics."""
    m = normalize_membership(membership)
    ts = pd.Timestamp(as_of).normalize()
    active = (m["membership_start"] <= ts) & (m["membership_end"].isna() | (m["membership_end"] >= ts))
    return set(m.loc[active, "security_id"].astype(str))


def materialize_membership_eligibility(
    dates: Iterable[date | str | pd.Timestamp],
    membership: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for raw_date in dates:
        as_of = pd.Timestamp(raw_date).normalize()
        for security_id in sorted(members_on(membership, as_of)):
            rows.append({"date": as_of, "security_id": security_id, "eligible": True})
    return pd.DataFrame(rows, columns=["date", "security_id", "eligible"])


def validate_membership(
    membership: pd.DataFrame | None,
    known_security_ids: set[str] | None = None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    if membership is None:
        report.add(
            Severity.WARNING,
            "pit_membership_missing",
            "Point-in-time membership dataset is absent",
            dataset="membership",
        )
        return report

    try:
        m = normalize_membership(membership)
    except (KeyError, TypeError, ValueError) as exc:
        report.add(
            Severity.ERROR,
            "membership_schema_invalid",
            str(exc),
            dataset="membership",
        )
        return report

    duplicate = m.duplicated(["security_id", "membership_start", "membership_end"])
    if duplicate.any():
        report.add(
            Severity.ERROR,
            "duplicate_membership_spell",
            "Duplicate identical membership spells",
            dataset="membership",
            row_count=int(duplicate.sum()),
        )

    bad_order = m["membership_end"].notna() & (m["membership_end"] < m["membership_start"])
    if bad_order.any():
        report.add(
            Severity.ERROR,
            "membership_end_before_start",
            "membership_end precedes membership_start",
            dataset="membership",
            row_count=int(bad_order.sum()),
        )

    if known_security_ids is not None:
        unknown = sorted(set(m["security_id"]) - known_security_ids)
        if unknown:
            report.add(
                Severity.ERROR,
                "unknown_membership_security_id",
                "Membership references securities absent from the daily panel/security master",
                dataset="membership",
                row_count=len(unknown),
                details={"security_ids": unknown[:20]},
            )

    overlap_count = 0
    for security_id, block in m.sort_values(["security_id", "membership_start"]).groupby("security_id"):
        previous_end: pd.Timestamp | None = None
        for row in block.itertuples(index=False):
            end = row.membership_end if pd.notna(row.membership_end) else pd.Timestamp.max.normalize()
            if previous_end is not None and row.membership_start <= previous_end:
                overlap_count += 1
            previous_end = max(previous_end, end) if previous_end is not None else end
        open_ended = int(block["membership_end"].isna().sum())
        if open_ended > 1:
            report.add(
                Severity.ERROR,
                "multiple_open_membership_spells",
                "Security has multiple open-ended membership spells",
                dataset="membership",
                row_count=open_ended,
                details={"security_id": str(security_id)},
            )
    if overlap_count:
        report.add(
            Severity.ERROR,
            "overlapping_membership_spell",
            "Overlapping membership spells for the same security",
            dataset="membership",
            row_count=overlap_count,
        )

    report.sections["membership"] = {
        "rows": len(m),
        "unique_security_ids": int(m["security_id"].nunique()),
        "open_ended_spells": int(m["membership_end"].isna().sum()),
        "membership_end_inclusive": True,
    }
    return report
