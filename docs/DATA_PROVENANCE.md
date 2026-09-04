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

Phase 2B adds an open-source reconstruction path:

```text
Wikipedia selected-change seed -> primary/archived/fallback evidence overlay
    -> event completeness/gap register -> PIT membership reconstruction
    -> date-aware Yahoo aliases -> Yahoo raw cache -> candidate normalized panel/actions
    -> WiseSheets local-export cross-check -> validation/certification
```

Wikipedia is never treated as an authority. Yahoo and WiseSheets are acquisition/cross-check inputs;
the deterministic backtest still consumes only normalized Parquet snapshots.

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

Phase 2B reconstruction uses the XNYS/NYSE exchange calendar from
`exchange-calendars==4.13.2`. In that mode, a before-open removal effective on `D` ends on the
previous XNYS session, not the previous calendar day. An after-close addition starts on the next XNYS
session. Reconstruction diagnostics and snapshot manifests record the calendar package/version.
UNKNOWN effective-session timing remains a certification blocker even if a provisional normalized
date can be computed.

## Yahoo Finance

Yahoo/yfinance acquisition must use:

```text
interval=1d
auto_adjust=False
back_adjust=False
repair=False
actions=True
```

`Close` is retained separately from `Adj Close` as `yahoo_close`; it is not promoted into
`raw_close`. Candidate total returns from `Adj Close` are recorded as
`yahoo_adjusted_close` and remain uncertified until split, dividend, and no-action diagnostics pass.
Raw Yahoo acquisition metadata records the pinned yfinance package version and the exact download
settings.

Per-symbol acquisition state files must preserve failed and partial symbols. Delisted/former
constituents are not dropped merely because Yahoo cannot resolve them.

Phase 2C provenance note: the real yfinance 1.5.1 CSV output included a second ticker-name header
row when a single-symbol MultiIndex frame was serialized. The Yahoo parser treats that row as
metadata and drops it before date normalization. This does not fill or repair any price/return row.

Phase 2C also established that Yahoo `Close` from the real acquisition is not a verified nominal
historical close source. Split auditing classified most usable split events as `likely_back_adjusted`,
so any future snapshot that uses Yahoo must keep `Close` as a failed candidate or obtain another
nominal-close source.

The separate Phase 2C `reconstructed_nominal_close` candidate reverses later Yahoo stock-split
factors into earlier Yahoo `Close` observations. It preserves Yahoo `Close` unchanged, records the
split-adjustment multiplier, and remains uncertified until an independent source such as a
WiseSheets export supports the reconstructed historical prices.

## Phase 2D Membership Evidence

Phase 2D adds a membership-only provenance layer. The command
`drift-replication data phase2d-membership-report` reads Phase 2C artifacts and writes local reports
under `reports/generated/phase2d/`.

The command extracts Wikipedia footnote target URLs from the frozen raw HTML, creates a resumable
verification queue, and caches bounded source fetches under
`data/raw/sp500_membership/phase2d_source_cache/`. Each cached artifact has metadata for original
URL, final URL, retrieval time, SHA-256, source tier, HTTP status, and linked event IDs. Cached
press releases are raw provenance material and remain gitignored.

Phase 2D verified 14 seed events against live S&P Global primary press releases and extracted
`BEFORE_OPEN` timing. It also recorded one additional 2004 replacement group from secondary
contemporaneous evidence. This improves evidence coverage but does not certify the event ledger:
723 seed events still lack primary/archive/fallback verification and effective-session timing.

The phase also writes machine-readable identity lineages and reconstruction-conservation reports.
Those reports are evidence registers, not hidden code overrides. They do not merge securities, force
counts to 500, or change strategy inputs.

## Phase 2E Snapshot Provenance

Phase 2E adds historical constituent snapshots as cross-check evidence, not authority. The command
`drift-replication data phase2e-snapshot-report` writes local reports under
`reports/generated/phase2e/` and caches bounded raw artifacts under
`data/raw/sp500_membership/phase2e_snapshot_cache/`.

Each fetched snapshot artifact records source ID, canonical URL, retrieval timestamp, HTTP status,
SHA-256, parser name/version, and known limitations. The evaluated source categories are:

- Wikipedia constituent-table revisions, parsed by deterministic revision fixtures but not live
  crawled by default.
- iShares IVV holdings snapshots from the riazarbi public archive, treated as ETF-proxy secondary
  snapshots with CUSIP, ISIN, and SEDOL enrichment where present.
- The fja05680 historical S&P 500 ticker-interval dataset, treated as secondary ticker-only evidence.
- riazarbi Wikipedia and tidyquant snapshots, treated as secondary snapshots.
- Official S&P constituent snapshots, which were evaluated as the preferred source but no public
  historical full-snapshot feed was acquired in this bounded pass.

Snapshot comparisons map source rows through date-aware internal security IDs before set
comparison. Rows that cannot be mapped, map ambiguously, or disagree across sources remain explicit
gap records. Phase 2E does not treat iShares holdings as official index membership, does not repair
membership from secondary snapshot agreement alone, and does not use Yahoo price availability to
resolve identity.

## Phase 3 Paper-Attribution Provenance

Phase 3 intentionally uses a fixed current-constituent paper-like universe:

```text
SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE
```

This is a controlled attribution input, not point-in-time membership evidence. The command
`drift-replication phase3-attribution` writes local artifacts under `reports/generated/phase3/`.
Those files include `input_manifest.json`, `anchor_universe.csv`, copied experiment YAMLs, summary
tables, daily returns, and security-level ledgers. The directory remains gitignored.

The Phase 3 anchor is the cached riazarbi iShares IVV holdings snapshot dated 2025-12-22 because it
is the nearest existing cached snapshot to the paper publication date, 2025-11-18. It has 503
constituents and secondary ETF-proxy provenance. It is 34 days after publication and is not official
S&P point-in-time membership. Five anchor symbols lacked cached Yahoo market data in the existing
Phase 2C acquisition; they remain recorded in the anchor and are simply ineligible where data is
unavailable.

The Phase 3 market panel is built from `reports/generated/phase2c/yahoo_source_panel.parquet`.
Duplicate Phase 2C provisional identities sharing the same Yahoo provider symbol/date are collapsed
for Phase 3 so a current anchor symbol cannot create multiple simultaneous paper-like positions.
No full Yahoo redownload is performed.

Phase 3 price lineage remains explicit:

- `yahoo_close` is Yahoo `Close`, used for R0/R1 VALUE only. It is not certified nominal raw close.
- `yahoo_adj_close` is Yahoo `Adj Close`, used to compute `yahoo_adj_close_pct_change`.
- `reconstructed_nominal_close` is rebuilt by applying the existing Phase 2C split de-adjustment to
  Yahoo `Close`. It is used for R2/R3 VALUE only and remains a forensic candidate pending
  independent cross-source validation.

Security-level ledgers in Phase 3 are deterministic accounting artifacts with signal date, weight
date, earned return date, return, contribution, turnover contribution, cost contribution, and net
contribution. They are generated locally and not committed because they are large derived market
data artifacts.

## Events And Delistings

Corporate actions are retained as event rows when the source provides them. Missing event coverage is
a capability limitation, not evidence that no event happened.

The Phase 1 pipeline does not invent delisting returns. It reports missing terminal returns, security
disappearance while still marked as a member, and finite membership exits that lack an explicit
terminal delisting/acquisition event.

Terminal audits use both the latest completed XNYS exchange session and the latest provider session
actually returned by acquisition. Active securities at the provider right edge are classified as
`right_censored_active`; an unfinished current daily bar is not treated as a missing terminal return.
