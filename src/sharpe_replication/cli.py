from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from .backtest import run_backtest
from .config import load_experiment, load_strategy
from .data.certification import certify_for_experiment, certify_report
from .data.io import read_table
from .data.normalize import (
    normalize_corporate_actions,
    normalize_daily_panel,
    normalize_from_mapping,
    normalize_membership,
    normalize_security_master,
)
from .data.snapshot import hash_snapshot, inspect_snapshot, write_snapshot
from .data.validation import validate_dataset
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
