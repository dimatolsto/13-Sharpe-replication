# Implementation status

## Phase 0 — scaffold (implemented)

- Frozen paper strategy specification and explicit ambiguity register.
- P0-P5 experiment manifests.
- Deterministic signal engine.
- Explicit daily-data timing alignment (`lag=1` forensic, `lag=2` corrected).
- Point-in-time signal architecture: time-series features use each security's full available history,
  then cross-sectional ranks/z-scores and portfolio eligibility use membership on the signal date.
- Separate `raw_close` vs `total_return` data contract.
- Long/short portfolio constructor and linear transaction costs.
- Independent accounting reconciliation audit.
- Sharpe/CAGR/volatility/drawdown/yearly metrics and block-bootstrap Sharpe CI; maximum drawdown is
  measured relative to initial wealth `1.0` before the first return.
- Strict YAML configuration validation for strategy, assumptions, and P0-P5 experiments.
- Paper walk-forward scaling for P0-P3 with explicit train/test windows, preserving unscaled results
  as the authoritative audit trail and writing scaled outputs separately when requested.
- WiseSheets capability gate without embedding undocumented endpoint semantics.
- Optional OpenAI Agents SDK supervisor + methodology auditor + red-team agents.
- Synthetic unit tests and GitHub Actions workflow.

## Phase 1 — data provenance and acquisition (implemented scaffold)

- Provider-agnostic normalized data layer for daily panels, point-in-time membership, security
  master / ticker history, and corporate-action events.
- Local CSV/Parquet import with explicit column mappings; `raw_close` is never inferred from
  `adjusted_close`.
- Immutable snapshot writer under `data/normalized/<snapshot_id>/` with Parquet datasets,
  `manifest.json`, deterministic SHA-256 file hashes, validation report, and certification report.
- Data validators for schema, duplicate rows, raw-close positivity, total-return consistency,
  split leakage, corporate-action event semantics, membership intervals, security-master intervals,
  survivorship/static-universe risk, and delisting/missing-terminal-return risk.
- Certification model with explicit `PASS` / `FAIL` / `UNVERIFIED` dimensions and P0-P5 experiment
  requirements.
- Data CLI:
  - `drift-replication data normalize`
  - `drift-replication data validate`
  - `drift-replication data inspect`
  - `drift-replication data hash`
  - `drift-replication data certify`
  - `drift-replication data wisesheets-capabilities`
- Backtest CLI can resolve `--snapshot data/normalized/<snapshot_id>` while preserving direct
  `--panel` / `--membership` development inputs.
- WiseSheets remains capability-gated and does not claim verified historical raw-close,
  total-return, split/dividend, delisting, ticker-history, stable-ID, or PIT membership coverage.
  No undocumented network endpoint was implemented.
- No P4/P5 headline performance run has been performed in Phase 1.

## Phase 1 — data-source tasks still open

1. Confirm WiseSheets API endpoint and actual response schema from the user's account or
   authoritative documentation.
2. Probe whether WiseSheets `Close` is truly historical nominal/unadjusted close; do not infer from
   naming.
3. Determine WiseSheets split/dividend/delisted-ticker coverage.
4. Obtain an independent point-in-time S&P 500 membership dataset with stable identifiers.
5. Certify at least one real immutable snapshot before using it for P2-P5 results.

## Phase 2A — real source discovery and acquisition preparation (implemented)

- Added an offline source discovery matrix and CLI command:
  - `drift-replication data discover-sources`
- Added immutable raw-acquisition metadata hashing with credential/signed-query redaction.
- Added deterministic S&P membership reconstruction support from add/remove change events using
  effective-at-start-of-date semantics.
- Added a membership-price join audit:
  - `drift-replication data audit-coverage`
- Added `docs/REAL_DATA_SOURCES.md` with source findings and certification implications.
- WiseSheets remains unverified for REST acquisition and raw `Close` semantics. No undocumented
  endpoint was implemented.
- No real P3-capable immutable snapshot was produced because the accessible sources reviewed did not
  jointly satisfy raw-close, total-return, delisted coverage, stable-ID, and PIT membership
  requirements.
- No P0-P5 strategy performance was run in Phase 2A.

## Phase 2 — forensic P0-P3

- Reproduce paper-style result on current constituents.
- Apply corrected daily timing.
- Apply raw nominal price / corporate-action-safe returns.
- Apply PIT constituent universe.

## Later certified-result targets

- P4: continuous 2009-2024 historical-clean result.
- P5: post-publication result from 2025-11-19 onward.
- Produce year-by-year attribution and red-team certification.

## Known implementation gaps

- P0-P3 exact OOS dates are interpreted as calendar years 2010, 2015, 2020; this remains an explicit
  paper ambiguity pending author code/data.
