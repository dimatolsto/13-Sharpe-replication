from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from sharpe_replication.cli import app
from sharpe_replication.data.acquisition import sanitize_url, write_raw_acquisition_metadata
from sharpe_replication.data.join_audit import audit_membership_price_join
from sharpe_replication.data.membership import members_on
from sharpe_replication.data.membership_reconstruction import (
    reconstruct_membership_from_change_events,
)
from sharpe_replication.data.source_discovery import source_discovery_report


def _small_panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                [
                    "2020-01-02",
                    "2020-01-03",
                    "2020-01-02",
                    "2020-01-03",
                ]
            ),
            "security_id": ["A", "A", "B", "B"],
            "ticker": ["A", "A", "B", "B"],
            "raw_close": [10.0, 10.5, 20.0, 20.5],
            "total_return": [0.0, 0.05, 0.0, pd.NA],
        }
    )


def test_source_discovery_matrix_preserves_unverified_capabilities():
    report = source_discovery_report()
    names = {item["source_name"] for item in report["assessments"]}
    assert {"WiseSheets Pro", "CRSP via WRDS", "Norgate Data"} <= names
    assert report["chosen_sources"]["certified_snapshot"] is None

    wisesheets = next(item for item in report["assessments"] if item["source_name"] == "WiseSheets Pro")
    assert wisesheets["provider_capabilities"]["historical_raw_close"] == "unverified"
    assert wisesheets["provider_capabilities"]["point_in_time_index_membership"] == "unsupported"
    assert "No authoritative REST base URL" in wisesheets["blocking_uncertainties"][0]

    norgate = next(item for item in report["assessments"] if item["source_name"] == "Norgate Data")
    assert norgate["provider_capabilities"]["point_in_time_index_membership"] == "supported"
    assert norgate["provider_capabilities"]["verified"] is False


def test_discover_sources_cli_and_wisesheets_secret_handling(monkeypatch: pytest.MonkeyPatch):
    runner = CliRunner()
    matrix = runner.invoke(app, ["data", "discover-sources"])
    assert matrix.exit_code == 0, matrix.output
    payload = json.loads(matrix.output)
    assert payload["phase"] == "2A"

    monkeypatch.setenv("WISESHEETS_API_KEY", "do-not-print-this")
    caps = runner.invoke(app, ["data", "wisesheets-capabilities"])
    assert caps.exit_code == 0, caps.output
    assert "do-not-print-this" not in caps.output
    assert json.loads(caps.output)["api_key_present"] is True


def test_raw_acquisition_metadata_hashes_files_redacts_urls_and_is_immutable(tmp_path: Path):
    raw_dir = tmp_path / "raw" / "provider" / "snapshot"
    raw_dir.mkdir(parents=True)
    (raw_dir / "AAPL.json").write_text('{"symbol":"AAPL"}\n', encoding="utf-8")

    assert sanitize_url("https://example.test/data?api_key=secret&symbol=AAPL").endswith(
        "api_key=<redacted>&symbol=AAPL"
    )
    metadata = write_raw_acquisition_metadata(
        raw_dir,
        provider="fixture",
        request_type="daily_panel",
        requested_date_range={"start": "2020-01-01", "end": "2020-01-31"},
        requested_symbols=["AAPL"],
        source_urls=["https://example.test/data?token=secret&symbol=AAPL"],
        documentation_urls=["https://example.test/docs"],
        response_count=1,
        limitations=["synthetic fixture"],
    )
    assert metadata["file_hashes"]["AAPL.json"]
    assert "secret" not in (raw_dir / "metadata.json").read_text(encoding="utf-8")
    assert "<redacted>" in json.dumps(metadata)
    with pytest.raises(FileExistsError):
        write_raw_acquisition_metadata(
            raw_dir,
            provider="fixture",
            request_type="daily_panel",
            requested_date_range={"start": None, "end": None},
        )


def test_membership_reconstruction_uses_effective_not_announcement_date_and_supports_reentry():
    events = pd.DataFrame(
        {
            "announcement_date": [
                "2019-12-20",
                "2020-01-04",
                "2020-01-06",
                "2020-01-08",
                "2020-01-11",
                "2020-01-14",
            ],
            "effective_date": [
                "2020-01-03",
                "2020-01-05",
                "2020-01-07",
                "2020-01-09",
                "2020-01-12",
                "2020-01-15",
            ],
            "security_id": ["A", "B", "B", "C", "A", "A"],
            "action": ["addition", "addition", "removal", "addition", "removal", "addition"],
        }
    )
    result = reconstruct_membership_from_change_events(
        events,
        anchor_date="2020-01-10",
        anchor_members={"A", "C"},
        start_date="2020-01-01",
        end_date="2020-01-20",
        source="fixture_changes",
    )
    membership = result.membership
    assert "A" not in members_on(membership, "2020-01-02")
    assert "A" in members_on(membership, "2020-01-03")
    assert members_on(membership, "2020-01-06") == {"A", "B"}
    assert "B" not in members_on(membership, "2020-01-07")
    assert "C" not in members_on(membership, "2020-01-08")
    assert "C" in members_on(membership, "2020-01-09")
    assert "A" not in members_on(membership, "2020-01-12")
    assert "A" in members_on(membership, "2020-01-15")
    assert result.diagnostics["effective_date_interpretation"].startswith("effective at start")


def test_membership_price_join_audit_reports_unmapped_and_missing_returns(tmp_path: Path):
    panel = _small_panel()
    membership = pd.DataFrame(
        {
            "security_id": ["A", "B", "C"],
            "membership_start": ["2020-01-02", "2020-01-02", "2020-01-02"],
            "membership_end": ["2020-01-03", "2020-01-03", "2020-01-03"],
        }
    )
    audit = audit_membership_price_join(panel, membership)
    assert audit["unmapped_membership_security_ids"] == ["C"]
    assert audit["member_dates_lacking_price_rows"] == 2
    assert audit["member_dates_lacking_total_return"] == 1

    panel_path = tmp_path / "panel.csv"
    membership_path = tmp_path / "membership.csv"
    panel.to_csv(panel_path, index=False)
    membership.to_csv(membership_path, index=False)
    result = CliRunner().invoke(
        app,
        [
            "data",
            "audit-coverage",
            "--panel",
            str(panel_path),
            "--membership",
            str(membership_path),
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["unmapped_membership_security_ids"] == ["C"]
