# Phase 2E Snapshot Triangulation

Run date: 2026-08-11

Phase 2E focused only on historical S&P 500 membership snapshots, internal security identity, and
count-inflation diagnostics. It did not run P0, P1, P2, P3, P4, P5, strategy returns, Sharpe, CAGR,
drawdown, wealth, or portfolio calculations.

## Inputs

- Wikipedia seed ledger: `data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_events.csv`
- Provisional anchor: `reports/generated/phase2c/wikipedia_current_anchor.csv`
- Phase 2D reports: `reports/generated/phase2d/`
- Prior Yahoo state, for plan-delta classification only: `reports/generated/phase2c/yahoo_state.csv`
- Identity-resolution config: `spec/phase2e_identity_resolutions.csv`

Generated reports are under `reports/generated/phase2e/`. Raw snapshot artifacts are cached under
`data/raw/sp500_membership/phase2e_snapshot_cache/`. Both generated report and raw cache locations
remain gitignored.

## Method

Phase 2D showed that broad event-by-event verification was too slow to resolve the dominant
membership failure. Phase 2E therefore uses independent historical constituent snapshots as
checkpoints:

1. Acquire bounded cached snapshots from public sources.
2. Normalize rows to source ticker, source company name, source identifiers, and snapshot date.
3. Map rows through a date-aware internal security resolver.
4. Compare mapped snapshot security IDs with reconstructed members for the same XNYS state.
5. Localize the first disagreement between nearby checkpoints.
6. Review only targeted identity mismatches, backward reconstruction failures, and reentries.
7. Rebuild membership from scratch after any approved identity-resolution config rows.

Snapshots are diagnostics. They are not used as unquestioned authority, and no correction is made
from ticker/name similarity or secondary snapshot agreement alone.

## Snapshot Sources

Phase 2E evaluated six source categories:

| source | tier | coverage used | identifiers | limitations |
|---|---|---|---|---|
| Wikipedia revision parser | `SECONDARY_SNAPSHOT` | parser implemented; live crawl not run by default | ticker, company, revision ID/timestamp, CIK where schema provides it | secondary source; table schema changes over time |
| riazarbi iShares IVV snapshots | `SECONDARY_SNAPSHOT` | 13 acquired dates from 2006-10-31 through 2026-07-31 | ticker, company, CUSIP, ISIN, SEDOL | ETF proxy, not official S&P membership |
| fja05680 historical ticker intervals | `SECONDARY_SNAPSHOT` | ticker intervals expanded to 12 checkpoints | ticker, interval start/end | ticker-only, secondary, no security IDs |
| riazarbi Wikipedia snapshots | `SECONDARY_SNAPSHOT` | 3 acquired dates from 2022-11-08 through 2026-08-11 | ticker, company, CIK where present | Wikipedia-derived secondary snapshots |
| riazarbi tidyquant snapshots | `SECONDARY_SNAPSHOT` | 2 acquired dates | ticker, company, CUSIP, ISIN, SEDOL | package-derived secondary source |
| S&P official historical snapshots | `SP_PRIMARY` | no full public history feed acquired | current public names/tickers where accessible | preferred source, but not available as a bounded public historical feed |

The bounded run acquired 19 raw snapshot artifacts with no download errors and compared 30
checkpoints after expanding interval data.

## Checkpoint Comparison

Summary from `reports/generated/phase2e/phase2e_summary.json`:

- Checkpoints compared: 30
- Median reconstructed count: 528
- Median source snapshot count: 503
- Median symmetric difference: 91
- Worst symmetric difference: 231
- Multi-source disagreement rows: 364

The recent control was clean: the 2026-08-11 riazarbi Wikipedia snapshot matched the 503-row
provisional anchor after mapping. Older checkpoints show large disagreement, especially the early
period where the reconstruction count is inflated and many source rows remain unmapped or ambiguous.

Worst observed checkpoint:

- Date/source: 2004-12-31, fja05680 interval data
- Snapshot count: 495
- Reconstructed count: 574
- Mapped snapshot count: 357
- Overlap: 350
- Reconstruction-only: 224
- Snapshot-only: 7
- Symmetric difference: 231

These numbers are diagnostics, not a target. The reconstruction was not forced toward 500.

## Count Inflation

Baseline from Phase 2D:

- Min/median/max count: 503 / 543 / 574
- Backward failures: 76
- Forward failures: 2
- Backward net excess: +74

After Phase 2E:

- Min/median/max count: 503 / 543 / 574
- Membership spells: 869
- Unique historical membership security IDs: 859
- Backward failures: 76
- Forward failures: 2
- Backward net excess: +74
- Count removed through approved repairs: 0

No count reduction is claimed because no evidence-backed identity-resolution row was approved.
Phase 2E improves localization and review queues, not certification status.

## Identity Review

Phase 2E reviewed the Phase 2D problem set:

