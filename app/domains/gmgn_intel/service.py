"""GMGN intelligence service — orchestration only, no transport, no parsing.

Client fetches raw, normalizers shape it, scoring ranks it. Every builder
returns ``evidence`` (which endpoint / bucket produced a fact) and ``missing``
(null + reason) so consumers can explain an empty field downstream.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.domains.gmgn_intel.client import GMGNIntelClient
from app.domains.gmgn_intel.coerce import canonical_chain, gap, gmgn_chain, now_iso
from app.domains.gmgn_intel.detail import normalize_rank_row, normalize_token_info
from app.domains.gmgn_intel.lifecycle import STAGES, build_lifecycle
from app.domains.gmgn_intel.scoring import WEIGHTS, WEIGHTS_VERSION, score_token
from app.domains.gmgn_intel.security import normalize_security
from app.domains.gmgn_intel.wallet import build_wallet_profile

log = get_logger(__name__)
RANK_LIMIT = 100


class GMGNIntelService:
    """Our own GMGN intelligence layer (raw data in, our judgement out)."""

    def __init__(self, provider: Any = None, client: GMGNIntelClient | None = None) -> None:
        self._provider = provider
        self._client = client

    def _get_client(self) -> GMGNIntelClient:
        if self._client is None:
            if self._provider is None:
                from app.databus.gmgn_provider import get_gmgn_provider

                self._provider = get_gmgn_provider()
            self._client = GMGNIntelClient(self._provider)
        return self._client

    def _get_provider(self) -> Any:
        if self._provider is None:
            from app.databus.gmgn_provider import get_gmgn_provider

            self._provider = get_gmgn_provider()
        return self._provider

    async def lifecycle(self, chain: str = "solana", limit: int = 60) -> dict[str, Any]:
        """Tokens across all launchpad stages, scored by our own formula."""
        observed_at = now_iso()
        client = self._get_client()
        buckets = await client.trenches(chain, limit)
        if not buckets:
            return {
                "chain": canonical_chain(chain),
                "observed_at": observed_at,
                "sources": ["POST /v1/trenches", "GET /v1/market/rank"],
                "counts": dict.fromkeys(STAGES, 0),
                "token_count": 0,
                "tokens": [],
                "missing": [gap("trenches", "upstream returned no data (rate limit or outage)")],
            }
        rank_rows = await client.rank(chain, "1h", RANK_LIMIT)
        result = build_lifecycle(buckets, rank_rows, chain, observed_at)
        if rank_rows is None:
            # 5 calls/window on the free tier: say so instead of reporting 0 trends.
            result["missing"] = [gap("trending", "rank unavailable (rate limit) — counts cover stages only")]
        return result

    async def ranked_trending(self, chain: str = "solana", interval: str = "1h", limit: int = 50) -> dict[str, Any]:
        """Trending tokens re-ranked by our score (GMGN's order kept as evidence)."""
        observed_at = now_iso()
        rows = await self._get_client().rank(chain, interval, max(limit, RANK_LIMIT))
        if not rows:
            return {
                "chain": canonical_chain(chain),
                "interval": interval,
                "observed_at": observed_at,
                "sources": ["GET /v1/market/rank"],
                "token_count": 0,
                "tokens": [],
                "missing": [gap("rank", "upstream returned no data (rate limit or outage)")],
            }
        tokens = []
        for row in rows:
            token = normalize_rank_row(row, observed_at)
            scored = score_token(token)
            token["score"] = scored["score"]
            token["score_grade"] = scored["grade"]
            token["score_breakdown"] = scored
            if token.get("address"):
                tokens.append(token)
        tokens.sort(key=lambda item: item.get("score") or 0.0, reverse=True)
        return {
            "chain": canonical_chain(chain),
            "interval": interval,
            "observed_at": observed_at,
            "sources": ["GET /v1/market/rank"],
            "ordering": "our_score_desc",
            "token_count": len(tokens),
            "tokens": tokens[:limit],
        }

    async def token_intel(
        self, address: str, chain: str = "solana", include_smart_money: bool = True
    ) -> dict[str, Any]:
        """Token detail: info + security (+ smart-money holders) in one payload."""
        observed_at = now_iso()
        client = self._get_client()
        info, raw_security = await asyncio.gather(
            client.token_info(address, chain),
            client.token_security(address, chain),
        )
        token = normalize_token_info(info, address, chain, observed_at)
        security = normalize_security(raw_security, observed_at)
        token["security"] = security
        scored = score_token(token, security)
        token["score"] = scored["score"]
        token["score_grade"] = scored["grade"]
        token["score_breakdown"] = scored
        if info is None:
            token["missing"].append(gap("token_info", "upstream returned no data (rate limit or outage)"))
        if include_smart_money:
            token["smart_money"] = await self._smart_money(address, chain)
        return token

    async def _smart_money(self, address: str, chain: str) -> dict[str, Any]:
        """Holder-derived smart-money view from the existing provider method."""
        try:
            # Provider methods take GMGN slugs (``sol``) — ``solana`` silently returns empty.
            return await self._get_provider().get_smart_money(address, gmgn_chain(chain))
        except (OSError, RuntimeError, ValueError) as exc:
            log.debug("gmgn intel: smart money failed for %s: %s", address, exc)
            return {"address": address, "source": "none", "smart_money_activity": []}

    async def wallet_intel(self, wallet: str, chain: str = "solana", period: str = "7d") -> dict[str, Any]:
        """Wallet stats + activity, normalized (no signed holdings call)."""
        observed_at = now_iso()
        detail = await self._get_provider().get_wallet_detail(wallet, gmgn_chain(chain), period)
        return build_wallet_profile(detail, wallet, chain, period, observed_at)

    def descriptor(self) -> dict[str, Any]:
        """Static contract for consumers: stages, weights, endpoints."""
        return {
            "stages": list(STAGES),
            "weights": WEIGHTS,
            "weights_version": WEIGHTS_VERSION,
            "endpoints": [
                "POST /v1/trenches",
                "GET /v1/market/rank",
                "GET /v1/token/info",
                "GET /v1/token/security",
                "GET /v1/user/wallet_stats",
                "GET /v1/user/wallet_activity",
                "GET /v1/market/token_top_holders (smart money)",
            ],
            "excluded": ["GET /v1/user/wallet_holdings (signed — no private key held)"],
            "null_contract": "every absent field appears in `missing` with a reason",
        }


_service: GMGNIntelService | None = None


def get_gmgn_intel_service() -> GMGNIntelService:
    """Process-wide service singleton (provider itself is a singleton)."""
    global _service
    if _service is None:
        _service = GMGNIntelService()
    return _service
