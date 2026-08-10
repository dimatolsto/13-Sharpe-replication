from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from sharpe_replication.cli import app
from sharpe_replication.data.certification import certify_for_experiment, certify_report
from sharpe_replication.data.io import read_table
from sharpe_replication.data.membership import members_on, validate_membership
from sharpe_replication.data.normalize import normalize_daily_panel, normalize_from_mapping
from sharpe_replication.data.schema import Severity, Status
from sharpe_replication.data.security_master import security_id_for_ticker
from sharpe_replication.data.snapshot import hash_snapshot, write_snapshot
from sharpe_replication.data.validation import (
    audit_split_raw_close,
    validate_dataset,
    validate_split_and_return_semantics,
    validate_survivorship,
)
from sharpe_replication.providers.wisesheets import WiseSheetsCapabilityGate


def _source_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(
        [
            "2020-01-02",
            "2020-01-03",
            "2020-01-06",
            "2020-01-07",
            "2020-01-08",
            "2020-01-09",
        ]
    )
    price_paths = {
        "SPLT": [100.0, 104.0, 108.0, 27.0, 28.0, 29.0],
        "SPL2": [60.0, 62.0, 31.0, 32.0, 33.0, 34.0],
        "REN": [50.0, 51.0, 52.0, 53.0, 54.0, 55.0],
        "REUSE1": [20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        "REUSE2": [30.0, 31.0],
    }
    tickers = {
        "SPLT": ["SPLT"] * 6,
        "SPL2": ["SPL2"] * 6,
        "REN": ["OLD", "OLD", "OLD", "NEW", "NEW", "NEW"],
        "REUSE1": ["DUP"] * 6,
        "REUSE2": ["DUP", "DUP"],
    }
    rows = []
    for security_id, prices in price_paths.items():
        row_dates = dates if security_id != "REUSE2" else dates[-2:]
        prior = None
        for raw_date, price, ticker in zip(row_dates, prices, tickers[security_id], strict=True):
            split_day = (
                security_id == "SPLT" and raw_date == pd.Timestamp("2020-01-07")
            ) or (security_id == "SPL2" and raw_date == pd.Timestamp("2020-01-06"))
            if prior is None or split_day:
                total_return = 0.0
            else:
                total_return = price / prior - 1.0
            rows.append(
                {
                    "src_date": raw_date,
                    "src_id": security_id,
                    "src_ticker": ticker,
                    "src_close": price,
                    "src_tr": total_return,
                }
            )
            prior = price
    daily = pd.DataFrame(rows)
    membership = pd.DataFrame(
        {
            "sid": ["SPLT", "SPL2", "REN", "REUSE1", "REUSE2"],
            "start": ["2020-01-02", "2020-01-02", "2020-01-02", "2020-01-02", "2020-01-08"],
            "end": [None, None, None, "2020-01-06", None],
        }
    )
    security_master = pd.DataFrame(
        {
            "sid": ["SPLT", "SPL2", "REN", "REN", "REUSE1", "REUSE2"],
            "tic": ["SPLT", "SPL2", "OLD", "NEW", "DUP", "DUP"],
            "start": [
                "2020-01-02",
                "2020-01-02",
                "2020-01-02",
                "2020-01-07",
                "2020-01-02",
                "2020-01-08",
            ],
            "end": [None, None, "2020-01-06", None, "2020-01-06", None],
            "src": ["fixture"] * 6,
            "external": ["FIGI1", "FIGI1B", "FIGI2", "FIGI2", "FIGI3", "FIGI4"],
        }
    )
    corporate_actions = pd.DataFrame(
        {
            "sid": ["SPL2", "SPLT", "REN", "REUSE1"],
            "date": ["2020-01-06", "2020-01-07", "2020-01-07", "2020-01-06"],
            "event": ["split", "split", "ticker_change", "delisting"],
            "factor": [2.0, 4.0, None, None],
            "cash": [None, None, None, None],
            "src": ["fixture"] * 4,
            "event_id": ["split-2", "split-1", "ticker-1", "delist-1"],
        }
    )
    return daily, membership, security_master, corporate_actions


def _normalized_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    daily, membership, security_master, corporate_actions = _source_frames()
    daily_map = {
        "src_date": "date",
        "src_id": "security_id",
        "src_ticker": "ticker",
        "src_close": "raw_close",
        "src_tr": "total_return",
    }
    membership_map = {"sid": "security_id", "start": "membership_start", "end": "membership_end"}
    master_map = {
        "sid": "security_id",
        "tic": "ticker",
        "start": "effective_start",
        "end": "effective_end",
        "src": "source",
        "external": "external_id",
    }
    actions_map = {
        "sid": "security_id",
        "date": "date",
        "event": "event_type",
        "factor": "split_factor",
        "cash": "dividend_cash",
        "src": "source",
        "event_id": "source_event_id",
    }
    return (
        normalize_from_mapping(daily, "daily_panel", daily_map, "fixture"),
        normalize_from_mapping(membership, "membership", membership_map, "fixture"),
        normalize_from_mapping(security_master, "security_master", master_map, "fixture"),
        normalize_from_mapping(corporate_actions, "corporate_actions", actions_map, "fixture"),
    )


def _issue_codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_raw_nominal_close_survives_split_and_adjusted_history_is_detected():
    panel, _, _, actions = _normalized_frames()
    audit = audit_split_raw_close(panel, actions)
    assert set(audit["security_id"]) == {"SPLT", "SPL2"}
    assert set(audit["classification"]) == {"consistent_with_nominal"}

    adjusted = panel.copy()
    adjusted.loc[
        adjusted["security_id"].eq("SPLT") & (adjusted["date"] < pd.Timestamp("2020-01-07")),
        "raw_close",
    ] /= 4.0
    adjusted_audit = audit_split_raw_close(adjusted, actions)
    assert "likely_back_adjusted" in set(adjusted_audit["classification"])

    report = validate_split_and_return_semantics(adjusted, actions)
    assert "suspicious_raw_price_adjustment" in _issue_codes(report)


def test_total_return_split_validation_flags_catastrophic_split_loss_only():
    panel, _, _, actions = _normalized_frames()
    clean = validate_split_and_return_semantics(panel, actions)
    assert "suspicious_split_return" not in _issue_codes(clean)

    bad = panel.copy()
    bad.loc[
        bad["security_id"].eq("SPLT") & bad["date"].eq(pd.Timestamp("2020-01-07")),
        "total_return",
    ] = -0.75
    report = validate_split_and_return_semantics(bad, actions)
    assert "suspicious_split_return" in _issue_codes(report)


def test_duplicate_invalid_price_and_terminal_return_are_reported():
    panel, membership, master, actions = _normalized_frames()
    duplicate_report = validate_dataset(pd.concat([panel, panel.iloc[[0]]]), membership, master, actions)
    assert "duplicate_security_date" in _issue_codes(duplicate_report)

    bad_price = panel.copy()
    bad_price.loc[0, "raw_close"] = 0.0
    invalid_report = validate_dataset(bad_price, membership, master, actions)
    assert "invalid_raw_close" in _issue_codes(invalid_report)

    missing_terminal = panel.copy()
    missing_terminal.loc[missing_terminal["security_id"].eq("REN") & missing_terminal["date"].eq(panel["date"].max()), "total_return"] = pd.NA
    terminal_report = validate_dataset(missing_terminal, membership, master, actions)
    assert "missing_terminal_return" in _issue_codes(terminal_report)


def test_pit_membership_entry_exit_reentry_open_end_and_future_member_exclusion():
    membership = pd.DataFrame(
        {
            "security_id": ["A", "A", "B"],
            "membership_start": ["2020-01-01", "2020-01-06", "2020-01-08"],
            "membership_end": ["2020-01-03", None, None],
        }
    )
    assert members_on(membership, "2020-01-02") == {"A"}
    assert members_on(membership, "2020-01-03") == {"A"}
    assert members_on(membership, "2020-01-04") == set()
    assert members_on(membership, "2020-01-06") == {"A"}
    assert "B" not in members_on(membership, "2020-01-07")
    assert members_on(membership, "2020-01-08") == {"A", "B"}


def test_overlapping_membership_intervals_fail_and_static_universe_is_flagged():
    overlapping = pd.DataFrame(
        {
            "security_id": ["A", "A"],
            "membership_start": ["2020-01-01", "2020-01-03"],
            "membership_end": ["2020-01-05", "2020-01-06"],
        }
    )
    report = validate_membership(overlapping, known_security_ids={"A"})
    assert "overlapping_membership_spell" in _issue_codes(report)

    static = pd.DataFrame(
        {
            "security_id": ["A", "B", "C"],
            "membership_start": ["2000-01-01"] * 3,
            "membership_end": [None, None, None],
        }
    )
    static_report = validate_survivorship(static, panel=None, security_master=None)
    assert "static_universe_backfill" in _issue_codes(static_report)


def test_security_master_ticker_change_and_ticker_reuse_are_date_scoped():
    _, _, master, _ = _normalized_frames()
    assert security_id_for_ticker(master, "OLD", "2020-01-03") == "REN"
    assert security_id_for_ticker(master, "NEW", "2020-01-08") == "REN"
    assert security_id_for_ticker(master, "DUP", "2020-01-03") == "REUSE1"
    assert security_id_for_ticker(master, "DUP", "2020-01-08") == "REUSE2"


def test_manifest_hashes_reproduce_and_snapshot_overwrite_is_protected(tmp_path: Path):
    panel, membership, master, actions = _normalized_frames()
    snapshot = tmp_path / "snapshot-a"
    manifest = write_snapshot(
        snapshot,
        daily_panel=panel,
        membership=membership,
        security_master=master,
        corporate_actions=actions,
        raw_close_semantics="verified_nominal",
        total_return_source="provider",
    )
    assert manifest["files"]["daily_panel"]["sha256"] == hash_snapshot(snapshot)["daily_panel.parquet"]
    assert hash_snapshot(snapshot) == hash_snapshot(snapshot)
    with pytest.raises(FileExistsError):
        write_snapshot(snapshot, daily_panel=panel)


def test_unknown_provider_capability_and_raw_close_semantics_remain_unverified():
    report = WiseSheetsCapabilityGate().report()
    assert report["network_adapter"] == "not_configured"
    assert report["capabilities"]["historical_raw_close"] == "unverified"

    panel, membership, master, actions = _normalized_frames()
    validation = validate_dataset(panel, membership, master, actions)
    cert = certify_report(
        validation,
        raw_close_semantics="unknown",
        total_return_source="provider",
        membership_present=True,
        security_master_present=True,
    )
    assert cert.dimensions["raw_close_nominal_verified"] == Status.UNVERIFIED
    assert certify_for_experiment("P3", cert).status == Status.FAIL


def test_verified_nominal_raw_close_requires_multiple_split_checks():
    panel, membership, master, actions = _normalized_frames()
    single_split_panel = panel[panel["security_id"].ne("SPL2")]
    single_split_membership = membership[membership["security_id"].ne("SPL2")]
    single_split_master = master[master["security_id"].ne("SPL2")]
    single_split_actions = actions[actions["security_id"].ne("SPL2")]
    validation = validate_dataset(
        single_split_panel,
        single_split_membership,
        single_split_master,
        single_split_actions,
    )
    assert "inconsistent_total_return" not in _issue_codes(validation)
    cert = certify_report(
        validation,
        raw_close_semantics="verified_nominal",
        total_return_source="provider",
        membership_present=True,
        security_master_present=True,
    )
    assert cert.dimensions["raw_close_nominal_verified"] == Status.UNVERIFIED
    assert certify_for_experiment("P3", cert).status == Status.FAIL


def test_raw_close_cannot_fall_back_to_adjusted_close_implicitly():
    table = pd.DataFrame(
        {
            "date": ["2020-01-01"],
            "security_id": ["A"],
            "ticker": ["A"],
            "adjusted_close": [1.0],
            "total_return": [0.0],
        }
    )
    with pytest.raises(ValueError, match="raw_close"):
        normalize_daily_panel(table)


def test_p3_p4_p5_certification_fails_without_pit_membership():
    panel, _, master, actions = _normalized_frames()
    validation = validate_dataset(panel, None, master, actions)
    cert = certify_report(
        validation,
        raw_close_semantics="verified_nominal",
        total_return_source="provider",
        membership_present=False,
        security_master_present=True,
    )
    for exp_id in ["P3", "P4", "P5"]:
        assert certify_for_experiment(exp_id, cert).status == Status.FAIL


def test_local_csv_and_parquet_normalization_and_data_cli(tmp_path: Path):
    daily, membership, master, actions = _source_frames()
    daily_path = tmp_path / "daily.csv"
    membership_path = tmp_path / "membership.csv"
    master_path = tmp_path / "master.parquet"
    actions_path = tmp_path / "actions.csv"
    daily.to_csv(daily_path, index=False)
    membership.to_csv(membership_path, index=False)
    master.to_parquet(master_path, index=False)
    actions.to_csv(actions_path, index=False)

    daily_map = json.dumps(
        {
            "src_date": "date",
            "src_id": "security_id",
            "src_ticker": "ticker",
            "src_close": "raw_close",
            "src_tr": "total_return",
        }
    )
    membership_map = json.dumps({"sid": "security_id", "start": "membership_start", "end": "membership_end"})
    master_map = json.dumps(
        {
            "sid": "security_id",
            "tic": "ticker",
            "start": "effective_start",
            "end": "effective_end",
            "src": "source",
            "external": "external_id",
        }
    )
    actions_map = json.dumps(
        {
            "sid": "security_id",
            "date": "date",
            "event": "event_type",
            "factor": "split_factor",
            "cash": "dividend_cash",
            "src": "source",
            "event_id": "source_event_id",
        }
    )
    normalized = normalize_from_mapping(read_table(daily_path), "daily_panel", json.loads(daily_map), "fixture")
    assert list(normalized.columns[:5]) == ["date", "security_id", "ticker", "raw_close", "total_return"]
    assert normalized.sort_values(["date", "security_id"]).reset_index(drop=True).equals(normalized)

    runner = CliRunner()
    snapshot = tmp_path / "snapshot-cli"
    result = runner.invoke(
        app,
        [
            "data",
            "normalize",
            str(snapshot),
            "--daily-source",
            str(daily_path),
            "--daily-map",
            daily_map,
            "--membership-source",
            str(membership_path),
            "--membership-map",
            membership_map,
            "--security-master-source",
            str(master_path),
            "--security-master-map",
            master_map,
            "--corporate-actions-source",
            str(actions_path),
            "--corporate-actions-map",
            actions_map,
            "--raw-close-semantics",
            "verified_nominal",
            "--total-return-source",
            "provider",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (snapshot / "manifest.json").exists()
    assert (snapshot / "daily_panel.parquet").exists()

    inspect = runner.invoke(app, ["data", "inspect", str(snapshot)])
    assert inspect.exit_code == 0, inspect.output
    assert json.loads(inspect.output)["snapshot_id"] == "snapshot-cli"

    hashes = runner.invoke(app, ["data", "hash", str(snapshot)])
    assert hashes.exit_code == 0, hashes.output
    assert "daily_panel.parquet" in json.loads(hashes.output)["hashes"]

    certification = runner.invoke(app, ["data", "certify", str(snapshot), "--experiment-id", "P3"])
    assert certification.exit_code == 0, certification.output
    assert json.loads(certification.output)["status"] == "PASS"


def test_report_issue_severities_are_serializable():
    report = validate_dataset(*_normalized_frames())
    payload = report.to_dict()
    assert payload["status"] == "PASS"
    assert all(issue["severity"] in {Severity.ERROR.value, Severity.WARNING.value, Severity.INFO.value} for issue in payload["issues"])
