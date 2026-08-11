from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from .backtest import run_backtest
from .config import load_experiment, load_strategy
from .data.acquisition import write_raw_acquisition_metadata
from .data.certification import certify_for_experiment, certify_report
from .data.identity import stable_security_id_from_event
from .data.io import read_table, write_json
from .data.join_audit import audit_membership_price_join
from .data.membership_reconstruction import reconstruct_membership_from_change_events
from .data.normalize import (
    normalize_corporate_actions,
    normalize_daily_panel,
    normalize_from_mapping,
    normalize_membership,
    normalize_security_master,
)
from .data.phase2d_membership import write_phase2d_reports
from .data.phase2e_snapshots import write_phase2e_reports
from .data.snapshot import hash_snapshot, inspect_snapshot, write_snapshot
from .data.source_discovery import source_discovery_report
from .data.sp500_events import (
    event_completeness_report,
    event_ledger_to_change_events,
    gap_register_from_events,
    merge_event_evidence,
    parse_wikipedia_current_constituents,
    parse_wikipedia_file,
    read_event_ledger,
    write_event_ledger,
)
from .data.trading_calendar import TradingCalendar
from .data.validation import (
    audit_reconstructed_nominal_close,
    split_diagnostic_context,
    split_diagnostic_summary,
    stratified_split_diagnostic_sample,
    terminal_return_audit,
    validate_dataset,
)
from .data.wisesheets_crosscheck import (
    compare_wisesheets_to_yahoo,
    read_wisesheets_export,
    requested_wisesheets_test_pack,
)
from .data.yahoo_provider import (
    YahooDownloadResult,
    classify_yahoo_error,
    download_yahoo_symbol,
    initial_yahoo_acquisition_state,
    pending_yahoo_symbols,
    read_yahoo_history,
    read_yahoo_state,
    record_yahoo_result,
    write_yahoo_settings,
    write_yahoo_state,
    yahoo_acquisition_summary,
    yahoo_candidate_audit,
    yahoo_provider_metadata,
)
from .providers.wisesheets import WiseSheetsCapabilityGate

app = typer.Typer(no_args_is_help=True)
data_app = typer.Typer(no_args_is_help=True)
app.add_typer(data_app, name="data")


def _read_table(path: Path) -> pd.DataFrame:
    return read_table(path)


def _series_summary(result) -> dict:
    return {
        "metrics": result.metrics,
        "sharpe_ci_95": result.sharpe_ci_95,
        "audit": result.audit,
    }


def _print_json(payload: dict) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _parse_map(raw: str | None) -> dict[str, str] | None:
    if raw is None:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise typer.BadParameter("column map must be a JSON object of source-column to target-column strings")
    return data


def _load_optional(path: Path | None) -> pd.DataFrame | None:
    return _read_table(path) if path is not None and path.exists() else None


