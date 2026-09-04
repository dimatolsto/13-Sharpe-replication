# Phase 3B Reproduction Gap Forensics

Phase 3B keeps the Phase 3 input data frozen and asks why the favorable
paper-like R0 run still does not reproduce the paper. It does not revisit PIT
membership, redownload Yahoo, optimize parameters, or promote invalid
look-ahead diagnostics to strategies.

All empirical outputs remain:

```text
SURVIVORSHIP-BIASED PAPER-LIKE UNIVERSE
```

Generated reports are local and gitignored under `reports/generated/phase3b/`.

## Command

Phase 3B requires the Phase 3 reports to exist first:

```bash
uv run drift-replication phase3-attribution --out-dir reports/generated/phase3
uv run drift-replication phase3b-reproduction-gap \
  --phase3-dir reports/generated/phase3 \
  --out-dir reports/generated/phase3b
```

The Phase 3B command verifies `reports/generated/phase3/input_manifest.json`
and the frozen Phase 3 input-panel SHA-256 before running diagnostics. The
diagnostic panel is restricted to rows through 2021-02-01 because all requested
training/test windows and +/-10-session return-alignment checks end shortly
after the 2020 paper window.

## Baseline Reproduction

Phase 3B first reproduced the existing Phase 3 summary exactly:

| experiment | expected Sharpe | reproduced Sharpe | abs error |
|---|---:|---:|---:|
| R0 unscaled | 0.8384885885 | 0.8384885885 | 0 |
| R1 unscaled | 0.6298441786 | 0.6298441786 | 0 |
| R2 unscaled | 0.4022709667 | 0.4022709667 | 0 |
| R3 unscaled | 0.0187204934 | 0.0187204934 | 0 |

This confirms Phase 3B did not investigate a moving baseline.

## Window Finding

The Phase 3 legacy R0 path used the existing `compute_signals` implementation.
That implementation rolls reversal and regime windows through signal date `t`.
The paper's explicit regime equation uses `return[t-k]` for `k=1..63`, which
excludes current return `t`.

Phase 3B therefore reports two separate fingerprints:

- `R0_PHASE3_LEGACY_CURRENT_DAY_FEATURES`: exact Phase 3 R0, labeled invalid
  for paper-equation forensics because regime/reversal include current `t`.
- `R0_PAPER_SPEC_EXCLUDE_CURRENT`: same Phase 3 input, Yahoo Close VALUE,
  same lag-1 paper-like return alignment, but prior-window regime/reversal.

The paper-spec version is still survivorship-biased and still uses the
paper-like lag-1 convention. It is not a certified investable strategy.

## Training/Test Matrix

The paper reports extremely high training and test Sharpes. Neither exact
legacy R0 nor paper-spec R0 approaches those numbers.

| pair | paper train | paper-spec train | legacy train | paper test | paper-spec test | legacy test |
|---|---:|---:|---:|---:|---:|---:|
| 2005-2010 / 2010 | 19.42 | 0.796 | 0.834 | 16.89 | 1.025 | 2.052 |
| 2010-2015 / 2015 | 27.79 | 0.651 | 1.206 | 22.87 | -0.170 | 0.427 |
| 2015-2020 / 2020 | 16.63 | 0.455 | 1.039 | 5.11 | 0.921 | 0.738 |

This points to a fundamental data/specification/alignment gap, not merely weak
walk-forward test performance.

## Paper Fingerprints

The paper cites approximately 35% active stock-days, 187 long positions, 189
short positions, 42% daily turnover, 67% winning days, and 0.63% median daily
OOS return.

For `R0_PAPER_SPEC_EXCLUDE_CURRENT` in the selected test windows:

| window | active stock-days | avg active names | avg long | avg short | win days | median daily return | one-way turnover |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2010 | 9.4% | 39.8 | 20.1 | 19.7 | 49.2% | 0.00% | 30.5% |
| 2015 | 7.1% | 32.5 | 16.4 | 16.1 | 56.7% | 0.05% | 30.3% |
| 2020 | 12.5% | 60.2 | 29.8 | 30.4 | 52.2% | 0.01% | 31.2% |

The position-count fingerprint is internally hard to reconcile with the stated
algorithm. If only non-zero EDGE names are traded, 35% active stock-days over
roughly 500 names implies about 175 active names/day, not roughly 376 long plus
short positions/day.

