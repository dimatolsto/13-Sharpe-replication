from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

import sharpe_replication.cli as cli_module
from sharpe_replication.cli import app
from sharpe_replication.data.certification import certify_for_experiment, certify_report
from sharpe_replication.data.identity import (
    detect_identity_ambiguities,
    stable_security_id_from_event,
    yahoo_aliases_from_security_master,
)
from sharpe_replication.data.join_audit import audit_membership_price_join
from sharpe_replication.data.membership import members_on
from sharpe_replication.data.membership_reconstruction import (
    reconstruct_membership_from_change_events,
)
from sharpe_replication.data.normalize import (
    normalize_membership,
)
from sharpe_replication.data.schema import Status
from sharpe_replication.data.snapshot import hash_snapshot, write_snapshot
from sharpe_replication.data.sp500_events import (
    event_completeness_report,
    event_ledger_to_change_events,
    gap_register_from_events,
    merge_event_evidence,
    normalize_event_ledger,
    parse_wikipedia_change_table,
    parse_wikipedia_current_constituents,
    parse_wikipedia_file,
)
from sharpe_replication.data.trading_calendar import (
    TradingCalendar,
    is_trading_session,
    latest_completed_exchange_session,
    next_session,
    previous_session,
    session_close_timestamp,
    trading_calendar_metadata,
)
from sharpe_replication.data.validation import (
    audit_reconstructed_nominal_close,
    audit_split_raw_close,
    reconstruct_nominal_close_from_splits,
    split_diagnostic_context,
    terminal_return_audit,
    validate_dataset,
)
from sharpe_replication.data.wisesheets_crosscheck import (
    compare_wisesheets_to_yahoo,
    read_wisesheets_export,
    requested_wisesheets_test_pack,
)
from sharpe_replication.data.yahoo_provider import (
    YAHOO_DOWNLOAD_SETTINGS,
    YahooDownloadResult,
    classify_yahoo_error,
    initial_yahoo_acquisition_state,
    normalize_yahoo_history,
    pending_yahoo_symbols,
    record_yahoo_result,
    yahoo_candidate_audit,
)

FIXTURE_HTML = Path("tests/fixtures/wikipedia_sp500_changes.html")


def _events() -> pd.DataFrame:
    return parse_wikipedia_file(FIXTURE_HTML, source_url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")


def _verified_event(event_id: str, **updates) -> pd.DataFrame:
    base = _events().iloc[0].to_dict()
    base.update(
        {
            "event_id": event_id,
            "announcement_date": "2020-06-12",
            "effective_date": "2020-06-22",
            "effective_session": "BEFORE_OPEN",
            "source_tier": "SP_PRIMARY",
            "source_url": "https://press.spglobal.com/example-primary",
            "verification_status": "VERIFIED_PRIMARY",
            "evidence_notes": "Short factual extraction only.",
        }
    )
    base.update(updates)
    return normalize_event_ledger(pd.DataFrame([base]))


def _yahoo_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                [
                    "2020-08-27 00:00:00-04:00",
                    "2020-08-28 00:00:00-04:00",
                    "2020-08-31 00:00:00-04:00",
                    "2020-09-01 00:00:00-04:00",
                    "2020-09-02 00:00:00-04:00",
                ]
            ),
            "Open": [500.0, 504.0, 126.0, 130.0, 128.0],
            "High": [506.0, 508.0, 132.0, 132.0, 130.0],
            "Low": [498.0, 500.0, 124.0, 127.0, 127.0],
            "Close": [500.0, 508.0, 127.0, 129.0, 128.0],
            "Adj Close": [125.0, 127.0, 127.0, 129.0, 128.0],
            "Volume": [1000, 1100, 5000, 3000, 2500],
            "Dividends": [0.0, 0.0, 0.0, 0.82, 0.0],
            "Stock Splits": [0.0, 0.0, 4.0, 0.0, 0.0],
        }
    )


def test_wikipedia_current_constituent_parser_builds_unverified_anchor_rows():
    html = """
    <table class="wikitable">
      <tr>
        <th>Symbol</th><th>Security</th><th>GICS Sector</th>
        <th>GICS Sub-Industry</th><th>Headquarters Location</th>
        <th>Date added</th><th>CIK</th><th>Founded</th>
      </tr>
      <tr>
        <td>BRK.B</td><td><a href="/wiki/Berkshire_Hathaway">Berkshire Hathaway</a></td>
        <td>Financials</td><td>Multi-Sector Holdings</td><td>Omaha, Nebraska</td>
        <td>February 16, 2010</td><td>1067983</td><td>1839</td>
      </tr>
      <tr>
        <td>ACME</td><td>Acme Corp</td><td>Industrials</td><td>Conglomerates</td>
        <td>New York, New York</td><td></td><td></td><td>1999</td>
      </tr>
    </table>
    """
    anchor = parse_wikipedia_current_constituents(html, source_url="https://example.test/wiki")
    assert list(anchor["ticker"]) == ["ACME", "BRK.B"]
    assert set(anchor["source_tier"]) == {"WIKIPEDIA_ANCHOR"}
    assert set(anchor["verification_status"]) == {"UNVERIFIED"}
    assert anchor.loc[anchor["ticker"].eq("BRK.B"), "date_added"].iloc[0] == pd.Timestamp("2010-02-16")


