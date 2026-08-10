from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


DAILY_REQUIRED = ["date", "security_id", "ticker", "raw_close", "total_return"]
DAILY_OPTIONAL = [
    "open",
    "high",
    "low",
    "adjusted_close",
    "volume",
    "unadjusted_volume",
    "dividend_cash",
    "split_factor",
    "source_symbol",
    "source_security_id",
    "source",
    "source_timestamp",
]
DAILY_ORDER = DAILY_REQUIRED + DAILY_OPTIONAL

MEMBERSHIP_REQUIRED = ["security_id", "membership_start", "membership_end"]
MEMBERSHIP_ORDER = MEMBERSHIP_REQUIRED + ["source", "source_security_id"]

SECURITY_MASTER_REQUIRED = ["security_id", "ticker", "effective_start", "effective_end", "source"]
SECURITY_MASTER_OPTIONAL = ["source_security_id", "external_id"]
SECURITY_MASTER_ORDER = SECURITY_MASTER_REQUIRED + SECURITY_MASTER_OPTIONAL

CORPORATE_ACTION_REQUIRED = ["security_id", "date", "event_type"]
CORPORATE_ACTION_OPTIONAL = [
    "split_factor",
    "dividend_cash",
    "source",
    "source_event_id",
    "ticker",
]
CORPORATE_ACTION_ORDER = CORPORATE_ACTION_REQUIRED + CORPORATE_ACTION_OPTIONAL

CORPORATE_ACTION_TYPES = {
    "split",
    "cash_dividend",
    "special_dividend",
    "merger_acquisition",
    "delisting",
    "ticker_change",
}

RAW_CLOSE_SEMANTICS = {"verified_nominal", "likely_adjusted", "unknown"}
TOTAL_RETURN_SOURCES = {"provider", "reconstructed", "unknown"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    code: str
    message: str
    dataset: str | None = None
    row_count: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
        }
        if self.dataset is not None:
            out["dataset"] = self.dataset
        if self.row_count is not None:
            out["row_count"] = self.row_count
        if self.details:
            out["details"] = self.details
        return out


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)
    sections: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> Status:
        if any(issue.severity == Severity.ERROR for issue in self.issues):
            return Status.FAIL
        return Status.PASS

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        dataset: str | None = None,
        row_count: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                severity=severity,
                code=code,
                message=message,
                dataset=dataset,
                row_count=row_count,
                details=details or {},
            )
        )

    def has_code(self, *codes: str) -> bool:
        wanted = set(codes)
        return any(issue.code in wanted for issue in self.issues)

    def errors_with_code(self, *codes: str) -> list[ValidationIssue]:
        wanted = set(codes)
        return [
            issue
            for issue in self.issues
            if issue.severity == Severity.ERROR and issue.code in wanted
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "issues": [issue.to_dict() for issue in self.issues],
            "sections": self.sections,
        }


@dataclass
class CertificationReport:
    dimensions: dict[str, Status]
    status: Status
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "dimensions": {key: value.value for key, value in self.dimensions.items()},
            "blocking_issues": self.blocking_issues,
            "warnings": self.warnings,
        }
