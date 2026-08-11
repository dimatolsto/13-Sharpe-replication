# Paper ambiguities tracked explicitly

The implementation must not silently resolve these items.

1. **Nominal price direction.** The paper describes inverse price converted to percentile rank. We
   define larger `1 / raw_close` as a larger value score.
2. **Reversal return convention.** We define a 10-session total return ending on signal date `t`.
3. **63-day up-fraction window.** We include the daily total return ending on signal date `t` and the
   preceding 62 returns. This is one declared interpretation and is covered by tests.
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
