# Phase 4 — incremental drift-regime predictive value

Phase 4 changes the research priority from reproducing the paper's headline Sharpe to testing its
economic conditioning hypothesis:

> Under the frozen Phase 3/3B data and causal paper-spec implementation, does BASE predict future
> cross-sectional returns more strongly when the 63-day drift regime is active?

This is a signal-information study, not a parameter search or a new strategy specification. It does
not change Phase 2 certification, reconstruct point-in-time membership, or claim that the frozen
survivorship-biased universe is investable.

## Frozen design

- Primary sample: every available session from 2010-01-01 through 2024-12-31 after lookbacks.
- VALUE: inverse Yahoo Close percentile rank, matching the Phase 3B paper-spec R0 diagnostic.
- REVERSAL: negative compounded return over the 10 observations before signal date `t`.
- BASE: `0.70 * VALUE + 0.30 * REVERSAL` with the Phase 3B cross-sectional scaling.
- UpFraction: positive-return fraction over exactly `t-63 ... t-1`; return `t` is excluded.
- REGIME: `UpFraction > 0.60`. With 63 observations, at least 38 must be positive.
- Primary group-size rule: at least 20 REGIME=1 and 20 REGIME=0 names on a date.
- Primary horizon: one trading day. Two-, five-, and ten-day horizons are secondary.
- Information timing: signal through close `t` predicts the return `t -> t+1`.
- Corrected delayed timing: signal at close `t`, execution at close `t+1`, return `t+1 -> t+2`.
- HAC/Newey-West lag: 20 daily observations for all reported coefficient/metric time series.
- Block bootstrap: 2,000 moving-block replications, 20 sessions per block, seed 13.
- Matched random regimes: 1,000 masks preserving the exact active count on every usable date,
  seed 13.
- Fixed UpFraction bins: `<45%`, `45%-50%`, `50%-55%`, `55%-60%`, `60%-65%`, `65%-70%`,
  and `>=70%`.

The raw BASE interaction is primary. Same-date cross-sectional standardized BASE is an
interpretability/robustness scale only; it does not change rankings or the production signal.

## Statistical hierarchy

The primary test is the 2010-2024 one-day Fama-MacBeth time series of `BASE * REGIME`
coefficients, paired with the difference between REGIME=1 and REGIME=0 daily BASE IC. The corrected
delayed one-day result is the primary validation. Horizons, continuous UpFraction interactions,
matched masks, bins, annual stability, transitions, components, concentration, and portfolios are
secondary diagnostics and cannot replace the preregistered result.

Each daily interaction regression is fit only within one date:

```text
future_return[i,t] = alpha[t]
                   + beta1[t] * BASE[i,t]
                   + beta2[t] * REGIME[i,t]
                   + beta3[t] * BASE[i,t] * REGIME[i,t]
                   + error[i,t]
```

The daily coefficient time series is summarized with fixed-lag HAC inference and an independent
moving-block bootstrap confidence interval. Conditional Spearman IC, Pearson IC, univariate slope,
and equal-weight top-minus-bottom quintile spreads rank BASE separately inside each regime group.

## Reproduction

From the repository root:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run drift-replication spec-check
uv run drift-replication phase4-regime-edge --out-dir reports/generated/phase4
git diff --check
```

The command verifies the Phase 3 manifest and panel hash, reads the existing frozen panel, and makes
no network request. Generated empirical artifacts are local and gitignored under
`reports/generated/phase4/`; code, tests, and this methodology record are tracked.

## Results

The frozen-data run used 1,755,295 eligible stock-days from 2010-01-04 through 2024-12-31.
REGIME=1 covered 205,643 stock-days (11.72%), with median 470 eligible and 48 active names per
date. The minimum active count was one; the fixed 20-names-per-group regression rule left 3,124
usable dates and skipped 650 dates.

### Primary one-day information test

The raw BASE interaction was effectively zero and slightly negative:

| Quantity | Estimate | HAC SE | t-stat | HAC 95% CI | Block-bootstrap 95% CI |
| --- | ---: | ---: | ---: | ---: | ---: |
| `beta1` BASE | 0.000338 | 0.000135 | 2.51 | [0.000074, 0.000603] | [0.000048, 0.000600] |
| `beta2` REGIME | 0.000116 | 0.000078 | 1.49 | [-0.000037, 0.000269] | [-0.000024, 0.000281] |
| `beta3` BASE x REGIME | -0.000012 | 0.000166 | -0.07 | [-0.000338, 0.000314] | [-0.000355, 0.000293] |

Daily `beta3` was positive on 49.87% of usable dates. Cross-sectional standardized BASE agreed:
its interaction was `-0.0000008`, with t-stat `-0.01`. BASE itself had positive average predictive
content outside the interaction; the evidence does not show that the binary regime enhanced it.

Conditional Spearman IC averaged 0.00655 outside and 0.00734 inside the regime. The paired daily
difference was +0.00215 (t-stat 0.63; 95% CI [-0.00456, 0.00887]). Pearson IC, univariate slope,
and top-minus-bottom spread differences were negative: -0.00183, -0.000012, and -0.000070,
respectively, and all confidence intervals included zero.

### Validation and secondary diagnostics

Corrected delayed execution did not improve the conclusion. Its interaction was -0.000101
(t-stat -0.60; 95% CI [-0.000429, 0.000227]); its conditional spread difference was -0.000205.
The 2-, 5-, and 10-day interactions were all negative, and their IC/spread differences were also
negative.

The fixed UpFraction bins were not economically monotone: the rank correlation between bin
UpFraction and mean daily IC was -0.31. The continuous `BASE * UpFraction` interaction was
-0.00323 (t-stat -3.39; HAC 95% CI [-0.00510, -0.00136]), contradicting a smoothly increasing
regime benefit. This is a secondary diagnostic and does not replace the binary primary test.

The actual matched-regime interaction statistic was at the 48.6th percentile of 1,000 equally broad
random masks (one-sided p=0.514). Regime spread mean was at the 37.4th percentile (p=0.626), while
regime IC was suggestive but below the preregistered 95th-percentile bar: 92.3rd percentile
(p=0.078). Random masks used seed 13 and matched the active-security count on every usable date.

Nine of 15 annual interactions and nine annual IC differences were positive, versus six negative
for each, but magnitudes and signs were unstable. The entry event study showed no clear persistent
post-entry alignment improvement; entry day zero was small with a confidence interval spanning
zero. The rank-outcome robustness interaction was also insignificant (t-stat 0.65). The top ten
absolute contributors represented 7.16% of total absolute security contribution, so the result was
not concentrated in a tiny set of names.

VALUE's interaction was positive but insignificant (t-stat 0.77); REVERSAL's was negative and
insignificant (t-stat -1.09); BASE's was near zero. Supporting delayed portfolios were consistent
with no regime enhancement: BASE_ALL Sharpe was 0.81, BASE_REGIME 0.15, and BASE_NONREGIME 0.85.
Portfolio performance is supporting evidence only, not the primary test.

## Conclusion

Classification: **WEAK_OR_MIXED**.

Under the frozen survivorship-biased Phase 3/3B dataset and causal paper-spec implementation, the
63-day, strict-`>60%` drift regime does not provide compelling incremental predictive value to BASE.
The primary interaction is essentially zero, delayed timing agrees in sign, matched-random results
are ordinary, longer horizons are negative, and the continuous interaction is significantly
negative. The slightly higher in-regime Spearman IC and nine positive annual signs prevent this from
being clean evidence of a uniformly negative binary interaction, but neither is statistically
reliable. This is absence of persuasive regime enhancement, not a claim that the paper is wrong.