def test_wikipedia_current_constituent_parser_fails_when_anchor_schema_changes():
    with pytest.raises(ValueError, match="Symbol/Security/GICS"):
        parse_wikipedia_current_constituents("<table><tr><th>Symbol</th></tr></table>", source_url="x")


def test_wikipedia_parser_emits_unverified_seed_events_and_multiple_same_date_rows():
    events = _events()
    assert len(events) == 6
    assert set(events["source_tier"]) == {"WIKIPEDIA_SEED"}
    assert set(events["verification_status"]) == {"UNVERIFIED"}
    assert int(events["effective_date"].eq(pd.Timestamp("2020-09-21")).sum()) == 4
    assert events["wikipedia_reference_urls"].str.contains("press.spglobal.com").any()


def test_wikipedia_parser_fails_on_missing_change_schema():
    with pytest.raises(ValueError, match="Date/Added/Removed"):
        parse_wikipedia_change_table("<table><tr><th>Only current constituents</th></tr></table>", source_url="x")


def test_primary_evidence_supersedes_wikipedia_discrepancy_and_records_it():
    seed = _events()
    evidence = _verified_event(
        seed.iloc[0]["event_id"],
        effective_date="2020-06-23",
        ticker_as_reported="TMUS",
    )
    merged, discrepancies = merge_event_evidence(seed, evidence)
    row = merged[merged["event_id"].eq(seed.iloc[0]["event_id"])].iloc[0]
    assert row["verification_status"] == "VERIFIED_PRIMARY"
    assert row["effective_date"] == pd.Timestamp("2020-06-23")
    assert "effective_date" in set(discrepancies["field"])


def test_event_completeness_and_gap_register_keep_unverified_events_visible():
    events = _events()
    gaps = gap_register_from_events(events)
    report = event_completeness_report(events, gaps)
    assert report["wikipedia_seed_events"] == 6
    assert report["unresolved"] == 6
    assert report["unknown_effective_session_events"] == 6
    assert len(gaps) == 6
    assert gaps["blocking_for_P3"].all()


def test_event_changes_can_use_provisional_security_ids_without_ticker_only_identity():
    events = _events()
    changes = event_ledger_to_change_events(events, include_unverified=True, provisional_security_ids=True)
    first = events.iloc[0]
    expected_id = stable_security_id_from_event(first["ticker_as_reported"], first["company_name"])
    assert expected_id in set(changes["security_id"])
    assert first["ticker_as_reported"] not in set(changes["security_id"])


def test_event_conversion_filters_out_of_window_rows_before_calendar_normalization():
    events = _events().copy()
    early = events.iloc[0].copy()
    early["event_id"] = "wikipedia:early"
    early["effective_date"] = pd.Timestamp("1976-07-01")
    events = pd.concat([pd.DataFrame([early]), events], ignore_index=True)
    changes = event_ledger_to_change_events(
        events,
        calendar=TradingCalendar.xnys(),
        include_unverified=True,
        start_date="2004-01-01",
        end_date="2026-08-11",
    )
    assert changes["effective_date"].min() >= pd.Timestamp("2004-01-01")


def test_unknown_effective_session_remains_p3_blocking_after_membership_normalization():
    seed = _events()
    evidence = _verified_event(
        seed.iloc[0]["event_id"],
        effective_session="UNKNOWN",
        verification_status="VERIFIED_PRIMARY",
    )
    assert gap_register_from_events(evidence)["problem_type"].iloc[0] == "unknown_effective_session_timing"
    changes = event_ledger_to_change_events(evidence, calendar=TradingCalendar.xnys())
    result = reconstruct_membership_from_change_events(
        changes,
        anchor_date="2020-06-19",
        anchor_members=set(),
        start_date="2020-06-19",
        end_date="2020-06-30",
        calendar=TradingCalendar.xnys(),
    )
    assert result.diagnostics["timing_uncertain_event_count"] == 1
    assert result.diagnostics["trading_calendar"]["package"] == "exchange-calendars"
    report = validate_dataset(panel=pd.DataFrame(
        {
            "date": ["2020-06-22"],
            "security_id": ["TMUS"],
            "ticker": ["TMUS"],
            "raw_close": [100.0],
            "total_return": [0.0],
        }
    ), membership=result.membership)
    assert report.has_code("unknown_effective_session_timing")
    cert = certify_report(report, raw_close_semantics="unknown", total_return_source="unknown", membership_present=True)
    assert certify_for_experiment("P3", cert).status == Status.FAIL


