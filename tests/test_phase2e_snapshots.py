from __future__ import annotations

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from sharpe_replication.cli import app
from sharpe_replication.data.phase2d_membership import reconstruction_conservation_audit
from sharpe_replication.data.phase2e_snapshots import (
    _events_with_resolved_security_ids,
    _reentry_review_phase2e,
    apply_identity_resolutions_to_security_id,
    collapse_membership_spells,
    compare_snapshot_to_membership,
    load_identity_resolutions,
    localize_error_intervals,
    map_snapshot_date_to_membership_session,
    map_snapshot_identities,
    normalize_snapshot_frame,
    parse_ishares_snapshot_csv,
    parse_wikipedia_revision_snapshot,
    rebuild_membership_from_scratch,
    snapshot_source_disagreements,
)
from sharpe_replication.data.sp500_events import normalize_event_ledger, write_event_ledger


def _event(event_id: str, *, effective_date: str, action: str, ticker: str, company: str) -> dict[str, str]:
    return {
        "event_id": event_id,
        "announcement_date": "",
        "effective_date": effective_date,
        "effective_session": "UNKNOWN",
        "action": action,
        "company_name": company,
        "ticker_as_reported": ticker,
        "replacement_group_id": "g1",
        "paired_event_id": "",
        "source_tier": "WIKIPEDIA_SEED",
        "source_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "archive_url": "",
        "source_retrieved_at_utc": "",
        "source_sha256": "",
        "verification_status": "UNVERIFIED",
        "evidence_notes": "",
        "source_security_name": company,
        "source_symbol": ticker,
        "wikipedia_row_id": event_id,
        "wikipedia_reference_urls": "",
    }


def _ledger() -> pd.DataFrame:
    return normalize_event_ledger(
        pd.DataFrame(
            [
                _event("add-old", effective_date="2024-06-03", action="ADD", ticker="OLD", company="Old Co"),
                _event("remove-drop", effective_date="2024-06-03", action="REMOVE", ticker="DROP", company="Drop Co"),
            ]
        )
    )


def _membership() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "security_id": ["wiki:AAA:ALPHA", "wiki:BBB:BETA"],
            "membership_start": ["2024-01-02", "2024-01-02"],
            "membership_end": ["2024-12-31", "2024-12-31"],
        }
    )


def _resolutions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "resolution_id": ["r1", "r2"],
            "old_security_id": ["wiki:OLD:OLD-CO", "wiki:REU:OLD-ISSUER"],
            "canonical_security_id": ["internal:OLD:CANONICAL", "wiki:REU:NEW-ISSUER"],
            "effective_start": ["2024-01-01", "2024-01-01"],
            "effective_end": ["", ""],
            "resolution_type": ["SAME_SECURITY_TICKER_RENAME", "KEEP_SEPARATE_TICKER_REUSE"],
            "evidence_url": ["https://press.spglobal.com/example", "https://press.spglobal.com/example"],
            "evidence_source_tier": ["SP_PRIMARY", "SP_PRIMARY"],
            "reason": ["fixture", "fixture"],
            "reviewer_status": ["APPROVED", "APPROVED"],
        }
    )


def test_wikipedia_revision_parser_parses_constituent_table_and_rejects_changes_only():
    html = """
    <table>
      <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th><th>CIK</th></tr>
      <tr><td>ABC</td><td>ABC Corp</td><td>Industrials</td><td>123</td></tr>
    </table>
    """
    parsed = parse_wikipedia_revision_snapshot(
        html,
        revision_id="123456",
        revision_timestamp="2024-03-31T12:00:00Z",
        source_url="https://en.wikipedia.org/w/index.php?oldid=123456",
    )
    assert parsed.loc[0, "source_ticker"] == "ABC"
    assert parsed.loc[0, "revision_id"] == "123456"

    changes_only = """
    <table>
      <tr><th>Date</th><th>Added</th><th>Removed</th></tr>
      <tr><td>2024-01-01</td><td>ABC</td><td>XYZ</td></tr>
    </table>
    """
    try:
        parse_wikipedia_revision_snapshot(
            changes_only,
            revision_id="bad",
            revision_timestamp="2024-03-31T12:00:00Z",
            source_url="fixture",
        )
    except ValueError as exc:
        assert "current S&P 500 constituent table" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("changes-only table should be rejected")


