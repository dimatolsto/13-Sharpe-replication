# Open-Source S&P 500 Reconstruction

Access date: 2026-08-11

Phase 2B adds a reproducible open/low-cost reconstruction pipeline. It does not certify Wikipedia as
an authority, does not certify Yahoo `Close` by provider claim, and does not run strategy
performance.

## Source Hierarchy

Membership evidence uses three tiers:

1. `SP_PRIMARY`: S&P Global / S&P Dow Jones Indices announcements and current primary pages.
2. `SP_ARCHIVE`: archived copies of S&P / Standard & Poor's primary announcements.
3. `CONTEMPORANEOUS_FALLBACK`: Reuters, exchange announcements, company releases, or similar
   contemporaneous evidence when a primary release cannot be recovered.

Wikipedia's "Selected changes" table is `WIKIPEDIA_SEED`. It is an event-discovery index only. Seed
events remain `UNVERIFIED` until a stronger evidence row supersedes them.

Relevant public references:

- https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
- https://www.spglobal.com/spdji/en/indices/equity/sp-500/
- https://press.spglobal.com/
- https://web.archive.org/

## Event Ledger

The canonical event ledger is a table with one row per membership action:

- `announcement_date`
- `effective_date`
- `effective_session`: `BEFORE_OPEN`, `AFTER_CLOSE`, `DATE_ONLY`, or `UNKNOWN`
- `action`: `ADD` or `REMOVE`
- `source_tier`
- `verification_status`
- factual ticker/name fields
- source/archive URL and SHA-256 metadata fields

The parser stores factual extraction and source links, not full release text. Evidence overlays keep
discrepancies visible instead of silently reconciling them.

## Effective Dates

Membership eligibility is normalized to XNYS/NYSE exchange sessions using
`exchange-calendars==4.13.2`:

- before-open addition: eligible on that trading session;
- before-open removal: last eligible date is the previous trading session;
- after-close addition: first eligible date is the next trading session;
- after-close removal: last eligible date is that session.

Calendar logic is centralized in `src/sharpe_replication/data/trading_calendar.py` through
`is_trading_session(date)`, `previous_session(date)`, and `next_session(date)`. Reconstructed
membership diagnostics and snapshot manifests record the calendar package/version. UNKNOWN
effective-session timing remains an explicit P3 blocker even if a provisional normalized date can be
computed.

## Yahoo Acquisition

Yahoo Finance is an acquisition source only. The deterministic backtest never calls Yahoo.

The raw acquisition settings are fixed as:

```text
interval="1d"
auto_adjust=False
back_adjust=False
repair=False
actions=True
```

`repair=False` is intentional because yfinance repair logic can alter historical values. `Close` and
`Adj Close` are stored separately. `Close` is preserved as `yahoo_close` for raw-close investigation
and is not promoted into certified `raw_close`.

The optional acquisition dependency is pinned as `yfinance==1.5.1`. Yahoo raw-acquisition metadata
records both the installed yfinance version and the exact download settings.

Official yfinance reference: https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html

## Yahoo Limitations

Yahoo is ticker-centric and can fail for delisted or renamed securities. Failures are acquisition
facts, not rows to drop. The acquisition state records `pending`, `complete`, `partial`,
`failed_retryable`, and `failed_permanent`.

Ticker aliases must come from the reconstructed historical membership and security-master mapping.
Fetching only current S&P 500 symbols would recreate survivorship bias.

The Phase 2C real acquisition used the union of provisional historical member identities and found
728 completed alias downloads and 205 permanent/no-data alias failures. Empirical split auditing of
the acquired Yahoo `Close` field failed nominal raw-close certification: 489 of 616 split checks were
classified `likely_back_adjusted`. Yahoo `Close` therefore cannot be promoted to certified
`raw_close` for this reconstruction.

Phase 2C now keeps Yahoo `Close` as an explicit source field and also computes a separate
`reconstructed_nominal_close` candidate by reversing subsequent Yahoo split factors. That candidate
audited as 603 of 616 nominal-consistent split checks, with 4 likely back-adjusted and 9 ambiguous
events, but it is not certified until independent historical-price cross-checks support it.

## Certification

Open reconstruction is provisional until:

- enough membership events are primary/archived/fallback verified;
- unresolved gaps are immaterial and documented;
- raw close passes multiple real split checks;
- adjusted-close or reconstructed total returns pass split/dividend/no-action audits;
- member-day join coverage and terminal-return checks pass;
- stable identifiers are sufficient for the target experiment.

No Phase 2B code weakens P2/P3 certification. If the open-source evidence remains incomplete, P3
stays `FAIL` or `UNVERIFIED`.