def test_xnys_calendar_handles_weekends_standard_holidays_good_friday_and_unscheduled_closure():
    assert not is_trading_session("2004-01-01")
    assert previous_session("2004-01-01") == pd.Timestamp("2003-12-31")
    assert next_session("2004-01-01") == pd.Timestamp("2004-01-02")

    assert previous_session("2020-01-04") == pd.Timestamp("2020-01-03")
    assert next_session("2020-01-04") == pd.Timestamp("2020-01-06")

    assert not is_trading_session("2020-01-01")
    assert previous_session("2020-01-01") == pd.Timestamp("2019-12-31")
    assert next_session("2020-01-01") == pd.Timestamp("2020-01-02")

    assert not is_trading_session("2020-12-25")
    assert previous_session("2020-12-25") == pd.Timestamp("2020-12-24")
    assert next_session("2020-12-25") == pd.Timestamp("2020-12-28")

    assert not is_trading_session("2020-04-10")
    assert previous_session("2020-04-10") == pd.Timestamp("2020-04-09")
    assert next_session("2020-04-10") == pd.Timestamp("2020-04-13")

    assert not is_trading_session("2012-10-29")
    assert not is_trading_session("2012-10-30")
    assert previous_session("2012-10-29") == pd.Timestamp("2012-10-26")
    assert next_session("2012-10-29") == pd.Timestamp("2012-10-31")
    assert session_close_timestamp("2026-08-11") == pd.Timestamp("2026-08-11 20:00:00+0000")
    assert trading_calendar_metadata()["calendar"] == "XNYS"


def test_latest_completed_xnys_session_handles_open_during_after_close_and_weekend():
    assert latest_completed_exchange_session("2026-08-11T12:00:00Z") == pd.Timestamp("2026-08-10")
    assert latest_completed_exchange_session("2026-08-11T15:00:00Z") == pd.Timestamp("2026-08-10")
    assert latest_completed_exchange_session("2026-08-11T21:00:00Z") == pd.Timestamp("2026-08-11")
    assert latest_completed_exchange_session("2026-08-15T16:00:00Z") == pd.Timestamp("2026-08-14")


def test_effective_sessions_use_xnys_calendar_for_before_open_after_close_boundaries():
    calendar = TradingCalendar.xnys()
    events = normalize_event_ledger(
        pd.DataFrame(
            [
                {
                    "event_id": "primary:add-before-weekend",
                    "announcement_date": "2020-01-01",
                    "effective_date": "2020-01-04",
                    "effective_session": "BEFORE_OPEN",
                    "action": "ADD",
                    "company_name": "New Co",
                    "ticker_as_reported": "NEW",
                    "replacement_group_id": "g1",
                    "paired_event_id": "",
                    "source_tier": "SP_PRIMARY",
                    "source_url": "https://press.spglobal.com/example",
                    "archive_url": "",
                    "source_retrieved_at_utc": "",
                    "source_sha256": "",
                    "verification_status": "VERIFIED_PRIMARY",
                    "evidence_notes": "",
                    "source_security_name": "New Co",
                    "source_symbol": "NEW",
                    "wikipedia_row_id": "",
                    "wikipedia_reference_urls": "",
                },
                {
                    "event_id": "primary:remove-before",
                    "announcement_date": "2020-01-01",
                    "effective_date": "2020-01-06",
                    "effective_session": "BEFORE_OPEN",
                    "action": "REMOVE",
                    "company_name": "Old Co",
                    "ticker_as_reported": "OLD",
                    "replacement_group_id": "g1",
                    "paired_event_id": "",
                    "source_tier": "SP_PRIMARY",
                    "source_url": "https://press.spglobal.com/example",
                    "archive_url": "",
                    "source_retrieved_at_utc": "",
                    "source_sha256": "",
                    "verification_status": "VERIFIED_PRIMARY",
                    "evidence_notes": "",
                    "source_security_name": "Old Co",
                    "source_symbol": "OLD",
                    "wikipedia_row_id": "",
                    "wikipedia_reference_urls": "",
                },
                {
                    "event_id": "primary:remove-after",
                    "announcement_date": "2020-01-17",
                    "effective_date": "2020-01-17",
                    "effective_session": "AFTER_CLOSE",
                    "action": "REMOVE",
                    "company_name": "Late Co",
                    "ticker_as_reported": "LATE",
                    "replacement_group_id": "g2",
                    "paired_event_id": "",
                    "source_tier": "SP_PRIMARY",
                    "source_url": "https://press.spglobal.com/example",
                    "archive_url": "",
                    "source_retrieved_at_utc": "",
                    "source_sha256": "",
                    "verification_status": "VERIFIED_PRIMARY",
                    "evidence_notes": "",
                    "source_security_name": "Late Co",
                    "source_symbol": "LATE",
                    "wikipedia_row_id": "",
                    "wikipedia_reference_urls": "",
                },
                {
                    "event_id": "primary:add-after-holiday",
                    "announcement_date": "2020-01-17",
                    "effective_date": "2020-01-17",
                    "effective_session": "AFTER_CLOSE",
                    "action": "ADD",
                    "company_name": "Next Co",
                    "ticker_as_reported": "NEXT",
                    "replacement_group_id": "g3",
                    "paired_event_id": "",
                    "source_tier": "SP_PRIMARY",
                    "source_url": "https://press.spglobal.com/example",
                    "archive_url": "",
                    "source_retrieved_at_utc": "",
                    "source_sha256": "",
                    "verification_status": "VERIFIED_PRIMARY",
                    "evidence_notes": "",
                    "source_security_name": "Next Co",
                    "source_symbol": "NEXT",
                    "wikipedia_row_id": "",
                    "wikipedia_reference_urls": "",
                },
            ]
        )
    )
    changes = event_ledger_to_change_events(events, calendar=calendar)
    assert changes[changes["security_id"].eq("NEW")]["effective_date"].iloc[0] == pd.Timestamp("2020-01-06")
    assert changes[changes["security_id"].eq("LATE")]["effective_date"].iloc[0] == pd.Timestamp("2020-01-21")
    assert changes[changes["security_id"].eq("NEXT")]["effective_date"].iloc[0] == pd.Timestamp("2020-01-21")

    membership = reconstruct_membership_from_change_events(
        changes,
        anchor_date="2020-01-03",
        anchor_members={"OLD", "LATE"},
        start_date="2020-01-03",
        end_date="2020-01-22",
        calendar=calendar,
    ).membership
    old_spell = membership[membership["security_id"].eq("OLD")].iloc[0]
    late_spell = membership[membership["security_id"].eq("LATE")].iloc[0]
    assert old_spell["membership_end"] == pd.Timestamp("2020-01-03")
    assert late_spell["membership_end"] == pd.Timestamp("2020-01-17")
    assert "NEW" not in members_on(membership, "2020-01-03")
    assert "NEW" in members_on(membership, "2020-01-06")
    assert "NEXT" in members_on(membership, "2020-01-21")