- P4 and P5 are unscaled by default. No rolling 5-year/1-year scaling extension is enabled or
  described as paper scaling.
- WiseSheets API calls are intentionally not implemented until the endpoint/schema is verified from
  authoritative account documentation. No API key is stored in the repository.
- Phase 1 provides synthetic/local validation coverage only. It has not acquired or certified a real
  historical S&P 500 market dataset.
- Phase 2A prepares source selection and acquisition validation, but still requires licensed CRSP,
  Norgate, or verified provider exports before a P3-certified real snapshot can be frozen.
- Phase 2C executed the open-source path and found current blockers for P2/P3: Yahoo `Close` is not
  a defensible nominal raw-close source, Wikipedia seed events remain unverified, and
  former-security price/terminal coverage is materially incomplete.

## Phase 2B — open-source reconstruction pipeline (implemented)

- Added a canonical S&P 500 event-ledger parser for Wikipedia's selected-change table.
  - Wikipedia rows are `WIKIPEDIA_SEED` and `UNVERIFIED` by default.
  - Primary/archived/fallback evidence overlays record discrepancies instead of silently reconciling.
- Added XNYS/NYSE trading-session effective-date normalization with `exchange-calendars==4.13.2`
  for before-open, after-close, weekend, standard-holiday, Good Friday, and unscheduled-closure
  cases. UNKNOWN timing remains a P3 blocker.
- Added provisional security-identity helpers that avoid ticker-only IDs by default and expose ticker
  rename/reuse diagnostics.
- Added Yahoo/yfinance acquisition preparation:
  - optional pinned `acquire` dependency only (`yfinance==1.5.1`);
  - fixed raw settings `interval=1d`, `auto_adjust=False`, `back_adjust=False`, `repair=False`,
    `actions=True`;
  - resumable per-symbol status files;
  - raw acquisition metadata records yfinance version and settings;
  - fixture normalization for `Close`, `Adj Close`, dividends, and splits.
- Added WiseSheets local-export parsing and Yahoo comparison. No WiseSheets REST API was invented.
- Added deterministic CLI demos for Wikipedia parsing, event completeness, membership
  reconstruction, Yahoo normalization/audit, WiseSheets comparison, and Yahoo acquisition planning.
- No real immutable open-source snapshot was certified in this implementation pass.
- No P0-P5 strategy performance was run in Phase 2B.

## Phase 2C — real open-source reconstruction run (executed)

- Fetched and froze a real Wikipedia S&P 500 seed snapshot on 2026-08-11.
  - Parsed 772 seed events total, with 737 events from 2004-01-01 through 2026-08-11.
  - Parsed a 503-row current Wikipedia constituent anchor as `WIKIPEDIA_ANCHOR` /
    `UNVERIFIED`.
- Built a provisional 2004-present membership reconstruction from the current anchor plus Wikipedia
  seed events.
  - 869 membership spells and 859 provisional security IDs.
  - XNYS target-session member counts range from 503 to 574 with median 543.
  - All in-window transitions remain unverified and `UNKNOWN` effective-session timing.
- Planned Yahoo acquisition from the union of provisional historical member identities, not only
  current constituents.
  - 933 planned alias rows / 857 unique Yahoo symbols.
  - 728 completed alias downloads and 205 permanent/no-data failures.
  - Candidate Yahoo panel contains 3,646,880 rows; raw files remain gitignored.
- Ran real split, return, dividend, join, terminal, and survivorship audits.
  - Yahoo `Close` failed nominal raw-close certification: 489 of 616 split checks were
    `likely_back_adjusted`.
  - A separate `reconstructed_nominal_close` candidate was implemented by reversing subsequent Yahoo
    split factors; its split audit improved to 603 of 616 nominal-consistent checks, but independent
    cross-source validation is still pending.
  - Provider-edge member-day join found 640,008 member dates lacking price rows in the provisional
    reconstruction.
  - Corrected terminal audit now treats the 503 active current constituents at the acquisition right
    edge as `right_censored_active`, not `disappears_while_member`; true disappearance-while-member
    count is 0, with 190 unmapped/no-price securities still unresolved.
- No immutable normalized real snapshot was frozen because the evidence does not support P2/P3
  certification.
- See `docs/PHASE2C_REAL_RECONSTRUCTION.md` for detailed counts and local generated report paths.
- No P0-P5 strategy performance was run in Phase 2C.

## Phase 2D — membership reconciliation and identity audit (executed)

- Added `drift-replication data phase2d-membership-report`, a data-only command that consumes the
  Phase 2C seed ledger, anchor, membership, raw Wikipedia HTML, and optional Yahoo state.
- Extracted real Wikipedia citation footnote targets into a persistent verification queue.
- Fetched and cached 30 candidate source artifacts with URL, retrieval time, SHA-256, source tier,
  and linked event IDs.
