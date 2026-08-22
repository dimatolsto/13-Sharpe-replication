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
uv run drift-replication data parse-wikipedia-anchor \
  --html data/raw/sp500_membership/wikipedia/<id>/wikipedia_sp500.html \
  --out reports/generated/phase2c/wikipedia_current_anchor.csv
uv run drift-replication data event-completeness --events data/raw/.../wikipedia_events.csv
uv run drift-replication data reconstruct-membership \
  --events data/raw/.../verified_events.csv \
  --anchor-members data/raw/.../anchor_members.csv \
  --anchor-date 2026-08-11 \
  --start-date 2004-01-01 \
  --end-date 2026-08-11 \
  --out data/raw/.../membership.csv \
  --metadata-out data/raw/.../membership_metadata.json \
  --provisional-security-ids
uv run drift-replication data acquire-yahoo \
  --state data/raw/yahoo/<id>/state.csv \
  --raw-dir data/raw/yahoo/<id> \
  --start-date 2003-10-01 \
  --symbol-sleep 0.1
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

Phase 2C executed the real open-source reconstruction run and is documented in
`docs/PHASE2C_REAL_RECONSTRUCTION.md`. The run produced real Wikipedia/Yahoo acquisition artifacts
and audit reports, but no certified immutable snapshot: Yahoo `Close` failed nominal raw-close split
certification, all in-window Wikipedia seed events remain unverified/UNKNOWN timing, and
former-security member-day coverage has material gaps. A separate `reconstructed_nominal_close`
candidate exists for follow-up validation, but it is not certified. P0-P5 performance still has not
been run.

Phase 2D is documented in `docs/PHASE2D_MEMBERSHIP_RECONCILIATION.md`. It added a resumable
membership-verification queue, source-artifact cache metadata, reconstruction-conservation audit,
event-group report, explicit identity-lineage table, residual gap register, and manual-review queue.
The bounded verification pass moved 14 Wikipedia seed events to primary verified with `BEFORE_OPEN`
timing and found one additional 2004 replacement group supported by secondary contemporaneous
evidence. The reconstructed member-count range remains 503 to 574 with median 543 because Phase 2D
diagnosed, rather than mechanically repaired, the dominant provisional identity-alias failures.
Membership certification remains `FAIL`, and P2/P3 remain blocked. No P0-P5 performance was run.

Phase 2E is documented in `docs/PHASE2E_SNAPSHOT_TRIANGULATION.md`. It adds a data-only snapshot
triangulation command that compares the provisional PIT reconstruction with bounded historical
constituent checkpoints from Wikipedia-derived snapshots, iShares IVV holdings snapshots, and
independent secondary ticker-interval data. The pass acquired 19 raw snapshot artifacts and compared
30 checkpoints, but applied zero identity repairs because the discrepancies did not yet meet the
evidence standard for lineage changes. Membership remains `FAIL`: the count range is still 503 to
574 with median 543, all 64 Phase 2D identity mismatches remain unresolved, all 12 backward
reconstruction failures remain unresolved, and the 2004-2006 period remains a P3-blocking gap. No
Yahoo full redownload or P0-P5 performance was run.

Phase 3 is documented in `docs/PHASE3_PAPER_ATTRIBUTION.md`. It intentionally pauses PIT membership
archaeology and preserves the paper's acknowledged current-constituent survivorship bias to isolate
other methodology questions. The fixed anchor is a cached 2025-12-22 iShares IVV snapshot with 503
rows, 34 days after the paper publication date; 498 anchor securities have cached Yahoo market data.
The Phase 3 command is:

```bash
uv run drift-replication phase3-attribution --out-dir reports/generated/phase3
```

Every Phase 3 result is labeled `SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE` and is not an investable
PIT simulation. Under favorable R0 paper-like assumptions, reproduced paper-scaled Sharpe was 2.05,
0.43, and 0.74 in the 2010, 2015, and 2020 paper windows, far below the paper's 16.89, 22.87, and
5.11. Corrected R2 selected-window Sharpe was 0.40 unscaled, and continuous corrected R3
survivorship-biased Sharpe over 2010-2024 was 0.02. Phase 3 does not change Phase 2 P2/P3
certification: Yahoo Close remains failed as nominal raw close, reconstructed nominal close remains
candidate-only, and true PIT membership work remains deferred.

Phase 3B is documented in `docs/PHASE3B_REPRODUCTION_GAP.md`. It keeps the Phase 3 data frozen and
forensically audits why R0 still cannot reproduce the paper. The Phase 3 baseline reproduced exactly,
but the exact legacy R0 path was found to include current-day reversal/regime windows; a paper-spec
prior-window R0 is weaker, with selected-window Sharpe 0.65 versus paper values near 13 combined.
Invalid leakage diagnostics, including lag0 return alignment, current-day reversal, current-day
regime, and regime lead/lag scans, do not approach the paper's reported Sharpe. The pass also
documents internal fingerprint contradictions around active stock-days, position counts, and gross
exposure normalization. No parameters were optimized, no PIT membership work was resumed, and no
Yahoo redownload was run.

Phase 4 is documented in `docs/PHASE4_REGIME_EDGE.md`. The project priority is now the narrower
economic question of whether the frozen paper-spec drift regime improves BASE's predictive content,
not further reverse engineering of Sharpe 13. The report-only command is:

```bash
uv run drift-replication phase4-regime-edge --out-dir reports/generated/phase4
```

On the 2010-2024 frozen Phase 3/3B panel, the primary one-day `BASE x REGIME` interaction was
effectively zero (`-0.000012`, HAC t-stat `-0.07`), and corrected delayed timing was also negative.
The actual regime's interaction statistic was at the 48.6th percentile of 1,000 equally broad
random masks, while the continuous `BASE x UpFraction` diagnostic was significantly negative.
Evidence is classified `WEAK_OR_MIXED`: in-regime Spearman IC was slightly higher but statistically
uncertain, and the broader inference set does not show compelling incremental regime value. No
parameters, Phase 3/3B specs, PIT membership, or market data were changed.

## Architecture

The calculation engine is deterministic. LLM agents are optional and limited to orchestration,
methodology review, and red-team audit. Agents never calculate portfolio returns or modify the frozen
strategy parameters after results are available.
