from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .corporate_actions import validate_corporate_actions
from .membership import members_on, validate_membership
from .normalize import (
    normalize_corporate_actions,
    normalize_daily_panel,
    normalize_membership,
    normalize_security_master,
)
from .schema import Severity, ValidationReport
from .security_master import validate_security_master
from .trading_calendar import latest_completed_exchange_session


def _known_security_ids(*frames: pd.DataFrame | None) -> set[str]:
    ids: set[str] = set()
    for df in frames:
        if df is not None and "security_id" in df.columns:
            ids |= set(df["security_id"].astype(str))
    return ids


def validate_daily_panel(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None = None,
    report: ValidationReport | None = None,
    price_return_tolerance: float = 0.02,
) -> ValidationReport:
    report = report or ValidationReport()
    try:
        p = normalize_daily_panel(panel)
    except (KeyError, TypeError, ValueError) as exc:
        report.add(Severity.ERROR, "daily_panel_schema_invalid", str(exc), dataset="daily_panel")
        return report

    duplicate = p.duplicated(["date", "security_id"])
    if duplicate.any():
        report.add(
            Severity.ERROR,
            "duplicate_security_date",
            "Duplicate (date, security_id) rows in daily panel",
            dataset="daily_panel",
            row_count=int(duplicate.sum()),
        )

    missing_price = p["raw_close"].isna()
    if missing_price.any():
        report.add(
            Severity.ERROR,
            "missing_price",
            "raw_close is missing",
            dataset="daily_panel",
            row_count=int(missing_price.sum()),
        )

    invalid_price = p["raw_close"].notna() & (p["raw_close"] <= 0)
    if invalid_price.any():
        report.add(
            Severity.ERROR,
            "invalid_raw_close",
            "raw_close must be positive",
            dataset="daily_panel",
            row_count=int(invalid_price.sum()),
        )

    non_finite_return = p["total_return"].notna() & ~np.isfinite(p["total_return"].astype(float))
    if non_finite_return.any():
        report.add(
            Severity.ERROR,
            "non_finite_return",
            "total_return must be finite when present",
            dataset="daily_panel",
            row_count=int(non_finite_return.sum()),
        )

    impossible_return = p["total_return"].notna() & (p["total_return"] <= -1.0)
    if impossible_return.any():
        report.add(
            Severity.ERROR,
            "impossible_or_extreme_return",
            "total_return <= -100% is impossible for a tradable close-to-close return",
            dataset="daily_panel",
            row_count=int(impossible_return.sum()),
        )

    extreme_return = p["total_return"].notna() & (p["total_return"].abs() > 0.9) & ~impossible_return
    if extreme_return.any():
        report.add(
            Severity.WARNING,
            "impossible_or_extreme_return",
            "total_return magnitude exceeds 90%; verify corporate-action treatment",
            dataset="daily_panel",
            row_count=int(extreme_return.sum()),
        )

    non_monotonic = 0
    raw = panel.copy()
    if {"security_id", "date"}.issubset(raw.columns):
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        for _, block in raw.groupby("security_id", sort=False):
            if not block["date"].is_monotonic_increasing:
                non_monotonic += 1
    if non_monotonic:
        report.add(
            Severity.ERROR,
            "non_monotonic_date_history",
            "Input daily history is not chronological within at least one security_id",
            dataset="daily_panel",
            row_count=non_monotonic,
        )

    action_keys: set[tuple[str, pd.Timestamp]] = set()
    if corporate_actions is not None:
        try:
            actions = normalize_corporate_actions(corporate_actions)
            action_keys = set(zip(actions["security_id"], actions["date"], strict=False))
        except (KeyError, TypeError, ValueError):
            action_keys = set()

    sorted_panel = p.sort_values(["security_id", "date"]).copy()
    sorted_panel["previous_raw_close"] = sorted_panel.groupby("security_id")["raw_close"].shift()
    sorted_panel["expected_return"] = sorted_panel["raw_close"] / sorted_panel["previous_raw_close"] - 1.0
    no_action = ~pd.Series(
        list(zip(sorted_panel["security_id"], sorted_panel["date"], strict=False)),
        index=sorted_panel.index,
    ).isin(action_keys)
    comparable = (
        no_action
        & sorted_panel["previous_raw_close"].notna()
        & sorted_panel["total_return"].notna()
        & sorted_panel["expected_return"].replace([np.inf, -np.inf], np.nan).notna()
    )
    inconsistent = comparable & (
        (sorted_panel["total_return"] - sorted_panel["expected_return"]).abs() > price_return_tolerance
    )
    if inconsistent.any():
        report.add(
            Severity.ERROR,
            "inconsistent_total_return",
            "total_return disagrees with raw close-to-close return on no-action days",
            dataset="daily_panel",
            row_count=int(inconsistent.sum()),
        )

    report.sections["daily_panel"] = {
        "rows": len(p),
        "first_date": p["date"].min().date().isoformat() if len(p) else None,
        "last_date": p["date"].max().date().isoformat() if len(p) else None,
        "unique_security_ids": int(p["security_id"].nunique()),
        "unique_tickers": int(p["ticker"].nunique()),
    }
    return report


