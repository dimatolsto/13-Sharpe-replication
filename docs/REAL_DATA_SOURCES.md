# Real Data Source Assessment

Access date: 2026-08-10

Phase 2A evaluated real-data paths for a frozen historical S&P 500 dataset. No strategy run, P4/P5
run, or headline Sharpe calculation was performed.

## Requirements

A P3-capable source combination must provide:

- raw historical nominal close, empirically verified against multiple splits;
- corporate-action-safe close-to-close total returns or enough actions to reconstruct them;
- historical and delisted securities, not only names that still trade;
- point-in-time S&P 500 membership with effective dates;
- stable tradable-security identifiers and date-aware ticker history;
- delisting/acquisition/terminal-return diagnostics.

Ticker alone and current constituents copied backward are not acceptable for certified P3 data.

## Source Matrix

The machine-readable matrix is embedded in
`src/sharpe_replication/data/source_discovery.py` and exposed through:

```bash
uv run drift-replication data discover-sources
```

The serious sources evaluated were:

| source | role | result |
|---|---|---|
| WiseSheets Pro | price/export candidate | Not certified. Public docs establish spreadsheet access to historical `Close`, `AdjClose`, and dividend fields, but not a REST endpoint/schema or raw nominal close semantics. |
| CRSP via WRDS | preferred gold-standard path | Best documented P0-P3 path if licensed: PERMNO/PERMCO, prices, returns, distributions, delisting data, and CRSP S&P 500 member tables where subscribed. |
| Norgate Data | commercial fallback | Strong documented fallback if subscribed: unadjusted close, adjusted data options, assetid, delisted securities, and S&P 500 index-constituent time series. |
| S&P Dow Jones Indices public pages/press releases | membership cross-check | Official event/effective-date spot-check source, but no free complete historical PIT constituent database was found. |
| Wikipedia and derived S&P 500 change histories | provisional event source | Useful for provisional reconstruction and cross-checks only. Not authoritative or complete enough for P3 certification. |
| SEC EDGAR | identifier/event cross-check | Useful for CIK/entity/filing evidence. CIK is not sufficient as a tradable-security ID and there is no price or S&P membership coverage. |
| OpenFIGI | identifier enrichment | Useful for FIGI/share-class mapping with rate limits. Does not solve price, returns, or PIT membership. |
| Nasdaq Trader / NYSE corporate-action products | action cross-check | Useful for listed-security events if licensed. Not a full price panel or S&P membership source. |
| Alpha Vantage | secondary price/action API | Documents raw daily and adjusted daily fields, dividends, and splits, but ticker-only identity and delisted/PIT gaps prevent P3 certification alone. |
| Nasdaq Data Link WIKI EOD | rejected | Legacy community dataset; insufficient authority, current coverage, PIT membership, and stable IDs. |

## WiseSheets Finding

Authoritative public material reviewed:

- https://www.wisesheets.io/pages/docs
- https://www.wisesheets.io/available-data

The docs describe the `WISEPRICE` spreadsheet function and historical parameters such as `Close`,
`AdjClose`, `Open`, `High`, `Low`, `Volume`, and dividend outputs. They also state WiseSheets uses a
Yahoo Finance style ticker system.

Phase 2A did not find authoritative REST API documentation with:

- base URL;
- authentication header/query scheme;
- historical price endpoint;
- response schema;
- pagination/rate-limit behavior;
- raw `Close` adjustment semantics;
- delisted-security or stable-ID coverage.

Therefore the repository still must not map WiseSheets `Close` to `raw_close` automatically.
WiseSheets exports can be imported through explicit local mappings, but P2/P3 certification requires
empirical split auditing and documented field lineage.

## Point-In-Time Membership

S&P Dow Jones Indices public press releases are authoritative for sampled change events and often
state effective-date timing such as "prior to the open." They are useful for cross-checking a
reconstruction, but the public pages reviewed do not provide a complete machine-readable historical
constituent table with stable identifiers.

The repository now includes deterministic reconstruction support for add/remove event histories in
`src/sharpe_replication/data/membership_reconstruction.py`. Phase 2B normalizes membership-effective
dates with the XNYS/NYSE exchange calendar from `exchange-calendars==4.13.2`:

```text
ADD before open D    -> membership_start = D
REMOVE before open D -> membership_end = previous XNYS session
ADD after close D    -> membership_start = next XNYS session
REMOVE after close D -> membership_end = D
```

UNKNOWN effective-session timing remains a certification blocker; it is not resolved by applying the
calendar mechanically.

## Price And Return Data

No real normalized price snapshot was produced in Phase 2A because no accessible source simultaneously
provided verified nominal close, corporate-action-safe returns, delisted coverage, stable identifiers,
and PIT S&P 500 membership.

Recommended paths:

1. CRSP/WRDS if the user has or obtains access to CRSP stock data plus CRSP S&P 500 membership
   tables.
