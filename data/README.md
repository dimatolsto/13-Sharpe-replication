# Data Contract

This repository treats normalized Parquet snapshots as the authoritative input to the deterministic
backtest. Provider APIs, spreadsheets, and raw exports are acquisition inputs only.

## Snapshot Layout

```text
data/
  raw/
    <provider>/
      <snapshot_id>/
  normalized/
    <snapshot_id>/
      daily_panel.parquet
      membership.parquet
      security_master.parquet
      corporate_actions.parquet
      manifest.json
      validation_report.json
      certification_report.json
```

Raw and normalized market datasets are intentionally not committed. A snapshot directory is
immutable by default; attempting to normalize into an existing directory fails unless `--force` is
used for a non-certified development snapshot.

## Daily Security Panel

One row per `(date, security_id)`, sorted by `date, security_id`.

Required columns:

| column | meaning |
|---|---|
| `date` | trading date |
| `security_id` | stable tradable-security identifier; ticker alone is insufficient |
| `ticker` | ticker valid on that date |
| `raw_close` | nominal historical close actually applicable on that date |
| `total_return` | corporate-action-correct close-to-close return ending on `date` |

Optional columns include `open`, `high`, `low`, `adjusted_close`, `volume`,
`unadjusted_volume`, `dividend_cash`, `split_factor`, `source_symbol`, and
`source_security_id`.

`raw_close` must not incorporate future splits. The inverse-price signal uses this field directly,
so a retrospectively split-adjusted close is not acceptable. The normalizer refuses to populate
`raw_close` from `adjusted_close` by implicit fallback.

`total_return[date=t]` is the return from close `t-1` to close `t`. It may be provider-supplied or
explicitly reconstructed, but the manifest must state which convention is used.

## Point-In-Time Membership

One row per S&P 500 membership spell, sorted by `security_id, membership_start, membership_end`.

| column | meaning |
|---|---|
| `security_id` | stable identifier present in the panel/security master |
| `membership_start` | first eligible signal date |
| `membership_end` | last eligible signal date, inclusive; null means ongoing |

Multiple non-overlapping spells for the same security are valid. Overlapping spells, duplicate
spells, invalid dates, and references to unknown securities fail validation. The engine uses
`membership_start <= signal_date <= membership_end`.

## Security Master

The security master represents identifier and ticker history:

| column | meaning |
|---|---|
| `security_id` | stable tradable-security identifier |
| `ticker` | ticker used during the effective interval |
| `effective_start` | first date for this identifier/ticker mapping |
| `effective_end` | last date, inclusive; null means ongoing |
| `source` | source of the mapping |
| `source_security_id` | provider-native or external identifier when available |

The model can represent ticker renames, ticker reuse by different issuers, acquisitions, share-class
changes, spin-offs, relistings, and delistings. It does not invent continuity from ticker or company
name similarity. CIK alone is not treated as sufficient proof for one tradable share class.

## Corporate Actions

The event table is sorted by `date, security_id, event_type`.

| column | meaning |
|---|---|
| `security_id` | stable identifier |
| `date` | event/effective date |
| `event_type` | normalized event type |
| `split_factor` | positive N-for-1 split factor, when applicable |
| `dividend_cash` | cash dividend amount, when available |
| `source` | provider/source name |
| `source_event_id` | provider event identifier when available |

Supported event types are `split`, `cash_dividend`, `special_dividend`,
`merger_acquisition`, `delisting`, and `ticker_change`. Missing provider coverage is represented as
a capability limitation, not as evidence that no event occurred.

## Certification

Parsing is not certification. Certification reports use `PASS`, `FAIL`, and `UNVERIFIED` states
across schema, raw-close, total-return, identifier, membership, survivorship, duplicate, interval,
and terminal-return checks.

P2 requires verified nominal raw close and corporate-action-safe returns. The default raw-close
certification rule requires explicit `verified_nominal` provenance plus at least two successful
split checks. P3-P5 also require point-in-time membership, sufficiently stable identifiers,
survivorship checks, valid membership intervals, and missing-terminal-return checks. P0-P1 can be run
on less complete current-constituent data for forensic attribution.