Phase 2C left certification in that negative state:

- P2: `FAIL` due Yahoo `Close` split/back-adjustment evidence.
- P3: `FAIL` due P2 failure, unverified/UNKNOWN-timing membership events, provisional IDs, missing
  member-day coverage, and unresolved former-security coverage. The current-session right edge is
  handled as `right_censored_active`, not disappearance while member.
- P4/P5: not run and not certifiable while P3 fails.

Phase 2D added a membership-reconciliation layer on top of this provisional reconstruction. It
extracts underlying citation targets from the frozen Wikipedia HTML, builds a resumable verification
queue, caches bounded source fetches, and writes explicit reports for reconstruction conservation,
count-inflation causes, event groups, identity lineages, reentries, residual gaps, manual review,
anchor comparison, historical snapshot checks, and Yahoo plan delta.

The first bounded pass verified 14 seed events against S&P Global primary releases and found two
additional 2004 secondary event rows. It did not repair identity chains or force counts near 500.
The member-count range remains 503 to 574 with median 543, now explained primarily by provisional
identity alias mismatches and additions not present during backward inversion. Membership remains
`FAIL`.

Phase 2E changes the next membership workflow from broad event verification to snapshot
triangulation. Historical constituent snapshots are used only as checkpoints to locate
disagreements:

- `wikipedia_revision`: deterministic constituent-table revision parser, secondary evidence.
- `riazarbi_ishares`: iShares IVV holdings snapshots, secondary ETF-proxy evidence with CUSIP/ISIN
  and SEDOL enrichment where present.
- `fja05680_sp500`: independent historical ticker-interval dataset, secondary ticker-only evidence.
- `riazarbi_wikipedia` and `riazarbi_tidyquant`: secondary public snapshot archives.
- `sp_primary_snapshot`: preferred official category, but no public historical full-snapshot feed was
  acquired in this bounded pass.

The Phase 2E command compares snapshots by date-aware internal security IDs, not raw ticker strings,
and writes set differences, multi-source disagreements, localized error intervals, targeted primary
research cases, residual gaps, and Yahoo-plan deltas. The first pass compared 30 checkpoints and
found substantial early/mid-period disagreement, but applied no identity repair because secondary
snapshot evidence alone was not enough to merge provisional securities. Membership remains `FAIL`.

## Commands

```bash
uv run drift-replication data acquire-wikipedia-events --raw-dir data/raw/sp500_membership/wikipedia/<id>
uv run drift-replication data parse-wikipedia-events --html data/raw/.../wikipedia_sp500.html --out data/raw/.../wikipedia_events.csv
uv run drift-replication data parse-wikipedia-anchor --html data/raw/.../wikipedia_sp500.html --out reports/generated/phase2c/wikipedia_current_anchor.csv
uv run drift-replication data verify-sp500-events --seed-events seed.csv --evidence-events evidence.csv --out verified.csv
uv run drift-replication data event-completeness --events verified.csv --gaps-out gaps.csv
uv run drift-replication data reconstruct-membership --events verified.csv --anchor-members anchor.csv --anchor-date 2026-08-11 --start-date 2004-01-01 --end-date 2026-08-11 --out membership.csv --metadata-out membership_metadata.json --provisional-security-ids
uv run drift-replication data plan-yahoo --aliases yahoo_aliases.csv --state-out yahoo_state.csv
uv run drift-replication data acquire-yahoo --state yahoo_state.csv --raw-dir data/raw/yahoo/<id> --start-date 2003-10-01 --symbol-sleep 0.1
uv run drift-replication data normalize-yahoo --raw yahoo_symbol.csv --security-id sid --ticker AAPL --panel-out panel.csv --actions-out actions.csv
uv run drift-replication data audit-yahoo --panel panel.csv --corporate-actions actions.csv
uv run drift-replication data compare-wisesheets --yahoo-panel panel.csv --wisesheets-export wisesheets.csv
uv run drift-replication data phase2d-membership-report \
  --events data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_events.csv \
  --anchor reports/generated/phase2c/wikipedia_current_anchor.csv \
  --wikipedia-html data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_sp500.html \
  --membership reports/generated/phase2c/provisional_membership.csv
uv run drift-replication data phase2e-snapshot-report \
  --events data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_events.csv \
  --anchor reports/generated/phase2c/wikipedia_current_anchor.csv \
  --phase2d-dir reports/generated/phase2d \
  --identity-resolutions spec/phase2e_identity_resolutions.csv \
  --out-dir reports/generated/phase2e
```

These commands are data/provenance tools. They do not compute strategy Sharpe, CAGR, wealth, or
drawdown.
