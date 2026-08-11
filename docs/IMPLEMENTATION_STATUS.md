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

## Phase 3 — primary results

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
