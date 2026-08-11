# Data Provenance

Phase 1 separates acquisition from backtesting:

```text
external provider -> raw immutable snapshot -> normalization -> validation/certification
                  -> normalized immutable Parquet -> deterministic P0-P5 engine
```

The deterministic engine consumes normalized Parquet. It must not call WiseSheets or any other
provider during portfolio calculations.

Phase 2A adds source-discovery and acquisition-preparation artifacts. Source capabilities are
reported by `drift-replication data discover-sources`; raw acquisition directories can carry
credential-redacted `metadata.json` files with SHA-256 hashes before normalization.

## Field Lineage

The snapshot manifest records how provider fields map into normalized fields. Examples:

| normalized field | acceptable lineage |
|---|---|
| `raw_close` | provider nominal close verified as not retrospectively split-adjusted |
| `adjusted_close` | provider adjusted close, retained only as a separate diagnostic field |
| `total_return` | provider corporate-action-correct close-to-close return, or explicit reconstruction |
| `dividend_cash` | provider cash-dividend event/amount |
| `split_factor` | provider split event factor |
| `source_security_id` | provider-native stable identifier, PERMNO, FIGI, ISIN/CUSIP-history, or equivalent |

`raw_close` cannot be filled from `adjusted_close` by fallback. If the provider cannot prove nominal
historical close semantics, the snapshot remains uncertified for P2-P5.

## WiseSheets Status

The repository has a WiseSheets capability gate, but no WiseSheets network acquisition endpoint is
implemented in Phase 1. Public material available to the repo is ambiguous about whether historical
`Close` is true nominal close or split-adjusted. Until authoritative endpoint/schema documentation is
available and a probe verifies split behavior, WiseSheets market-data capabilities remain
`unverified`.

Phase 2A found public WiseSheets spreadsheet-function documentation, but still no authoritative REST
base URL, authentication scheme, historical price endpoint, or response schema suitable for a
network adapter. The repository therefore keeps WiseSheets acquisition behind local exports or future
documented adapter work.

The only allowed credential mechanism is `WISESHEETS_API_KEY` in the environment. API keys must not
be written to code, YAML, fixtures, docs, logs, or committed `.env` files.

## Identifier Handling

`security_id` represents a stable tradable security, not a ticker string. The security master records
ticker history with effective intervals. This allows one security to retain continuity through a
ticker rename while allowing the same ticker to be reused later by a different security.

Continuity is not inferred from company-name similarity, ticker similarity, or CIK alone.

## Membership Handling

Point-in-time S&P 500 membership is represented as inclusive membership spells:

```text
membership_start <= signal_date <= membership_end
```

Multiple non-overlapping spells are valid. A future constituent does not affect current
cross-sectional ranks or weights, though its own pre-entry price history may later be used for its
time-series features after it becomes eligible.

When membership is reconstructed from add/remove events, the Phase 2A helper treats event dates as
effective at the start of the `effective_date`. A removal effective on date `D` is normalized to an
inclusive `membership_end` of `D - 1 calendar day`. This is appropriate only for sources whose events
are documented as effective prior to the open.

## Events And Delistings

Corporate actions are retained as event rows when the source provides them. Missing event coverage is
a capability limitation, not evidence that no event happened.

The Phase 1 pipeline does not invent delisting returns. It reports missing terminal returns, security
disappearance while still marked as a member, and finite membership exits that lack an explicit
terminal delisting/acquisition event.
