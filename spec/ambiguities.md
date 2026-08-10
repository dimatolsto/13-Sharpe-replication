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
