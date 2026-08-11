# 13-Sharpe Replication

Independent forensic replication of the drift-regime cross-sectional equity strategy described in
*Discovery of a 13-Sharpe OOS Factor: Drift Regimes Unlock Hidden Cross-Sectional Predictability*.

## Objective

The project does **not** optimize the strategy. It estimates how the reported Sharpe changes after
correcting the main methodological problems identified in the paper:

1. same-close / timing ambiguity;
2. researcher/data-snooping risk;
3. only three isolated OOS test years;
4. nominal-price signal + corporate-action contamination risk;
5. portfolio-accounting inconsistencies;
6. survivorship bias from using current S&P 500 constituents historically.

The final outputs are:

- **P4 historical-clean Sharpe**: continuous 2009-2024 evaluation using point-in-time membership,
  corporate-action-safe data, and a full daily-bar execution lag.
- **P5 forward Sharpe**: unchanged strategy from 2025-11-19 onward.

## Non-negotiable replication rules

- Daily data only. No intraday reconstruction.
- Published parameters are frozen before results are inspected.
- Signals computed from close `t` cannot earn the `t -> t+1` return in the corrected runs. The
  corrected implementation assumes execution at close `t+1`; the first earned return is
  `t+1 -> t+2`.
- The inverse-price feature uses **historical raw nominal close**, not a future-back-adjusted series.
- P&L uses a separate corporate-action-correct total-return series.
- Point-in-time index membership is required for P3-P5.
- No parameter search is permitted after P4/P5 results are visible.

## Quick start

```bash
uv sync --extra dev
uv run pytest
uv run drift-replication spec-check
```

Later, after a snapshot is certified and P0-P3 attribution has been reviewed:

```bash
uv run drift-replication run --experiment experiments/P4_continuous_oos.yaml \
  --snapshot data/normalized/<certified_snapshot_id>
```

Run summaries keep the deterministic unscaled audit trail at the legacy top-level fields and under
`unscaled`. Experiments that explicitly request paper walk-forward scaling also include `scaled` and
`scaling_windows`; scaled daily/ledger/yearly files are written with a `_scaled_` filename suffix.
P4 and P5 are unscaled by default.

Phase 1 adds data-only commands for building and auditing local snapshots. These commands do not run
strategy performance:

```bash
uv run drift-replication data normalize data/normalized/dev-snapshot \
  --daily-source /path/to/daily.csv \
  --daily-map '{"source_date":"date","source_security_id":"security_id","source_ticker":"ticker","source_close":"raw_close","source_return":"total_return"}' \
  --raw-close-semantics unknown \
  --total-return-source provider

uv run drift-replication data validate --panel data/normalized/dev-snapshot/daily_panel.parquet
uv run drift-replication data inspect data/normalized/dev-snapshot
uv run drift-replication data certify data/normalized/dev-snapshot --experiment-id P3
uv run drift-replication data wisesheets-capabilities
uv run drift-replication data discover-sources
uv run drift-replication data parse-wikipedia-events \
  --html data/raw/sp500_membership/wikipedia/<id>/wikipedia_sp500.html \
  --out data/raw/sp500_membership/wikipedia/<id>/wikipedia_events.csv
uv run drift-replication data event-completeness --events data/raw/.../wikipedia_events.csv
uv run drift-replication data reconstruct-membership \
  --events data/raw/.../verified_events.csv \
  --anchor-members data/raw/.../anchor_members.csv \
  --anchor-date 2026-08-11 \
  --start-date 2004-01-01 \
  --end-date 2026-08-11 \
  --out data/raw/.../membership.csv \
  --metadata-out data/raw/.../membership_metadata.json
uv run drift-replication data acquire-yahoo \
  --state data/raw/yahoo/<id>/state.csv \
  --raw-dir data/raw/yahoo/<id> \
  --start-date 2003-10-01
uv run drift-replication data audit-coverage \
  --panel data/normalized/dev-snapshot/daily_panel.parquet \
  --membership data/normalized/dev-snapshot/membership.parquet
```

Normalized snapshots are immutable by default and include `manifest.json`,
`validation_report.json`, and `certification_report.json`. Actual market data under `data/raw/` or
`data/normalized/` is not intended to be committed.

## Data contract

The normalized daily panel must contain:

- `date`
- `security_id` (stable identifier; ticker alone is insufficient)
- `ticker`
- `raw_close` — nominal close as known on that historical date
- `total_return` — close-to-close corporate-action-correct return
- `volume` (optional for the core replication)

Point-in-time membership must contain:

- `security_id`
- `membership_start`
- `membership_end` (nullable = still active)

See `data/README.md` for details.

For point-in-time experiments, rolling time-series features are computed from all available history
for each security. Membership is then applied as a signal-date eligibility mask before any
cross-sectional rank, z-score, EDGE standardization, or portfolio weight is calculated.

## WiseSheets

WiseSheets is treated as a pluggable source, not as an assumed source of point-in-time S&P 500
membership. The public WiseSheets documentation distinguishes historical `Close` and `AdjClose`
fields, while some WiseSheets educational material describes historical close data as split-adjusted.
Because the paper's signal uses *nominal* price, this project refuses to infer the semantics. The
provider includes a capability/provenance check and requires raw-vs-adjusted semantics to be
verified before P2-P5 can be certified.

The API key must be supplied only through `WISESHEETS_API_KEY` and is never stored in the repo.
No WiseSheets network endpoint is implemented yet because authoritative API endpoint/schema
documentation is not present in this repository. The current CLI reports WiseSheets market-data
capabilities as `unverified` rather than guessing.

Phase 2A source discovery is documented in `docs/REAL_DATA_SOURCES.md`. No real P3-capable snapshot
has been certified yet; CRSP/WRDS or Norgate are the current documented defensible paths if licensed,
while WiseSheets remains a local-export/provisional source until its raw-close and API semantics are
proved.

Phase 2B open-source reconstruction tooling is documented in
`docs/OPEN_SOURCE_RECONSTRUCTION.md` and `docs/WISESHEETS_EXPORT_WORKFLOW.md`. Wikipedia is treated
only as an event seed, Yahoo Finance is an acquisition source only, and WiseSheets exports are
cross-checks only. P4/P5 performance has not been run.

## Architecture

The calculation engine is deterministic. LLM agents are optional and limited to orchestration,
methodology review, and red-team audit. Agents never calculate portfolio returns or modify the frozen
strategy parameters after results are available.
