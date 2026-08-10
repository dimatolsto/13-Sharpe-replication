from __future__ import annotations

import os
from dataclasses import asdict

from .base import ProviderCapabilities


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
            historical_raw_close=False,
            adjusted_close=True,
            dividends=True,
            splits=False,
            delisted_securities=False,
            point_in_time_index_membership=False,
            stable_security_ids=False,
            verified=False,
            notes=(
                "Provisional only. Raw nominal close, split events, delisted coverage, stable IDs, "
                "and PIT membership must be independently verified."
            ),
        )

    def report(self) -> dict:
        return {
            "api_key_present": self.api_key_present,
            "base_url_configured": bool(self.base_url),
            "capabilities": asdict(self.provisional_capabilities()),
        }