def test_backward_reconstruction_and_reentry_from_effective_events():
    events = pd.DataFrame(
        {
            "effective_date": ["2020-01-06", "2020-01-08", "2020-01-10"],
            "security_id": ["B", "A", "A"],
            "action": ["addition", "removal", "addition"],
        }
    )
    result = reconstruct_membership_from_change_events(
        events,
        anchor_date="2020-01-09",
        anchor_members={"B"},
        start_date="2020-01-03",
        end_date="2020-01-15",
    )
    assert "A" in members_on(result.membership, "2020-01-07")
    assert "A" not in members_on(result.membership, "2020-01-08")
    assert "A" in members_on(result.membership, "2020-01-10")


def test_reconstruction_diagnostics_are_deduplicated_across_state_recomputations():
    events = pd.DataFrame(
        {
            "effective_date": ["2020-01-06", "2020-01-08", "2020-01-10"],
            "security_id": ["A", "A", "A"],
            "action": ["addition", "addition", "addition"],
        }
    )
    result = reconstruct_membership_from_change_events(
        events,
        anchor_date="2020-01-03",
        anchor_members={"A"},
        start_date="2020-01-03",
        end_date="2020-01-15",
    )
    assert result.diagnostics["duplicate_additions"] == [
        {"date": "2020-01-06", "security_id": "A"},
        {"date": "2020-01-08", "security_id": "A"},
        {"date": "2020-01-10", "security_id": "A"},
    ]


def test_date_aware_identity_handles_ticker_rename_and_reuse_without_fuzzy_merges():
    master = pd.DataFrame(
        {
            "security_id": ["sid-rename", "sid-rename", "sid-old-abc", "sid-new-abc"],
            "ticker": ["ABC", "XYZ", "ABC", "ABC"],
            "effective_start": ["2010-01-01", "2015-01-01", "2004-01-01", "2020-01-01"],
            "effective_end": ["2014-12-31", None, "2006-12-31", None],
            "source": ["fixture"] * 4,
            "external_id": ["e1", "e1", "e-old", "e-new"],
        }
    )
    aliases = yahoo_aliases_from_security_master(master)
    diagnostics = detect_identity_ambiguities(master)
    assert set(aliases["provider_symbol"]) == {"ABC", "XYZ"}
    assert diagnostics["ticker_changes"] == 1
    assert diagnostics["ticker_reuse_cases"] == 1
    assert stable_security_id_from_event("ABC", "First Issuer") != stable_security_id_from_event("ABC", "Second Issuer")


def test_yahoo_settings_disable_adjustment_repair_and_preserve_close_adjclose_actions():
    assert YAHOO_DOWNLOAD_SETTINGS["auto_adjust"] is False
    assert YAHOO_DOWNLOAD_SETTINGS["back_adjust"] is False
    assert YAHOO_DOWNLOAD_SETTINGS["repair"] is False
    assert YAHOO_DOWNLOAD_SETTINGS["actions"] is True
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    assert {"raw_close", "yahoo_close", "adjusted_close", "raw_close_source"} <= set(panel.columns)
    split = actions[actions["event_type"].eq("split")].iloc[0]
    dividend = actions[actions["event_type"].eq("cash_dividend")].iloc[0]
    assert split["split_factor"] == 4.0
    assert dividend["dividend_cash"] == 0.82
    assert set(panel["raw_close_source"]) == {"not_populated_yahoo_close_failed_split_audit"}
    assert panel["raw_close"].isna().all()
    assert panel.loc[panel["date"].eq(pd.Timestamp("2020-08-31")), "yahoo_close"].iloc[0] == 127.0