def test_snapshot_date_mapping_uses_previous_xnys_session_for_weekend_and_holiday():
    session = map_snapshot_date_to_membership_session("2024-03-31", convention="AS_OF_OR_PREVIOUS_SESSION")
    assert session.date().isoformat() == "2024-03-28"


def test_identity_aware_set_comparison_excludes_unmapped_rows():
    raw_snapshot = pd.DataFrame(
        {
            "snapshot_date": ["2024-01-31", "2024-01-31"],
            "source_id": ["fixture", "fixture"],
            "source_name": ["fixture", "fixture"],
            "source_tier": ["SECONDARY_SNAPSHOT", "SECONDARY_SNAPSHOT"],
            "source_security_name": ["Alpha", "Unmapped"],
            "source_ticker": ["AAA", "ZZZ"],
        }
    )
    snapshot = normalize_snapshot_frame(raw_snapshot, date_convention="AS_OF_OR_PREVIOUS_SESSION")
    lineages = pd.DataFrame({"security_id": ["wiki:AAA:ALPHA"], "ticker": ["AAA"]})
    mapped = map_snapshot_identities(snapshot, lineages=lineages, membership=_membership(), resolutions=pd.DataFrame())
    comparison, details = compare_snapshot_to_membership(mapped, _membership())

    assert comparison.loc[0, "unresolved_identity_rows"] == 1
    assert "wiki:BBB:BETA" in comparison.loc[0, "only_reconstructed"]
    assert set(details["difference_side"]) == {"only_reconstructed"}


def test_identity_resolution_maps_ticker_rename_but_keeps_ticker_reuse_separate():
    mapped, evidence = apply_identity_resolutions_to_security_id("wiki:OLD:OLD-CO", "2024-06-03", _resolutions())
    assert mapped == "internal:OLD:CANONICAL"
    assert "SAME_SECURITY_TICKER_RENAME" in evidence

    reused, reuse_evidence = apply_identity_resolutions_to_security_id("wiki:REU:OLD-ISSUER", "2024-06-03", _resolutions())
    assert reused == "wiki:REU:OLD-ISSUER"
    assert reuse_evidence == ""


def test_snapshot_identity_mapping_uses_resolution_config_and_flags_reuse_ambiguity():
    snapshot = normalize_snapshot_frame(
        pd.DataFrame(
            {
                "snapshot_date": ["2024-06-30", "2024-06-30"],
                "source_id": ["fixture", "fixture"],
                "source_name": ["fixture", "fixture"],
                "source_tier": ["SECONDARY_SNAPSHOT", "SECONDARY_SNAPSHOT"],
                "source_security_name": ["Old Co", "Reuse Issuer"],
                "source_ticker": ["OLD", "REU"],
            }
        ),
        date_convention="AS_OF_OR_PREVIOUS_SESSION",
    )
    lineages = pd.DataFrame(
        {
            "security_id": ["wiki:OLD:OLD-CO", "wiki:REU:OLD-ISSUER", "wiki:REU:NEW-ISSUER"],
            "ticker": ["OLD", "REU", "REU"],
        }
    )
    membership = pd.DataFrame(
        {
            "security_id": ["wiki:OLD:OLD-CO", "wiki:REU:OLD-ISSUER", "wiki:REU:NEW-ISSUER"],
            "membership_start": ["2024-01-02", "2024-01-02", "2024-01-02"],
            "membership_end": ["2024-12-31", "2024-12-31", "2024-12-31"],
        }
    )
    mapped = map_snapshot_identities(snapshot, lineages=lineages, membership=membership, resolutions=_resolutions())
    assert mapped.loc[mapped["source_ticker"].eq("OLD"), "mapped_security_id"].iloc[0] == "internal:OLD:CANONICAL"
    assert mapped.loc[mapped["source_ticker"].eq("REU"), "mapping_status"].iloc[0] == "ambiguous_ticker_candidates"


