from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import typer

from .backtest import run_backtest
from .config import load_experiment, load_strategy
from .providers.wisesheets import WiseSheetsCapabilityGate

app = typer.Typer(no_args_is_help=True)


def _read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)


@app.command("spec-check")
def spec_check(
    strategy_path: Path = typer.Option(Path("spec/paper_strategy.yaml")),
) -> None:
    cfg = load_strategy(strategy_path)
    typer.echo(cfg.model_dump_json(indent=2))


@app.command("wisesheets-capabilities")
def wisesheets_capabilities() -> None:
    gate = WiseSheetsCapabilityGate()
    typer.echo(json.dumps(gate.report(), indent=2))


@app.command("run")
def run(
    experiment: Path = typer.Option(..., exists=True),
    panel: Path = typer.Option(..., exists=True),
    membership: Path | None = typer.Option(None),
    strategy_path: Path = typer.Option(Path("spec/paper_strategy.yaml")),
    out_dir: Path = typer.Option(Path("reports/generated")),
) -> None:
    strategy = load_strategy(strategy_path)
    exp = load_experiment(experiment)
    panel_df = _read_table(panel)
    membership_df = _read_table(membership) if membership else None
    result = run_backtest(panel_df, strategy, exp, membership_df)

    out_dir.mkdir(parents=True, exist_ok=True)
    result.daily.to_parquet(out_dir / f"{exp.id}_daily.parquet", index=False)
    result.ledger.to_parquet(out_dir / f"{exp.id}_ledger.parquet", index=False)
    result.yearly.to_csv(out_dir / f"{exp.id}_yearly.csv", index=False)
    summary = {
        "experiment": exp.id,
        "metrics": result.metrics,
        "sharpe_ci_95": result.sharpe_ci_95,
        "audit": result.audit,
    }
    (out_dir / f"{exp.id}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":
    app()
