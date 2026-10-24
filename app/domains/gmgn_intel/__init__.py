"""Our GMGN intelligence layer (raw GMGN data, our normalization + ranking).

Public surface: :class:`GMGNIntelService` and its singleton accessor. GMGN's
own ordering is never propagated — every list we emit is sorted by our score.
"""

from __future__ import annotations

from app.domains.gmgn_intel.service import GMGNIntelService, get_gmgn_intel_service

__all__ = ["GMGNIntelService", "get_gmgn_intel_service"]