def audit_split_raw_close(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None,
    tolerance: float = 0.25,
) -> pd.DataFrame:
    columns = [
        "security_id",
        "ticker",
        "split_date",
        "declared_split_factor",
        "previous_raw_close",
        "split_day_raw_close",
        "observed_price_ratio",
        "expected_ratio",
        "tolerance",
        "classification",
    ]
    if corporate_actions is None:
        return pd.DataFrame(columns=columns)
    p = normalize_daily_panel(panel)
    actions = normalize_corporate_actions(corporate_actions)
    splits = actions[actions["event_type"].eq("split")].copy()
    rows: list[dict[str, Any]] = []
    for split in splits.itertuples(index=False):
        block = p[p["security_id"].eq(split.security_id)].sort_values("date")
        previous = block[block["date"] < split.date].tail(1)
        split_day = block[block["date"].eq(split.date)]
        if previous.empty or split_day.empty or pd.isna(split.split_factor):
            rows.append(
                {
                    "security_id": split.security_id,
                    "ticker": getattr(split, "ticker", None),
                    "split_date": split.date,
                    "declared_split_factor": split.split_factor,
                    "previous_raw_close": np.nan,
                    "split_day_raw_close": np.nan,
                    "observed_price_ratio": np.nan,
                    "expected_ratio": split.split_factor,
                    "tolerance": tolerance,
                    "classification": "insufficient_data",
                }
            )
            continue
        previous_raw_close = float(previous["raw_close"].iloc[0])
        split_day_raw_close = float(split_day["raw_close"].iloc[0])
        observed = previous_raw_close / split_day_raw_close if split_day_raw_close > 0 else np.nan
        expected = float(split.split_factor)
        if not np.isfinite(observed):
            classification = "insufficient_data"
        elif abs(observed - expected) / expected <= tolerance:
            classification = "consistent_with_nominal"
        elif abs(observed - 1.0) <= tolerance:
            classification = "likely_back_adjusted"
        else:
            classification = "ambiguous"
        rows.append(
            {
                "security_id": split.security_id,
                "ticker": split_day["ticker"].iloc[0] if not split_day.empty else getattr(split, "ticker", None),
                "split_date": split.date,
                "declared_split_factor": expected,
                "previous_raw_close": previous_raw_close,
                "split_day_raw_close": split_day_raw_close,
                "observed_price_ratio": observed,
                "expected_ratio": expected,
                "tolerance": tolerance,
                "classification": classification,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def reconstruct_nominal_close_from_splits(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None,
    *,
    close_column: str = "raw_close",
    output_column: str = "reconstructed_nominal_close",
) -> pd.DataFrame:
    """Create a separate candidate as-traded nominal close by reversing later splits.

    The input close is assumed to be expressed on the latest share basis.  For a split event with
    ratio `shares_after / shares_before`, all observations strictly before the split event date are
    multiplied by that ratio.  The split date itself is treated as the first post-split session,
    matching Yahoo's daily split event convention; unknown before-open/after-close timing remains a
    provenance limitation for certification.
    """

    p = normalize_daily_panel(panel).copy()
    if close_column not in p.columns:
        raise ValueError(f"panel missing close_column={close_column!r}")
    p[output_column] = pd.to_numeric(p[close_column], errors="raise")
    p["split_adjustment_multiplier"] = 1.0
    if corporate_actions is None or len(corporate_actions) == 0:
        return p
    actions = normalize_corporate_actions(corporate_actions)
    splits = actions[
        actions["event_type"].eq("split")
        & actions["split_factor"].notna()
        & (actions["split_factor"] > 0)
    ].sort_values(["security_id", "date"])
    for split in splits.itertuples(index=False):
        mask = p["security_id"].eq(split.security_id) & (p["date"] < split.date)
        p.loc[mask, output_column] = p.loc[mask, output_column] * float(split.split_factor)
        p.loc[mask, "split_adjustment_multiplier"] = (
            p.loc[mask, "split_adjustment_multiplier"] * float(split.split_factor)
        )
    return p


def audit_reconstructed_nominal_close(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None,
    *,
    close_column: str = "raw_close",
) -> pd.DataFrame:
    """Run the split audit against the separate reconstructed nominal-close candidate."""

    reconstructed = reconstruct_nominal_close_from_splits(
        panel,
        corporate_actions,
        close_column=close_column,
    )
    audit_panel = reconstructed.copy()
    audit_panel["raw_close"] = audit_panel["reconstructed_nominal_close"]
    return audit_split_raw_close(audit_panel, corporate_actions)


def split_diagnostic_context(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None,
    *,
    price_column: str = "raw_close",
) -> pd.DataFrame:
    """Return split-audit rows enriched with adjacent Close/Adj Close observations."""

    p = normalize_daily_panel(panel).copy()
    if price_column != "raw_close":
        if price_column not in p.columns:
            raise ValueError(f"panel missing price_column={price_column!r}")
        audit_panel = p.copy()
        audit_panel["raw_close"] = audit_panel[price_column]
    else:
        audit_panel = p
    audit = audit_split_raw_close(audit_panel, corporate_actions)
    rows: list[dict[str, Any]] = []
    for item in audit.itertuples(index=False):
        block = p[p["security_id"].eq(item.security_id)].sort_values("date").reset_index(drop=True)
        before = block[block["date"] < item.split_date].tail(2)
        day = block[block["date"].eq(item.split_date)].tail(1)
        after = block[block["date"] > item.split_date].head(1)

        def _value(frame: pd.DataFrame, column: str, offset: int) -> float | None:
            if frame.empty or column not in frame.columns or len(frame) <= abs(offset) - 1:
                return None
            value = frame[column].iloc[offset]
            return None if pd.isna(value) else float(value)

        factor = float(item.declared_split_factor)
        if item.classification == "likely_back_adjusted":
            reason = "observed Close ratio is near 1.0 rather than declared split factor"
        elif item.classification == "consistent_with_nominal" and factor <= 1.25:
            reason = "small split ratio falls within current broad tolerance; needs independent confirmation"
        elif item.classification == "consistent_with_nominal":
            reason = "observed Close ratio is near declared split factor"
        elif item.classification == "ambiguous":
            reason = "observed Close ratio is neither near 1.0 nor near declared split factor"
        else:
            reason = "adjacent price observations are insufficient"
        rows.append(
            {
                **item._asdict(),
                "split_year": pd.Timestamp(item.split_date).year,
                "split_direction": "reverse" if factor < 1.0 else "forward",
                "close_m2": _value(before, price_column, 0),
                "close_m1": _value(before, price_column, -1),
                "close_0": _value(day, price_column, -1),
                "close_p1": _value(after, price_column, 0),
                "adj_close_m2": _value(before, "adjusted_close", 0),
                "adj_close_m1": _value(before, "adjusted_close", -1),
                "adj_close_0": _value(day, "adjusted_close", -1),
                "adj_close_p1": _value(after, "adjusted_close", 0),
                "has_previous_observation": not before.empty,
                "has_split_day_observation": not day.empty,
                "has_next_observation": not after.empty,
                "classification_reason": reason,
            }
        )
    return pd.DataFrame(rows)


def stratified_split_diagnostic_sample(
    split_context: pd.DataFrame,
    *,
    per_class: int = 20,
) -> pd.DataFrame:
    if split_context.empty:
        return split_context.copy()
    return (
        split_context.sort_values(["classification", "split_date", "security_id"])
        .groupby("classification", group_keys=False)
        .head(per_class)
        .reset_index(drop=True)
    )


def split_diagnostic_summary(split_context: pd.DataFrame) -> dict[str, Any]:
    if split_context.empty:
        return {}
    context = split_context.copy()
    context["split_ratio"] = context["declared_split_factor"].round(6)
    context["adjacent_observation_state"] = np.select(
        [
            context["has_previous_observation"] & context["has_split_day_observation"] & context["has_next_observation"],
            context["has_previous_observation"] & context["has_split_day_observation"],
        ],
        ["previous_event_next", "previous_event_only"],
        default="insufficient",
    )

    def _counts(frame: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
        return frame.groupby(columns).size().reset_index(name="count").to_dict(orient="records")

    return {
        "by_classification": {str(k): int(v) for k, v in context["classification"].value_counts().items()},
        "by_split_ratio": _counts(context, ["classification", "split_ratio"]),
        "by_direction": _counts(context, ["classification", "split_direction"]),
        "by_year": _counts(context, ["split_year", "classification"]),
        "by_security": _counts(context, ["security_id", "classification"])[:200],
        "by_adjacent_observations": _counts(context, ["classification", "adjacent_observation_state"]),
    }


def validate_split_and_return_semantics(
    panel: pd.DataFrame,
    corporate_actions: pd.DataFrame | None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    split_audit = audit_split_raw_close(panel, corporate_actions)
    records = split_audit.copy()
    if "split_date" in records.columns:
        records["split_date"] = records["split_date"].astype("string")
    report.sections["split_diagnostics"] = records.to_dict(orient="records")
    if not split_audit.empty:
        likely = split_audit["classification"].eq("likely_back_adjusted")
        ambiguous = split_audit["classification"].eq("ambiguous")
        insufficient = split_audit["classification"].eq("insufficient_data")
        if likely.any():
            report.add(
                Severity.ERROR,
                "suspicious_raw_price_adjustment",
                "Split audit indicates raw_close may be retrospectively adjusted",
                dataset="daily_panel",
                row_count=int(likely.sum()),
            )
        if ambiguous.any():
            report.add(
                Severity.WARNING,
                "ambiguous_split_raw_close",
                "Split audit could not classify raw_close semantics",
                dataset="daily_panel",
                row_count=int(ambiguous.sum()),
            )
        if insufficient.any():
            report.add(
                Severity.WARNING,
                "insufficient_split_data",
                "Split audit lacks adjacent raw_close observations",
                dataset="daily_panel",
                row_count=int(insufficient.sum()),
            )

    if corporate_actions is None:
        return report

    p = normalize_daily_panel(panel)
    actions = normalize_corporate_actions(corporate_actions)
    split_keys = set(
        zip(
            actions.loc[actions["event_type"].eq("split"), "security_id"],
            actions.loc[actions["event_type"].eq("split"), "date"],
            strict=False,
        )
    )
    if split_keys:
        key_series = pd.Series(list(zip(p["security_id"], p["date"], strict=False)), index=p.index)
        split_rows = p[key_series.isin(split_keys)]
        suspicious = split_rows["total_return"].notna() & (split_rows["total_return"] < -0.30)
        if suspicious.any():
            report.add(
                Severity.ERROR,
                "suspicious_split_return",
                "total_return shows a catastrophic loss on a split date",
                dataset="daily_panel",
                row_count=int(suspicious.sum()),
            )
    return report


def survivorship_diagnostics(
    membership: pd.DataFrame | None,
    panel: pd.DataFrame | None = None,
    security_master: pd.DataFrame | None = None,
) -> dict[str, Any]:
    if membership is None or membership.empty:
        return {"available": False, "static_universe_like": None}
    m = normalize_membership(membership)
    if panel is not None and not panel.empty and "date" in panel.columns:
        dates = pd.to_datetime(panel["date"]).drop_duplicates().sort_values()
    else:
        starts = m["membership_start"]
        ends = m["membership_end"].dropna()
        dates = pd.Index(sorted(pd.concat([starts, ends]).drop_duplicates()))
    counts = [len(members_on(m, raw_date)) for raw_date in dates]
    entries_per_year = m["membership_start"].dt.year.value_counts().sort_index()
    exits_per_year = m.loc[m["membership_end"].notna(), "membership_end"].dt.year.value_counts().sort_index()
    reentries = int((m.groupby("security_id").size() > 1).sum())
    last_date = dates.max() if len(dates) else m["membership_start"].max()
    active_at_end = members_on(m, last_date)
    inactive_at_end = sorted(set(m["security_id"]) - active_at_end)

    ticker_changes = None
    if security_master is not None and not security_master.empty:
        master = normalize_security_master(security_master)
        ticker_changes = int((master.groupby("security_id")["ticker"].nunique() > 1).sum())

    all_same_start = m["membership_start"].nunique() == 1
    no_exits = m["membership_end"].isna().all() or m["membership_end"].nunique(dropna=True) <= 1
    no_reentries = reentries == 0
    no_later_entries = int(entries_per_year.iloc[1:].sum()) == 0 if len(entries_per_year) > 1 else True
    static_like = bool(all_same_start and no_exits and no_reentries and no_later_entries)
    return {
        "available": True,
        "first_date": m["membership_start"].min().date().isoformat(),
        "last_date": last_date.date().isoformat() if pd.notna(last_date) else None,
        "number_of_unique_security_ids": int(m["security_id"].nunique()),
        "members_per_date": {
            "min": int(min(counts)) if counts else 0,
            "max": int(max(counts)) if counts else 0,
            "mean": float(np.mean(counts)) if counts else 0.0,
        },
        "entries_per_year": {str(k): int(v) for k, v in entries_per_year.items()},
        "exits_per_year": {str(k): int(v) for k, v in exits_per_year.items()},
        "reentries": reentries,
        "securities_no_longer_active_at_dataset_end": len(inactive_at_end),
        "inactive_security_ids_sample": inactive_at_end[:20],
        "ticker_changes": ticker_changes,
        "delisted_or_inactive_securities_represented": bool(len(inactive_at_end)),
        "static_universe_like": static_like,
    }


def validate_survivorship(
    membership: pd.DataFrame | None,
    panel: pd.DataFrame | None,
    security_master: pd.DataFrame | None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    diagnostics = survivorship_diagnostics(membership, panel, security_master)
    report.sections["survivorship_diagnostics"] = diagnostics
    if diagnostics.get("static_universe_like"):
        report.add(
            Severity.ERROR,
            "static_universe_backfill",
            "Membership resembles one static constituent set copied through history",
            dataset="membership",
        )
    return report


def _terminal_event_keys(corporate_actions: pd.DataFrame | None) -> set[tuple[str, pd.Timestamp]]:
    if corporate_actions is None:
        return set()
    actions = normalize_corporate_actions(corporate_actions)
    terminal = actions["event_type"].isin({"delisting", "merger_acquisition"})
    return set(zip(actions.loc[terminal, "security_id"], actions.loc[terminal, "date"], strict=False))


def terminal_return_audit(
    panel: pd.DataFrame,
    membership: pd.DataFrame,
    corporate_actions: pd.DataFrame | None = None,
    *,
    as_of: str | pd.Timestamp | None = None,
    latest_available_provider_session: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Classify terminal coverage using explicit exchange and provider right edges."""

    p = normalize_daily_panel(panel)
    m = normalize_membership(membership)
    event_keys = _terminal_event_keys(corporate_actions)
    exchange_edge = latest_completed_exchange_session(as_of)
    provider_edge = (
        pd.Timestamp(latest_available_provider_session).normalize()
        if latest_available_provider_session is not None
        else p["date"].max()
    )
    audit_edge = min(exchange_edge, provider_edge) if pd.notna(provider_edge) else exchange_edge
    security_ids = sorted(set(m["security_id"].astype(str)) | set(p["security_id"].astype(str)))
    rows: list[dict[str, Any]] = []
    for security_id in security_ids:
        spells = m[m["security_id"].eq(security_id)]
        price_block = p[p["security_id"].eq(security_id)].sort_values("date")
        finite_exits = spells["membership_end"].dropna() if not spells.empty else pd.Series(dtype="datetime64[ns]")
        active_at_audit_edge = False
        active_after_last = False
        last_membership_session = pd.NaT
        if not spells.empty:
            active_at_audit_edge = (
                (spells["membership_start"] <= audit_edge)
                & (spells["membership_end"].isna() | (spells["membership_end"] >= audit_edge))
            ).any()
            last_membership_session = audit_edge if active_at_audit_edge else finite_exits.max()

        if price_block.empty:
            status = "unmapped_no_yahoo_price"
            blocking = bool(
                not spells.empty
                and (
                    (spells["membership_start"] <= audit_edge)
                    & (spells["membership_end"].isna() | (spells["membership_end"] >= spells["membership_start"]))
                ).any()
            )
            rows.append(
                {
                    "security_id": security_id,
                    "last_membership_date": None if pd.isna(last_membership_session) else last_membership_session.date().isoformat(),
                    "last_price_date": None,
                    "last_return_date": None,
                    "final_return_present": False,
                    "active_at_audit_edge": active_at_audit_edge,
                    "latest_completed_exchange_session": exchange_edge.date().isoformat(),
                    "latest_available_provider_session": provider_edge.date().isoformat() if pd.notna(provider_edge) else None,
                    "audit_end_session": audit_edge.date().isoformat(),
                    "terminal_status": status,
                    "blocking_risk": blocking,
                }
            )
            continue

        last_row = price_block.tail(1).iloc[0]
        last_date = pd.Timestamp(last_row["date"]).normalize()
        return_dates = price_block.loc[price_block["total_return"].notna(), "date"]
        last_return_date = return_dates.max() if len(return_dates) else pd.NaT
        final_return_present = pd.notna(last_row["total_return"])
        if not spells.empty:
            bounded_after_last = spells.copy()
            bounded_after_last["bounded_end"] = bounded_after_last["membership_end"].fillna(audit_edge)
            bounded_after_last["bounded_end"] = bounded_after_last["bounded_end"].where(
                bounded_after_last["bounded_end"] <= audit_edge,
                audit_edge,
            )
            active_after_last = (
                (bounded_after_last["membership_start"] <= audit_edge)
                & (bounded_after_last["bounded_end"] > last_date)
                & (last_date < audit_edge)
            ).any()

        if active_at_audit_edge and last_date >= audit_edge:
            status = "right_censored_active"
            blocking = False
        elif active_after_last:
            status = "disappears_while_member"
            blocking = True
        elif not finite_exits.empty and last_date >= finite_exits.max():
            status = "price_ends_after_membership"
            blocking = False
        elif not finite_exits.empty and (security_id, finite_exits.max()) in event_keys:
            status = "acquisition_or_delisting_explained"
            blocking = False
        elif pd.isna(last_membership_session):
            status = "price_without_membership"
            blocking = False
        else:
            status = "unresolved"
            blocking = bool(last_date < audit_edge)
        if not final_return_present:
            blocking = True
        rows.append(
            {
                "security_id": security_id,
                "last_membership_date": None if pd.isna(last_membership_session) else last_membership_session.date().isoformat(),
                "last_price_date": last_date.date().isoformat(),
                "last_return_date": None if pd.isna(last_return_date) else pd.Timestamp(last_return_date).date().isoformat(),
                "final_return_present": bool(final_return_present),
                "active_at_audit_edge": bool(active_at_audit_edge),
                "latest_completed_exchange_session": exchange_edge.date().isoformat(),
                "latest_available_provider_session": provider_edge.date().isoformat() if pd.notna(provider_edge) else None,
                "audit_end_session": audit_edge.date().isoformat(),
                "terminal_status": status,
                "blocking_risk": bool(blocking),
            }
        )
    return pd.DataFrame(rows)


def validate_missing_terminal_returns(
    panel: pd.DataFrame,
    membership: pd.DataFrame | None,
    corporate_actions: pd.DataFrame | None,
    report: ValidationReport | None = None,
    *,
    as_of: str | pd.Timestamp | None = None,
    latest_available_provider_session: str | pd.Timestamp | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    if membership is None:
        report.sections["delisting_diagnostics"] = {"available": False}
        return report
    p = normalize_daily_panel(panel)
    m = normalize_membership(membership)
    terminal = terminal_return_audit(
        p,
        m,
        corporate_actions,
        as_of=as_of,
        latest_available_provider_session=latest_available_provider_session,
    )
    disappearance_while_member = int(terminal["terminal_status"].eq("disappears_while_member").sum())
    missing_terminal_return = int(
        terminal["last_price_date"].notna().sum()
        - terminal.loc[terminal["last_price_date"].notna(), "final_return_present"].sum()
    )
    event_keys = _terminal_event_keys(corporate_actions)
    missing_terminal_event = 0
    for security_id, spells in m.groupby("security_id"):
        price_block = p[p["security_id"].eq(security_id)]
        if price_block.empty:
            continue
        last_date = price_block["date"].max()
        finite_exits = spells["membership_end"].dropna()
        if not finite_exits.empty:
            latest_exit = finite_exits.max()
            if last_date <= latest_exit and (security_id, latest_exit) not in event_keys:
                missing_terminal_event += 1
    if disappearance_while_member:
        report.add(
            Severity.ERROR,
            "security_disappears_while_member",
            "A security disappears from prices while still marked as an index member",
            dataset="daily_panel",
            row_count=disappearance_while_member,
        )
    if missing_terminal_return:
        report.add(
            Severity.ERROR,
            "missing_terminal_return",
            "Final observed row for a security has missing total_return",
            dataset="daily_panel",
            row_count=missing_terminal_return,
        )
    if missing_terminal_event:
        report.add(
            Severity.WARNING,
            "delisting_event_missing",
            "A finite membership exit lacks an explicit terminal delisting/acquisition event",
            dataset="corporate_actions",
            row_count=missing_terminal_event,
        )
    issue_rows = []
    for row in terminal[terminal["terminal_status"].eq("disappears_while_member")].to_dict(orient="records"):
        issue_rows.append(
            {
                "security_id": row["security_id"],
                "last_price_date": row["last_price_date"],
                "classification": "security_disappears_while_member",
            }
        )
    for row in terminal[(terminal["last_price_date"].notna()) & (~terminal["final_return_present"])].to_dict(orient="records"):
        issue_rows.append(
            {
                "security_id": row["security_id"],
                "last_price_date": row["last_price_date"],
                "classification": "missing_terminal_return",
            }
        )
    status_counts = terminal["terminal_status"].value_counts().to_dict() if len(terminal) else {}
    report.sections["delisting_diagnostics"] = {
        "issues": issue_rows,
        "security_disappearance_while_member": disappearance_while_member,
        "missing_terminal_return": missing_terminal_return,
        "finite_exits_without_terminal_event": missing_terminal_event,
        "right_censored_active": int(status_counts.get("right_censored_active", 0)),
        "terminal_status_counts": {str(k): int(v) for k, v in status_counts.items()},
    }
    return report


def validate_dataset(
    panel: pd.DataFrame,
    membership: pd.DataFrame | None = None,
    security_master: pd.DataFrame | None = None,
    corporate_actions: pd.DataFrame | None = None,
) -> ValidationReport:
    report = ValidationReport()
    panel_master_ids = _known_security_ids(panel, security_master)
    all_known_ids = _known_security_ids(panel, membership, security_master)
    validate_daily_panel(panel, corporate_actions, report)
    validate_corporate_actions(corporate_actions, all_known_ids or None, report)
    validate_membership(membership, panel_master_ids or None, report)
    validate_security_master(security_master, all_known_ids or None, report)
    validate_split_and_return_semantics(panel, corporate_actions, report)
    validate_survivorship(membership, panel, security_master, report)
    validate_missing_terminal_returns(panel, membership, corporate_actions, report)
    report.sections["snapshot_overview"] = {
        "daily_panel_rows": len(panel),
        "membership_rows": len(membership) if membership is not None else 0,
        "security_master_rows": len(security_master) if security_master is not None else 0,
        "corporate_action_rows": len(corporate_actions) if corporate_actions is not None else 0,
    }
    return report
