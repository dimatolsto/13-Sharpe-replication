# Phase 3 Paper Attribution

Phase 3 intentionally pauses point-in-time S&P 500 membership reconstruction. The paper
acknowledges using current S&P 500 constituents historically, so this phase preserves that
survivorship bias to isolate other methodological issues:

```text
SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE
```

These results are not an investable historical simulation and do not certify P3/P4/P5.

## Inputs

Command:

```bash
uv run drift-replication phase3-attribution --out-dir reports/generated/phase3
```

Tracked experiment specs:

- `experiments/R0_phase3_paper_like_reproduction.yaml`
- `experiments/R1_phase3_timing_fix.yaml`
- `experiments/R2_phase3_nominal_price_fix.yaml`
- `experiments/R3_phase3_continuous_survivorship_biased.yaml`

Generated local artifacts are under `reports/generated/phase3/` and remain gitignored. The command
writes `input_manifest.json`, `anchor_universe.csv`, copied experiment specs, daily returns,
security ledgers, timing/accounting audits, bootstrap output, and summary CSV/JSON tables.

Implementation note: a row-order alignment bug in the reconstructed nominal-close assignment was
found before finalization. The transform now joins reconstructed values by `(date, security_id)`,
the regression is covered by tests, and all R0/R1/R2/R3 reports were regenerated from scratch after
the fix.

## Anchor

The frozen Phase 3 anchor is the closest existing cached snapshot to the paper publication date:

| field | value |
|---|---:|
| paper publication date | 2025-11-18 |
| anchor date | 2025-12-22 |
| source | riazarbi iShares IVV holdings snapshot |
| tier | SECONDARY_SNAPSHOT |
| constituents | 503 |
| distance from publication | 34 days |
| with cached Yahoo market data | 498 |
| without cached Yahoo market data | 5 |

Missing cached Yahoo symbols are BK, CTRA, DAY, HOLX, and MMC. They remain in the anchor file but
are not eligible on dates without market data. The run did not redownload Yahoo.

The source panel contains 2,655,209 Phase 3 rows for 498 anchor securities from 2003-01-02 through
2026-08-10. It removes 264,438 duplicate `(provider_symbol, date)` rows caused by Phase 2C
provisional identity duplication before constructing the fixed current-symbol universe.

## Price And Return Semantics

Phase 3 keeps three price fields distinct:

- `yahoo_close`: used for R0/R1 inverse-price VALUE. It is not certified nominal raw close.
- `yahoo_adj_close`: used only to compute `yahoo_adj_close_pct_change`.
- `reconstructed_nominal_close`: used for R2/R3 inverse-price VALUE as a
  `FORENSIC_CANDIDATE_NOMINAL_CLOSE`; Phase 2C found 603/616 split checks nominal-consistent after
  split de-adjustment, but independent validation remains pending.

P&L and reversal/regime features use `yahoo_adj_close_pct_change`, not reconstructed nominal-close
returns.

## Experiments

| experiment | purpose | VALUE price | lag | scaling |
|---|---|---|---:|---|
| R0 | paper-like reproduction | Yahoo Close | 1 | paper walk-forward |
| R1 | timing fix only | Yahoo Close | 2 | paper walk-forward |
| R2 | nominal-price fix after timing | reconstructed nominal close | 2 | paper walk-forward |
| R3 | continuous corrected survivorship-biased run | reconstructed nominal close | 2 | none |

R0/R1 differ only by timing. R1/R2 differ only by VALUE price representation. All runs use the same
frozen anchor, total-return source, transaction cost, and published strategy parameters.

## Headline Results

Combined selected paper-window results:

| experiment | series | Sharpe | annualized return | vol | max DD | cumulative return | turnover |
|---|---|---:|---:|---:|---:|---:|---:|
| R0 | unscaled | 0.838 | 9.50% | 11.63% | -14.69% | 31.35% | 0.310 |
| R0 | paper-scaled | 0.669 | 12.79% | 21.38% | -26.90% | 43.56% | 0.484 |
| R1 | unscaled | 0.630 | 6.37% | 10.71% | -14.30% | 20.39% | 0.309 |
| R1 | paper-scaled | 0.588 | 9.99% | 19.35% | -22.20% | 33.13% | 0.423 |
| R2 | unscaled | 0.402 | 3.91% | 11.06% | -13.67% | 12.23% | 0.314 |
| R2 | paper-scaled | 0.331 | 4.68% | 19.45% | -22.30% | 14.72% | 0.414 |
| R3 | continuous unscaled | 0.019 | -0.21% | 8.66% | -36.25% | -3.13% | 0.302 |

R0 does not remotely reproduce the paper's headline OOS Sharpe values even under favorable,
paper-like assumptions. Correcting timing reduces selected-window unscaled Sharpe by 0.209. Replacing
Yahoo Close with reconstructed nominal close reduces it by a further 0.247 and materially changes
VALUE ranks.