def test_snapshot_source_disagreement_and_error_interval_localization():
    mapped = pd.DataFrame(
        {
            "snapshot_date": pd.to_datetime(["2024-01-31", "2024-01-31", "2024-01-31", "2024-01-31"]),
            "membership_session": pd.to_datetime(["2024-01-31", "2024-01-31", "2024-01-31", "2024-01-31"]),
            "snapshot_date_convention": ["AS_OF_OR_PREVIOUS_SESSION"] * 4,
            "source_id": ["a", "a", "b", "b"],
            "source_name": ["A", "A", "B", "B"],
            "source_tier": ["SECONDARY_SNAPSHOT"] * 4,
            "source_security_name": ["One", "Two", "One", "Three"],
            "source_ticker": ["ONE", "TWO", "ONE", "THREE"],
            "isin": [""] * 4,
            "cik": [""] * 4,
            "figi": [""] * 4,
            "other_external_id": [""] * 4,
            "mapped_security_id": ["sid1", "sid2", "sid1", "sid3"],
            "mapping_status": ["mapped_unique_ticker_candidate"] * 4,
            "mapping_evidence": ["fixture"] * 4,
            "source_url": ["fixture"] * 4,
            "revision_id": [""] * 4,
            "revision_timestamp": [""] * 4,
            "parser_version": ["fixture"] * 4,
            "row_status": ["active_constituent"] * 4,
            "notes": [""] * 4,
        }
    )
    membership = pd.DataFrame(
        {
            "security_id": ["sid1", "sid2"],
            "membership_start": ["2024-01-02", "2024-01-02"],
            "membership_end": ["2024-12-31", "2024-12-31"],
        }
    )
    disagreements = snapshot_source_disagreements(mapped, membership)
    assert set(disagreements["classification"]) == {"SOURCE_DISAGREEMENT"}

    comparisons = pd.DataFrame(
        {
            "source_id": ["a", "a"],
            "membership_session": ["2024-01-31", "2024-02-29"],
            "symmetric_difference_count": [1, 40],
        }
    )
    events = normalize_event_ledger(
        pd.DataFrame(
            [
                _event("jan-event", effective_date="2024-02-15", action="ADD", ticker="X", company="X Co"),
                _event("mar-event", effective_date="2024-03-15", action="ADD", ticker="Y", company="Y Co"),
            ]
        )
    )
    intervals = localize_error_intervals(comparisons, events, agree_threshold=25)
    assert intervals.loc[0, "last_agreeing_checkpoint"] == "2024-01-31"
    assert intervals.loc[0, "candidate_events_between"] == "jan-event"


def test_rebuild_from_scratch_applies_approved_identity_resolution():
    anchor = pd.DataFrame(
        {
            "security_id": ["internal:OLD:CANONICAL"],
            "ticker": ["OLD"],
            "company_name": ["Old Co"],
            "date_added": [""],
            "cik": [""],
            "source_url": ["fixture"],
        }
    )
    rebuilt = rebuild_membership_from_scratch(
        _ledger(),
        anchor,
        resolutions=_resolutions(),
        start_date="2024-01-02",
        anchor_date="2024-12-31",
        end_date="2024-12-31",
    )
    assert "internal:OLD:CANONICAL" in set(rebuilt["security_id"])
    assert "wiki:OLD:OLD-CO" not in set(rebuilt["security_id"])


def test_post_repair_conservation_uses_resolved_security_ids():
    events = normalize_event_ledger(
        pd.DataFrame(
            [_event("add-old", effective_date="2024-06-03", action="ADD", ticker="OLD", company="Old Co")]
        )
    )
    resolved_events = _events_with_resolved_security_ids(events, _resolutions())
    conservation, summary = reconstruction_conservation_audit(
        resolved_events,
        anchor_members={"internal:OLD:CANONICAL"},
        start_date="2024-01-02",
        anchor_date="2024-12-31",
        end_date="2024-12-31",
    )

    assert set(conservation["security_id"]) == {"internal:OLD:CANONICAL"}
    assert summary["backward_conservation_failures"] == 0
    assert summary["forward_conservation_failures"] == 0