- Identity mismatches reviewed: 64
- Same-security rename/name-change resolutions: 0
- Provider-symbol normalizations: 0
- Acquisitions kept separate by explicit repair row: 0
- Ticker reuse cases kept separate by explicit repair row: 0
- Duplicate provisional IDs resolved: 0
- Unresolved identity mismatches: 64

The tracked config `spec/phase2e_identity_resolutions.csv` is intentionally empty after this pass.
Future repairs must add evidence-backed rows there instead of hardcoding identity decisions in
Python.

## Backward Failures

Phase 2D identified 12 non-identity backward reconstruction failures:

- Reviewed: 12
- Resolved: 0
- Unresolved: 12

The unresolved symbols include AGN plus ADD-not-present cases such as UA, FB, KORS, DLPH, IR, PCLN,
HRS, COG, JEC, TSO, and LUK. Snapshot evidence narrowed these into manual-review items but did not
establish a defensible event or identity correction.

## Reentries

Phase 2D found 10 provisional reentries and confirmed none. Phase 2E reviewed all 10:

- Confirmed same-security reentries: 0
- Identity/reconstruction artifacts proven: 0
- Unresolved: 10

No reentry is promoted to same-security continuity without direct lineage evidence.

## Early Period 2004-2006

Early-period status: `FAIL`.

Evidence available:

- fja05680 secondary ticker-interval checkpoints for 2004-2006.
- iShares IVV secondary holdings snapshots starting 2006-10-31.
- The Phase 2D secondary 2004 E*TRADE/FleetBoston replacement remains known.

Remaining gaps:

- No primary or archived-primary full constituent snapshots for 2004-2006 were acquired.
- Wikipedia selected-change seed has zero 2004 rows.
- Targeted 2004-2006 event evidence remains incomplete.

## Targeted Primary Research

Phase 2E searched targeted cases from the 64 identity mismatches and 12 backward failures rather than
all unresolved events:

- Targeted cases investigated: 76
- S&P primary candidate URLs located: 22
- Archived-primary candidate URLs located: 1
- Resolved using primary: 0
- Resolved using archived primary: 0
- Resolved using strong secondary or multi-source evidence: 0
- Unresolved targeted cases: 76

Candidate primary URLs are retained in `reports/generated/phase2e/targeted_primary_research.csv`.
They are leads for manual review, not automatic lineage approvals.

## Yahoo Plan Delta

No full Yahoo acquisition was run.

- Previous required historical IDs: 859
- Revised required historical IDs: 859
- Yahoo alias IDs not in rebuilt membership: 74
- Genuinely new historical members: 0
- Old Yahoo failures still required: 190
- Old Yahoo failures no longer relevant after rebuilt membership comparison: 15
- Alias changes from approved identity repairs: 0

These numbers are a plan impact only. They do not certify Yahoo coverage or raw-close semantics.

## Certification Outcome

Membership component status after Phase 2E: `FAIL`.

Reasons:

- Persistent unexplained count inflation remains.
- Conservation audit remains materially failing.
- All 64 identity mismatches remain unresolved.
- All 12 backward reconstruction failures remain unresolved.
- All 10 provisional reentries remain unresolved.
- The 2004-2006 period is not defensible from primary/archive evidence.
- The residual gap register contains 112 P3-blocking membership gaps.

P2 raw-close status remains the Phase 2C `FAIL`. Overall P3 remains `FAIL` because both membership
and P2 data requirements are not satisfied.

## Generated Reports

Main local reports:

- `snapshot_sources.csv`
- `snapshot_checkpoints.csv`
- `snapshot_artifacts.csv`
- `snapshot_filtered_rows.csv`
- `snapshot_rows_mapped.csv`
- `snapshot_set_differences.csv`
- `snapshot_set_difference_details.csv`
- `multi_source_disagreements.csv`
- `error_intervals.csv`
- `identity_mismatch_review.csv`
- `backward_failure_review.csv`
- `reentry_review.csv`
- `targeted_primary_research.csv`
- `identity_resolutions.csv`
- `identity_identifier_enrichment.csv`
- `identity_ambiguities.csv`
- `reconstruction_conservation.csv`
- `membership_count_progress.csv`
- `residual_membership_gaps.csv`
- `manual_review_queue.csv`
- `yahoo_plan_delta.csv`
- `phase2e_summary.json`

## Confirmations

- Wikipedia remained a discovery/checkpoint source, not authority.
- Secondary snapshots were not forced into the ledger.
- No missing event was invented.
- No constituent count was forced to 500.
- No identity was merged from fuzzy ticker or name similarity alone.
- Yahoo price availability was not used to resolve membership.
- No full Yahoo redownload was run.
- No strategy parameter was changed.
- No P0-P5 performance, Sharpe, CAGR, wealth, portfolio return, or drawdown calculation was run.
