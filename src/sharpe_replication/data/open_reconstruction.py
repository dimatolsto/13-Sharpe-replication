from __future__ import annotations

from typing import Any

import pandas as pd

from .certification import certify_for_experiment, certify_report
from .identity import detect_identity_ambiguities
from .join_audit import audit_membership_price_join
from .membership import members_on
from .normalize import normalize_daily_panel, normalize_membership, normalize_security_master
from .sp500_events import (
    event_completeness_report,
    gap_register_from_events,
    normalize_event_ledger,
)
from .validation import survivorship_diagnostics, validate_dataset


def membership_spot_checks(membership: pd.DataFrame, dates: list[str]) -> list[dict[str, Any]]:
    members = normalize_membership(membership)
    rows = []
    for date in dates:
        active = sorted(members_on(members, date))
        rows.append(
            {
                "date": date,
                "reconstructed_constituent_count": len(active),
                "sample_security_ids": active[:10],
            }
        )
    return rows


def open_reconstruction_report(
    *,
    events: pd.DataFrame,
    membership: pd.DataFrame | None = None,
    panel: pd.DataFrame | None = None,
    security_master: pd.DataFrame | None = None,
    corporate_actions: pd.DataFrame | None = None,
    raw_close_semantics: str = "unknown",
    total_return_source: str = "unknown",
) -> dict[str, Any]:
    ledger = normalize_event_ledger(events)
    gaps = gap_register_from_events(ledger)
    payload: dict[str, Any] = {
        "event_ledger": event_completeness_report(ledger, gaps),
        "gap_register_rows": len(gaps),
        "membership": None,
        "identity": None,
        "join_audit": None,
        "certification_by_experiment": {},
    }
    if membership is not None:
        members = normalize_membership(membership)
        payload["membership"] = survivorship_diagnostics(members, panel, security_master)
    if security_master is not None:
        payload["identity"] = detect_identity_ambiguities(normalize_security_master(security_master))
    if panel is not None and membership is not None:
        payload["join_audit"] = audit_membership_price_join(panel, membership, security_master)
        validation = validate_dataset(
            normalize_daily_panel(panel),
            normalize_membership(membership),
            normalize_security_master(security_master) if security_master is not None else None,
            corporate_actions,
        )
        cert = certify_report(
            validation,
            raw_close_semantics=raw_close_semantics,
            total_return_source=total_return_source,
            membership_present=True,
            security_master_present=security_master is not None,
        )
        for exp_id in ["P0", "P1", "P2", "P3", "P4", "P5"]:
            payload["certification_by_experiment"][exp_id] = certify_for_experiment(exp_id, cert).to_dict()
    return payload
