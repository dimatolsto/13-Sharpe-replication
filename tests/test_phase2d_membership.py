from __future__ import annotations

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from sharpe_replication.cli import app
from sharpe_replication.data.phase2d_membership import (
    build_verification_queue,
    count_inflation_causes,
    event_group_model,
    extract_effective_session_from_text,
    extract_wikipedia_citation_references,
    identity_ambiguities,
    reconstruction_conservation_audit,
    source_text_supports_event,
)
from sharpe_replication.data.sp500_events import normalize_event_ledger, write_event_ledger


def _event(
    event_id: str,
    *,
    effective_date: str,
    action: str,
    ticker: str,
    company: str,
    group: str = "g1",
    verification_status: str = "UNVERIFIED",
    source_tier: str = "WIKIPEDIA_SEED",
    refs: str = "#cite_note-1",
) -> dict:
    return {
        "event_id": event_id,
        "announcement_date": "",
        "effective_date": effective_date,
        "effective_session": "UNKNOWN",
        "action": action,
        "company_name": company,
        "ticker_as_reported": ticker,
        "replacement_group_id": group,
        "paired_event_id": "",
        "source_tier": source_tier,
        "source_url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "archive_url": "",
        "source_retrieved_at_utc": "",
        "source_sha256": "",
        "verification_status": verification_status,
        "evidence_notes": "",
        "source_security_name": company,
        "source_symbol": ticker,
        "wikipedia_row_id": "wiki-row-1",
        "wikipedia_reference_urls": refs,
    }


def _ledger() -> pd.DataFrame:
    return normalize_event_ledger(
        pd.DataFrame(
            [
                _event(
                    "wiki:add:new",
                    effective_date="2024-09-23",
                    action="ADD",
                    ticker="NEW",
                    company="New Company",
                    verification_status="VERIFIED_PRIMARY",
                    source_tier="SP_PRIMARY",
                ),
                _event(
                    "wiki:remove:old",
                    effective_date="2024-09-23",
                    action="REMOVE",
                    ticker="OLD",
                    company="Old Company",
                    verification_status="VERIFIED_PRIMARY",
                    source_tier="SP_PRIMARY",
                ),
            ]
        )
    )


def test_wikipedia_footnote_targets_seed_verification_queue_with_primary_urls():
    html = """
    <ol>
      <li id="cite_note-1" data-mw-footnote-number="1">
        <cite>
          <a href="https://press.spglobal.com/2024-09-06-New-Company-Set-to-Join-S-P-500">
            New Company Set to Join S&amp;P 500
          </a>
        </cite>
      </li>
      <li id="cite_note-2" data-mw-footnote-number="2">
        <a href="https://en.wikipedia.org/wiki/New_Company">New Company</a>
      </li>
    </ol>
    """
    citations = extract_wikipedia_citation_references(html)
    assert set(citations["source_tier"]) == {"SP_PRIMARY", "IRRELEVANT"}

    event = normalize_event_ledger(pd.DataFrame([_event("wiki:add:new", effective_date="2024-09-23", action="ADD", ticker="NEW", company="New Company")]))
    queue = build_verification_queue(event, citations)
    assert queue.loc[0, "status"] == "pending"
    assert "press.spglobal.com" in queue.loc[0, "source_url"]


def test_conservation_audit_flags_backward_add_not_present_and_cause():
    audit, summary = reconstruction_conservation_audit(
        _ledger(),
        anchor_members={"wiki:OLD:OLD-COMPANY"},
        start_date="2024-01-01",
        anchor_date="2024-12-31",
        end_date="2024-12-31",
    )
    backward_add = audit[audit["event_id"].eq("wiki:add:new") & audit["traversal"].eq("BACKWARD")].iloc[0]
    assert "BACKWARD_ADD_NOT_PRESENT" in backward_add["flags"]
    assert backward_add["count_excess_delta"] == 1
    assert summary["backward_conservation_failures"] >= 1

    causes = count_inflation_causes(audit)
    assert "addition not present during backward inversion" in set(causes["cause"])


def test_event_group_expected_net_change_requires_verified_balanced_group():
    groups = event_group_model(_ledger())
    assert groups.loc[0, "seed_net_count_change"] == 0
    assert groups.loc[0, "expected_net_count_change"] == 0

    unverified = _ledger().copy()
    unverified["verification_status"] = "UNVERIFIED"
    groups = event_group_model(unverified)
    assert pd.isna(groups.loc[0, "expected_net_count_change"])


def test_identity_ambiguities_report_uses_ticker_labels_not_counts():
    lineages = pd.DataFrame(
        {
            "security_id": ["sid-a", "sid-b"],
            "ticker": ["DUP", "DUP"],
            "confidence/status": ["PROVISIONAL", "PROVISIONAL"],
        }
    )
    out = identity_ambiguities(lineages, pd.DataFrame(columns=["flags"]))
    assert out.loc[0, "ticker"] == "DUP"
    assert out.loc[0, "security_ids"] == "sid-a;sid-b"


def test_source_text_support_and_timing_extraction_do_not_infer_before_open_from_date_only():
    row = _ledger().iloc[0]
    assert extract_effective_session_from_text(
        "S&P Dow Jones Indices will make the changes effective prior to the open of trading on September 23, 2024."
    ) == "BEFORE_OPEN"
    assert extract_effective_session_from_text("New Company joins the S&P 500 on September 23, 2024.") == "DATE_ONLY"
    assert source_text_supports_event(
        "New Company will join the S&P 500 effective prior to the open of trading on September 23, 2024.",
        row,
    )


def test_phase2d_cli_writes_data_only_report_pack(tmp_path: Path):
    events_path = tmp_path / "events.csv"
    write_event_ledger(events_path, _ledger())
    anchor = pd.DataFrame(
        {
            "security_id": ["wiki:OLD:OLD-COMPANY"],
            "ticker": ["OLD"],
            "company_name": ["Old Company"],
            "date_added": [""],
            "cik": [""],
            "source_url": ["fixture"],
        }
    )
    anchor_path = tmp_path / "anchor.csv"
    anchor.to_csv(anchor_path, index=False)
    membership = pd.DataFrame(
        {
            "security_id": ["wiki:OLD:OLD-COMPANY"],
            "membership_start": ["2024-01-02"],
            "membership_end": ["2024-12-31"],
        }
    )
    membership_path = tmp_path / "membership.csv"
    membership.to_csv(membership_path, index=False)
    html_path = tmp_path / "wiki.html"
    html_path.write_text(
        '<li id="cite_note-1" data-mw-footnote-number="1">'
        '<a href="https://press.spglobal.com/2024-09-06-New-Company-Set-to-Join-S-P-500">source</a>'
        "</li>",
        encoding="utf-8",
    )
    out_dir = tmp_path / "phase2d"

    result = CliRunner().invoke(
        app,
        [
            "data",
            "phase2d-membership-report",
            "--events",
            str(events_path),
            "--anchor",
            str(anchor_path),
            "--wikipedia-html",
            str(html_path),
            "--membership",
            str(membership_path),
            "--out-dir",
            str(out_dir),
            "--source-cache-dir",
            str(tmp_path / "cache"),
            "--start-date",
            "2024-01-01",
            "--anchor-date",
            "2024-12-31",
            "--end-date",
            "2024-12-31",
            "--max-source-fetches",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "reconstruction_conservation.csv").exists()
    assert (out_dir / "verification_queue.csv").exists()
    assert (out_dir / "membership_gaps.csv").exists()
    summary = (out_dir / "phase2d_summary.json").read_text(encoding="utf-8")
    assert '"strategy_performance_run": false' in summary