- Improved event verification from zero to 14 primary-verified seed events; all 14 have
  `BEFORE_OPEN` timing extracted from S&P Global press releases.
- Found one additional 2004 S&P 500 replacement group from secondary contemporaneous evidence:
  E*TRADE Financial added and FleetBoston Financial removed after close on 2004-03-31.
- Added row-level reconstruction-conservation reports. The 503-574 member-count inflation is now
  decomposed as mainly provisional identity-alias duplication, not ordinary share-class variation.
- Added explicit identity-lineage, identity-ambiguity, reentry-review, residual-gap, manual-review,
  anchor-comparison, historical-snapshot-check, and Yahoo-plan-delta report outputs.
- Membership remains `FAIL`: 723 seed events remain unresolved/UNKNOWN timing, the anchor is still
  provisional, 232 identity ambiguity rows remain, and 2004 primary/archive evidence is incomplete.
- P2 remains `FAIL` from Phase 2C raw-close findings, so overall P3 cannot pass.
- No P0-P5 strategy performance was run in Phase 2D.

## Phase 2E — snapshot triangulation and identity-repair targeting (executed)

- Added `drift-replication data phase2e-snapshot-report`, a data-only command that evaluates
  historical constituent snapshots as cross-checks against the reconstructed PIT universe.
- Implemented deterministic Wikipedia revision constituent-table parsing, snapshot-source cataloging,
  cached bounded CSV acquisition, iShares non-equity filtering, snapshot-date to XNYS session
  mapping, identity-aware set comparison, multi-source disagreement reporting, error-interval
  localization, identity-resolution config application, and rebuild-from-scratch membership output.
- Evaluated six snapshot-source categories and acquired 19 bounded raw snapshot artifacts:
  13 iShares IVV holdings snapshots, one independent fja05680 ticker-interval dataset expanded to
  12 checkpoints, three Wikipedia-derived riazarbi snapshots, and two tidyquant-derived snapshots.
- Compared 30 checkpoints. The median reconstructed count was 528, the median source snapshot count
  was 503, the median symmetric difference was 91, and the worst symmetric difference was 231.
- Reviewed the Phase 2D problem set using snapshot diagnostics: 64 identity mismatches, 12 backward
  reconstruction failures, and 10 provisional reentries. No repair was applied because no case yet
  satisfied the evidence standard for a tracked lineage change.
- Rebuilt membership from scratch with the empty Phase 2E identity-resolution config. Counts remain
  503 to 574 with median 543, with 869 spells and 859 reconstructed historical membership IDs.
- Membership remains `FAIL`: 112 residual P3-blocking gaps remain, including unresolved identity
  mismatches, backward failures, reentries, early-period gaps, and worst checkpoint differences.
- P2 remains `FAIL` from Phase 2C raw-close findings, so overall P3 cannot pass.
- No full Yahoo redownload and no P0-P5 strategy performance were run in Phase 2E.

## Phase 3 — paper reproduction and methodological attribution (executed)

- Added tracked Phase 3 experiment specs R0/R1/R2/R3 and the dedicated
  `drift-replication phase3-attribution` command.
- Intentionally froze a survivorship-biased current-constituent paper-like universe instead of
  continuing PIT membership archaeology:
  `SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE`.
- Selected the closest existing cached paper-date anchor: riazarbi iShares IVV holdings snapshot
  dated 2025-12-22, 34 days after the paper publication date, with 503 constituents. Cached Yahoo
  market data exists for 498 of them; five anchor symbols remain unavailable in the existing cache.
- Built a Phase 3 input panel from the existing Phase 2C Yahoo source panel without a full Yahoo
  redownload. Yahoo `Close`, Yahoo `Adj Close`, and `reconstructed_nominal_close` remain separate.
- R0/R1 differ only by timing (`lag=1` vs `lag=2`). R1/R2 differ only by the VALUE price
  representation (Yahoo Close vs reconstructed nominal close). R3 is continuous 2010-2024,
  unscaled, corrected timing, and survivorship-biased.
- Added augmented security ledgers with signal date, weight date, earned return date, security
  return, gross contribution, turnover contribution, cost contribution, and net contribution.
  Generated ledgers reconcile to daily net returns within numerical tolerance.
- Tightened portfolio accounting so missing returns are counted as blocking only when the security
  has nonzero weight; missing weighted returns are not silently filled with zero.
- Phase 3 results do not reproduce the paper. R0 paper-scaled Sharpe is 2.05, 0.43, and 0.74 for
  2010, 2015, and 2020, versus paper values 16.89, 22.87, and 5.11. R2 unscaled selected-window
  Sharpe is 0.40, and R3 continuous 2010-2024 Sharpe is 0.02.
