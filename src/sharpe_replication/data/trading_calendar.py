from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import exchange_calendars as xcals
import pandas as pd

TRADING_CALENDAR_NAME = "XNYS"
TRADING_CALENDAR_PACKAGE = "exchange-calendars"
TRADING_CALENDAR_VERSION = importlib.metadata.version(TRADING_CALENDAR_PACKAGE)
TRADING_CALENDAR_START = "1990-01-01"
TRADING_CALENDAR_END = "2035-12-31"


@lru_cache(maxsize=1)
def _xnys_calendar():
    return xcals.get_calendar(TRADING_CALENDAR_NAME, start=TRADING_CALENDAR_START, end=TRADING_CALENDAR_END)


def _date(value: str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value).normalize()


def _utc_timestamp(value: str | pd.Timestamp | None = None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def is_trading_session(value: str | pd.Timestamp) -> bool:
    """Return whether `value` is an XNYS exchange session."""

    return bool(_xnys_calendar().is_session(_date(value)))


def next_session(value: str | pd.Timestamp, *, include_current: bool = True) -> pd.Timestamp:
    """Return the next XNYS session for an arbitrary date."""

    day = _date(value)
    if include_current:
        return _xnys_calendar().date_to_session(day, direction="next").normalize()
    if is_trading_session(day):
        return _xnys_calendar().next_session(day).normalize()
    return _xnys_calendar().date_to_session(day, direction="next").normalize()


def previous_session(value: str | pd.Timestamp, *, include_current: bool = False) -> pd.Timestamp:
    """Return the previous XNYS session for an arbitrary date."""

    day = _date(value)
    if include_current:
        return _xnys_calendar().date_to_session(day, direction="previous").normalize()
    if is_trading_session(day):
        return _xnys_calendar().previous_session(day).normalize()
    return _xnys_calendar().date_to_session(day, direction="previous").normalize()


def session_close_timestamp(value: str | pd.Timestamp) -> pd.Timestamp:
    """Return the scheduled close timestamp for an XNYS session in UTC."""

    session = _date(value)
    if not is_trading_session(session):
        raise ValueError(f"{session.date().isoformat()} is not an XNYS session")
    return _xnys_calendar().session_close(session)


def latest_completed_exchange_session(as_of: str | pd.Timestamp | None = None) -> pd.Timestamp:
    """Return the latest XNYS session whose scheduled close has passed at `as_of`.

    Naive timestamps are interpreted as UTC.  This is an exchange-completion boundary only; a
    provider may still lag the exchange and omit that completed bar.
    """

    ts = _utc_timestamp(as_of)
    exchange_day = ts.tz_convert("America/New_York").normalize().tz_localize(None)
    if is_trading_session(exchange_day) and ts >= session_close_timestamp(exchange_day):
        return exchange_day.normalize()
    return previous_session(exchange_day, include_current=False)


def trading_calendar_metadata() -> dict[str, str]:
    return {
        "calendar": TRADING_CALENDAR_NAME,
        "package": TRADING_CALENDAR_PACKAGE,
        "version": TRADING_CALENDAR_VERSION,
        "start": TRADING_CALENDAR_START,
        "end": TRADING_CALENDAR_END,
    }


@dataclass(frozen=True)
class TradingCalendar:
    """XNYS session calendar wrapper used for S&P membership event normalization."""

    calendar_name: str = TRADING_CALENDAR_NAME
    package: str = TRADING_CALENDAR_PACKAGE
    package_version: str = TRADING_CALENDAR_VERSION
    metadata: dict[str, Any] = field(default_factory=trading_calendar_metadata)

    @classmethod
    def xnys(cls) -> TradingCalendar:
        return cls()

    @classmethod
    def from_dates(cls, holidays: list[str] | set[str] | None = None) -> TradingCalendar:
        if holidays:
            raise ValueError(
                "Manual holiday lists are not supported; XNYS sessions come from exchange-calendars"
            )
        return cls.xnys()

    def is_session(self, value: str | pd.Timestamp) -> bool:
        return is_trading_session(value)

    def next_session(self, value: str | pd.Timestamp, *, include_current: bool = True) -> pd.Timestamp:
        return next_session(value, include_current=include_current)

    def previous_session(self, value: str | pd.Timestamp, *, include_current: bool = False) -> pd.Timestamp:
        return previous_session(value, include_current=include_current)


def first_membership_session(
    effective_date: str | pd.Timestamp,
    effective_session: str,
    calendar: TradingCalendar,
) -> pd.Timestamp:
    session = str(effective_session).upper()
    if session == "AFTER_CLOSE":
        return calendar.next_session(effective_date, include_current=False)
    return calendar.next_session(effective_date, include_current=True)


def first_nonmember_session(
    effective_date: str | pd.Timestamp,
    effective_session: str,
    calendar: TradingCalendar,
) -> pd.Timestamp:
    session = str(effective_session).upper()
    if session == "AFTER_CLOSE":
        return calendar.next_session(effective_date, include_current=False)
    return calendar.next_session(effective_date, include_current=True)
