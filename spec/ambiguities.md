# Paper ambiguities tracked explicitly

The implementation must not silently resolve these items.

1. **Nominal price direction.** The paper describes inverse price converted to percentile rank. We
   define larger `1 / raw_close` as a larger value score.
2. **Reversal return convention.** The paper says trailing 10-day return but does not give an
   equation comparable to the regime definition. Phase 3 legacy code used a 10-session total return
   ending on signal date `t`; Phase 3B also reports a prior-10-session interpretation excluding
   current return `t`. Endpoint prior-10 and compounded prior-10 match numerically on the Phase 3
   panel, while current-day reversal leakage remains an invalid diagnostic only.
3. **63-day up-fraction window.** The paper equation defines
   `UpFraction[t] = (1/63) * sum_{k=1..63} I(return[t-k] > 0)`, excluding current return `t`. Phase 3
   legacy code instead rolled through signal date `t`; Phase 3B preserves that exact result as a
   legacy/invalid paper-equation fingerprint and separately reports the paper-spec prior-window
   variant. Future production changes to this convention must be versioned and rerun consistently.
4. **Second standardization.** The paper states valid non-zero EDGE values are standardized again.
   We z-score only active names, then take positive scores long and negative scores short.
5. **Side weighting.** The paper says normalize each side but does not give the exact rule. We use
   weights proportional to `abs(z_edge)` within each side. Equal-weight is retained as a diagnostic
   variant but is not the primary specification.
6. **Training-derived scaling.** The paper's scale formula is allowed to exceed 1.0. The replication
   preserves that behavior in the paper-scaled P0-P3 variants, while always reporting unscaled Sharpe
   too. The explicit train/test mappings are encoded in experiment YAML as half-open intervals:
   `[2005-01-01, 2010-01-01) -> [2010-01-01, 2011-01-01)`,
   `[2010-01-01, 2015-01-01) -> [2015-01-01, 2016-01-01)`, and
   `[2015-01-01, 2020-01-01) -> [2020-01-01, 2021-01-01)`.
7. **Transaction-cost turnover convention.** We define one-way turnover as
   `0.5 * sum(abs(w_t - w_{t-1}))`; a cost of `x` bp per unit turnover means
   `turnover * x * 1e-4` deducted from portfolio return.
8. **Sharpe risk-free rate.** We use zero, matching common short-horizon backtest convention unless
   the paper's code establishes otherwise.
9. **Paper OOS date labels.** The paper calls the three test periods `2010–2011`, `2015–2016`,
   and `2020–2021` while also describing each as one year. P0-P3 interpret these labels as half-open
   one-year intervals `[2010, 2011)`, `[2015, 2016)`, and `[2020, 2021)`, i.e. calendar years
   2010, 2015, and 2020. This interpretation is explicitly tagged and must be changed only if author
   code/data establishes different exact dates.
10. **Continuous scaling extension.** P4 is the primary continuous historical-clean result and P5 is
    the forward result; both are unscaled by default. Any future rolling 5-year/1-year scaling variant
    must be labeled as an extension, not as the paper's scaling rule.
11. **Provider close semantics.** A provider field named `Close` is not assumed to be raw nominal
    historical close. The data pipeline requires explicit provenance and split-audit evidence before
    marking `raw_close` as `verified_nominal`.
12. **Point-in-time membership effective dates.** The repository normalizes membership intervals to
    inclusive signal-date eligibility, `membership_start <= signal_date <= membership_end`. A source
    with different add/delete effective-date semantics must document the transformation before
    certification.
13. **Stable security identity.** Ticker, company name, and CIK alone are not assumed to identify one
    tradable share class through time. Continuity across ticker changes or share-class events must
    come from a security master or provider-native stable identifier.
14. **Terminal returns.** The Phase 1 pipeline reports missing terminal returns and missing
    delisting/acquisition events but does not invent CRSP-style delisting returns. P3-P5
    certification fails when a security disappears while still a member or its final observed return
    is missing.