The engine's turnover convention is one-way:

```text
0.5 * sum(abs(w_t - w_{t-1}))
```

Gross traded notional is twice that. The paper's 42% is closer to the one-way
figures around 30-32% than to gross traded notional around 61-63%, but the
difference is not explained by a simple convention flip.

## Leakage Diagnostics

All leakage diagnostics are explicitly labeled invalid. None get close to a
paper-like Sharpe.

| diagnostic | selected-window Sharpe | notes |
|---|---:|---|
| exact Phase 3 legacy R0, current-day features | 0.838 | reproduces Phase 3 baseline |
| paper-spec prior-window R0 | 0.646 | lower than legacy R0 |
| current-day regime only | 0.463 | invalid contemporaneous regime diagnostic |
| current-day reversal only | 0.975 | invalid current-day reversal diagnostic |
| lag0 contemporaneous return | -7.755 | invalid and strongly negative |
| best future regime offset | 1.290 | offset +4 sessions, invalid |
| corrected return offset 2 | 0.630 | matches R1 timing effect |

The return-alignment scan covered offsets -10 through +10 using fixed R0
weights; signals were not recomputed. Offset 0 is the explicit lag0
contemporaneous-return diagnostic and is strongly negative. Negative offsets
were also strongly negative. The largest invalid positive result was the
legacy offset +1 result, still only 0.838.

## Reversal And VALUE Audits

Prior compounded 10-session reversal and endpoint prior-10-session reversal
matched to numerical tolerance:

```text
max absolute difference = 1.33e-15
correlation = 1.0
```

Including current return in reversal changed the signal materially
(`raw_reversal_correlation = 0.886`) and raised selected-window Sharpe to
0.975, but this remains far below the paper.

VALUE direction is correct: lower price produces larger inverse price and
higher percentile rank. The maximum difference between `pandas rank(pct=True)`
and `(rank-1)/(n-1)` on sampled dates is about 0.0024, too small to explain the
gap.

## BASE And EDGE Order

The production interpretation is:

1. compute VALUE percentile and REVERSAL z-score;
2. compute `BASE = 0.7 * VALUE + 0.3 * REVERSAL`;
3. apply the regime mask;
4. z-score active non-zero EDGE values only;
5. long positive z-scores and short negative z-scores.

The BASE mix is scale-heterogeneous as written in the paper:

| feature | overall mean | overall std |
|---|---:|---:|
| VALUE | 0.501 | 0.289 |
| REVERSAL z | ~0.000 | 1.000 |
| BASE | 0.351 | 0.364 |

EDGE interpretation diagnostics:

| interpretation | selected Sharpe | positioned names/day | note |
|---|---:|---:|---|
| active EDGE only | 0.646 | 44.2 | paper-text interpretation |
| z-score BASE then regime | 0.353 | 44.2 | alternative order |
| z-score including inactive zeros | 0.968 | 448.9 | trades inactive names, not paper-text |
| active BASE only | 0.646 | 44.2 | equivalent to active EDGE when regime=1 |

The inactive-zero interpretation can create hundreds of positions, but it does
so by trading inactive stocks, contradicting the stated "valid, non-zero EDGE"
construction, and still does not approach the paper's Sharpe.

## Exposure Narrative

The paper's gross-exposure-shrink narrative does not follow from the stated
normalization. Under paper-spec R0, active fraction ranges from about 6% to 13%
across the training/test windows, but average gross exposure remains near 100%
whenever both sides exist because long and short sides are normalized to
`+50%/-50%`.

## Classification

Phase 3B classifies the reproduction gap as:

```text
MULTIPLE: DATA_SOURCE_DIFFERENCE + PAPER_INTERNAL_INCONSISTENCY
```

The most likely explanation is not a small implementation convention. The
paper's reported training/test Sharpes, active-stock fraction, position counts,
winning-day rate, and median daily return are not jointly reproduced by the
stated algorithm under the frozen Phase 3 input. Plausible implementation
ambiguities and explicitly invalid alignment/leakage variants remain far below
the reported 13-Sharpe scale.

This is not misconduct evidence. It is a forensic reproduction gap under the
repository's frozen survivorship-biased Phase 3 inputs. Author code/data or a
different vendor feed could still explain some of the difference.