def test_yahoo_parser_drops_serialized_single_symbol_ticker_header_row():
    raw = pd.DataFrame(
        [
            ["", "AAPL", "AAPL", "AAPL", "AAPL", "AAPL", "AAPL", "AAPL", "AAPL"],
            ["2020-08-28", 127.0, 508.0, 0.0, 508.0, 500.0, 504.0, 0.0, 1100],
            ["2020-08-31", 127.0, 127.0, 0.0, 132.0, 124.0, 126.0, 4.0, 5000],
        ],
        columns=["Date", "Adj Close", "Close", "Dividends", "High", "Low", "Open", "Stock Splits", "Volume"],
    )
    panel, actions = normalize_yahoo_history(raw, security_id="AAPL-SEC", ticker="AAPL")
    assert list(panel["date"]) == [pd.Timestamp("2020-08-28"), pd.Timestamp("2020-08-31")]
    assert panel["yahoo_close"].tolist() == [508.0, 127.0]
    assert panel["raw_close"].isna().all()
    assert actions["event_type"].tolist() == ["split"]


def test_yahoo_date_normalization_handles_timezone_daily_rows_without_utc_shift():
    panel, _ = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    assert panel["date"].iloc[0] == pd.Timestamp("2020-08-27")
    assert panel["date"].iloc[-1] == pd.Timestamp("2020-09-02")


def test_yahoo_no_implicit_fill_keeps_missing_prices_visible():
    raw = _yahoo_raw()
    raw.loc[2, "Close"] = pd.NA
    panel, _ = normalize_yahoo_history(raw, security_id="AAPL-SEC", ticker="AAPL")
    assert panel["yahoo_close"].isna().sum() == 1
    assert panel["raw_close"].isna().all()
    report = validate_dataset(panel)
    assert report.has_code("missing_price")


def test_yahoo_acquisition_resume_and_error_classification():
    aliases = pd.DataFrame({"security_id": ["A", "B"], "provider_symbol": ["AAA", "BBB"]})
    state = initial_yahoo_acquisition_state(aliases)
    state = record_yahoo_result(state, provider_symbol="AAA", result=YahooDownloadResult(status="complete", rows=10))
    retry_status, retryable = classify_yahoo_error("timeout while downloading")
    permanent_status, permanent_retryable = classify_yahoo_error("No data found for symbol")
    state = record_yahoo_result(
        state,
        provider_symbol="BBB",
        result=YahooDownloadResult(status=retry_status, error="timeout", retryable=retryable),
    )
    assert pending_yahoo_symbols(state) == ["BBB"]
    assert permanent_status == "failed_permanent"
    assert permanent_retryable is False
    state = record_yahoo_result(
        state,
        provider_symbol="BBB",
        result=YahooDownloadResult(status=permanent_status, error="no data", retryable=permanent_retryable),
    )
    assert pending_yahoo_symbols(state) == []
    assert pending_yahoo_symbols(state, include_permanent=True) == ["BBB"]


def test_split_audit_nominal_and_adjusted_history_classification():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    yahoo_close_candidate = panel.copy()
    yahoo_close_candidate["raw_close"] = yahoo_close_candidate["yahoo_close"]
    audit = audit_split_raw_close(yahoo_close_candidate, actions)
    assert audit["classification"].iloc[0] == "consistent_with_nominal"
    adjusted = panel.copy()
    adjusted["raw_close"] = adjusted["adjusted_close"]
    adjusted_audit = audit_split_raw_close(adjusted, actions)
    assert adjusted_audit["classification"].iloc[0] == "likely_back_adjusted"


def test_reconstructed_nominal_close_deadjusts_yahoo_like_split_adjusted_history_separately():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    adjusted = panel.copy()
    adjusted["raw_close"] = adjusted["adjusted_close"]
    reconstructed = reconstruct_nominal_close_from_splits(adjusted, actions)
    assert "reconstructed_nominal_close" in reconstructed.columns
    assert reconstructed.loc[reconstructed["date"].eq(pd.Timestamp("2020-08-28")), "reconstructed_nominal_close"].iloc[0] == pytest.approx(508.0)
    assert reconstructed.loc[reconstructed["date"].eq(pd.Timestamp("2020-08-31")), "reconstructed_nominal_close"].iloc[0] == pytest.approx(127.0)
    assert adjusted.loc[adjusted["date"].eq(pd.Timestamp("2020-08-28")), "raw_close"].iloc[0] == pytest.approx(127.0)
    audit = audit_reconstructed_nominal_close(adjusted, actions)
    assert audit["classification"].iloc[0] == "consistent_with_nominal"


def test_reconstructed_nominal_close_handles_multiple_and_reverse_splits():
    panel = pd.DataFrame(
        {
            "date": ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"],
            "security_id": ["A", "A", "A", "A"],
            "ticker": ["A", "A", "A", "A"],
            "raw_close": [10.0, 10.0, 10.0, 10.0],
            "total_return": [0.0, 0.0, 0.0, 0.0],
        }
    )
    actions = pd.DataFrame(
        {
            "security_id": ["A", "A"],
            "date": ["2020-01-06", "2020-01-07"],
            "event_type": ["split", "split"],
            "split_factor": [4.0, 0.2],
        }
    )
    reconstructed = reconstruct_nominal_close_from_splits(panel, actions)
    assert reconstructed["reconstructed_nominal_close"].tolist() == pytest.approx([8.0, 8.0, 2.0, 10.0])
    assert reconstructed["split_adjustment_multiplier"].tolist() == pytest.approx([0.8, 0.8, 0.2, 1.0])


