from __future__ import annotations

from typing import Any

import pandas as pd

from .normalize import normalize_daily_panel, normalize_membership, normalize_security_master


def audit_membership_price_join(
    panel: pd.DataFrame,
    membership: pd.DataFrame,
    security_master: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Report coverage gaps between PIT membership and the normalized daily panel."""

    prices = normalize_daily_panel(panel)
    members = normalize_membership(membership)
    master = normalize_security_master(security_master) if security_master is not None else None

    membership_ids = set(members["security_id"].astype(str))
    price_ids = set(prices["security_id"].astype(str))
    master_ids = set(master["security_id"].astype(str)) if master is not None else set()
    price_dates = pd.Index(sorted(prices["date"].dropna().unique()))
    panel_end = prices["date"].max() if len(prices) else pd.NaT

    expected_rows = []
    for row in members.itertuples(index=False):
        start = row.membership_start
        end = row.membership_end if pd.notna(row.membership_end) else panel_end
        if pd.isna(end):
            continue
        active_dates = price_dates[(price_dates >= start) & (price_dates <= end)]
        for active_date in active_dates:
            expected_rows.append({"date": active_date, "security_id": row.security_id})
    expected = pd.DataFrame(expected_rows, columns=["date", "security_id"])
    if len(expected):
        observed = prices[["date", "security_id", "raw_close", "total_return"]]
        joined = expected.merge(observed, on=["date", "security_id"], how="left", indicator=True)
        missing_price_rows = joined[joined["_merge"].eq("left_only")]
        missing_raw_close = joined[joined["_merge"].eq("both") & joined["raw_close"].isna()]
        missing_total_return = joined[joined["_merge"].eq("both") & joined["total_return"].isna()]
    else:
        missing_price_rows = pd.DataFrame(columns=["date", "security_id"])
        missing_raw_close = pd.DataFrame(columns=["date", "security_id"])
        missing_total_return = pd.DataFrame(columns=["date", "security_id"])

    partial = []
    for security_id, block in members.groupby("security_id"):
        price_block = prices[prices["security_id"].eq(security_id)]
        if price_block.empty:
            continue
        first_price = price_block["date"].min()
        last_price = price_block["date"].max()
        for row in block.itertuples(index=False):
            end = row.membership_end if pd.notna(row.membership_end) else panel_end
            if first_price > row.membership_start or (pd.notna(end) and last_price < end):
                partial.append(
                    {
                        "security_id": str(security_id),
                        "membership_start": row.membership_start.date().isoformat(),
                        "membership_end": None if pd.isna(row.membership_end) else row.membership_end.date().isoformat(),
                        "first_price_date": first_price.date().isoformat(),
                        "last_price_date": last_price.date().isoformat(),
                    }
                )

    def _examples(frame: pd.DataFrame) -> list[dict[str, str]]:
        examples = []
        for row in frame.head(50).itertuples(index=False):
            examples.append(
                {
                    "date": pd.Timestamp(row.date).date().isoformat(),
                    "security_id": str(row.security_id),
                }
            )
        return examples

    return {
        "membership_security_ids": len(membership_ids),
        "price_security_ids": len(price_ids),
        "security_master_ids": len(master_ids) if master is not None else None,
        "unmapped_membership_security_ids": sorted(membership_ids - price_ids)[:100],
        "membership_ids_without_security_master": sorted(membership_ids - master_ids)[:100] if master is not None else [],
        "partially_mapped_membership_spells": partial[:100],
        "member_dates_lacking_price_rows": len(missing_price_rows),
        "member_dates_lacking_raw_close": len(missing_raw_close),
        "member_dates_lacking_total_return": len(missing_total_return),
        "missing_price_examples": _examples(missing_price_rows),
        "missing_raw_close_examples": _examples(missing_raw_close),
        "missing_total_return_examples": _examples(missing_total_return),
    }