## Paper Comparison

Paper-scaled comparison by reported OOS test year:

| window | paper Sharpe | R0 Sharpe | R1 Sharpe | R2 Sharpe | paper return | R0 return | R1 return | R2 return |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2010 | 16.89 | 2.05 | 0.99 | 0.77 | 206.7% | 12.9% | 6.3% | 3.2% |
| 2015 | 22.87 | 0.43 | -0.19 | -0.22 | 207.2% | 4.4% | -1.7% | -2.4% |
| 2020 | 5.11 | 0.74 | 0.91 | 0.56 | 62.0% | 21.8% | 27.4% | 13.9% |

The reproduced paper-style scales also differ from the paper: R0 scales were 0.929, 1.838, and
1.924 for 2010, 2015, and 2020 respectively.

## Timing Audit

The timing audit explicitly records the close used for the signal, weight date, and earned return
date. Examples:

| experiment | signal date | weight date | earned return date | interpretation |
|---|---|---|---|---|
| R0 | 2009-12-30 | 2009-12-30 | 2009-12-31 | paper-like lag 1 |
| R1 | 2009-12-30 | 2009-12-31 | 2010-01-04 | corrected lag 2 |
| R2 | 2009-12-30 | 2009-12-31 | 2010-01-04 | corrected lag 2 |

This confirms that the R1/R2 corrected runs execute at close `t+1` and first earn `t+1 -> t+2`.

## Continuous History

R3 covers all calendar years 2010 through 2024; it is not a concatenation of the three paper years.

| year | Sharpe | annualized return | max DD |
|---:|---:|---:|---:|
| 2010 | 0.77 | 5.23% | -4.11% |
| 2011 | 0.23 | 1.86% | -9.74% |
| 2012 | 1.18 | 8.17% | -6.23% |
| 2013 | -0.07 | -0.57% | -10.40% |
| 2014 | -0.55 | -2.51% | -4.81% |
| 2015 | -0.22 | -1.57% | -7.79% |
| 2016 | 0.63 | 4.72% | -5.21% |
| 2017 | 0.73 | 3.13% | -3.55% |
| 2018 | -0.36 | -2.67% | -5.91% |
| 2019 | 0.45 | 2.08% | -3.47% |
| 2020 | 0.56 | 8.33% | -11.99% |
| 2021 | -1.23 | -11.70% | -16.18% |
| 2022 | -1.37 | -18.75% | -27.83% |
| 2023 | 0.16 | 0.94% | -4.68% |
| 2024 | 0.63 | 3.99% | -4.97% |

Best year by Sharpe was 2012. Worst year was 2022.

## Accounting And Diagnostics

The security-level ledgers reconcile to portfolio daily net return within numerical tolerance. The
largest max absolute net-return reconciliation error is about `1.4e-17`; R3 is `6.9e-18`. Missing
weighted returns are zero in the generated Phase 3 outputs.

R2/R3 bootstrap settings:

- block length: 10 trading sessions
- repetitions: 2,000
- seed: 13

Bootstrap Sharpe intervals:

| experiment | Sharpe | 95% CI |
|---|---:|---|
| R2 selected windows | 0.402 | [-0.916, 1.624] |
| R3 continuous | 0.019 | [-0.501, 0.510] |

Using reconstructed nominal prices materially changes cross-sectional VALUE ranks: mean absolute
rank delta is 0.131, median 0.075, and 76.1% of observations move by more than five percentile
points. Long-set and short-set mean Jaccard overlaps from R1 to R2 are about 0.732 and 0.729.

Signal-attribution diagnostics under R3:

| variant | Sharpe | cumulative return |
|---|---:|---:|
| value only | 0.581 | 46.66% |
| reversal only | 0.370 | 45.58% |
| base | 0.567 | 69.22% |
| value x regime | 0.056 | 2.27% |
| reversal x regime | -0.084 | -18.34% |
| full EDGE | 0.019 | -3.13% |

The regime reduces the number of active positions on many days, but gross exposure remains near 100%
when positions exist because the strategy normalizes long and short sides.

## Interpretation

The strongest supported conclusion is a basic reproduction/data/implementation discrepancy: R0, the
most favorable paper-like run, does not approximate the paper's reported OOS Sharpe or return. Timing
and nominal-price treatment still matter, but they are secondary to the fact that the paper-style
reproduction already fails by a large margin. The continuous corrected survivorship-biased history is
weak and near flat, so the selected OOS windows do not reveal a robust continuous effect in this
implementation.

Phase 3 does not resolve whether a different vendor dataset, exact author code, or an exact
publication-date constituent list could narrow the discrepancy. It also does not change Phase 2 P2
or PIT membership certification: Yahoo Close remains failed as nominal raw close, reconstructed
nominal close remains candidate-only, and PIT S&P 500 membership work remains deferred.