def test_split_diagnostic_context_preserves_adjacent_close_observations_and_reason():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    adjusted = panel.copy()
    adjusted["raw_close"] = adjusted["adjusted_close"]
    context = split_diagnostic_context(adjusted, actions)
    row = context.iloc[0]
    assert row["classification"] == "likely_back_adjusted"
    assert row["close_m1"] == pytest.approx(127.0)
    assert row["close_0"] == pytest.approx(127.0)
    assert row["adj_close_m1"] == pytest.approx(127.0)
    assert "near 1.0" in row["classification_reason"]


def test_adjusted_close_return_is_split_safe_but_remains_candidate_source():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    split_return = panel.loc[panel["date"].eq(pd.Timestamp("2020-08-31")), "total_return"].iloc[0]
    assert split_return > -0.05
    audit = yahoo_candidate_audit(panel, actions)
    assert audit["total_return_source"] == "yahoo_adjusted_close"
    assert audit["split_classifications"]["consistent_with_nominal"] == 1


def test_wisesheets_export_parser_and_yahoo_comparison_classifies_disagreements(tmp_path: Path):
    panel, _ = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    wise = pd.DataFrame(
        {
            "date": ["2020-08-27", "2020-08-28", "2020-08-31"],
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "Close": [500.0, 508.02, 130.0],
            "AdjClose": [125.0, 127.0, 127.0],
            "Dividend": [0.0, 0.0, 0.0],
        }
    )
    wise_path = tmp_path / "wise.csv"
    wise.to_csv(wise_path, index=False)
    parsed = read_wisesheets_export(wise_path)
    comparison = compare_wisesheets_to_yahoo(panel, parsed)
    assert comparison["classifications"]["agree"] >= 1
    assert comparison["classifications"]["small_rounding_difference"] >= 1
    assert comparison["classifications"]["material_disagreement"] >= 1


def test_wisesheets_request_pack_can_prioritize_real_split_and_dividend_diagnostics():
    split_diagnostics = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC"],
            "split_date": ["2020-01-06", "2020-02-06", "2020-03-06"],
            "classification": ["likely_back_adjusted", "consistent_with_nominal", "ambiguous"],
        }
    )
    dividend_audit = pd.DataFrame(
        {
            "ticker": ["DDD", "EEE"],
            "date": ["2020-04-06", "2020-05-06"],
            "classification": ["material_dividend_mismatch", "candidate_adjusted_return_consistent"],
        }
    )
    pack = requested_wisesheets_test_pack(split_diagnostics, dividend_audit)
    purposes = set(pack["purpose"])
    assert "Yahoo split audit likely_back_adjusted" in purposes
    assert "Yahoo split audit consistent_with_nominal" in purposes
    assert "Yahoo split audit ambiguous" in purposes
    assert "Yahoo dividend-return anomaly" in purposes
    assert "ordinary Yahoo dividend cross-check" in purposes
    assert pack.loc[pack["ticker"].eq("AAA"), "start_date"].iloc[0] == "2020-01-01"
    assert pack.loc[pack["ticker"].eq("AAA"), "end_date"].iloc[0] == "2020-01-11"


def test_membership_price_join_terminal_disappearance_and_p3_incomplete_certification():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    later = pd.DataFrame(
        {
            "date": [pd.Timestamp("2020-09-03"), pd.Timestamp("2020-09-04")],
            "security_id": ["OTHER", "OTHER"],
            "ticker": ["OTH", "OTH"],
            "raw_close": [10.0, 11.0],
            "total_return": [0.0, 0.1],
        }
    )
    panel = pd.concat([panel, later], ignore_index=True)
    membership = normalize_membership(
        pd.DataFrame(
            {
                "security_id": ["AAPL-SEC", "MISSING", "OTHER"],
                "membership_start": ["2020-08-27", "2020-08-27", "2020-09-03"],
                "membership_end": ["2020-09-04", "2020-09-04", "2020-09-04"],
            }
        )
    )
    audit = audit_membership_price_join(panel, membership)
    assert audit["unmapped_membership_security_ids"] == ["MISSING"]
    assert audit["member_dates_lacking_price_rows"] > 0
    report = validate_dataset(panel, membership=membership, corporate_actions=actions)
    assert report.has_code("security_disappears_while_member", "unknown_membership_security_id")
    cert = certify_report(report, raw_close_semantics="unknown", total_return_source="yahoo_adjusted_close", membership_present=True)
    assert certify_for_experiment("P3", cert).status == Status.FAIL