15. **Membership change-event timing.** Phase 2A reconstruction treats add/remove events as
    effective at the start of `effective_date`; a removal effective on `D` becomes an inclusive
    `membership_end` of `D - 1 calendar day`. This must be used only for sources documented as
    prior-to-open effective changes.
16. **Source capability evidence.** Provider documentation is not enough to certify raw nominal
    close. Real snapshots still require empirical split/raw-close and total-return audits.
    WiseSheets remains unverified until an authoritative API/export schema and split probes prove
    semantics.
17. **Wikipedia selected changes.** Wikipedia's S&P 500 change table is an event-discovery seed, not
    an authoritative PIT membership database. Missing events can produce a plausible-looking but
    wrong membership reconstruction. P3 must remain unverified or fail while material seed events are
    not supported by primary, archived-primary, or high-quality contemporaneous fallback evidence.
18. **Open-source effective sessions.** If an S&P release says "prior to the open", membership dates
    are normalized with the XNYS calendar from `exchange-calendars==4.13.2`. UNKNOWN
    effective-session timing remains explicitly uncertain and must block P3 certification until the
    event evidence establishes before-open, after-close, or another defensible convention.
19. **Yahoo ticker coverage.** Yahoo/yfinance is ticker-centric and may not resolve delisted,
    renamed, or acquired former constituents. Missing Yahoo histories are acquisition failures, not
    permission to drop historical members.
20. **Yahoo adjusted-close returns.** `Adj Close` percentage returns are a candidate corporate-action
    series only. They are not certified total returns until split, dividend, no-action, and terminal
    diagnostics pass.
21. **Yahoo close in Phase 2C.** The real Phase 2C Yahoo acquisition showed that `Close` is not
    reliable nominal historical close for this project: 489 of 616 split checks classified it as
    `likely_back_adjusted`. Future work must not reinterpret that field as certified `raw_close`
    without new source evidence and a new split audit.
22. **Reconstructed nominal close candidate.** Reversing subsequent Yahoo split factors into prior
    Yahoo `Close` observations is an offline reconstruction of historical as-traded prices, not a
    predictive strategy input. The Phase 2C `reconstructed_nominal_close` field remains a candidate
    until independent historical observations validate it.
23. **Current-session right edge.** Data audits must distinguish the latest completed XNYS exchange
    session from the latest provider session actually returned. Active constituents at that right edge
    are `right_censored_active`; an unfinished current daily bar is not a terminal disappearance.
24. **Phase 2D event verification remains incomplete.** A bounded Phase 2D pass verified 14 seed
    events against S&P Global primary releases and found one 2004 secondary replacement group, but
    723 in-window seed events remain unresolved with `UNKNOWN` timing. Future work must not infer
    effective sessions from common S&P practice without source wording.
25. **Phase 2D count inflation is an identity diagnostic, not a repair target.** The 503-574
    reconstructed member-count range is mainly explained by provisional `wiki:<ticker>:<name>`
    identity alias mismatches and additions not present during backward inversion. Do not force
    counts to 500 or invent events to eliminate the anomaly.
26. **2004 remains a suspected membership gap.** Wikipedia has zero 2004 seed rows. Phase 2D found a
    secondary E*TRADE/FleetBoston replacement effective after close on 2004-03-31, but no primary or
    archived-primary S&P copy was recovered. Treat 2004 as incomplete until primary/archive or
    multiple reliable fallback sources resolve it.
27. **Internal lineages are explicit but mostly provisional.** Phase 2D writes identity lineages and
    ambiguity registers, but CIK enrichment is issuer-level only and does not certify tradable
    security identity. Ticker/name similarity must remain insufficient for merging identities.
28. **Historical snapshots are cross-checks.** Phase 2E compares Wikipedia-derived snapshots,
    iShares IVV holdings snapshots, tidyquant snapshots, and independent ticker-interval data against
    the reconstructed PIT universe. These sources localize disagreement but do not become authority
    simply because they are dated snapshots.
