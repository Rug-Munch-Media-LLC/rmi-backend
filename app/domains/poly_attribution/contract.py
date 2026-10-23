"""Validated, already-hashed event payload handed to the ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — dataclass field annotation
from decimal import Decimal  # noqa: TC003 — dataclass field annotation


@dataclass(frozen=True, slots=True)
class EventInput:
    """One attribution event, post-validation and post-hashing."""

    partner: str
    campaign: str
    surface: str
    event: str
    market_id: str | None
    wallet_hash: str | None
    builder_code_hash: str | None
    idem_key: str
    ts: datetime
    value_usd: Decimal | None
    ts_from_client: bool = False

    def fingerprint(self) -> str:
        """Stable digest of the payload — detects idempotency-key reuse."""
        payload = {
            "partner": self.partner,
            "campaign": self.campaign,
            "surface": self.surface,
            "event": self.event,
            "market_id": self.market_id,
            "wallet_hash": self.wallet_hash,
            "builder_code_hash": self.builder_code_hash,
            "value_usd": str(self.value_usd) if self.value_usd is not None else None,
        }
        if self.ts_from_client:
            payload["ts"] = self.ts.isoformat()
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()
