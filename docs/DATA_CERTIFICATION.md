# Data Certification

Certification is separate from parsing. A snapshot can load successfully and still be `FAIL` or
`UNVERIFIED`.

## States

| state | meaning |
|---|---|
| `PASS` | evidence satisfies the requirement |
| `FAIL` | validation found a blocking issue |
| `UNVERIFIED` | no blocking contradiction, but evidence is insufficient |

The generic snapshot certification reports all dimensions. `drift-replication data certify
<snapshot_dir> --experiment-id P3` applies the stricter requirement set for that experiment.

## Dimensions

Certification currently covers:

- `schema_valid`
- `raw_close_nominal_verified`
- `total_return_corporate_action_safe`
- `stable_identifiers_sufficient`
- `pit_membership_available`
- `survivorship_bias_checks_passed`
- `duplicate_checks_passed`
- `membership_interval_checks_passed`
- `missing_terminal_return_checks_passed`
- `provider_capabilities_documented`

## Experiment Requirements

P0 and P1 require schema validity and duplicate checks. These phases intentionally allow
paper/current-constituent style inputs for attribution.

P2 additionally requires verified nominal `raw_close` and corporate-action-safe `total_return`.

P3, P4, and P5 require the P2 conditions plus point-in-time membership, sufficiently stable
identifiers, survivorship checks, valid membership intervals, and terminal-return diagnostics.

Phase 1 has not run P4 or P5 performance. The purpose of the new pipeline is to make a later P0 ->
P1 -> P2 -> P3 -> P4 -> P5 sequence defensible before any headline Sharpe is inspected.

Phase 2A did not certify a real snapshot. CRSP/WRDS and Norgate are documented as likely
P3-capable paths if licensed and empirically audited. WiseSheets remains `UNVERIFIED` for raw-close,
total-return, delisted-security, stable-ID, and PIT-membership suitability.

Phase 2B adds an open/low-cost reconstruction pipeline, but parsing Wikipedia or Yahoo successfully
is still not certification. P3 membership remains `UNVERIFIED` or `FAIL` while material events lack
primary/archived/fallback evidence, effective-session semantics are unknown, identity mappings are
provisional, or member-day Yahoo coverage is incomplete.

Effective-session timing is part of certification evidence. Reconstructed memberships built from
`UNKNOWN` event timing carry `timing_uncertain=True`; validation emits
`unknown_effective_session_timing`, and P3/P4/P5 membership interval certification fails until the
event evidence establishes the timing convention.

Yahoo `Close` can become certified raw close only through empirical split checks and an explicit
mapping decision. A candidate
`total_return_source=yahoo_adjusted_close` is explicit provenance, not an automatic PASS; split,
dividend, no-action, extreme-return, and missing-return diagnostics must support it before it can be
treated as corporate-action safe.

The Phase 2C real Yahoo acquisition did not satisfy this standard. The acquired Yahoo `Close`
candidate produced 489 `likely_back_adjusted` classifications across 616 split checks, so Yahoo
`Close` is `FAIL` for nominal raw-close certification in that run. This blocks P2 and therefore
P3/P4/P5 for the open-source reconstruction until a separate true nominal close source is obtained
or an independently verified transformation is introduced and documented. Phase 2C introduced a
separate `reconstructed_nominal_close` candidate by reversing subsequent Yahoo split factors; it is
not certified because independent cross-source validation is still pending.

For P3+, a security master with only ticker intervals is not sufficient. Rows need a provider-native
or external stable identifier (`external_id` or equivalent). Missing stable IDs keep
`stable_identifiers_sufficient` at `UNVERIFIED`.

Phase 2D evaluated only the membership/identity component. It does not change raw-close or
total-return certification. The membership component remains `FAIL` after Phase 2D because:

- 723 of 737 in-window Wikipedia seed events remain unresolved and `UNKNOWN` timing.
- The 2026-08-11 anchor remains a provisional Wikipedia current table.
- The conservation audit still shows 76 backward failures and 74 net excess count delta.
- Identity lineages remain mostly provisional, with 232 ambiguity rows and 10 unresolved reentries.
- The 2004 seed has zero rows; one secondary replacement group was found, but primary/archive
  confirmation and completeness remain unresolved.

The Phase 2D result therefore cannot promote P3. P2 also remains `FAIL` from Phase 2C raw-close
evidence, so overall P3 remains `FAIL` even if later membership work improves.

Phase 2E evaluated the membership component through independent historical snapshot checkpoints
instead of attempting to verify every unresolved event. It compared 30 checkpoints across secondary
snapshot families and reviewed the known Phase 2D identity/backward/reentry problem sets. No
lineage repair was certified because the snapshot evidence did not yet provide primary/archive or
strong enough contemporaneous support for any merge or event correction. The membership component
therefore remains `FAIL` after Phase 2E because:

- The reconstructed count range is unchanged at 503 to 574 with median 543.
- All 64 Phase 2D identity-mismatch failures remain unresolved.
- All 12 backward reconstruction failures remain unresolved.
- The 10 provisional reentries remain unconfirmed.
- The 2004-2006 period has only secondary snapshot/checkpoint support and lacks primary or
  archived-primary full constituent evidence.
- The residual gap register contains 112 P3-blocking membership gaps.

The Phase 2E result does not change P2 raw-close certification. Overall P3 remains `FAIL`.

## Split Audit

For each known N-for-1 split, the split audit compares the previous nominal close to the split-day
nominal close:

```text
observed_price_ratio = previous_raw_close / split_day_raw_close
expected_ratio = declared_split_factor
```

Results are classified as `consistent_with_nominal`, `likely_back_adjusted`, `ambiguous`, or
`insufficient_data`. A `likely_back_adjusted` result fails certification. Consistent split behavior
is evidence, but a snapshot still needs explicit raw-close provenance before `raw_close` is marked
verified. The default certification rule requires at least two successful split checks, so one clean
split in isolation is not enough to certify a source.

## Total-Return Audit

On no-corporate-action days, `total_return[t]` should match
`raw_close[t] / raw_close[t-1] - 1` within tolerance. On split dates, `total_return` must not show a
catastrophic artificial split loss. Dividend and special-dividend days are treated as corporate-action
days requiring explicit source semantics.

Raw-close failure does not mechanically fail adjusted-return semantics. Phase 2C keeps
`yahoo_adjusted_close` return certification separate: split and dividend checks were mostly
consistent, but 4 split-return anomalies and 10 material dividend mismatches keep total return
`UNVERIFIED` until reviewed.

## Survivorship Audit

The survivorship report summarizes unique securities, members per date, entries/exits per year,
reentries, inactive securities at dataset end, ticker changes, and static-universe risk. The pipeline
does not impose an arbitrary historical unique-security threshold, but it fails structures that
resemble one static constituent set copied backward through the full history.