def test_terminal_audit_right_censors_current_active_security_at_provider_edge():
    panel = pd.DataFrame(
        {
            "date": ["2026-08-07", "2026-08-10"],
            "security_id": ["A", "A"],
            "ticker": ["A", "A"],
            "raw_close": [10.0, 10.5],
            "total_return": [0.0, 0.05],
        }
    )
    membership = pd.DataFrame(
        {"security_id": ["A"], "membership_start": ["2026-01-01"], "membership_end": [None]}
    )
    audit = terminal_return_audit(
        panel,
        membership,
        as_of="2026-08-11T15:00:00Z",
        latest_available_provider_session="2026-08-10",
    )
    row = audit.iloc[0]
    assert row["latest_completed_exchange_session"] == "2026-08-10"
    assert row["latest_available_provider_session"] == "2026-08-10"
    assert row["terminal_status"] == "right_censored_active"
    assert not bool(row["blocking_risk"])


def test_terminal_audit_after_close_requires_provider_bar_before_advancing_edge():
    panel_yesterday = pd.DataFrame(
        {
            "date": ["2026-08-10"],
            "security_id": ["A"],
            "ticker": ["A"],
            "raw_close": [10.0],
            "total_return": [0.0],
        }
    )
    panel_today = pd.DataFrame(
        {
            "date": ["2026-08-10", "2026-08-11"],
            "security_id": ["A", "A"],
            "ticker": ["A", "A"],
            "raw_close": [10.0, 11.0],
            "total_return": [0.0, 0.1],
        }
    )
    membership = pd.DataFrame(
        {"security_id": ["A"], "membership_start": ["2026-01-01"], "membership_end": [None]}
    )
    without_bar = terminal_return_audit(
        panel_yesterday,
        membership,
        as_of="2026-08-11T21:00:00Z",
        latest_available_provider_session="2026-08-10",
    )
    with_bar = terminal_return_audit(
        panel_today,
        membership,
        as_of="2026-08-11T21:00:00Z",
        latest_available_provider_session="2026-08-11",
    )
    assert without_bar.iloc[0]["audit_end_session"] == "2026-08-10"
    assert with_bar.iloc[0]["audit_end_session"] == "2026-08-11"
    assert {without_bar.iloc[0]["terminal_status"], with_bar.iloc[0]["terminal_status"]} == {
        "right_censored_active"
    }


def test_terminal_audit_weekend_right_edge_and_historical_disappearance():
    current_panel = pd.DataFrame(
        {
            "date": ["2026-08-14"],
            "security_id": ["CUR"],
            "ticker": ["CUR"],
            "raw_close": [20.0],
            "total_return": [0.0],
        }
    )
    current_membership = pd.DataFrame(
        {"security_id": ["CUR"], "membership_start": ["2026-01-01"], "membership_end": [None]}
    )
    weekend = terminal_return_audit(
        current_panel,
        current_membership,
        as_of="2026-08-15T16:00:00Z",
        latest_available_provider_session="2026-08-14",
    )
    assert weekend.iloc[0]["terminal_status"] == "right_censored_active"

    historical_panel = pd.DataFrame(
        {
            "date": ["2020-01-02", "2020-01-03", "2020-01-06"],
            "security_id": ["A", "A", "B"],
            "ticker": ["A", "A", "B"],
            "raw_close": [10.0, 10.5, 30.0],
            "total_return": [0.0, 0.05, 0.0],
        }
    )
    historical_membership = pd.DataFrame(
        {
            "security_id": ["A", "B"],
            "membership_start": ["2020-01-02", "2020-01-06"],
            "membership_end": ["2020-01-08", "2020-01-06"],
        }
    )
    historical = terminal_return_audit(
        historical_panel,
        historical_membership,
        as_of="2026-08-11T21:00:00Z",
        latest_available_provider_session="2020-01-06",
    )
    assert historical.loc[historical["security_id"].eq("A"), "terminal_status"].iloc[0] == "disappears_while_member"


def test_ticker_only_security_master_keeps_stable_identifier_unverified():
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    membership = pd.DataFrame(
        {"security_id": ["AAPL-SEC"], "membership_start": ["2020-08-28"], "membership_end": ["2020-09-02"]}
    )
    master = pd.DataFrame(
        {
            "security_id": ["AAPL-SEC"],
            "ticker": ["AAPL"],
            "effective_start": ["2020-08-27"],
            "effective_end": [None],
            "source": ["ticker_only_fixture"],
        }
    )
    report = validate_dataset(panel, membership=membership, security_master=master, corporate_actions=actions)
    assert report.has_code("stable_identifier_unverified")
    cert = certify_report(
        report,
        raw_close_semantics="verified_nominal",
        total_return_source="reconstructed",
        membership_present=True,
        security_master_present=True,
    )
    assert cert.dimensions["stable_identifiers_sufficient"] == Status.UNVERIFIED


