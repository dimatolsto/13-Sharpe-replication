from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sharpe_replication.config import ExperimentConfig

from .schema import CertificationReport, Status, ValidationReport

DEFAULT_MIN_NOMINAL_SPLIT_CHECKS = 2

REQUIRED_BY_EXPERIMENT: dict[str, set[str]] = {
    "P0": {"schema_valid", "duplicate_checks_passed"},
    "P1": {"schema_valid", "duplicate_checks_passed"},
    "P2": {
        "schema_valid",
        "duplicate_checks_passed",
        "raw_close_nominal_verified",
        "total_return_corporate_action_safe",
    },
    "P3": {
        "schema_valid",
        "duplicate_checks_passed",
        "raw_close_nominal_verified",
        "total_return_corporate_action_safe",
        "stable_identifiers_sufficient",
        "pit_membership_available",
        "survivorship_bias_checks_passed",
        "membership_interval_checks_passed",
        "missing_terminal_return_checks_passed",
    },
    "P4": {
        "schema_valid",
        "duplicate_checks_passed",
        "raw_close_nominal_verified",
        "total_return_corporate_action_safe",
        "stable_identifiers_sufficient",
        "pit_membership_available",
        "survivorship_bias_checks_passed",
        "membership_interval_checks_passed",
        "missing_terminal_return_checks_passed",
    },
    "P5": {
        "schema_valid",
        "duplicate_checks_passed",
        "raw_close_nominal_verified",
        "total_return_corporate_action_safe",
        "stable_identifiers_sufficient",
        "pit_membership_available",
        "survivorship_bias_checks_passed",
        "membership_interval_checks_passed",
        "missing_terminal_return_checks_passed",
    },
}


def _status_from_bool(ok: bool) -> Status:
    return Status.PASS if ok else Status.FAIL


def _has_any(report: ValidationReport, *codes: str) -> bool:
    return report.has_code(*codes)


def _split_status(
    report: ValidationReport,
    raw_close_semantics: str,
    minimum_successful_split_checks: int,
) -> Status:
    if raw_close_semantics == "likely_adjusted":
        return Status.FAIL
    split_records = report.sections.get("split_diagnostics", [])
    classifications = {row.get("classification") for row in split_records}
    if "likely_back_adjusted" in classifications:
        return Status.FAIL
    successful = sum(1 for row in split_records if row.get("classification") == "consistent_with_nominal")
    if raw_close_semantics == "verified_nominal" and successful >= minimum_successful_split_checks:
        return Status.PASS
    return Status.UNVERIFIED


def _total_return_status(report: ValidationReport, total_return_source: str) -> Status:
    if _has_any(
        report,
        "inconsistent_total_return",
        "suspicious_split_return",
        "impossible_or_extreme_return",
        "non_finite_return",
    ):
        return Status.FAIL
    if total_return_source in {"provider", "reconstructed"}:
        return Status.PASS
    return Status.UNVERIFIED


def certify_report(
    validation_report: ValidationReport,
    *,
    raw_close_semantics: str = "unknown",
    total_return_source: str = "unknown",
    membership_present: bool = False,
    security_master_present: bool = False,
    provider_capabilities: Mapping[str, Any] | None = None,
    minimum_successful_split_checks: int = DEFAULT_MIN_NOMINAL_SPLIT_CHECKS,
) -> CertificationReport:
    dimensions = {
        "schema_valid": validation_report.status,
        "raw_close_nominal_verified": _split_status(
            validation_report,
            raw_close_semantics,
            minimum_successful_split_checks,
        ),
        "total_return_corporate_action_safe": _total_return_status(
            validation_report,
            total_return_source,
        ),
        "stable_identifiers_sufficient": Status.PASS
        if security_master_present
        and not _has_any(
            validation_report,
            "unknown_membership_security_id",
            "stable_identifier_unverified",
        )
        else Status.UNVERIFIED,
        "pit_membership_available": Status.PASS if membership_present else Status.FAIL,
        "survivorship_bias_checks_passed": Status.PASS
        if membership_present and not _has_any(validation_report, "static_universe_backfill")
        else (Status.FAIL if _has_any(validation_report, "static_universe_backfill") else Status.UNVERIFIED),
        "duplicate_checks_passed": _status_from_bool(
            not _has_any(
                validation_report,
                "duplicate_security_date",
                "duplicate_membership_spell",
            )
        ),
        "membership_interval_checks_passed": _status_from_bool(
            not _has_any(
                validation_report,
                "membership_end_before_start",
                "overlapping_membership_spell",
                "multiple_open_membership_spells",
                "unknown_effective_session_timing",
            )
        ),
        "missing_terminal_return_checks_passed": _status_from_bool(
            not _has_any(
                validation_report,
                "missing_terminal_return",
                "security_disappears_while_member",
            )
        ),
    }
    if provider_capabilities:
        dimensions["provider_capabilities_documented"] = Status.PASS
    else:
        dimensions["provider_capabilities_documented"] = Status.UNVERIFIED

    blocking = [
        f"{name}={status.value}"
        for name, status in dimensions.items()
        if status == Status.FAIL
    ]
    warnings = [
        f"{name}=UNVERIFIED"
        for name, status in dimensions.items()
        if status == Status.UNVERIFIED
    ]
    if blocking:
        status = Status.FAIL
    elif warnings:
        status = Status.UNVERIFIED
    else:
        status = Status.PASS
    return CertificationReport(
        dimensions=dimensions,
        status=status,
        blocking_issues=blocking,
        warnings=warnings,
    )


def certify_for_experiment(
    experiment: ExperimentConfig | str,
    certification_report: CertificationReport,
) -> CertificationReport:
    if isinstance(experiment, str):
        if experiment in REQUIRED_BY_EXPERIMENT:
            exp_id = experiment
        else:
            raise ValueError(f"Unknown experiment id for data certification: {experiment}")
    else:
        exp_id = experiment.id
    required = REQUIRED_BY_EXPERIMENT.get(exp_id)
    if required is None:
        raise ValueError(f"Unknown experiment id for data certification: {exp_id}")
    dimensions = dict(certification_report.dimensions)
    blocking = [
        f"{name}={dimensions.get(name, Status.UNVERIFIED).value}"
        for name in sorted(required)
        if dimensions.get(name, Status.UNVERIFIED) != Status.PASS
    ]
    status = Status.FAIL if blocking else Status.PASS
    return CertificationReport(
        dimensions=dimensions,
        status=status,
        blocking_issues=blocking,
        warnings=certification_report.warnings,
    )