def test_reentry_review_stays_unresolved_without_continuity_evidence():
    base = pd.DataFrame(
        {
            "security_id": ["sid"],
            "spell_count": [2],
            "first_start": ["2020-01-01"],
            "last_end": ["2024-12-31"],
        }
    )
    out = _reentry_review_phase2e(base, pd.DataFrame())
    assert out.loc[0, "review_status"] == "EXPLICITLY_UNRESOLVED"
    assert out.loc[0, "same_security_reentry"] == ""


def test_ishares_parser_filters_non_equity_rows_and_retains_identifiers():
    text = """symbol,name,asset_class,CUSIP,ISIN,SEDOL,date
AAA,Alpha Inc,Equity,000000AAA,US000000AAA0,AAA111,2024-01-31
USD,Cash Position,Cash,,,,2024-01-31
"""
    candidate = type(
        "Candidate",
        (),
        {
            "source_id": "ishares",
            "source_name": "iShares fixture",
            "source_tier": "SECONDARY_SNAPSHOT",
            "snapshot_date": "2024-01-31",
        },
    )()
    parsed, filtered = parse_ishares_snapshot_csv(text, candidate=candidate, source_url="fixture")
    assert len(parsed) == 1
    assert parsed.loc[0, "isin"] == "US000000AAA0"
    assert len(filtered) == 1
    assert filtered.loc[0, "filter_reason"] == "non_equity_or_blank_symbol"


def test_collapse_membership_spells_merges_overlaps_after_identity_repair():
    raw = pd.DataFrame(
        {
            "security_id": ["sid", "sid"],
            "membership_start": ["2024-01-02", "2024-06-01"],
            "membership_end": ["2024-06-30", "2024-12-31"],
        }
    )
    collapsed = collapse_membership_spells(raw)
    assert len(collapsed) == 1
    assert collapsed.loc[0, "membership_end"].date().isoformat() == "2024-12-31"


def test_phase2e_cli_writes_data_only_report_pack(tmp_path: Path):
    events_path = tmp_path / "events.csv"
    write_event_ledger(events_path, _ledger())
    anchor_path = tmp_path / "anchor.csv"
    pd.DataFrame(
        {
            "security_id": ["wiki:OLD:OLD-CO"],
            "ticker": ["OLD"],
            "company_name": ["Old Co"],
            "date_added": [""],
            "cik": [""],
            "source_url": ["fixture"],
        }
    ).to_csv(anchor_path, index=False)
    resolutions_path = tmp_path / "resolutions.csv"
    _resolutions().iloc[0:0].to_csv(resolutions_path, index=False)

    result = CliRunner().invoke(
        app,
        [
            "data",
            "phase2e-snapshot-report",
            "--events",
            str(events_path),
            "--anchor",
            str(anchor_path),
            "--phase2d-dir",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / "phase2e"),
            "--snapshot-cache-dir",
            str(tmp_path / "cache"),
            "--identity-resolutions",
            str(resolutions_path),
            "--start-date",
            "2024-01-02",
            "--anchor-date",
            "2024-12-31",
            "--end-date",
            "2024-12-31",
            "--max-snapshot-downloads",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "phase2e" / "snapshot_sources.csv").exists()
    assert (tmp_path / "phase2e" / "phase2e_summary.json").exists()
    assert '"strategy_performance_run": false' in (tmp_path / "phase2e" / "phase2e_summary.json").read_text(encoding="utf-8")


def test_identity_resolution_config_schema_round_trip(tmp_path: Path):
    path = tmp_path / "resolutions.csv"
    _resolutions().to_csv(path, index=False)
    loaded = load_identity_resolutions(path)
    assert next(iter(loaded.columns)) == "resolution_id"
    assert len(loaded) == 2
