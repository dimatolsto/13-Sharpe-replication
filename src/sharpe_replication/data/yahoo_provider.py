from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .io import read_table, write_json
from .normalize import normalize_corporate_actions, normalize_daily_panel
from .validation import audit_split_raw_close, validate_dataset

YAHOO_DOWNLOAD_SETTINGS = {
    "interval": "1d",
    "auto_adjust": False,
    "back_adjust": False,
    "repair": False,
    "actions": True,
    "keepna": True,
    "threads": False,
    "progress": False,
}
YFINANCE_PACKAGE = "yfinance"

YAHOO_STATUS_ORDER = ["pending", "complete", "partial", "failed_retryable", "failed_permanent"]


@dataclass(frozen=True)
class YahooDownloadResult:
    status: str
    rows: int = 0
    error: str = ""
    retryable: bool = False


def _flatten_yahoo_columns(frame: pd.DataFrame, source_symbol: str | None = None) -> pd.DataFrame:
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame.copy()
    if source_symbol and source_symbol in frame.columns.get_level_values(-1):
        return frame.xs(source_symbol, axis=1, level=-1, drop_level=True).copy()
    if len(set(frame.columns.get_level_values(-1))) == 1:
        return frame.droplevel(-1, axis=1).copy()
    raise ValueError("Yahoo frame has multiple tickers; pass source_symbol for deterministic parsing")


def _normalize_yahoo_date(series: pd.Series) -> pd.Series:
    dates = pd.to_datetime(series, errors="raise")
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_convert(None)
    return dates.dt.normalize()


