from __future__ import annotations

import pandas as pd

from .normalize import normalize_corporate_actions
from .schema import CORPORATE_ACTION_TYPES, Severity, ValidationReport


def validate_corporate_actions(
    corporate_actions: pd.DataFrame | None,
    known_security_ids: set[str] | None = None,
    report: ValidationReport | None = None,
) -> ValidationReport:
    report = report or ValidationReport()
    if corporate_actions is None:
        report.add(
            Severity.WARNING,
            "corporate_actions_missing",
            "Corporate-action/event table is absent",
            dataset="corporate_actions",
        )
        return report
    try:
        actions = normalize_corporate_actions(corporate_actions)
    except (KeyError, TypeError, ValueError) as exc:
        report.add(
            Severity.ERROR,
            "corporate_actions_schema_invalid",
            str(exc),
            dataset="corporate_actions",
        )
        return report

    unknown_events = sorted(set(actions["event_type"].dropna()) - CORPORATE_ACTION_TYPES)
    if unknown_events:
        report.add(
            Severity.ERROR,
            "unknown_corporate_action_type",
            "Unsupported corporate action event types",
            dataset="corporate_actions",
            details={"event_types": unknown_events},
        )
    if known_security_ids is not None:
        unknown = sorted(set(actions["security_id"]) - known_security_ids)
        if unknown:
            report.add(
                Severity.ERROR,
                "unknown_corporate_action_security_id",
                "Corporate actions reference unknown security_id values",
                dataset="corporate_actions",
                row_count=len(unknown),
                details={"security_ids": unknown[:20]},
            )

    split_bad = (
        actions["event_type"].eq("split")
        & ("split_factor" not in actions.columns or actions["split_factor"].isna() | (actions["split_factor"] <= 0))
    )
    if isinstance(split_bad, bool):
        split_bad_count = int(actions["event_type"].eq("split").sum()) if split_bad else 0
    else:
        split_bad_count = int(split_bad.sum())
    if split_bad_count:
        report.add(
            Severity.ERROR,
            "invalid_split_factor",
            "Split events require positive split_factor values",
            dataset="corporate_actions",
            row_count=split_bad_count,
        )

    if "dividend_cash" in actions.columns:
        dividend_bad = (
            actions["event_type"].isin({"cash_dividend", "special_dividend"})
            & actions["dividend_cash"].notna()
            & (actions["dividend_cash"] < 0)
        )
        if dividend_bad.any():
            report.add(
                Severity.ERROR,
                "invalid_dividend_cash",
                "Dividend cash amounts cannot be negative",
                dataset="corporate_actions",
                row_count=int(dividend_bad.sum()),
            )

    report.sections["corporate_actions"] = {
        "rows": len(actions),
        "event_counts": {
            str(key): int(value) for key, value in actions["event_type"].value_counts().sort_index().items()
        },
    }
    return report