- Phase 3 does not change certification: P2 raw-close remains `FAIL`, reconstructed nominal close
  remains candidate-only pending independent validation, PIT membership remains deferred, and overall
  P3 cannot pass.

## Phase 3B — reproduction-gap forensics (executed)

- Added `drift-replication phase3b-reproduction-gap`, a report-only diagnostic command that verifies
  the frozen Phase 3 input manifest and writes compact local artifacts under
  `reports/generated/phase3b/`.
- Reproduced the Phase 3 baseline exactly before running diagnostics: R0/R1/R2/R3 unscaled Sharpes
  matched the Phase 3 summary to zero reported numerical error.
- Audited the Phase 3 legacy R0 signal path and found that the existing rolling reversal and regime
  windows end on signal date `t`. The paper's explicit regime equation excludes current return `t`,
  so Phase 3B preserves exact legacy R0 as an invalid paper-equation fingerprint and separately
  reports a paper-spec prior-window R0 variant.
- Paper-spec R0 remains far below the paper: selected-window Sharpe is 0.65 combined, with 2010,
  2015, and 2020 test Sharpes of 1.03, -0.17, and 0.92. Paper-reported test Sharpes are 16.89,
  22.87, and 5.11.
- Training-period Sharpes are also low under paper-spec R0: 0.80, 0.65, and 0.46 versus paper
  training Sharpes of 19.42, 27.79, and 16.63. This points to a fundamental reproduction/data/spec
  gap, not merely weak OOS walk-forward behavior.
- Invalid diagnostics do not explain the gap. Current-day reversal reaches 0.98 selected-window
  Sharpe, current-day regime reaches 0.46, lag0 contemporaneous return is strongly negative
  (-7.75), and the strongest invalid future-regime offset reaches only 1.29.
- Fingerprint audits show that the paper's stated 35% active stock-days cannot naturally coexist
  with roughly 187 long plus 189 short positions if the algorithm trades only valid non-zero EDGE
  names. Under paper-spec R0, selected-window positioned names average about 40, 32, and 60 in 2010,
  2015, and 2020.
- EDGE standardization alternatives were tested as interpretation diagnostics, not strategies. The
  inactive-zero z-score variant trades hundreds of inactive names and still reaches only 0.97
  selected-window Sharpe, while contradicting the paper's non-zero EDGE rule.
- Phase 3B classification is `MULTIPLE: DATA_SOURCE_DIFFERENCE + PAPER_INTERNAL_INCONSISTENCY`.
  This is not evidence of intent; it is a bounded forensic reproduction gap under the repository's
  frozen survivorship-biased Phase 3 inputs.
- No strategy parameters were changed, no PIT membership work was resumed, no full Yahoo redownload
  occurred, and no P4/P5 runs were performed.

## Phase 4 — incremental regime-edge information test (executed)

- Added `drift-replication phase4-regime-edge`, a report-only study using the verified frozen Phase
  3 panel and Phase 3B paper-spec prior-window signal helpers. Production Phase 3/3B specs were not
  changed.
- Predeclared 2010-2024 and the one-day information horizon as primary; retained corrected delayed
  timing, fixed 2/5/10-day horizons, HAC lag 20, 2,000 block-bootstrap replications, minimum group
  size 20, and 1,000 matched masks with seed 13.
- Analyzed 1,755,295 eligible stock-days. REGIME=1 covered 205,643 (11.72%); median eligible and
  active names per day were 470 and 48. The fixed interaction rule produced 3,124 usable dates.
- The primary raw `BASE x REGIME` interaction was -0.000012 (HAC t-stat -0.07; 95% CI
  [-0.000338, 0.000314]); standardized BASE was also effectively zero. Corrected delayed timing was
  negative with t-stat -0.60.
- In-regime Spearman IC was slightly higher, but the paired difference was insignificant. Pearson
  IC, slope, and spread differences were negative. All 2/5/10-day interactions were negative.
- The actual interaction was at the 48.6th percentile of equally broad random masks (one-sided
  p=0.514). The continuous `BASE x UpFraction` interaction was significantly negative (t-stat
  -3.39), and fixed-bin efficacy was not monotone.
- Annual interaction and IC signs were positive in nine years and negative in six, without stable
  magnitude. VALUE, REVERSAL, rank-outcome, transition, concentration, and supporting portfolio
  checks did not establish a robust regime enhancement.
- Phase 4 classification is `WEAK_OR_MIXED`: there is no compelling incremental predictive value
  under this frozen causal implementation, but the small positive Spearman-IC diagnostic prevents a
  stronger uniformly negative binary-regime conclusion.
- No threshold, horizon, BASE weight, reversal definition, market dataset, PIT membership, or
  favorable subperiod was selected from results. Generated artifacts remain local and gitignored
  under `reports/generated/phase4/`.