def _drop_serialized_yfinance_ticker_row(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop the extra ticker-name row produced by CSV serializing yfinance MultiIndex columns."""

    if "Date" not in frame.columns or frame.empty:
        return frame
    date_text = frame["Date"].astype("string").fillna("").str.strip()
    ticker_header = date_text.eq("")
    if not ticker_header.any():
        return frame
    non_date = [column for column in frame.columns if column != "Date"]
    metadata_like = frame.loc[ticker_header, non_date].map(
        lambda value: isinstance(value, str) and bool(value.strip())
    ).all(axis=1)
    if not metadata_like.any():
        return frame
    return frame.loc[~(ticker_header & metadata_like)].reset_index(drop=True)


def normalize_yahoo_history(
    raw: pd.DataFrame,
    *,
    security_id: str,
    ticker: str,
    source_symbol: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convert a yfinance daily response into candidate normalized panel/actions.

    Yahoo `Close` is preserved as `yahoo_close` for raw-close investigation. It is not populated into
    certified-field `raw_close`. Missing observations are preserved; this function does not
    forward-fill prices or returns.
    """

    frame = _flatten_yahoo_columns(raw, source_symbol)
    if "Date" not in frame.columns:
        frame = frame.reset_index()
    frame = _drop_serialized_yfinance_ticker_row(frame)
    rename = {
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "yahoo_close",
        "Adj Close": "adjusted_close",
        "Volume": "volume",
        "Dividends": "dividend_cash",
        "Stock Splits": "split_factor",
    }
    missing = sorted({"Date", "Close", "Adj Close"} - set(frame.columns))
    if missing:
        raise ValueError(f"Yahoo history missing required columns: {missing}")
    out = frame.rename(columns=rename).copy()
    out["date"] = _normalize_yahoo_date(out["date"])
    out["security_id"] = str(security_id)
    out["ticker"] = ticker
    out["source_symbol"] = source_symbol or ticker
    out = out.sort_values("date").reset_index(drop=True)
    out["raw_close"] = pd.NA
    out["raw_close_source"] = "not_populated_yahoo_close_failed_split_audit"
    out["total_return"] = pd.to_numeric(out["adjusted_close"], errors="coerce").pct_change()
    panel_columns = [
        "date",
        "security_id",
        "ticker",
        "raw_close",
        "total_return",
        "open",
        "high",
        "low",
        "yahoo_close",
        "adjusted_close",
        "volume",
        "dividend_cash",
        "split_factor",
        "source_symbol",
        "raw_close_source",
    ]
    panel = normalize_daily_panel(out[[column for column in panel_columns if column in out.columns]], source="yahoo")

    actions = []
    if "dividend_cash" in out.columns:
        for row in out[pd.to_numeric(out["dividend_cash"], errors="coerce").fillna(0) != 0].itertuples(index=False):
            actions.append(
                {
                    "security_id": security_id,
                    "date": row.date,
                    "event_type": "cash_dividend",
                    "dividend_cash": float(row.dividend_cash),
                    "source": "yahoo",
                    "source_event_id": f"yahoo:{source_symbol or ticker}:dividend:{row.date.date()}",
                    "ticker": ticker,
                }
            )
    if "split_factor" in out.columns:
        for row in out[pd.to_numeric(out["split_factor"], errors="coerce").fillna(0) != 0].itertuples(index=False):
            actions.append(
                {
                    "security_id": security_id,
                    "date": row.date,
                    "event_type": "split",
                    "split_factor": float(row.split_factor),
                    "source": "yahoo",
                    "source_event_id": f"yahoo:{source_symbol or ticker}:split:{row.date.date()}",
                    "ticker": ticker,
                }
            )
    action_frame = normalize_corporate_actions(pd.DataFrame(actions)) if actions else pd.DataFrame(
        columns=["security_id", "date", "event_type", "split_factor", "dividend_cash", "source", "source_event_id", "ticker"]
    )
    return panel, action_frame


def read_yahoo_history(path: str | Path, *, security_id: str, ticker: str, source_symbol: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    return normalize_yahoo_history(read_table(path), security_id=security_id, ticker=ticker, source_symbol=source_symbol)


def yahoo_candidate_audit(panel: pd.DataFrame, corporate_actions: pd.DataFrame | None) -> dict[str, Any]:
    p = normalize_daily_panel(panel)
    actions = normalize_corporate_actions(corporate_actions) if corporate_actions is not None and len(corporate_actions) else None
    report = validate_dataset(p, corporate_actions=actions)
    close_candidate = p.copy()
    if "yahoo_close" in close_candidate.columns:
        close_candidate["raw_close"] = close_candidate["yahoo_close"]
    split_audit = audit_split_raw_close(close_candidate, actions)
    dividend_mismatches = 0
    dividend_checks = 0
    if actions is not None:
        dividends = actions[actions["event_type"].eq("cash_dividend")]
        enriched = p.sort_values(["security_id", "date"]).copy()
        close_column = "yahoo_close" if "yahoo_close" in enriched.columns else "raw_close"
        enriched["previous_candidate_close"] = enriched.groupby("security_id")[close_column].shift()
        for div in dividends.itertuples(index=False):
            row = enriched[enriched["security_id"].eq(div.security_id) & enriched["date"].eq(div.date)]
            if row.empty or pd.isna(row["previous_candidate_close"].iloc[0]) or pd.isna(row["total_return"].iloc[0]):
                continue
            dividend_checks += 1
            expected = (row[close_column].iloc[0] + div.dividend_cash) / row["previous_candidate_close"].iloc[0] - 1.0
            if abs(float(row["total_return"].iloc[0]) - float(expected)) > 0.03:
                dividend_mismatches += 1
    return {
        "validation_status": report.status.value,
        "validation_issues": [issue.to_dict() for issue in report.issues],
        "split_events": len(split_audit),
        "split_classifications": split_audit["classification"].value_counts().to_dict() if len(split_audit) else {},
        "dividend_events": int(actions["event_type"].eq("cash_dividend").sum()) if actions is not None else 0,
        "dividend_checks": dividend_checks,
        "dividend_mismatches": dividend_mismatches,
        "total_return_source": "yahoo_adjusted_close",
    }


def initial_yahoo_acquisition_state(aliases: pd.DataFrame) -> pd.DataFrame:
    required = {"security_id", "provider_symbol"}
    missing = sorted(required - set(aliases.columns))
    if missing:
        raise ValueError(f"Yahoo aliases missing required columns: {missing}")
    state = aliases.copy()
    state["status"] = "pending"
    state["attempts"] = 0
    state["rows"] = 0
    state["error"] = ""
    return state.sort_values(["security_id", "provider_symbol"]).reset_index(drop=True)


def classify_yahoo_error(message: str) -> tuple[str, bool]:
    text = message.lower()
    retryable_markers = ["timeout", "temporarily", "rate", "429", "connection", "reset"]
    permanent_markers = ["no data", "not found", "delisted", "invalid", "404"]
    if any(marker in text for marker in permanent_markers):
        return "failed_permanent", False
    if any(marker in text for marker in retryable_markers):
        return "failed_retryable", True
    return "failed_retryable", True


def record_yahoo_result(
    state: pd.DataFrame,
    *,
    provider_symbol: str,
    result: YahooDownloadResult,
) -> pd.DataFrame:
    out = state.copy()
    mask = out["provider_symbol"].eq(provider_symbol)
    if not mask.any():
        raise ValueError(f"Unknown Yahoo provider_symbol={provider_symbol!r}")
    out.loc[mask, "status"] = result.status
    out.loc[mask, "attempts"] = out.loc[mask, "attempts"].astype(int) + 1
    out.loc[mask, "rows"] = result.rows
    out.loc[mask, "error"] = result.error
    return out


def pending_yahoo_symbols(state: pd.DataFrame, *, include_permanent: bool = False) -> list[str]:
    retryable_statuses = ["pending", "failed_retryable"]
    if include_permanent:
        retryable_statuses.append("failed_permanent")
    retryable = state["status"].isin(retryable_statuses)
    return sorted(set(state.loc[retryable, "provider_symbol"].astype(str)))


def yahoo_acquisition_summary(state: pd.DataFrame) -> dict[str, Any]:
    counts = {status: int((state["status"] == status).sum()) for status in YAHOO_STATUS_ORDER}
    return {
        "symbols": len(state),
        **counts,
        "pending_symbols": pending_yahoo_symbols(state)[:100],
    }


def write_yahoo_state(path: str | Path, state: pd.DataFrame) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    state.to_csv(path, index=False)


def read_yahoo_state(path: str | Path) -> pd.DataFrame:
    state = read_table(path)
    if "status" not in state.columns:
        raise ValueError("Yahoo acquisition state missing status column")
    for column in ["security_id", "provider_symbol", "status", "error"]:
        if column in state.columns:
            state[column] = state[column].fillna("").astype(str)
    for column in ["attempts", "rows"]:
        if column in state.columns:
            state[column] = pd.to_numeric(state[column], errors="coerce").fillna(0).astype(int)
    invalid = sorted(set(state["status"]) - set(YAHOO_STATUS_ORDER))
    if invalid:
        raise ValueError(f"Unsupported Yahoo acquisition status values: {invalid}")
    return state


def download_yahoo_symbol(symbol: str, *, start: str, end: str | None = None) -> pd.DataFrame:
    try:
        import yfinance as yf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised only in live acquisition
        raise RuntimeError("Install optional acquisition dependency yfinance to download Yahoo data") from exc
    return yf.download(symbol, start=start, end=end, **YAHOO_DOWNLOAD_SETTINGS)


def write_yahoo_settings(path: str | Path) -> None:
    write_json(path, yahoo_provider_metadata())


def yfinance_version() -> str:
    try:
        return importlib.metadata.version(YFINANCE_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def yahoo_provider_metadata() -> dict[str, Any]:
    return {
        "provider": "yahoo",
        "acquisition_library": YFINANCE_PACKAGE,
        "acquisition_library_version": yfinance_version(),
        "settings": YAHOO_DOWNLOAD_SETTINGS,
    }