2. Norgate Data Platinum/Diamond as a commercial fallback if the user can run the Windows/Python
   integration and export unadjusted close, total-return-equivalent adjusted data/actions, asset IDs,
   delisted securities, and `$SPX` constituent time series.
3. WiseSheets/local exports only as provisional P0/P1/P2 candidates after split and total-return
   audits prove field semantics.

Phase 2B implements the open/low-cost path as tooling, not as a certified snapshot:

- Wikipedia selected changes seed the event ledger but remain `UNVERIFIED`.
- S&P primary and archived announcements are represented as evidence overlays.
- Yahoo Finance/yfinance can supply candidate `Close`, `Adj Close`, dividends, and splits with
  `auto_adjust=False` and `repair=False`.
- WiseSheets local exports can cross-check Yahoo values, but never override them automatically.

This route may support a provisional P0/P1/P2 input later, but P3 requires much stronger membership,
identity, terminal-return, and member-day coverage evidence than Wikipedia plus ticker-centric Yahoo
can provide by default.

Phase 2C tested this route with real acquisitions. The result was useful but negative:

- Wikipedia supplied 737 in-window seed events and a 503-row current anchor, but no events were
  primary/archived/fallback verified in that run and timing remained `UNKNOWN`.
- Yahoo/yfinance completed 728 of 933 planned alias rows and failed 205, largely former/delisted or
  unresolved historical names.
- Yahoo `Close` failed empirical nominal raw-close certification with 489 `likely_back_adjusted`
  split classifications out of 616 checks.
- A separate split-deadjusted `reconstructed_nominal_close` candidate improved split-audit behavior
  but remains uncertified until independent historical-price validation is available.
- Corrected terminal diagnostics classify the 503 active right-edge constituents as
  `right_censored_active`; former-security member-day coverage remains materially incomplete.

The open-source path is therefore not currently P2/P3 certifiable without a separate nominal-close
source, primary event verification, stable identifiers, and former-security price resolution.

Phase 2D advanced the membership side of the open-source route but did not certify it:

- Useful primary event URLs are current S&P Global press releases under `https://press.spglobal.com/`.
- Legacy `standardandpoors.com` article/PDF links in Wikipedia footnotes often redirect to generic
  S&P pages and need Wayback-specific recovery.
- A bounded fetch of 30 candidate sources verified 14 seed events against live S&P Global primary
  releases and extracted `BEFORE_OPEN` timing.
- One 2004 replacement group was found via a secondary PRNewswire mirror, but no primary/archive
  2004 S&P copy was recovered.
- The high reconstructed member counts were decomposed as provisional identity-chain failures rather
  than treated as proof of missing events.

The practical source implication is unchanged: public S&P releases can verify many event facts, but
they are not yet a complete machine-readable PIT membership source.

## Identifier Handling

CRSP PERMNO is the preferred stable security identifier when available. Norgate `assetid` is the
preferred identifier for a Norgate-sourced snapshot. FIGI/shareClassFIGI can enrich mappings but does
not by itself establish historical membership or return semantics.

CIK remains a company/filer identifier, not a certified share-class security identifier.

## Delistings And Terminal Returns

Phase 2A did not invent missing delisting returns. Any future snapshot must quantify:

- securities that exit membership before price history ends;
- securities that disappear while still marked as members;
- missing final return observations;
- acquisition/delisting/ticker-change evidence.

The membership-price join audit is available as:

```bash
uv run drift-replication data audit-coverage \
  --panel data/normalized/<snapshot_id>/daily_panel.parquet \
  --membership data/normalized/<snapshot_id>/membership.parquet \
  --security-master data/normalized/<snapshot_id>/security_master.parquet
```

## Certification Implications

No real immutable P0-P3 snapshot was certified in Phase 2A.

| experiment | current real-data status |
|---|---|
| P0 | No real snapshot produced; local current-constituent inputs remain possible for later attribution. |
| P1 | Same as P0. |
| P2 | Blocked until raw nominal close and corporate-action-safe returns are empirically certified. |
| P3 | Blocked until P2 is satisfied and PIT membership/stable IDs/survivorship/terminal checks pass. |
| P4 | Not run; requires a P3-quality continuous historical snapshot. |
| P5 | Not run; requires a P3-quality post-publication forward snapshot. |

## Next Actions

1. If CRSP/WRDS is available, implement a local extractor for CRSP stock, distributions/delistings,
   name history, and SPX membership tables.
2. If Norgate is chosen, implement a local exporter using the documented Python API and subscription
   capabilities; retain raw exports under `data/raw/norgate/<acquisition_id>/`.
3. If WiseSheets is used, obtain authoritative account/API documentation or produce a spreadsheet
   export with explicit field mappings, then run the split and total-return validators before
   certification.
4. Cross-check sampled S&P change events against S&P Dow Jones Indices press releases before any
   reconstructed membership source is promoted beyond provisional.