def _write_table(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    else:
        raise typer.BadParameter(f"Unsupported output extension for {path}")


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _load_snapshot_manifest(snapshot: Path) -> dict:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    return manifest


def _certify_snapshot_for_experiment(snapshot: Path, manifest: dict, experiment_id: str) -> None:
    membership_path = snapshot / "membership.parquet"
    security_master_path = snapshot / "security_master.parquet"
    corporate_actions_path = snapshot / "corporate_actions.parquet"
    report = validate_dataset(
        _read_table(snapshot / "daily_panel.parquet"),
        _load_optional(membership_path),
        _load_optional(security_master_path),
        _load_optional(corporate_actions_path),
    )
    certification = certify_report(
        report,
        raw_close_semantics=manifest.get("raw_close_semantics", "unknown"),
        total_return_source=manifest.get("total_return_source", "unknown"),
        membership_present=membership_path.exists(),
        security_master_present=security_master_path.exists(),
        provider_capabilities=manifest.get("provider_capabilities") or None,
    )
    experiment_certification = certify_for_experiment(experiment_id, certification)
    if experiment_certification.status.value != "PASS":
        blocking = ", ".join(experiment_certification.blocking_issues)
        raise typer.BadParameter(f"snapshot is not certified for {experiment_id}: {blocking}")


@app.command("spec-check")
def spec_check(
    strategy_path: Annotated[Path, typer.Option()] = Path("spec/paper_strategy.yaml"),
) -> None:
    cfg = load_strategy(strategy_path)
    typer.echo(cfg.model_dump_json(indent=2))


@app.command("wisesheets-capabilities")
def wisesheets_capabilities() -> None:
    gate = WiseSheetsCapabilityGate()
    _print_json(gate.report())


@app.command("run")
def run(
    experiment: Annotated[Path, typer.Option(exists=True)],
    panel: Annotated[Path | None, typer.Option(exists=True)] = None,
    membership: Annotated[Path | None, typer.Option()] = None,
    snapshot: Annotated[Path | None, typer.Option(exists=True)] = None,
    strategy_path: Annotated[Path, typer.Option()] = Path("spec/paper_strategy.yaml"),
    out_dir: Annotated[Path, typer.Option()] = Path("reports/generated"),
) -> None:
    strategy = load_strategy(strategy_path)
    exp = load_experiment(experiment)
    if snapshot is not None:
        manifest = _load_snapshot_manifest(snapshot)
        _certify_snapshot_for_experiment(snapshot, manifest, exp.id)
        panel_df = _read_table(snapshot / "daily_panel.parquet")
        membership_path = snapshot / "membership.parquet"
        membership_df = _read_table(membership_path) if membership_path.exists() else None
    else:
        if panel is None:
            raise typer.BadParameter("Either --panel or --snapshot is required")
        panel_df = _read_table(panel)
        membership_df = _read_table(membership) if membership else None
    result = run_backtest(panel_df, strategy, exp, membership_df)

    out_dir.mkdir(parents=True, exist_ok=True)
    result.daily.to_parquet(out_dir / f"{exp.id}_daily.parquet", index=False)
    result.ledger.to_parquet(out_dir / f"{exp.id}_ledger.parquet", index=False)
    result.yearly.to_csv(out_dir / f"{exp.id}_yearly.csv", index=False)
    if result.scaled is not None:
        result.scaled.daily.to_parquet(out_dir / f"{exp.id}_scaled_daily.parquet", index=False)
        result.scaled.ledger.to_parquet(out_dir / f"{exp.id}_scaled_ledger.parquet", index=False)
        result.scaled.yearly.to_csv(out_dir / f"{exp.id}_scaled_yearly.csv", index=False)
    summary = {
        "experiment": exp.id,
        "unscaled": _series_summary(result.unscaled),
        "scaled": _series_summary(result.scaled) if result.scaled is not None else None,
        "scaling_windows": result.scaling_windows,
        # Backward-compatible top-level fields are the unscaled audit trail.
        "metrics": result.metrics,
        "sharpe_ci_95": result.sharpe_ci_95,
        "audit": result.audit,
    }
    (out_dir / f"{exp.id}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _print_json(summary)


@data_app.command("wisesheets-capabilities")
def data_wisesheets_capabilities() -> None:
    """Report WiseSheets capability status without making network calls or printing secrets."""
    gate = WiseSheetsCapabilityGate()
    _print_json(gate.report())


@data_app.command("discover-sources")
def data_discover_sources(
    output_format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    """Report the offline Phase 2A source discovery matrix."""
    payload = source_discovery_report()
    if output_format == "json":
        _print_json(payload)
        return
    if output_format != "text":
        raise typer.BadParameter("--format must be json or text")
    typer.echo("Phase 2A source discovery")
    typer.echo(f"Access date: {payload['access_date']}")
    typer.echo(
        "Recommended defensible path: "
        f"{payload['chosen_sources']['recommended_defensible_path']}"
    )
    for item in payload["assessments"]:
        typer.echo(f"- {item['source_name']}: {item['recommendation']}")


@data_app.command("acquire-wikipedia-events")
def data_acquire_wikipedia_events(
    raw_dir: Annotated[Path, typer.Option()],
    source_url: Annotated[str, typer.Option()] = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    timeout: Annotated[float, typer.Option()] = 30.0,
    user_agent: Annotated[str, typer.Option()] = "sharpe-replication/0.1 forensic-data-acquisition",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Fetch the Wikipedia S&P 500 page as raw seed input and parse its change table."""
    import httpx

    raw_dir.mkdir(parents=True, exist_ok=True)
    html_path = raw_dir / "wikipedia_sp500.html"
    if html_path.exists() and not force:
        raise typer.BadParameter(f"Raw Wikipedia snapshot already exists: {html_path}")
    response = httpx.get(source_url, timeout=timeout, follow_redirects=True, headers={"User-Agent": user_agent})
    response.raise_for_status()
    html_path.write_text(response.text, encoding="utf-8")
    metadata = write_raw_acquisition_metadata(
        raw_dir,
        provider="wikipedia",
        request_type="sp500_membership_seed",
        requested_date_range={"start": None, "end": None},
        source_urls=[source_url],
        documentation_urls=["https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"],
        response_count=1,
        limitations=["Wikipedia is an unverified event seed, not an authoritative PIT database."],
        force=force,
    )
    events = parse_wikipedia_file(html_path, source_url=source_url)
    event_path = raw_dir / "wikipedia_events.csv"
    write_event_ledger(event_path, events)
    _print_json({"raw_html": str(html_path), "event_ledger": str(event_path), "events": len(events), "metadata": metadata})


@data_app.command("parse-wikipedia-events")
def data_parse_wikipedia_events(
    html: Annotated[Path, typer.Option(exists=True)],
    source_url: Annotated[str, typer.Option()] = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Parse a frozen Wikipedia HTML snapshot into an unverified S&P 500 event ledger."""
    events = parse_wikipedia_file(html, source_url=source_url)
    if out is not None:
        write_event_ledger(out, events)
    _print_json({"events": len(events), "wikipedia_seed_events": len(events), "out": str(out) if out else None})


@data_app.command("parse-wikipedia-anchor")
def data_parse_wikipedia_anchor(
    html: Annotated[Path, typer.Option(exists=True)],
    source_url: Annotated[str, typer.Option()] = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Parse the frozen Wikipedia current table as an unverified provisional anchor."""
    anchor = parse_wikipedia_current_constituents(html.read_text(encoding="utf-8"), source_url=source_url)
    anchor["security_id"] = [
        stable_security_id_from_event(row.ticker, row.company_name)
        for row in anchor.itertuples(index=False)
    ]
    columns = ["security_id", *[column for column in anchor.columns if column != "security_id"]]
    anchor = anchor[columns]
    if out is not None:
        _write_table(out, anchor)
    _print_json(
        {
            "anchor_rows": len(anchor),
            "unique_tickers": int(anchor["ticker"].nunique()),
            "verification_status": "UNVERIFIED",
            "source_tier": "WIKIPEDIA_ANCHOR",
            "out": str(out) if out else None,
        }
    )


@data_app.command("verify-sp500-events")
def data_verify_sp500_events(
    seed_events: Annotated[Path, typer.Option(exists=True)],
    evidence_events: Annotated[Path, typer.Option(exists=True)],
    out: Annotated[Path, typer.Option()],
    discrepancies_out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Overlay primary/archived/fallback evidence rows on a seed event ledger."""
    merged, discrepancies = merge_event_evidence(read_event_ledger(seed_events), read_event_ledger(evidence_events))
    write_event_ledger(out, merged)
    if discrepancies_out is not None:
        _write_table(discrepancies_out, discrepancies)
    _print_json({"events": len(merged), "discrepancies": len(discrepancies), "out": str(out)})


@data_app.command("event-completeness")
def data_event_completeness(
    events: Annotated[Path, typer.Option(exists=True)],
    gaps_out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Report transparent event verification counts and unresolved primary-source gaps."""
    ledger = read_event_ledger(events)
    gaps = gap_register_from_events(ledger)
    if gaps_out is not None:
        _write_table(gaps_out, gaps)
    _print_json(event_completeness_report(ledger, gaps))


def _anchor_members(path: Path) -> list[str]:
    frame = _read_table(path)
    column = "security_id" if "security_id" in frame.columns else frame.columns[0]
    return [str(value) for value in frame[column].dropna()]


@data_app.command("reconstruct-membership")
def data_reconstruct_membership(
    events: Annotated[Path, typer.Option(exists=True)],
    anchor_members: Annotated[Path, typer.Option(exists=True)],
    anchor_date: Annotated[str, typer.Option()],
    start_date: Annotated[str, typer.Option()],
    end_date: Annotated[str, typer.Option()],
    out: Annotated[Path, typer.Option()],
    include_unverified: Annotated[bool, typer.Option()] = False,
    provisional_security_ids: Annotated[bool, typer.Option()] = False,
    metadata_out: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Reconstruct PIT membership from event ledger plus an explicit anchor set."""
    calendar = TradingCalendar.xnys()
    change_events = event_ledger_to_change_events(
        read_event_ledger(events),
        calendar=calendar,
        include_unverified=include_unverified,
        provisional_security_ids=provisional_security_ids,
        start_date=min(pd.Timestamp(start_date).normalize(), pd.Timestamp(anchor_date).normalize()),
        end_date=max(pd.Timestamp(end_date).normalize(), pd.Timestamp(anchor_date).normalize()),
    )
    result = reconstruct_membership_from_change_events(
        change_events,
        anchor_date=anchor_date,
        anchor_members=_anchor_members(anchor_members),
        start_date=start_date,
        end_date=end_date,
        source="open_reconstruction",
        calendar=calendar,
    )
    _write_table(out, result.membership)
    if metadata_out is not None:
        write_json(metadata_out, result.to_dict())
    _print_json({"out": str(out), **result.to_dict()})


@data_app.command("phase2d-membership-report")
def data_phase2d_membership_report(
    events: Annotated[Path, typer.Option(exists=True)],
    anchor: Annotated[Path, typer.Option(exists=True)],
    wikipedia_html: Annotated[Path, typer.Option(exists=True)],
    membership: Annotated[Path, typer.Option(exists=True)],
    out_dir: Annotated[Path, typer.Option()] = Path("reports/generated/phase2d"),
    source_cache_dir: Annotated[Path, typer.Option()] = Path("data/raw/sp500_membership/phase2d_source_cache"),
    yahoo_state: Annotated[Path | None, typer.Option(exists=True)] = None,
    start_date: Annotated[str, typer.Option()] = "2004-01-01",
    anchor_date: Annotated[str, typer.Option()] = "2026-08-11",
    end_date: Annotated[str, typer.Option()] = "2026-08-11",
    max_source_fetches: Annotated[int, typer.Option(min=0)] = 0,
    source_sleep_seconds: Annotated[float, typer.Option(min=0.0)] = 0.25,
    force_sources: Annotated[bool, typer.Option()] = False,
) -> None:
    """Generate Phase 2D membership/identity reports without running strategy performance."""
    summary = write_phase2d_reports(
        events_path=events,
        anchor_path=anchor,
        wikipedia_html_path=wikipedia_html,
        membership_path=membership,
        out_dir=out_dir,
        source_cache_dir=source_cache_dir,
        yahoo_state_path=yahoo_state,
        start_date=start_date,
        anchor_date=anchor_date,
        end_date=end_date,
        max_source_fetches=max_source_fetches,
        source_sleep_seconds=source_sleep_seconds,
        force_sources=force_sources,
    )
    _print_json(summary)


@data_app.command("phase2e-snapshot-report")
def data_phase2e_snapshot_report(
    events: Annotated[Path, typer.Option(exists=True)],
    anchor: Annotated[Path, typer.Option(exists=True)],
    phase2d_dir: Annotated[Path, typer.Option(exists=True)] = Path("reports/generated/phase2d"),
    out_dir: Annotated[Path, typer.Option()] = Path("reports/generated/phase2e"),
    snapshot_cache_dir: Annotated[Path, typer.Option()] = Path("data/raw/sp500_membership/phase2e_snapshot_cache"),
    identity_resolutions: Annotated[Path | None, typer.Option()] = None,
    yahoo_state: Annotated[Path | None, typer.Option(exists=True)] = None,
    start_date: Annotated[str, typer.Option()] = "2004-01-01",
    anchor_date: Annotated[str, typer.Option()] = "2026-08-11",
    end_date: Annotated[str, typer.Option()] = "2026-08-11",
    max_snapshot_downloads: Annotated[int, typer.Option(min=0)] = 0,
    snapshot_sleep_seconds: Annotated[float, typer.Option(min=0.0)] = 0.25,
    force_snapshots: Annotated[bool, typer.Option()] = False,
    snapshot_timeout: Annotated[float, typer.Option(min=1.0)] = 20.0,
) -> None:
    """Generate Phase 2E snapshot-triangulation reports without running strategy performance."""
    summary = write_phase2e_reports(
        events_path=events,
        anchor_path=anchor,
        phase2d_dir=phase2d_dir,
        out_dir=out_dir,
        snapshot_cache_dir=snapshot_cache_dir,
        identity_resolutions_path=identity_resolutions,
        yahoo_state_path=yahoo_state,
        start_date=start_date,
        anchor_date=anchor_date,
        end_date=end_date,
        max_snapshot_downloads=max_snapshot_downloads,
        snapshot_sleep_seconds=snapshot_sleep_seconds,
        force_snapshots=force_snapshots,
        snapshot_timeout=snapshot_timeout,
    )
    _print_json(summary)


@data_app.command("plan-yahoo")
def data_plan_yahoo(
    aliases: Annotated[Path, typer.Option(exists=True)],
    state_out: Annotated[Path, typer.Option()],
) -> None:
    """Create a resumable Yahoo acquisition state file from date-aware symbol aliases."""
    state = initial_yahoo_acquisition_state(_read_table(aliases))
    write_yahoo_state(state_out, state)
    _print_json(yahoo_acquisition_summary(state))


@data_app.command("acquire-yahoo")
def data_acquire_yahoo(
    state: Annotated[Path, typer.Option(exists=True)],
    raw_dir: Annotated[Path, typer.Option()],
    start_date: Annotated[str, typer.Option()],
    end_date: Annotated[str | None, typer.Option()] = None,
    max_symbols: Annotated[int, typer.Option()] = 0,
    retry_attempts: Annotated[int, typer.Option(min=1)] = 3,
    retry_sleep: Annotated[float, typer.Option(min=0.0)] = 1.0,
    symbol_sleep: Annotated[float, typer.Option(min=0.0)] = 0.0,
    retry_permanent: Annotated[bool, typer.Option()] = False,
    dry_run: Annotated[bool, typer.Option()] = False,
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Download pending Yahoo histories into a raw cache and update acquisition state.

    The command uses the fixed forensic yfinance settings in yahoo_provider.py. Tests exercise this
    through mocks/local fixtures; live network acquisition is operator-run only.
    """
    state_frame = read_yahoo_state(state)
    symbols = pending_yahoo_symbols(state_frame, include_permanent=retry_permanent)
    if max_symbols > 0:
        symbols = symbols[:max_symbols]
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_yahoo_settings(raw_dir / "settings.json")
    if dry_run:
        _print_json({"dry_run": True, "symbols_to_acquire": symbols, **yahoo_acquisition_summary(state_frame)})
        return

    acquired = 0
    skipped_cached = 0
    for symbol in symbols:
        raw_path = raw_dir / f"{_safe_filename(symbol)}.csv"
        if raw_path.exists() and not force:
            cached = pd.read_csv(raw_path)
            state_frame = record_yahoo_result(
                state_frame,
                provider_symbol=symbol,
                result=YahooDownloadResult(status="complete", rows=len(cached)),
            )
            write_yahoo_state(state, state_frame)
            skipped_cached += 1
            continue

        result = YahooDownloadResult(status="failed_retryable", error="not attempted", retryable=True)
        for attempt in range(retry_attempts):
            try:
                raw = download_yahoo_symbol(symbol, start=start_date, end=end_date)
                if raw.empty:
                    status, retryable = classify_yahoo_error("No data found for symbol")
                    result = YahooDownloadResult(status=status, rows=0, error="No data found for symbol", retryable=retryable)
                else:
                    raw.reset_index().to_csv(raw_path, index=False)
                    result = YahooDownloadResult(status="complete", rows=len(raw))
                    acquired += 1
                break
            except (RuntimeError, OSError, ValueError) as exc:  # pragma: no cover - live acquisition path
                status, retryable = classify_yahoo_error(str(exc))
                result = YahooDownloadResult(status=status, rows=0, error=str(exc), retryable=retryable)
                if not retryable or attempt == retry_attempts - 1:
                    break
                time.sleep(retry_sleep * (2**attempt))
        state_frame = record_yahoo_result(state_frame, provider_symbol=symbol, result=result)
        write_yahoo_state(state, state_frame)
        if symbol_sleep and symbol != symbols[-1]:
            time.sleep(symbol_sleep)

    metadata = write_raw_acquisition_metadata(
        raw_dir,
        provider="yahoo",
        request_type="daily_history",
        requested_date_range={"start": start_date, "end": end_date},
        requested_symbols=symbols,
        documentation_urls=["https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html"],
        response_count=acquired + skipped_cached,
        limitations=[
            "Yahoo is ticker-centric and may not resolve delisted or renamed historical securities.",
            "Close is preserved as yahoo_close and is not promoted to certified raw_close.",
        ],
        extra_metadata={"provider_metadata": yahoo_provider_metadata()},
        force=True,
    )
    _print_json({"raw_dir": str(raw_dir), "acquired": acquired, "skipped_cached": skipped_cached, "metadata": metadata, **yahoo_acquisition_summary(state_frame)})


@data_app.command("normalize-yahoo")
def data_normalize_yahoo(
    raw: Annotated[Path, typer.Option(exists=True)],
    security_id: Annotated[str, typer.Option()],
    ticker: Annotated[str, typer.Option()],
    panel_out: Annotated[Path, typer.Option()],
    actions_out: Annotated[Path, typer.Option()],
    source_symbol: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Normalize a raw Yahoo/yfinance CSV or Parquet response without live network calls."""
    panel, actions = read_yahoo_history(raw, security_id=security_id, ticker=ticker, source_symbol=source_symbol)
    _write_table(panel_out, panel)
    _write_table(actions_out, actions)
    _print_json({"panel_rows": len(panel), "action_rows": len(actions), "panel_out": str(panel_out), "actions_out": str(actions_out)})


@data_app.command("audit-yahoo")
def data_audit_yahoo(
    panel: Annotated[Path, typer.Option(exists=True)],
    corporate_actions: Annotated[Path, typer.Option(exists=True)],
) -> None:
    """Audit candidate Yahoo Close/Adj Close/action semantics without certifying them by claim."""
    _print_json(yahoo_candidate_audit(_read_table(panel), _read_table(corporate_actions)))


@data_app.command("split-diagnostics")
def data_split_diagnostics(
    panel: Annotated[Path, typer.Option(exists=True)],
    corporate_actions: Annotated[Path, typer.Option(exists=True)],
    sample_out: Annotated[Path | None, typer.Option()] = None,
    summary_out: Annotated[Path | None, typer.Option()] = None,
    price_column: Annotated[str, typer.Option()] = "raw_close",
    per_class: Annotated[int, typer.Option(min=1)] = 20,
) -> None:
    """Write stratified split diagnostics without changing raw-close certification rules."""
    context = split_diagnostic_context(
        _read_table(panel),
        _read_table(corporate_actions),
        price_column=price_column,
    )
    sample = stratified_split_diagnostic_sample(context, per_class=per_class)
    summary = split_diagnostic_summary(context)
    if sample_out is not None:
        _write_table(sample_out, sample)
    if summary_out is not None:
        write_json(summary_out, summary)
    _print_json(
        {
            "events": len(context),
            "sample_rows": len(sample),
            "classifications": summary.get("by_classification", {}),
            "sample_out": str(sample_out) if sample_out else None,
            "summary_out": str(summary_out) if summary_out else None,
        }
    )


@data_app.command("audit-reconstructed-nominal-close")
def data_audit_reconstructed_nominal_close(
    panel: Annotated[Path, typer.Option(exists=True)],
    corporate_actions: Annotated[Path, typer.Option(exists=True)],
    audit_out: Annotated[Path | None, typer.Option()] = None,
    close_column: Annotated[str, typer.Option()] = "raw_close",
) -> None:
    """Audit a separate split-deadjusted nominal-close candidate."""
    audit = audit_reconstructed_nominal_close(
        _read_table(panel),
        _read_table(corporate_actions),
        close_column=close_column,
    )
    if audit_out is not None:
        _write_table(audit_out, audit)
    counts = audit["classification"].value_counts().to_dict() if len(audit) else {}
    _print_json(
        {
            "events": len(audit),
            "classifications": {str(k): int(v) for k, v in counts.items()},
            "audit_out": str(audit_out) if audit_out else None,
        }
    )


@data_app.command("compare-wisesheets")
def data_compare_wisesheets(
    yahoo_panel: Annotated[Path, typer.Option(exists=True)],
    wisesheets_export: Annotated[Path, typer.Option(exists=True)],
) -> None:
    """Compare local WiseSheets export values against Yahoo candidates without overriding either."""
    wise = read_wisesheets_export(wisesheets_export)
    _print_json(compare_wisesheets_to_yahoo(_read_table(yahoo_panel), wise))


@data_app.command("wisesheets-test-pack")
def data_wisesheets_test_pack(
    out: Annotated[Path, typer.Option()],
    split_diagnostics: Annotated[Path | None, typer.Option(exists=True)] = None,
    dividend_audit: Annotated[Path | None, typer.Option(exists=True)] = None,
) -> None:
    """Write a compact WiseSheets export request pack for data-semantics cross-checks."""
    pack = requested_wisesheets_test_pack(
        split_diagnostics=_read_table(split_diagnostics) if split_diagnostics else None,
        dividend_audit=_read_table(dividend_audit) if dividend_audit else None,
    )
    _write_table(out, pack)
    _print_json({"out": str(out), "rows": len(pack)})


@data_app.command("normalize")
def data_normalize(
    snapshot_dir: Annotated[Path, typer.Argument()],
    daily_source: Annotated[Path, typer.Option(exists=True)],
    daily_map: Annotated[str, typer.Option(help="JSON source-column -> normalized-column map")],
    membership_source: Annotated[Path | None, typer.Option(exists=True)] = None,
    membership_map: Annotated[str | None, typer.Option()] = None,
    security_master_source: Annotated[Path | None, typer.Option(exists=True)] = None,
    security_master_map: Annotated[str | None, typer.Option()] = None,
    corporate_actions_source: Annotated[Path | None, typer.Option(exists=True)] = None,
    corporate_actions_map: Annotated[str | None, typer.Option()] = None,
    provider: Annotated[str, typer.Option()] = "local",
    source_description: Annotated[str, typer.Option()] = "local file import",
    raw_close_semantics: Annotated[str, typer.Option()] = "unknown",
    total_return_source: Annotated[str, typer.Option()] = "unknown",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    daily = normalize_from_mapping(
        _read_table(daily_source),
        "daily_panel",
        _parse_map(daily_map) or {},
        provider,
    )
    membership_df = (
        normalize_from_mapping(
            _read_table(membership_source),
            "membership",
            _parse_map(membership_map) or {},
            provider,
        )
        if membership_source is not None
        else None
    )
    security_master_df = (
        normalize_from_mapping(
            _read_table(security_master_source),
            "security_master",
            _parse_map(security_master_map) or {},
            provider,
        )
        if security_master_source is not None
        else None
    )
    corporate_actions_df = (
        normalize_from_mapping(
            _read_table(corporate_actions_source),
            "corporate_actions",
            _parse_map(corporate_actions_map) or {},
            provider,
        )
        if corporate_actions_source is not None
        else None
    )
    manifest = write_snapshot(
        snapshot_dir,
        daily_panel=daily,
        membership=membership_df,
        security_master=security_master_df,
        corporate_actions=corporate_actions_df,
        provider=provider,
        source_description=source_description,
        raw_close_semantics=raw_close_semantics,
        total_return_source=total_return_source,
        known_limitations=[],
        force=force,
    )
    _print_json(manifest)


@data_app.command("validate")
def data_validate(
    panel: Annotated[Path, typer.Option(exists=True)],
    membership: Annotated[Path | None, typer.Option(exists=True)] = None,
    security_master: Annotated[Path | None, typer.Option(exists=True)] = None,
    corporate_actions: Annotated[Path | None, typer.Option(exists=True)] = None,
) -> None:
    report = validate_dataset(
        normalize_daily_panel(_read_table(panel)),
        normalize_membership(_read_table(membership)) if membership else None,
        normalize_security_master(_read_table(security_master)) if security_master else None,
        normalize_corporate_actions(_read_table(corporate_actions)) if corporate_actions else None,
    )
    payload = report.to_dict()
    _print_json(payload)
    if payload["status"] == "FAIL":
        raise typer.Exit(1)


@data_app.command("audit-coverage")
def data_audit_coverage(
    panel: Annotated[Path, typer.Option(exists=True)],
    membership: Annotated[Path, typer.Option(exists=True)],
    security_master: Annotated[Path | None, typer.Option(exists=True)] = None,
) -> None:
    """Audit PIT membership to daily-panel coverage without computing strategy performance."""
    payload = audit_membership_price_join(
        _read_table(panel),
        _read_table(membership),
        _read_table(security_master) if security_master else None,
    )
    _print_json(payload)
    if (
        payload["unmapped_membership_security_ids"]
        or payload["member_dates_lacking_price_rows"]
        or payload["member_dates_lacking_raw_close"]
        or payload["member_dates_lacking_total_return"]
    ):
        raise typer.Exit(1)


@data_app.command("audit-terminal")
def data_audit_terminal(
    panel: Annotated[Path, typer.Option(exists=True)],
    membership: Annotated[Path, typer.Option(exists=True)],
    corporate_actions: Annotated[Path | None, typer.Option(exists=True)] = None,
    out: Annotated[Path | None, typer.Option()] = None,
    as_of: Annotated[str | None, typer.Option(help="Acquisition timestamp; naive values are UTC.")] = None,
    latest_available_provider_session: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Classify terminal coverage using XNYS completed-session and provider-availability edges."""
    audit = terminal_return_audit(
        _read_table(panel),
        _read_table(membership),
        _read_table(corporate_actions) if corporate_actions else None,
        as_of=as_of,
        latest_available_provider_session=latest_available_provider_session,
    )
    if out is not None:
        _write_table(out, audit)
    status_counts = audit["terminal_status"].value_counts().to_dict() if len(audit) else {}
    _print_json(
        {
            "rows": len(audit),
            "terminal_status_counts": {str(k): int(v) for k, v in status_counts.items()},
            "blocking_risks": int(audit["blocking_risk"].sum()) if len(audit) else 0,
            "out": str(out) if out else None,
        }
    )


@data_app.command("inspect")
def data_inspect(snapshot_dir: Annotated[Path, typer.Argument(exists=True)]) -> None:
    _print_json(inspect_snapshot(snapshot_dir))


@data_app.command("hash")
def data_hash(snapshot_dir: Annotated[Path, typer.Argument(exists=True)]) -> None:
    _print_json({"snapshot_id": snapshot_dir.name, "hashes": hash_snapshot(snapshot_dir)})


@data_app.command("certify")
def data_certify(
    snapshot_dir: Annotated[Path, typer.Argument(exists=True)],
    experiment_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    manifest = _load_snapshot_manifest(snapshot_dir)
    report = validate_dataset(
        _read_table(snapshot_dir / "daily_panel.parquet"),
        _load_optional(snapshot_dir / "membership.parquet"),
        _load_optional(snapshot_dir / "security_master.parquet"),
        _load_optional(snapshot_dir / "corporate_actions.parquet"),
    )
    certification = certify_report(
        report,
        raw_close_semantics=manifest.get("raw_close_semantics", "unknown"),
        total_return_source=manifest.get("total_return_source", "unknown"),
        membership_present=(snapshot_dir / "membership.parquet").exists(),
        security_master_present=(snapshot_dir / "security_master.parquet").exists(),
        provider_capabilities=manifest.get("provider_capabilities") or None,
    )
    if experiment_id:
        certification = certify_for_experiment(experiment_id, certification)
    payload = certification.to_dict()
    _print_json(payload)
    if payload["status"] != "PASS":
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