29. **ETF holdings are not official index membership.** iShares IVV holdings can enrich identifiers
    such as CUSIP, ISIN, and SEDOL, but cash rows, non-equity rows, fund timing, tracking behavior,
    and share-class representation can differ from official S&P membership.
30. **Snapshot-date semantics are explicit.** A weekend, holiday, month-end, or after-close snapshot
    must be mapped to a documented XNYS membership state before comparison. Silent date shifting can
    create false set differences.
31. **Phase 2E repair evidence remains insufficient.** The first snapshot-triangulation pass found
    large disagreements and useful targeted research cases, but applied no identity repairs. Future
    repairs must be represented in `spec/phase2e_identity_resolutions.csv` and supported by primary,
    archived-primary, strong multi-source, or contemporaneous evidence.
32. **Phase 3 intentionally preserves survivorship bias.** The paper acknowledges current S&P 500
    constituents were used historically. Phase 3 therefore freezes a current-constituent
    `SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE` to isolate timing, price, scaling, accounting, and
    window-selection issues. These runs must not be described as investable PIT simulations.
33. **Phase 3 anchor exactness is approximate.** The preferred exact publication-date constituent
    list was not reconstructed. Phase 3 uses the nearest existing cached snapshot, a 2025-12-22
    iShares IVV holdings file, 34 days after the 2025-11-18 publication date. The anchor is frozen
    before results and must not be changed based on performance.
34. **Phase 3 current symbols are paper-like provider identities.** The Phase 3 universe collapses
    duplicate Phase 2C provisional identities by current provider symbol/date so one anchor symbol
    cannot create multiple positions. This is appropriate only for the intentionally
    survivorship-biased paper-like reproduction and must not be reused as PIT security identity
    evidence.
35. **Phase 3 reconstructed nominal close remains candidate-only.** R2/R3 use the Phase 2C
    split-deadjusted `reconstructed_nominal_close` for the inverse-price VALUE feature, but P&L,
    reversal, and regime calculations continue to use Yahoo `Adj Close` percentage returns. The
    reconstructed nominal field remains uncertified until independent historical observations
    validate it.
36. **Phase 3 reproduction failure is not misconduct evidence.** The Phase 3 R0 paper-like run does
    not reproduce the paper's reported Sharpe or return, but that only establishes a
    replication/data/implementation discrepancy under the repository's frozen interpretation. It
    does not prove intent, and future author code/data or a different vendor feed could explain part
    of the difference.
37. **Phase 3B invalid diagnostics are not strategy variants.** Current-day regime, current-day
    reversal, lag0 return matching, negative return offsets, and future regime offsets are labeled
    invalid forensic diagnostics. They exist only to test whether an implementation/alignment bug can
    explain the reproduction gap. They must not be promoted into experiment YAMLs or described as
    tradable.
38. **Paper fingerprint contradiction remains unresolved.** The paper's stated 35% active stock-days
    implies roughly 175 active names out of 500, while the paper also reports about 187 long and 189
    short positions. Under the stated non-zero EDGE-only portfolio construction, those fingerprints
    cannot all describe the same portfolio without trading inactive names.
39. **Gross-exposure shrink narrative is not implied by side normalization.** The specified
    `+50%/-50%` side normalization keeps gross exposure near 100% when both sides exist. A smaller
    regime-active name count shrinks position count and increases concentration, not gross exposure,
    unless an additional unreported exposure-scaling rule exists.
40. **Phase 4 is a frozen signal-information test, not the certified P4 backtest.** Phase 4 regime
    research reuses the Phase 3B paper-spec Yahoo Close VALUE input, prior-10-day reversal, and
    previous-63-return strict-`>60%` regime on the frozen survivorship-biased panel. This is distinct
    from `experiments/P4_continuous_oos.yaml`, whose historical-clean objective still requires
    certified nominal price and point-in-time membership. Phase 4's null/mixed regime result must not
    be described as a certified PIT strategy result or used to revise either experiment after the
    fact.
