from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sharpe_replication.providers.base import CapabilityStatus, ProviderCapabilities

ACCESS_DATE = "2026-08-10"


@dataclass(frozen=True)
class SourceAssessment:
    source_name: str
    category: str
    official_documentation: list[str]
    access_date: str
    access_method: str
    cost_access_status: str
    relevant_fields: list[str]
    field_semantics: dict[str, str]
    historical_coverage: str
    delisted_coverage: str
    stable_id_capability: str
    pit_membership_capability: str
    licensing_redistribution: str
    confidence: str
    recommendation: str
    provider_capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    blocking_uncertainties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "category": self.category,
            "official_documentation": self.official_documentation,
            "access_date": self.access_date,
            "access_method": self.access_method,
            "cost_access_status": self.cost_access_status,
            "relevant_fields": self.relevant_fields,
            "field_semantics": self.field_semantics,
            "historical_coverage": self.historical_coverage,
            "delisted_coverage": self.delisted_coverage,
            "stable_id_capability": self.stable_id_capability,
            "pit_membership_capability": self.pit_membership_capability,
            "licensing_redistribution": self.licensing_redistribution,
            "confidence": self.confidence,
            "recommendation": self.recommendation,
            "provider_capabilities": self.provider_capabilities.as_dict(),
            "blocking_uncertainties": self.blocking_uncertainties,
        }


