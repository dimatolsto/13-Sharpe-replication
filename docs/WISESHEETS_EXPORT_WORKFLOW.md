# WiseSheets Export Workflow

Access date: 2026-08-11

WiseSheets is a local-export cross-check source for Phase 2B. The repository does not implement or
invent a WiseSheets REST API.

Official public references reviewed:

- https://www.wisesheets.io/pages/docs
- https://www.wisesheets.io/available-data

The public docs describe `WISEPRICE` for historical price data and dividends, with examples such as:

```text
=WISEPRICE("AAPL", "Close", , "01/01/2022", "01/30/2022")
=WISEPRICE("AAPL", "AdjClose", , "01/01/2022", "01/30/2022")
=WISEPRICE("AAPL", "Dividend")
```

The docs do not establish an authoritative REST endpoint/schema for this project, and they do not
prove whether `Close` is raw nominal historical close. WiseSheets `Close` must therefore remain a
cross-check field until empirical split audits prove its semantics.

## Requested Export Shape

Export CSV or Parquet with at least:

| column | required | note |
|---|---:|---|
| `date` | yes | trading date from the export |
| `ticker` | yes | WiseSheets/Yahoo-style ticker used for the request |
| `wisesheets_close` or `Close` | no | compared to Yahoo candidate `Close` |
| `wisesheets_adj_close` or `AdjClose` | no | compared to Yahoo `Adj Close` |
| `wisesheets_dividend` or `Dividend` | no | compared to Yahoo dividend events |
| `wisesheets_split` or `Stock Splits` | no | only if WiseSheets export supports it |

Do not include API keys or account-specific signed URLs in exports or metadata.

## Test Pack

Generate a compact request pack with:

```bash
uv run drift-replication data wisesheets-test-pack \
  --out reports/generated/wisesheets_requested_crosscheck.csv
```

The pack covers ordinary days, major splits, dividends, a ticker rename case, and a former
constituent/delisting check. It is for source-semantics validation only, not strategy selection.

Phase 2C did not find a local WiseSheets export in the expected project locations. After the real
Yahoo split audit, the requested pack was expanded to 58 rows at
`reports/generated/phase2c/wisesheets_requested_crosscheck_updated.csv`. It prioritizes likely
back-adjusted Yahoo split events, Yahoo nominal-consistent classifications, ambiguous split events,
dividend anomalies, and ordinary controls. The cross-check remains pending user export.

## Comparison

After exporting the requested fields:

```bash
uv run drift-replication data compare-wisesheets \
  --yahoo-panel data/normalized/<snapshot>/daily_panel.parquet \
  --wisesheets-export /path/to/wisesheets_export.csv
```

Classifications are:

- `agree`
- `small_rounding_difference`
- `material_disagreement`
- `insufficient_data`

WiseSheets never overrides Yahoo automatically. Material disagreements stay visible for review.
