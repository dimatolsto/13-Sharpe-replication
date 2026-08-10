from __future__ import annotations

import os

from .base import CapabilityStatus, ProviderCapabilities


class WiseSheetsCapabilityGate:
    """Provenance gate for WiseSheets data.

    Public WiseSheets material exposes historical Close/AdjClose/Dividend functionality, but the
    semantics relevant to this paper (specifically *unadjusted historical nominal close* and support
    for dead/delisted securities) must be verified from the user's actual account/API response before
    P2-P5 are certified. We therefore do not hard-code an undocumented API endpoint here.
    """

    def __init__(self) -> None:
        self.api_key_present = bool(os.getenv("WISESHEETS_API_KEY"))
        self.base_url = os.getenv("WISESHEETS_API_BASE_URL", "").strip()

    def provisional_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            historical_raw_close=CapabilityStatus.UNVERIFIED,
            adjusted_close=CapabilityStatus.UNVERIFIED,
            dividends=CapabilityStatus.UNVERIFIED,
            splits=CapabilityStatus.UNVERIFIED,
            delisted_securities=CapabilityStatus.UNVERIFIED,
            ticker_history=CapabilityStatus.UNVERIFIED,
            point_in_time_index_membership=CapabilityStatus.UNSUPPORTED,
            stable_security_ids=CapabilityStatus.UNVERIFIED,
            verified=False,
            notes=(
                "Not configured for acquisition. No authoritative WiseSheets endpoint/schema is "
                "encoded; raw nominal close, adjusted close, dividends, split events, delisted "
                "coverage, ticker history, and stable IDs must be verified from account/API docs."
            ),
        )

    def report(self) -> dict:
        return {
            "api_key_present": self.api_key_present,
            "base_url_configured": bool(self.base_url),
            "network_adapter": "not_configured",
            "capabilities": self.provisional_capabilities().as_dict(),
        }
