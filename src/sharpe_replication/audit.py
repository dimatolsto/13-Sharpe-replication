from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ExperimentConfig


class AuditFailure(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_experiment(exp: ExperimentConfig, ledger: pd.DataFrame, daily: pd.DataFrame) -> dict:
    findings: list[dict[str, str]] = []

    if exp.certified and exp.return_lag_sessions < 2:
        findings.append({"status": "FAIL", "check": "timing", "detail": "Certified lag < 2"})
    else:
        findings.append({"status": "PASS", "check": "timing", "detail": f"lag={exp.return_lag_sessions}"})

    if "missing_weighted_return" in ledger.columns:
        missing = int(ledger["missing_weighted_return"].sum()) if len(ledger) else 0
    else:
        missing = int((ledger["total_return"].isna() & ledger["signal_weight"].ne(0.0)).sum()) if len(ledger) else 0
    findings.append(
        {
            "status": "FAIL" if missing else "PASS",
            "check": "missing_security_returns",
            "detail": str(missing),
        }
    )

    # Independent accounting reconstruction.
    reconstructed = (
        ledger.assign(c=lambda x: x["signal_weight"] * x["total_return"])
        .groupby("return_date")["c"]
        .sum(min_count=1)
        .sort_index()
    )
    reported = daily.set_index("return_date")["gross_return"].sort_index()
    common = reconstructed.index.intersection(reported.index)
    ok = len(common) == len(reported) and np.allclose(
        reconstructed.loc[common], reported.loc[common], rtol=1e-12, atol=1e-12, equal_nan=True
    )
    findings.append(
        {
            "status": "PASS" if ok else "FAIL",
            "check": "accounting_reconciliation",
            "detail": "security contributions equal daily gross returns" if ok else "mismatch",
        }
    )

    if len(daily) and (daily["gross_exposure"] > 1.0000001).any():
        findings.append(
            {
                "status": "WARNING",
                "check": "gross_exposure",
                "detail": "Unscaled target gross exposure exceeded 100%",
            }
        )
    else:
        findings.append({"status": "PASS", "check": "gross_exposure", "detail": "<=100%"})

    status = "FAIL" if any(x["status"] == "FAIL" for x in findings) else "PASS"
    return {"status": status, "experiment": exp.id, "findings": findings}


def write_audit(report: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")
