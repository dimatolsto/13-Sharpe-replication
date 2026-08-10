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
