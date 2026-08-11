from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .io import read_table
from .normalize import normalize_daily_panel
from .sp500_events import normalize_reported_symbol

WISESHEETS_COLUMNS = [
    "date",
    "ticker",
    "wisesheets_close",
    "wisesheets_adj_close",
    "wisesheets_dividend",
    "wisesheets_split",
]


def read_wisesheets_export(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        try:
            table = pd.read_excel(path)
        except ImportError as exc:  # pragma: no cover - depends on optional local engine
            raise RuntimeError("Install an Excel reader such as openpyxl, or export WiseSheets to CSV") from exc
    else:
        table = read_table(path)
    lower = {column.lower().strip(): column for column in table.columns}
    aliases = {
        "date": ["date"],
        "ticker": ["ticker", "symbol"],
        "wisesheets_close": ["wisesheets_close", "close"],
        "wisesheets_adj_close": ["wisesheets_adj_close", "adjclose", "adj close"],
        "wisesheets_dividend": ["wisesheets_dividend", "dividend", "dividends"],
        "wisesheets_split": ["wisesheets_split", "split", "stock splits", "stock split"],
    }
    renamed: dict[str, str] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower:
                renamed[lower[candidate]] = target
                break
    missing = sorted({"date", "ticker"} - set(renamed.values()))
    if missing:
        raise ValueError(f"WiseSheets export missing required columns: {missing}")
    out = table.rename(columns=renamed).copy()
    for column in WISESHEETS_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    out = out[WISESHEETS_COLUMNS]
    out["date"] = pd.to_datetime(out["date"], errors="raise").dt.normalize()
    out["ticker"] = out["ticker"].map(normalize_reported_symbol)
    for column in WISESHEETS_COLUMNS[2:]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.sort_values(["date", "ticker"]).reset_index(drop=True)


def compare_wisesheets_to_yahoo(
    yahoo_panel: pd.DataFrame,
    wisesheets_export: pd.DataFrame,
    *,
    tolerance: float = 0.01,
) -> dict[str, Any]:
    yahoo = normalize_daily_panel(yahoo_panel).copy()
    wise = read_wisesheets_export(wisesheets_export) if isinstance(wisesheets_export, (str, Path)) else wisesheets_export
    yahoo["ticker"] = yahoo["ticker"].map(normalize_reported_symbol)
    if "adjusted_close" not in yahoo.columns:
        yahoo["adjusted_close"] = pd.NA
    joined = yahoo.merge(wise, on=["date", "ticker"], how="outer", indicator=True)
    rows = []
    for row in joined.to_dict(orient="records"):
        if row["_merge"] != "both":
            classification = "insufficient_data"
        else:
            differences = []
            if pd.notna(row["wisesheets_close"]) and pd.notna(row["raw_close"]):
                if abs(float(row["wisesheets_close"]) - float(row["raw_close"])) <= tolerance:
                    differences.append("close_agree")
                elif abs(float(row["wisesheets_close"]) - float(row["raw_close"])) <= max(
                    tolerance,
                    0.001 * float(row["raw_close"]),
                ):
                    differences.append("close_rounding")
                else:
                    differences.append("close_material")
            if pd.notna(row["wisesheets_adj_close"]) and pd.notna(row["adjusted_close"]):
                if abs(float(row["wisesheets_adj_close"]) - float(row["adjusted_close"])) <= tolerance:
                    differences.append("adj_agree")
                else:
                    differences.append("adj_material")
            if not differences:
                classification = "insufficient_data"
            elif any(item.endswith("material") for item in differences):
                classification = "material_disagreement"
            elif any(item.endswith("rounding") for item in differences):
                classification = "small_rounding_difference"
            else:
                classification = "agree"
        rows.append(
            {
                "date": pd.Timestamp(row["date"]).date().isoformat() if pd.notna(row["date"]) else None,
                "ticker": row["ticker"],
                "classification": classification,
            }
        )
    detail = pd.DataFrame(rows)
    counts = detail["classification"].value_counts().to_dict() if len(detail) else {}
    return {
        "rows_compared": len(detail),
        "classifications": {key: int(value) for key, value in counts.items()},
        "material_disagreements": detail[detail["classification"].eq("material_disagreement")].head(50).to_dict(orient="records"),
    }


def requested_wisesheets_test_pack() -> pd.DataFrame:
    """Return a small data-semantics export request pack, not a strategy sample."""

    return pd.DataFrame(
        [
            {"ticker": "AAPL", "start_date": "2020-08-24", "end_date": "2020-09-04", "purpose": "4-for-1 split"},
            {"ticker": "TSLA", "start_date": "2020-08-24", "end_date": "2020-09-04", "purpose": "5-for-1 split"},
            {"ticker": "MSFT", "start_date": "2024-02-12", "end_date": "2024-02-23", "purpose": "ordinary dividend"},
            {"ticker": "XOM", "start_date": "2024-02-07", "end_date": "2024-02-20", "purpose": "ordinary dividend"},
            {"ticker": "META", "start_date": "2022-06-01", "end_date": "2022-06-15", "purpose": "ticker rename check"},
            {"ticker": "TWTR", "start_date": "2022-10-20", "end_date": "2022-11-04", "purpose": "former constituent/delisting check"},
        ]
    )
