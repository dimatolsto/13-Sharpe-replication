from __future__ import annotations

import pandas as pd


def red_team_checks(
    panel: pd.DataFrame,
    membership: pd.DataFrame | None,
    return_lag_sessions: int,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    findings.append(
        {
            "status": "PASS" if return_lag_sessions >= 2 else "FAIL",
            "check": "same_close_leakage",
            "detail": f"return_lag_sessions={return_lag_sessions}",
        }
    )
    if membership is None:
        findings.append(
            {"status": "FAIL", "check": "pit_membership", "detail": "membership dataset missing"}
        )
    else:
        findings.append({"status": "PASS", "check": "pit_membership", "detail": "dataset supplied"})

    if "raw_close" not in panel:
        findings.append({"status": "FAIL", "check": "raw_close", "detail": "raw_close missing"})
    else:
        findings.append({"status": "PASS", "check": "raw_close", "detail": "raw_close field present"})

    if "total_return" not in panel:
        findings.append(
            {"status": "FAIL", "check": "corporate_action_returns", "detail": "total_return missing"}
        )
    else:
        findings.append(
            {"status": "PASS", "check": "corporate_action_returns", "detail": "total_return present"}
        )
    return findings
