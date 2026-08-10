from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

PANEL_REQUIRED = {"date", "security_id", "ticker", "raw_close", "total_return"}
MEMBERSHIP_REQUIRED = {"security_id", "membership_start", "membership_end"}


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def normalize_panel(panel: pd.DataFrame) -> pd.DataFrame:
    missing = PANEL_REQUIRED - set(panel.columns)
    if missing:
        raise ValueError(f"Panel missing required columns: {sorted(missing)}")
    out = panel.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out = out.sort_values(["security_id", "date"]).reset_index(drop=True)
    if out.duplicated(["date", "security_id"]).any():
        raise ValueError("Duplicate (date, security_id) rows")
    if (out["raw_close"] <= 0).any():
        raise ValueError("raw_close must be positive")
    return out


def normalize_membership(membership: pd.DataFrame) -> pd.DataFrame:
    missing = MEMBERSHIP_REQUIRED - set(membership.columns)
    if missing:
        raise ValueError(f"Membership missing required columns: {sorted(missing)}")
    out = membership.copy()
    out["membership_start"] = pd.to_datetime(out["membership_start"]).dt.normalize()
    out["membership_end"] = pd.to_datetime(out["membership_end"]).dt.normalize()
    bad = out["membership_end"].notna() & (out["membership_end"] < out["membership_start"])
    if bad.any():
        raise ValueError("membership_end precedes membership_start")
    return out


def apply_point_in_time_eligibility(
    panel: pd.DataFrame,
    membership: pd.DataFrame,
    eligible_column: str = "eligible",
) -> pd.DataFrame:
    """Add a point-in-time membership flag without dropping historical rows.

    Membership end is inclusive. Multiple spells are supported. The returned frame preserves all
    panel rows so per-security rolling features can use publicly available pre-membership history.
    """
    out = normalize_panel(panel)
    membership = normalize_membership(membership)
    keyed = out[["date", "security_id"]].reset_index(names="_row")
    merged = keyed.merge(membership, on="security_id", how="left", validate="many_to_many")
    active = (merged["date"] >= merged["membership_start"]) & (
        merged["membership_end"].isna() | (merged["date"] <= merged["membership_end"])
    )
    eligible_rows = merged.loc[active, "_row"].drop_duplicates()
    out[eligible_column] = False
    out.loc[eligible_rows, eligible_column] = True
    return out


def apply_point_in_time_membership(panel: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    """Filter panel to dates on which each security was actually an index member.

    Membership end is inclusive. Multiple spells are supported.
    """
    eligible = apply_point_in_time_eligibility(panel, membership)
    out = eligible.loc[eligible["eligible"], [c for c in eligible.columns if c != "eligible"]]
    return out.sort_values(["security_id", "date"]).reset_index(drop=True)