def test_snapshot_hash_reproducibility_for_fixture_snapshot(tmp_path: Path):
    panel, actions = normalize_yahoo_history(_yahoo_raw(), security_id="AAPL-SEC", ticker="AAPL")
    membership = pd.DataFrame(
        {"security_id": ["AAPL-SEC"], "membership_start": ["2020-08-27"], "membership_end": ["2020-09-02"]}
    )
    snapshot = tmp_path / "snapshot"
    write_snapshot(
        snapshot,
        daily_panel=panel,
        membership=membership,
        corporate_actions=actions,
        provider="fixture_yahoo",
        raw_close_semantics="verified_nominal",
        total_return_source="yahoo_adjusted_close",
    )
    assert hash_snapshot(snapshot) == hash_snapshot(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["membership_trading_calendar"]["calendar"] == "XNYS"
    assert manifest["membership_trading_calendar"]["package"] == "exchange-calendars"


def test_phase2b_cli_demos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runner = CliRunner()
    events_out = tmp_path / "events.csv"
    parse = runner.invoke(
        app,
        ["data", "parse-wikipedia-events", "--html", str(FIXTURE_HTML), "--out", str(events_out)],
    )
    assert parse.exit_code == 0, parse.output
    assert json.loads(parse.output)["events"] == 6

    completeness = runner.invoke(app, ["data", "event-completeness", "--events", str(events_out)])
    assert completeness.exit_code == 0, completeness.output
    assert json.loads(completeness.output)["unresolved"] == 6

    anchor = tmp_path / "anchor.csv"
    pd.DataFrame({"security_id": ["RHT", "HRB", "COTY"]}).to_csv(anchor, index=False)
    membership_out = tmp_path / "membership.csv"
    membership_metadata_out = tmp_path / "membership_metadata.json"
    reconstruct = runner.invoke(
        app,
        [
            "data",
            "reconstruct-membership",
            "--events",
            str(events_out),
            "--anchor-members",
            str(anchor),
            "--anchor-date",
            "2020-06-19",
            "--start-date",
            "2020-06-19",
            "--end-date",
            "2020-09-22",
            "--out",
            str(membership_out),
            "--metadata-out",
            str(membership_metadata_out),
            "--include-unverified",
        ],
    )
    assert reconstruct.exit_code == 0, reconstruct.output
    reconstruction_metadata = json.loads(membership_metadata_out.read_text(encoding="utf-8"))
    assert reconstruction_metadata["diagnostics"]["trading_calendar"]["calendar"] == "XNYS"

    yahoo_raw = tmp_path / "yahoo.csv"
    _yahoo_raw().to_csv(yahoo_raw, index=False)
    panel_out = tmp_path / "panel.csv"
    actions_out = tmp_path / "actions.csv"
    normalize_yahoo = runner.invoke(
        app,
        [
            "data",
            "normalize-yahoo",
            "--raw",
            str(yahoo_raw),
            "--security-id",
            "AAPL-SEC",
            "--ticker",
            "AAPL",
            "--panel-out",
            str(panel_out),
            "--actions-out",
            str(actions_out),
        ],
    )
    assert normalize_yahoo.exit_code == 0, normalize_yahoo.output

    audit_yahoo = runner.invoke(app, ["data", "audit-yahoo", "--panel", str(panel_out), "--corporate-actions", str(actions_out)])
    assert audit_yahoo.exit_code == 0, audit_yahoo.output

    wise = tmp_path / "wise.csv"
    pd.DataFrame({"date": ["2020-08-27"], "ticker": ["AAPL"], "Close": [500.0], "AdjClose": [125.0]}).to_csv(wise, index=False)
    compare = runner.invoke(app, ["data", "compare-wisesheets", "--yahoo-panel", str(panel_out), "--wisesheets-export", str(wise)])
    assert compare.exit_code == 0, compare.output

    aliases = tmp_path / "aliases.csv"
    pd.DataFrame({"security_id": ["AAPL-SEC"], "provider_symbol": ["AAPL"]}).to_csv(aliases, index=False)
    state_out = tmp_path / "state.csv"
    plan = runner.invoke(app, ["data", "plan-yahoo", "--aliases", str(aliases), "--state-out", str(state_out)])
    assert plan.exit_code == 0, plan.output
    assert json.loads(plan.output)["pending"] == 1

    raw_dir = tmp_path / "yahoo_raw"
    monkeypatch.setattr(cli_module, "download_yahoo_symbol", lambda symbol, start, end: _yahoo_raw().set_index("Date"))
    acquire = runner.invoke(
        app,
        [
            "data",
            "acquire-yahoo",
            "--state",
            str(state_out),
            "--raw-dir",
            str(raw_dir),
            "--start-date",
            "2020-01-01",
            "--max-symbols",
            "1",
            "--retry-attempts",
            "1",
            "--retry-sleep",
            "0",
            "--symbol-sleep",
            "0",
        ],
    )
    assert acquire.exit_code == 0, acquire.output
    acquire_payload = json.loads(acquire.output)
    assert acquire_payload["complete"] == 1
    assert (raw_dir / "AAPL.csv").exists()
    assert (raw_dir / "metadata.json").exists()
    raw_metadata = json.loads((raw_dir / "metadata.json").read_text(encoding="utf-8"))
    assert raw_metadata["provider_metadata"]["acquisition_library"] == "yfinance"
    assert raw_metadata["provider_metadata"]["settings"]["auto_adjust"] is False
    assert raw_metadata["provider_metadata"]["settings"]["repair"] is False