def source_assessments() -> list[SourceAssessment]:
    """Return the committed Phase 2A source discovery matrix.

    These records are evidence summaries, not live provider claims. A capability remains
    unverified unless the documentation and the repository's empirical validators can support it.
    """

    supported = CapabilityStatus.SUPPORTED
    unsupported = CapabilityStatus.UNSUPPORTED
    unverified = CapabilityStatus.UNVERIFIED
    return [
        SourceAssessment(
            source_name="WiseSheets Pro",
            category="price_return_candidate",
            official_documentation=[
                "https://www.wisesheets.io/pages/docs",
                "https://www.wisesheets.io/available-data",
            ],
            access_date=ACCESS_DATE,
            access_method="Excel/Google Sheets add-in formulas; no authoritative REST endpoint located",
            cost_access_status="User has Pro/API access, but programmatic endpoint remains undocumented here",
            relevant_fields=["Close", "AdjClose", "Open", "High", "Low", "Volume", "Dividend"],
            field_semantics={
                "Close": "Historical close is documented as a WISEPRICE parameter, but raw nominal semantics are not proven.",
                "AdjClose": "Documented historical price parameter; adjustment convention not sufficient for inverse-price raw_close.",
                "Dividend": "Documented WISEPRICE dividend history output; split-event support is not proven in the public docs reviewed.",
            },
            historical_coverage="Docs claim historical price/dividend access; exact US delisted depth not documented.",
            delisted_coverage="unverified",
            stable_id_capability="Uses Yahoo-style tickers in public docs; stable share-class identifiers unverified.",
            pit_membership_capability="none documented",
            licensing_redistribution="Subscription data; do not commit exports unless license permits.",
            confidence="medium for spreadsheet availability; low for forensic raw-close suitability",
            recommendation=(
                "Use only through explicit local export/import mappings until REST docs and split probes prove "
                "raw_close and total-return semantics."
            ),
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unverified,
                adjusted_close=unverified,
                dividends=unverified,
                splits=unverified,
                delisted_securities=unverified,
                ticker_history=unverified,
                point_in_time_index_membership=unsupported,
                stable_security_ids=unverified,
                verified=False,
                notes="No endpoint/schema is encoded; Close must pass empirical split audit before raw_close mapping.",
            ),
            blocking_uncertainties=[
                "No authoritative REST base URL, authentication scheme, pagination, or response schema found.",
                "Close may be split-adjusted or otherwise normalized; empirical split audit is mandatory.",
                "No documented PIT S&P 500 membership or permanent security identifier.",
            ],
        ),
        SourceAssessment(
            source_name="CRSP via WRDS",
            category="commercial_gold_standard",
            official_documentation=[
                "https://www.crsp.org/research/",
                "https://wrds-www.wharton.upenn.edu/demo/crsp/form/",
            ],
            access_date=ACCESS_DATE,
            access_method="WRDS/CRSP subscription; no local credentials assumed",
            cost_access_status="Paid/institutional subscription required",
            relevant_fields=[
                "PERMNO",
                "PERMCO",
                "date",
                "PRC",
                "RET",
                "DLRET",
                "distribution events",
                "name history",
                "dsp500list/dsp500list_v2 where licensed",
            ],
            field_semantics={
                "PERMNO": "Permanent security identifier suitable for ticker/name/corporate-action continuity.",
                "PRC": "CRSP daily price field can provide tape-like closing price with CRSP sign conventions.",
                "RET/DLRET": "Holding-period and delisting return fields are designed for corporate-action-safe returns.",
                "dsp500list": "Reported by WRDS guides as daily SPX membership where subscribed.",
            },
            historical_coverage="US stock database extends far before 2004; SPX membership access depends on license.",
            delisted_coverage="documented CRSP delisting history and inactive securities",
            stable_id_capability="PERMNO/PERMCO",
            pit_membership_capability="available through CRSP index membership tables if licensed",
            licensing_redistribution="Licensed research data; do not redistribute raw or normalized market data.",
            confidence="high if subscription includes CRSP stock and SPX list tables",
            recommendation="Preferred defensible source when credentials/licensing are available.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=supported,
                adjusted_close=supported,
                dividends=supported,
                splits=supported,
                delisted_securities=supported,
                ticker_history=supported,
                point_in_time_index_membership=supported,
                stable_security_ids=supported,
                verified=False,
                notes="Documentation supports suitability, but this repo has not acquired or empirically certified a local CRSP snapshot.",
            ),
            blocking_uncertainties=[
                "Requires subscription and WRDS access.",
                "Exact licensed table availability must be checked in the user's WRDS account.",
                "CRSP sign conventions and missing delisting returns need explicit normalization and audit.",
            ],
        ),
        SourceAssessment(
            source_name="Norgate Data",
            category="commercial_fallback",
            official_documentation=[
                "https://norgatedata.com/data-content-tables.php",
                "https://pypi.org/project/norgatedata/",
                "https://norgatedata.com/data-package-faq.php",
            ],
            access_date=ACCESS_DATE,
            access_method="Windows Norgate Data Updater plus Python/plugin API; no subscription assumed",
            cost_access_status="Paid subscription; Platinum/Diamond needed for historical index constituents",
            relevant_fields=[
                "Date",
                "Open",
                "High",
                "Low",
                "Close",
                "Unadjusted Close",
                "Dividend",
                "Capital Event",
                "assetid",
                "index_constituent_timeseries",
            ],
            field_semantics={
                "Unadjusted Close": "Provider API documents unadjusted close alongside adjusted price settings.",
                "index_constituent_timeseries": "Returns daily true/false membership for indices such as S&P 500.",
                "assetid": "Provider-generated unchanging numeric identifier.",
            },
            historical_coverage="S&P 500 historical constituents documented from March 1957; US data package scope depends on subscription.",
            delisted_coverage="US Delisted package documented; required for survivorship-bias-free work.",
            stable_id_capability="assetid",
            pit_membership_capability="S&P 500 $SPX daily constituent timeseries at Platinum/Diamond level",
            licensing_redistribution="Subscription data; do not commit market data.",
            confidence="high as a commercial fallback if subscription/API environment is available",
            recommendation="Strong fallback for P0-P3 acquisition if user obtains appropriate subscription and Windows API access.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=supported,
                adjusted_close=supported,
                dividends=supported,
                splits=supported,
                delisted_securities=supported,
                ticker_history=supported,
                point_in_time_index_membership=supported,
                stable_security_ids=supported,
                verified=False,
                notes="Documentation supports suitability; local acquisition was not possible without subscription/API runtime.",
            ),
            blocking_uncertainties=[
                "Requires paid subscription and Windows Norgate runtime.",
                "Provider's total-return convention must be mapped explicitly.",
                "Historical index constituent function returns time series, not raw change-event list.",
            ],
        ),
        SourceAssessment(
            source_name="S&P Dow Jones Indices press releases and current index page",
            category="index_membership_cross_check",
            official_documentation=[
                "https://press.spglobal.com/",
                "https://www.spglobal.com/spdji/en/indices/equity/sp-500/",
            ],
            access_date=ACCESS_DATE,
            access_method="Public web pages and press releases",
            cost_access_status="Public pages; bulk historical constituent product not identified as free",
            relevant_fields=["effective date", "action", "company name", "ticker", "GICS sector"],
            field_semantics={
                "effective date": "Press releases often state whether changes are effective prior to the open.",
                "current constituents": "Index page gives current/top constituent data, not full historical PIT membership.",
            },
            historical_coverage="Point announcements are searchable; complete bulk history not available from public pages reviewed.",
            delisted_coverage="Only as mentioned in announcement reasons.",
            stable_id_capability="Ticker/company name only in public release tables.",
            pit_membership_capability="official event spot checks; not a complete free PIT table",
            licensing_redistribution="Public web text is not a redistributable bulk index database.",
            confidence="high for individual announcement cross-checks; low for full reconstruction alone",
            recommendation="Use for sampled cross-checks of provisional event histories and effective-date conventions.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unsupported,
                adjusted_close=unsupported,
                dividends=unsupported,
                splits=unsupported,
                delisted_securities=unsupported,
                ticker_history=unverified,
                point_in_time_index_membership=unverified,
                stable_security_ids=unsupported,
                verified=False,
                notes="Official event source but not sufficient alone for complete normalized P3 membership.",
            ),
            blocking_uncertainties=[
                "No free authoritative bulk table with all historical S&P 500 membership spells found.",
                "Ticker-only releases require independent identifier mapping.",
            ],
        ),
        SourceAssessment(
            source_name="Wikipedia / derived S&P 500 change histories",
            category="provisional_index_events",
            official_documentation=[
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                "https://github.com/fja05680/sp500",
            ],
            access_date=ACCESS_DATE,
            access_method="Third-party public pages/repositories",
            cost_access_status="Free/public",
            relevant_fields=["date", "added ticker", "removed ticker", "security name", "reason"],
            field_semantics={
                "date": "Generally an effective date in derived datasets, but must be verified per event.",
                "selected changes": "Wikipedia explicitly should be treated as selected/provisional, not authoritative completeness.",
            },
            historical_coverage="Varies by derivative; some start around 1996, but completeness is not guaranteed.",
            delisted_coverage="Ticker/event rows only; no prices or terminal returns.",
            stable_id_capability="ticker/date only unless independently mapped",
            pit_membership_capability="provisional event reconstruction only",
            licensing_redistribution="Respect source license; do not commit large scraped extracts without review.",
            confidence="medium for cross-check/provisional scaffolding; low for certified P3 membership",
            recommendation="May seed a provisional reconstruction, but cannot certify P3 without primary-source cross-checks and stable IDs.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unsupported,
                adjusted_close=unsupported,
                dividends=unsupported,
                splits=unsupported,
                delisted_securities=unsupported,
                ticker_history=unverified,
                point_in_time_index_membership=unverified,
                stable_security_ids=unsupported,
                verified=False,
                notes="Useful as a cross-check or provisional event source only.",
            ),
            blocking_uncertainties=[
                "Completeness and effective-date semantics vary.",
                "Ticker reuse and share-class continuity are unresolved without another identifier source.",
            ],
        ),
        SourceAssessment(
            source_name="SEC EDGAR identifier files and filings",
            category="identifier_cross_check",
            official_documentation=[
                "https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/look-central-index-key-cik-number",
                "https://www.sec.gov/search-filings/cik-lookup",
                "https://www.sec.gov/files/company_tickers_exchange.json",
            ],
            access_date=ACCESS_DATE,
            access_method="Public SEC files/APIs with compliant User-Agent",
            cost_access_status="Free public government data",
            relevant_fields=["CIK", "ticker", "company name", "exchange", "filing dates"],
            field_semantics={
                "CIK": "SEC filer identifier, useful for filings but not guaranteed to identify one tradable share class.",
                "ticker": "Current SEC ticker mapping; not a full ticker-history or price database.",
            },
            historical_coverage="Filings history varies by issuer; current ticker exchange file is not PIT share-class history.",
            delisted_coverage="Filings may evidence events; no daily price/delisting return coverage.",
            stable_id_capability="CIK only; insufficient alone for share-class stable ID",
            pit_membership_capability="none",
            licensing_redistribution="US government source, but derived market data still must observe provider licenses.",
            confidence="high for filing/entity cross-checks; low as primary security master",
            recommendation="Use to cross-check company identity and corporate-event filings, not as sole security_id.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unsupported,
                adjusted_close=unsupported,
                dividends=unverified,
                splits=unverified,
                delisted_securities=unverified,
                ticker_history=unverified,
                point_in_time_index_membership=unsupported,
                stable_security_ids=unverified,
                verified=False,
                notes="CIK is helpful but is not a certified tradable-security identifier for this project.",
            ),
            blocking_uncertainties=[
                "CIK can group multiple share classes.",
                "Corporate action extraction from filings is event-specific and not a complete normalized action feed.",
            ],
        ),
        SourceAssessment(
            source_name="OpenFIGI",
            category="identifier_mapping",
            official_documentation=["https://www.openfigi.com/api/documentation"],
            access_date=ACCESS_DATE,
            access_method="Public mapping API; optional API key for higher rate limits",
            cost_access_status="Free public API with rate limits",
            relevant_fields=["figi", "shareClassFIGI", "compositeFIGI", "ticker", "exchCode", "securityType"],
            field_semantics={
                "figi": "Instrument identifier assigned by OpenFIGI/Bloomberg Open Symbology.",
                "shareClassFIGI": "Share-class level identifier where available.",
                "ticker": "Mapping input/output, not sufficient without date-aware query context.",
            },
            historical_coverage="Mapping service; not a historical price or PIT membership database.",
            delisted_coverage="Unlisted inclusion can be requested, but completeness for old S&P members is unverified.",
            stable_id_capability="FIGI/shareClassFIGI when mapped successfully",
            pit_membership_capability="none",
            licensing_redistribution="Observe OpenFIGI terms and API limits.",
            confidence="medium for identifier enrichment, not source-of-truth membership",
            recommendation="Use as supplemental identifier mapping after membership/security universe is established.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unsupported,
                adjusted_close=unsupported,
                dividends=unsupported,
                splits=unsupported,
                delisted_securities=unverified,
                ticker_history=unverified,
                point_in_time_index_membership=unsupported,
                stable_security_ids=supported,
                verified=False,
                notes="Helpful enrichment source; does not solve PIT membership or returns.",
            ),
            blocking_uncertainties=[
                "Ticker/date-only jobs can map to multiple instruments.",
                "Need explicit handling of no-match warnings and rate limits.",
            ],
        ),
        SourceAssessment(
            source_name="Nasdaq Trader / NYSE corporate action products",
            category="corporate_actions_cross_check",
            official_documentation=[
                "https://nasdaqtrader.com/Trader.aspx?id=DailyListPD",
                "https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs",
                "https://www.nyse.com/market-data/corporate-actions",
            ],
            access_date=ACCESS_DATE,
            access_method="Public current files plus paid corporate-action products",
            cost_access_status="Current symbol directory public; historical Daily List/corporate-action feeds require subscription/licensing",
            relevant_fields=["new listings", "delistings", "symbol changes", "name changes", "dividends", "stock splits"],
            field_semantics={
                "Daily List": "Nasdaq describes a corporate-action feed with historical data back to 1999.",
                "symbol directory": "Current-day listing reference, not historical PIT membership.",
            },
            historical_coverage="Nasdaq Daily List documentation says historical corporate actions back to 1999; NYSE package coverage requires license.",
            delisted_coverage="Exchange-specific events, not complete return treatment.",
            stable_id_capability="CUSIP availability may require product option; not enough by itself for all venues.",
            pit_membership_capability="none",
            licensing_redistribution="Exchange data is licensed; do not commit provider files.",
            confidence="medium as action cross-check, low as complete panel source",
            recommendation="Use to cross-check splits, dividends, delistings, and symbol changes if licensed.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unsupported,
                adjusted_close=unsupported,
                dividends=supported,
                splits=supported,
                delisted_securities=unverified,
                ticker_history=supported,
                point_in_time_index_membership=unsupported,
                stable_security_ids=unverified,
                verified=False,
                notes="Corporate-action support only; not a daily panel/PIT index source.",
            ),
            blocking_uncertainties=[
                "Requires product subscriptions for historical bulk data.",
                "Coverage is exchange-specific and must be joined to price source identities.",
            ],
        ),
        SourceAssessment(
            source_name="Alpha Vantage",
            category="price_return_secondary",
            official_documentation=["https://www.alphavantage.co/documentation/"],
            access_date=ACCESS_DATE,
            access_method="REST API with API key",
            cost_access_status="Free/premium tiers; full 20+ year daily adjusted endpoint is premium according to docs reviewed",
            relevant_fields=["open", "high", "low", "close", "adjusted close", "dividend amount", "split coefficient"],
            field_semantics={
                "TIME_SERIES_DAILY": "Docs describe raw as-traded OHLCV daily time series.",
                "TIME_SERIES_DAILY_ADJUSTED": "Docs include raw OHLCV, adjusted close, split and dividend events.",
            },
            historical_coverage="20+ years for supported symbols; full output may require premium.",
            delisted_coverage="Not documented as survivorship-bias-free for historical S&P members.",
            stable_id_capability="ticker symbol only in endpoint",
            pit_membership_capability="none",
            licensing_redistribution="API terms apply; do not commit bulk responses without license review.",
            confidence="medium for liquid current tickers; low for P3-certified universe",
            recommendation="Could support P0/P1 exploratory data or action cross-checks, not P3 certification alone.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unverified,
                adjusted_close=supported,
                dividends=supported,
                splits=supported,
                delisted_securities=unverified,
                ticker_history=unsupported,
                point_in_time_index_membership=unsupported,
                stable_security_ids=unsupported,
                verified=False,
                notes="Raw close claim still requires split audit and delisted coverage is not documented.",
            ),
            blocking_uncertainties=[
                "No survivorship-bias-free delisted S&P universe in docs reviewed.",
                "Ticker-only identity and rate/cost limits complicate bulk acquisition.",
            ],
        ),
        SourceAssessment(
            source_name="Nasdaq Data Link WIKI EOD",
            category="rejected_price_source",
            official_documentation=["https://docs.data.nasdaq.com/v1.0/docs/in-depth-usage"],
            access_date=ACCESS_DATE,
            access_method="Historical Nasdaq Data Link database reference",
            cost_access_status="Historical community dataset reference; not a current authoritative feed",
            relevant_fields=["open", "high", "low", "close", "volume", "dividends", "splits"],
            field_semantics={
                "WIKI": "Docs describe community-curated EOD prices, dividends, and splits for US companies.",
            },
            historical_coverage="Legacy/community dataset; not suitable as current frozen 2004-latest source.",
            delisted_coverage="not sufficient for certified survivorship-bias-free S&P 500 work",
            stable_id_capability="ticker/dataset code",
            pit_membership_capability="none",
            licensing_redistribution="Public-domain description in legacy docs, but dataset status is not adequate for Phase 2A.",
            confidence="low for certification",
            recommendation="Rejected for P2/P3 certification.",
            provider_capabilities=ProviderCapabilities(
                historical_raw_close=unverified,
                adjusted_close=unverified,
                dividends=unverified,
                splits=unverified,
                delisted_securities=unverified,
                ticker_history=unsupported,
                point_in_time_index_membership=unsupported,
                stable_security_ids=unsupported,
                verified=False,
                notes="Legacy community source does not meet forensic requirements.",
            ),
            blocking_uncertainties=[
                "Not authoritative enough for this replication.",
                "No PIT S&P 500 membership or stable identifiers.",
            ],
        ),
    ]


def source_discovery_report() -> dict[str, Any]:
    assessments = [assessment.to_dict() for assessment in source_assessments()]
    return {
        "phase": "2A",
        "access_date": ACCESS_DATE,
        "objective": "real S&P 500 source discovery for P0-P3 without strategy performance execution",
        "chosen_sources": {
            "certified_snapshot": None,
            "recommended_defensible_path": "CRSP/WRDS if licensed; Norgate Platinum/Diamond as commercial fallback.",
            "provisional_path": "WiseSheets or public exports only through explicit local mappings and empirical audits.",
        },
        "certification_implications": {
            "P0": "No real snapshot produced in this phase; local file imports remain available.",
            "P1": "No real snapshot produced in this phase; local file imports remain available.",
            "P2": "Requires empirical raw-close split certification and corporate-action-safe returns.",
            "P3": "Requires P2 plus PIT membership, stable IDs, survivorship and terminal-return checks.",
            "P4": "Not run; requires a P3-quality continuous historical snapshot.",
            "P5": "Not run; requires a P3-quality post-publication forward snapshot.",
        },
        "assessments": assessments,
    }
