# Phase 2D Membership Reconciliation

Run date: 2026-08-11

Phase 2D focused only on S&P 500 membership, effective-date/session evidence, and tradable-security
identity. It did not run P0, P1, P2, P3, P4, P5, strategy returns, Sharpe, CAGR, drawdown, wealth, or
portfolio calculations.

## Inputs

- Wikipedia seed ledger: `data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_events.csv`
- Wikipedia raw HTML: `data/raw/sp500_membership/wikipedia/20260811_wikipedia_sp500/wikipedia_sp500.html`
- Provisional anchor: `reports/generated/phase2c/wikipedia_current_anchor.csv`
- Provisional membership: `reports/generated/phase2c/provisional_membership.csv`
- Prior Yahoo state, for plan-delta classification only: `reports/generated/phase2c/yahoo_state.csv`

Generated local reports are under `reports/generated/phase2d/`. Raw retrieved source artifacts are
under `data/raw/sp500_membership/phase2d_source_cache/`; both locations remain gitignored.

## Event Verification

Phase 2D extracted Wikipedia footnote targets from the frozen raw HTML and built a persistent
verification queue. A bounded run fetched 30 candidate primary/archive URLs and cached SHA-256
metadata for each retrieved artifact.

Results for the 737 Wikipedia seed events from 2004-present:

- Primary verified: 14
- Archived-primary verified: 0
- Secondary verified inside the seed ledger: 0
- Unresolved seed events: 723
- Effective timing extracted as `BEFORE_OPEN`: 14
- Effective timing still `UNKNOWN`: 723

Verified primary examples include the S&P Global press releases for:

- Fox Corporation / 21st Century Fox, effective 2019-03-19 before open.
- Interactive Brokers / Walgreens Boots Alliance, effective 2025-08-28 before open.
- Solstice Advanced Materials and Qnity Electronics multi-date changes, effective before open.
- Sandisk / Interpublic Group, effective 2025-11-28 before open.
- Veeva Systems / Coterra Energy, effective 2026-05-07 before open.

This improves the Phase 2C state, where primary/archive/fallback verified counts were all zero, but
it is far short of a complete 2004-present event certification.

## 2004 Assessment

The Wikipedia seed still has zero 2004 rows. Phase 2D found one additional non-Wikipedia replacement
group from a Standard & Poor's PRNewswire release mirrored by ADVFN:

- Effective after close on 2004-03-31.
- Add E*TRADE Financial.
- Remove FleetBoston Financial.
- Source: `https://br.advfn.com/noticias/PRNUS/2004/artigo/7217409`

This is recorded as secondary contemporaneous evidence, not primary S&P evidence. A primary or
archived-primary 2004 S&P copy was not recovered in this pass, and 2004 remains a suspected
missing-event gap with P3-blocking uncertainty.

## Count-Inflation Diagnosis

The mandatory reconstruction-conservation audit starts at the 503-row 2026-08-11 provisional anchor
and walks the event ledger backward, recording one row per event with count-before/count-after,
operation, and flags.

Before Phase 2D:

- Member-count min: 503
- Member-count median: 543
- Member-count max: 574

After Phase 2D:

- Member-count min: 503
- Member-count median: 543
- Member-count max: 574
- Membership spells: 869
- Unique reconstructed historical membership security IDs: 859
- Count anomaly intervals: 1, spanning 2004-01-02 through 2019-03-18 with high counts from 527 to
  574.

The count was not repaired mechanically. The audit explains why it is inflated:

- Backward conservation failures: 76
- Net backward excess delta: 74
- Forward conservation failures: 2
- Dominant cause: 64 net excess from identity alias mismatches, usually current-anchor and event rows
  representing the same ticker/security with different provisional `wiki:<ticker>:<name>` IDs.
- Additional cause: 11 net excess from additions not present during backward inversion.
- Offset: 1 removal already present during backward inversion.

This shows the 543 median is primarily a provisional identity-chain artifact, not ordinary S&P 500
share-class variation and not something to fix by forcing counts to 500.

## Event Groups

Phase 2D materializes a group table keyed by `replacement_group_id`. It records group additions,
removals, seed net count change, and `expected_net_count_change` only where evidence supports it.
Unverified or unbalanced groups do not receive an asserted zero-net expectation.

## Identity Lineages

Phase 2D creates an explicit machine-readable lineage table instead of hiding identity assumptions in
Python:

- Lineage rows: 1,240
- Unique internal security IDs represented: 933
- Identity ambiguity rows: 232
- Provisional reentries reviewed: 10, all unresolved

The table records CIK enrichment from the current anchor where available, but CIK remains issuer-level
evidence and is not treated as tradable-security authority. No identity merge was made solely from
ticker or company-name similarity.

## Anchor

The current anchor remains the 2026-08-11 Wikipedia current-constituent table:

- Anchor count: 503
- Source tier: Wikipedia anchor / unverified
- Cross-check: no better primary public constituent list was located in this automated pass

The anchor remains provisional.

## Gaps And Certification

Phase 2D writes a residual gap register and manual-review queue. The generated summary contains:

- Total residual gaps: 2,024
- P3-blocking gaps: 2,024
- Manual-review queue size: 2,024

Membership component certification remains `FAIL`. P2 raw-close status remains the Phase 2C `FAIL`,
so overall P3 cannot pass regardless of membership progress.

## Yahoo Plan Impact

No full Yahoo redownload was performed. Phase 2D only compared the prior Yahoo state to the explicit
lineage table:

- Previous required historical IDs: 933
- Corrected required historical IDs: 933
- IDs removed as reconstruction/identity artifacts: 0
- Genuinely new historical members: 0
- Old Yahoo failures still required: 205
- Old Yahoo failures no longer relevant: 0

Because Phase 2D diagnosed rather than repaired identity chains, the Yahoo plan is unchanged and all
205 prior failures remain visible.

## Certification Outcome

Valid Phase 2D outcome: `FAIL`.

The phase materially improved evidence visibility and primary verification counts, but material
event, timing, identity, anchor, and 2004 completeness gaps remain. Wikipedia remains a discovery
seed, not authority.

## Phase 2E Follow-Up

Phase 2E used historical snapshot triangulation to target the Phase 2D identity and reconstruction
failures instead of attempting to verify the remaining events one by one. The follow-up compared
30 checkpoints from secondary snapshot families and reviewed the 64 identity mismatches, 12 backward
failures, and 10 provisional reentries identified here.

The follow-up did not change the Phase 2D membership counts or certification. No identity merge or
event correction was applied because the available snapshot evidence did not satisfy the repository's
standard for lineage repair. The Phase 2D conservation diagnosis remains the operative explanation
for the 503/543/574 count range.
