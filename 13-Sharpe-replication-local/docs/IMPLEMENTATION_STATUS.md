# Implementation status

## Phase 0 — scaffold (implemented)

- Frozen paper strategy specification and explicit ambiguity register.
- P0-P5 experiment manifests.
- Deterministic signal engine.
- Explicit daily-data timing alignment (`lag=1` forensic, `lag=2` corrected).
- Point-in-time membership filtering.
- Separate `raw_close` vs `total_return` data contract.
- Long/short portfolio constructor and linear transaction costs.
- Independent accounting reconciliation audit.
- Sharpe/CAGR/volatility/drawdown/yearly metrics and block-bootstrap Sharpe CI.
- WiseSheets capability gate without embedding undocumented endpoint semantics.
- Optional OpenAI Agents SDK supervisor + methodology auditor + red-team agents.
- Synthetic unit tests and GitHub Actions workflow.

## Phase 1 — data provenance and acquisition (next)

1. Confirm WiseSheets API endpoint and actual response schema from the user's account/documentation.
2. Probe whether `Close` is truly historical nominal/unadjusted close; do not infer from naming.
3. Determine split/dividend/delisted-ticker coverage.
4. Obtain an independent point-in-time S&P 500 membership dataset with stable identifiers.
5. Build identifier/ticker-history mapping and immutable local Parquet snapshots.

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

- `paper_scaling: true` is specified but not yet applied by `run_backtest`; unscaled results are the
  current deterministic core. The scaling layer will be added only after the unscaled path and exact
  training-window interpretation are verified.
- P0-P3 exact OOS dates are interpreted as calendar years 2010, 2015, 2020; this remains an explicit
  paper ambiguity pending author code/data.
- WiseSheets API calls are intentionally not implemented until the endpoint/schema is verified from
  authoritative account documentation. No API key is stored in the repository.
