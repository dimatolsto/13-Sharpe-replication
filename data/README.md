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

## Data CLI

The data commands are acquisition/provenance tools and do not compute strategy performance:

```bash
uv run drift-replication data normalize data/normalized/dev-snapshot \
  --daily-source /path/to/daily.csv \
  --daily-map '{"source_date":"date","source_security_id":"security_id","source_ticker":"ticker","source_close":"raw_close","source_return":"total_return"}'

uv run drift-replication data validate --panel data/normalized/dev-snapshot/daily_panel.parquet
uv run drift-replication data inspect data/normalized/dev-snapshot
uv run drift-replication data hash data/normalized/dev-snapshot
uv run drift-replication data certify data/normalized/dev-snapshot --experiment-id P3
uv run drift-replication data wisesheets-capabilities
uv run drift-replication data discover-sources
uv run drift-replication data acquire-wikipedia-events --raw-dir data/raw/sp500_membership/wikipedia/<id>
uv run drift-replication data parse-wikipedia-events --html data/raw/.../wikipedia_sp500.html --out data/raw/.../wikipedia_events.csv
uv run drift-replication data parse-wikipedia-anchor --html data/raw/.../wikipedia_sp500.html --out reports/generated/phase2c/wikipedia_current_anchor.csv
uv run drift-replication data verify-sp500-events --seed-events seed.csv --evidence-events evidence.csv --out verified.csv
uv run drift-replication data event-completeness --events verified.csv --gaps-out gaps.csv
uv run drift-replication data reconstruct-membership --events verified.csv --anchor-members anchor.csv --anchor-date 2026-08-11 --start-date 2004-01-01 --end-date 2026-08-11 --out membership.csv --metadata-out membership_metadata.json --provisional-security-ids
uv run drift-replication data plan-yahoo --aliases yahoo_aliases.csv --state-out yahoo_state.csv
uv run drift-replication data acquire-yahoo --state yahoo_state.csv --raw-dir data/raw/yahoo/<id> --start-date 2003-10-01 --symbol-sleep 0.1
uv run drift-replication data normalize-yahoo --raw yahoo_symbol.csv --security-id sid --ticker AAPL --panel-out yahoo_panel.csv --actions-out yahoo_actions.csv
uv run drift-replication data audit-yahoo --panel yahoo_panel.csv --corporate-actions yahoo_actions.csv
uv run drift-replication data compare-wisesheets --yahoo-panel yahoo_panel.csv --wisesheets-export wisesheets_export.csv
uv run drift-replication data audit-coverage \
  --panel data/normalized/dev-snapshot/daily_panel.parquet \
  --membership data/normalized/dev-snapshot/membership.parquet
```

Phase 2B open-source commands are staged acquisition tools. They do not run P0-P5 or compute
strategy performance. Wikipedia events are unverified seeds until superseded by primary, archived
primary, or fallback evidence. Yahoo `Close` is preserved as `yahoo_close` for raw-close
investigation; it is not promoted to certified `raw_close`.

Phase 2C executed those commands on real Wikipedia and Yahoo inputs. Local generated reports are
under `reports/generated/phase2c/`, and raw files are under gitignored `data/raw/...` directories.
No immutable normalized snapshot was frozen because the data failed certification:

- Wikipedia seed events from 2004 onward remain unverified and effective-session timing is unknown.
- Yahoo `Close` failed empirical nominal raw-close certification on real split events.
- Yahoo acquisition left former/delisted member aliases unresolved, with missing member-day and
  terminal-return blockers.
- A separate `reconstructed_nominal_close` candidate exists for investigation, but it is not certified
  until independent cross-source validation supports it.

The candidate Yahoo panel and audits are useful forensic evidence only; they must not be used as a
P2/P3 certified input.

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
`unadjusted_volume`, `dividend_cash`, `split_factor`, `source_symbol`, `source_security_id`,
`yahoo_close`, `reconstructed_nominal_close`, `split_adjustment_multiplier`, and
`raw_close_source`.

`raw_close` must not incorporate future splits. The inverse-price signal uses this field directly,
so a retrospectively split-adjusted close is not acceptable. The normalizer refuses to populate
`raw_close` from `adjusted_close` by implicit fallback.

Yahoo `Close` is preserved as `yahoo_close` and must not be copied into `raw_close` unless a future
source-specific certification explicitly proves nominal semantics. `reconstructed_nominal_close` is a
separate investigation field and must not silently replace `raw_close`.

`total_return[date=t]` is the return from close `t-1` to close `t`. It may be provider-supplied or
explicitly reconstructed, but the manifest must state which convention is used.

## Point-In-Time Membership

One row per S&P 500 membership spell, sorted by `security_id, membership_start, membership_end`.

| column | meaning |
|---|---|
| `security_id` | stable identifier present in the panel/security master |
| `membership_start` | first eligible signal date |
| `membership_end` | last eligible signal date, inclusive; null means ongoing |
| `timing_uncertain` | optional flag set by provisional reconstructions when event effective-session timing is unknown |

Multiple non-overlapping spells for the same security are valid. Overlapping spells, duplicate
spells, invalid dates, and references to unknown securities fail validation. The engine uses
`membership_start <= signal_date <= membership_end`. UNKNOWN effective-session timing remains a P3
certification blocker even when a provisional membership spell can be materialized.

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
