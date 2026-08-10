# Data contract

## Daily panel

One row per `(date, security_id)`.

Required columns:

| column | meaning |
|---|---|
| `date` | trading date |
| `security_id` | permanent identifier (PERMNO or equivalent preferred) |
| `ticker` | ticker valid on that date |
| `raw_close` | historical nominal close, not retroactively adjusted for future splits |
| `total_return` | corporate-action-correct close-to-close return ending on `date` |

Optional columns: `adj_close`, `split_factor`, `cash_dividend`, `volume`, `delisting_return`,
`source`, `source_timestamp`.

`total_return[date=t]` is the return from close `t-1` to close `t`.

## Membership

One row per membership spell:

| column | meaning |
|---|---|
| `security_id` | permanent identifier |
| `membership_start` | first date eligible as S&P 500 constituent |
| `membership_end` | last eligible date, inclusive; null if ongoing |

Historical membership must include removed, acquired, bankrupt, and delisted constituents.
