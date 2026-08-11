# Phase 2C Real Open-Source Reconstruction Run

Run date: 2026-08-11

Phase 2C executed the open-source reconstruction pipeline against real public inputs. It did not
run P0, P1, P2, P3, P4, P5, or any portfolio performance calculation.

## Raw Inputs

Wikipedia seed source:

- URL: `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies`
- Raw snapshot: `data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/`
- Raw HTML SHA-256: `8386fe7d000b151d264413fd20a79e6bdc4785bf54b9bbb825f9c779422dcaf7`
- Parsed seed events: 772 total, 737 from 2004-01-01 through 2026-08-11
- In-window additions/removals: 370 additions, 367 removals
- Earliest parsed event: 1976-07-01
- Latest parsed event: 2026-08-05
- Current-anchor rows parsed from Wikipedia: 503

Wikipedia remains a seed and provisional anchor, not authoritative PIT membership evidence. The
Phase 2C pass did not complete primary/archived S&P verification overlays, so all in-window seed
events remain `UNVERIFIED` and `UNKNOWN` effective-session timing.

Yahoo raw acquisition:

- Raw directory: `data/raw/yahoo/20260811_phase2c_open_reconstruction/`
- yfinance version: `1.5.1`
- Settings: `interval=1d`, `auto_adjust=False`, `back_adjust=False`, `repair=False`,
  `actions=True`, `keepna=True`, `threads=False`, `progress=False`
- Planned alias rows: 933
- Unique planned Yahoo symbols: 857
- Complete alias rows: 728
- Failed permanent/no-data alias rows: 205
- Candidate panel rows: 3,646,880
- Candidate corporate-action rows: 40,726
- Candidate price coverage: 2003-01-02 through 2026-08-10

The real Yahoo CSV output produced by yfinance 1.5.1 serializes single-symbol MultiIndex columns
with an extra ticker row. The parser drops that metadata row before date normalization; tests cover
this exact raw shape.

WiseSheets:

- No local WiseSheets export was present in the expected project locations.
- Requested cross-check pack written locally to
  `reports/generated/phase2c/wisesheets_requested_crosscheck_updated.csv`.
- Updated requested pack rows: 58, prioritizing Yahoo likely-back-adjusted splits,
  nominal-consistent split checks, ambiguous splits, dividend anomalies, and ordinary dividend
  controls.
- No WiseSheets REST API was invented or used.

## Event Reconciliation

Year-by-year reconciliation is written locally to
`reports/generated/phase2c/sp500_event_reconciliation.csv`.

Summary for 2004-present:

- Primary verified: 0
- Archived-primary verified: 0
- Fallback verified: 0
- Unresolved seed events: 737
- Additional events discovered outside Wikipedia: 0
- Timing-unknown events: 737
- 2004 has zero Wikipedia seed events and is flagged as a suspected gap year.

This is a blocking membership finding. A reconstructed table can be useful for audit debugging, but
it is not defensible P3 membership evidence.

## Provisional Membership

The provisional reconstruction used the 2026-08-11 Wikipedia current table as an unverified anchor
and applied Wikipedia seed changes backward using the pinned XNYS calendar.

- Membership rows: 869
- Unique provisional security IDs: 859
- Target-session reconstructed member-count range: 503 to 574
- Target-session median reconstructed count: 543
- Reentries: 10
- Static-universe diagnostic: false

The high early counts and large number of unmatched removal diagnostics are consistent with a
provisional current-anchor reconstruction plus incomplete/ambiguous historical event and identity
evidence. No events were invented to force the count near 500.

Historical spot checks are written locally to
`reports/generated/phase2c/historical_membership_spot_checks.csv`.

## Identity Status

Security IDs are provisional `wiki:<symbol>:<normalized-name>` identifiers. This avoids ticker-only
identity collapse, but it is not a provider-native permanent security identifier.

- Security-master alias rows: 933
- Unique membership security IDs: 859
- External stable IDs: absent
- Ticker renames: not certified
- Ticker reuse: kept separate by symbol+name where distinct, but unresolved without external IDs

This is P3-blocking until external stable identifiers or reviewed manual identity evidence are
added.

## Yahoo Price And Return Audits

Raw-close split audit:

- Split events checked: 616
- `consistent_with_nominal`: 114
- `likely_back_adjusted`: 489
- `ambiguous`: 13

This fails Yahoo `Close` as a certified nominal historical raw-close source. The paper inverse-price
feature requires nominal historical close; adjusted or split-normalized history is not acceptable.
The 114 nominal-consistent classifications do not rescue Yahoo `Close`: 103 of them are small split
ratios at or below 1.25 that fall inside the current broad tolerance, while 489 events, including
262 2-for-1 splits, look back-adjusted. All classes had adjacent observations available, so this is
not mainly an adjacent-data availability artifact.

Candidate split de-adjustment:

- Field: `reconstructed_nominal_close`
- Formula: multiply a Yahoo `Close` observation by the product of later Yahoo split factors, using
  dates strictly before each split event as pre-split observations.
- Reconstructed split audit: 616 events checked, 603 `consistent_with_nominal`, 4
  `likely_back_adjusted`, 9 `ambiguous`.

This transform is deterministic and provenance-tracked, but it is not yet certified. Cross-source
validation against independent historical observations, such as WiseSheets exports, remains pending.

Candidate total-return source:

- Source: `yahoo_adjusted_close`
- Split-date events with non-catastrophic candidate return: 612
- Split-date suspicious returns below -30%: 4
- Dividend events audited: 40,110
- Dividend classifications: 40,090 consistent, 10 insufficient data, 10 material mismatches
- No-action return consistency checks: 3,579,648
- No-action inconsistencies above 1%: 0
- Extreme absolute candidate returns above 25%: 1,988

The adjusted-return candidate is useful audit evidence, but the unresolved split/dividend/extreme
observations must remain visible. The four split-return anomalies are JCI on 2007-07-02, duplicate
EXPE identities on 2011-12-21, and MI on 2024-04-12. The ten material dividend mismatches are
written to `reports/generated/phase2c/dividend_material_anomalies.csv`. No observations were
repaired or deleted.

## Membership-Price Join

Join audit path: `reports/generated/phase2c/membership_price_join_audit.json`

- Corrected provider-edge audit does not count the unfinished 2026-08-11 session.
- Membership security IDs: 859
- Price security IDs: 728
- Member dates lacking price rows: 640,008
- Member dates lacking the legacy raw-close candidate field inside price rows: 137
- Member dates lacking candidate total return inside price rows: 276
- Affected missing security IDs: 750
- First missing date: 2004-01-02

Coverage by year is written to `reports/generated/phase2c/member_day_coverage.csv`. Any future
coverage table used for snapshot freeze must apply the provider-edge convention so the unfinished
current XNYS session is not treated as missing daily data.

## Terminal And Survivorship Audits

Corrected terminal-return audit path:
`reports/generated/phase2c/terminal_return_audit_corrected.csv`

- Latest completed XNYS session at acquisition: 2026-08-10
- Latest available Yahoo provider session: 2026-08-10
- `right_censored_active`: 503
- `price_ends_after_membership`: 166
- `unmapped_no_yahoo_price`: 190
- `price_without_membership`: 59
- True `disappears_while_member`: 0
- Terminal blocking risks after right-edge correction: 205

Survivorship diagnostics show 859 historical security IDs, 356 no longer active at the dataset end,
10 reentries, and `static_universe_like=false`. The reconstruction is not a static current-list
backfill, but it remains provisional and uncertified because membership evidence, identities, and
price coverage are incomplete.

## Snapshot And Certification

No immutable normalized real snapshot was frozen in Phase 2C. The evidence does not support a
certified snapshot because:

- Wikipedia seed events were not verified against primary/archived S&P evidence.
- All in-window event timing remains `UNKNOWN`.
- The anchor is a current Wikipedia table and is only provisional.
- Security IDs lack external stable identifiers.
- Yahoo failed for 205 planned aliases.
- Yahoo `Close` failed empirical nominal raw-close certification.
- Member-day coverage has material blockers.
- The corrected terminal audit no longer treats the in-progress 2026-08-11 session as missing data,
  but 190 unmapped/no-price securities remain terminal/coverage risks.

Experiment-specific data certification after Phase 2C:

- P0: `UNVERIFIED` because no immutable normalized snapshot was frozen.
- P1: `UNVERIFIED` because no immutable normalized snapshot was frozen.
- P2: `FAIL` because raw-close nominal certification fails. Total-return certification is a separate
  `UNVERIFIED` dimension pending investigation of 4 split-return anomalies and 10 material dividend
  mismatches.
- P3: `FAIL` because P2 fails and PIT membership/identity/member-day coverage are unresolved.
- P4: `FAIL` because P3 fails. P4 was not run.
- P5: `FAIL` because P3 fails. P5 was not run.

## Next Data-Resolution Tasks

1. Verify the 737 in-window Wikipedia seed events against primary S&P, archived primary S&P, or
   high-quality contemporaneous fallback evidence.
2. Discover missing 2004 and other non-Wikipedia S&P events from primary archives.
3. Replace provisional `wiki:<symbol>:<name>` identities with external stable IDs or reviewed
   manual identity mappings.
4. Resolve the 205 failed Yahoo aliases, prioritizing member-day gaps and former constituents.
5. Cross-check `reconstructed_nominal_close` against independent historical observations before any
   raw-close certification change.
6. Investigate the four suspicious split-return events, ten material dividend mismatches, ten
   insufficient dividend checks, and the highest extreme-return observations.
7. Obtain and ingest the 58-row WiseSheets export request pack for independent cross-checking.
