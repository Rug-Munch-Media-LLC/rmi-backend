"""Hybrid retrieval — query expansion, hybrid retriever, search entry points."""

from __future__ import annotations

import re
import time
from typing import Any

from app.core.logging import get_logger
from app.integrations.rag_helpers.rerank import reorder_by_relevance, rerank_results
from app.integrations.rag_helpers.tracing import trace_span
from app.rag.config import RAG_EXPANSION_TERMS
from app.rag.retrieval.filters import apply_news_boosts
from app.rag.retrieval.helpers import ScoredHit
from app.rag.retrieval.rerank import RelevanceScorer

log = get_logger(__name__)

# ── QueryExpander ────────────────────────────────────────────────────

# Simple crypto/security synonym map for query expansion. Covers the seed
# corpus domains: scam/security taxonomy, DeFi, TA, tokenomics and market
# microstructure. Values are appended space-separated to the query, so
# single words help both the dense embedding and the lexical sparse pillar.
_QUERY_SYNONYMS: dict[str, list[str]] = {
    # ── scam / security ─────────────────────────────────────────────
    "rug": ["rugpull", "exit_scam", "liquidity_grab"],
    "scam": ["fraud", "scheme", "fake", "malicious"],
    "honeypot": ["cannot_sell", "tax_trap", "sell_restricted"],
    "phishing": ["fake_site", "lookalike", "spoof"],
    "drain": ["drainer", "sweeper", "token_stealer"],
    "pump": ["pump_and_dump", "artificial_volume", "wash_trade"],
    "whale": ["large_holder", "concentrated_ownership", "insider"],
    "exploit": ["vulnerability", "attack", "breach", "hack"],
    "sanction": ["ofac", "blacklist", "banned", "restricted"],
    "laundry": ["mixer", "tumbler", "coinjoin", "obfuscation"],
    "flash": ["flash_loan", "uncollateralized_loan", "arbitrage"],
    "reentrancy": ["reentrant", "recursive_call", "external_call"],
    "oracle": ["price_feed", "data_feed", "manipulation"],
    "approval": ["allowance", "approve", "spender", "revoke"],
    "mint": ["supply_creation", "inflation", "new_supply"],
    "frontrun": ["front_running", "tx_ordering", "mempool", "sandwich"],
    "upgrade": ["upgradeable", "proxy", "implementation_swap"],
    "tax": ["fee", "transfer_fee", "buy_sell_fee"],
    "lock": ["lockup", "locked_liquidity", "timelock"],
    # ── defi ────────────────────────────────────────────────────────
    "liquidity": ["liquidity_pool", "depth", "reserves", "lp"],
    "yield": ["apy", "rewards", "interest", "earnings"],
    "lp": ["liquidity_provider", "liquidity_pool", "reserves"],
    "mev": ["maximal_extractable_value", "sandwich", "front_running"],
    "arb": ["arbitrage", "price_difference", "spread_trade"],
    "arbitrage": ["price_difference", "spread_trade", "cross_venue"],
    "farm": ["yield_farming", "incentives", "reward_pool"],
    "lending": ["borrow", "collateral", "loan", "supply_rate"],
    "borrow": ["lending", "collateral", "debt", "health_factor"],
    "stablecoin": ["pegged", "peg", "collateralized", "algorithmic"],
    "tvl": ["total_value_locked", "deposits", "locked_assets"],
    "amm": ["automated_market_maker", "constant_product", "swap_pool"],
    "bridge": ["cross_chain", "wrapped_asset", "bridge_exploit"],
    "stake": ["staking", "delegate", "lock_tokens", "rewards"],
    "liquidat": ["liquidation", "cascade", "health_factor", "collateral_call"],
    "liquidation": ["cascade", "health_factor", "collateral_call", "margin_call"],
    "liquidations": ["liquidation", "cascade", "health_factor"],
    # ── ta ──────────────────────────────────────────────────────────
    "rsi": ["relative_strength_index", "momentum", "overbought", "oversold"],
    "macd": ["moving_average_convergence", "momentum", "histogram"],
    "support": ["demand_zone", "floor", "buy_wall", "liquidity_zone"],
    "resistance": ["supply_zone", "ceiling", "sell_wall", "liquidity_zone"],
    "divergence": ["momentum_conflict", "reversal_warning"],
    "momentum": ["rate_of_change", "velocity", "trend_strength"],
    "trend": ["uptrend", "downtrend", "structure", "higher_highs"],
    "breakout": ["consolidation", "range_break", "liquidity_sweep"],
    "volume": ["participation", "turnover", "wash_trade", "fake_volume"],
    "candle": ["candlestick", "wick", "engulfing", "rejection"],
    # ── tokenomics ──────────────────────────────────────────────────
    "vesting": ["cliff", "unlock_schedule", "lockup", "emission"],
    "emission": ["inflation", "mint_rate", "rewards_schedule", "supply_growth"],
    "supply": ["circulation", "float", "max_supply", "total_supply"],
    "burn": ["deflation", "token_removal", "buyback_burn"],
    "buyback": ["repurchase", "treasury", "burn"],
    "inflation": ["dilution", "emission", "supply_growth", "minting"],
    "allocation": ["distribution", "insider_bucket", "team_tokens"],
    "airdrop": ["claim", "sybil", "distribution_event"],
    "staking": ["lock", "rewards", "delegate", "ve_tokens"],
    # ── market microstructure ───────────────────────────────────────
    "slippage": ["price_impact", "spread", "execution_cost"],
    "spread": ["bid_ask", "market_maker", "execution_cost"],
    "depth": ["market_depth", "order_book", "liquidity"],
    "maker": ["market_maker", "limit_order", "liquidity_provider"],
    "taker": ["market_order", "crossing_spread", "liquidity_remover"],
    "leverage": ["margin", "liquidation", "notional", "open_interest"],
    "funding": ["funding_rate", "perpetual", "basis"],
    "oi": ["open_interest", "derivative_positions", "crowded_trade"],
    "order": ["order_book", "limit_order", "stop_order", "iceberg"],
    "orders": ["order_book", "limit_order", "stop_order"],
}

# Multi-word phrase expansions: bigram keys (underscore-joined tokens) →
# space-separated synonym phrases. Matched against consecutive query tokens
# so e.g. "order block" recalls "supply demand zone institutional zone".
_QUERY_PHRASES: dict[str, str] = {
    "order_block": "supply demand zone institutional zone",
    "flash_loan": "uncollateralized loan instant arbitrage exploit",
    "impermanent_loss": "liquidity provider divergence holding",
    "order_book": "bid ask depth resting orders",
    "open_interest": "derivative positions leverage outstanding contracts",
    "funding_rate": "perpetual premium basis longs pay shorts",
    "rug_pull": "liquidity removal mint dump exit scam",
    "pump_dump": "artificial volume wash trade coordinated",
    "smart_contract": "program blockchain code audit vulnerability",
    "token_supply": "circulation float emission max supply",
}

# Common crypto misspellings / variants for fuzzy expansion.
_QUERY_VARIANTS: dict[str, list[str]] = {
    "defi": ["defi", "de-fi", "decentralized_finance"],
    "nft": ["nft", "non-fungible", "nonfungible"],
    "dex": ["dex", "decentralized_exchange", "amm"],
    "cex": ["cex", "centralized_exchange"],
}


class QueryExpander:
    """Expand a user query with synonyms and related terms for better recall.

    Strategy:
      1. Tokenise the query into lowercased terms.
      2. For each known term, append synonyms from the internal map.
      3. For each consecutive token pair, append phrase synonyms from the
         bigram map (e.g. ``order block`` → ``supply demand zone ...``).
      4. Return the original query plus an "expanded" version with the
         extra terms appended (space-separated, capped at ``max_terms``).

    Expansion is a recall lever only — the original query is always kept
    verbatim, so precision is never sacrificed.
    """

    def __init__(self) -> None:
        self.synonyms = _QUERY_SYNONYMS
        self.variants = _QUERY_VARIANTS
        self.phrases = _QUERY_PHRASES

    def expand(self, query: str, max_terms: int = RAG_EXPANSION_TERMS) -> str:
        """Return an expanded query with additional search terms."""
        if not query or not query.strip():
            return query

        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
        extra_terms: list[str] = []
        # Bigram phrase expansion FIRST (more specific than single tokens):
        # consecutive token pairs (underscore-joined) hit the phrase map,
        # e.g. tokens "order","block" → "order_block" → "supply demand zone".
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]}_{tokens[i + 1]}"
            if bigram in self.phrases:
                extra_terms.extend(self.phrases[bigram].split())
        # Then single-token synonyms and variants.
        for t in tokens:
            if t in self.synonyms:
                extra_terms.extend(self.synonyms[t])
            if t in self.variants:
                extra_terms.extend(v for v in self.variants[t] if v != t)

        # Deduplicate, limit, and append
        seen = set(tokens)
        extra = [et for et in extra_terms if et not in seen]
        expanded = query.strip()
        if extra:
            expanded += " " + " ".join(extra[:max_terms])

        log.debug("query_expanded", original_len=len(query), expanded_len=len(expanded))
        return expanded

    def expand_multi(self, queries: list[str]) -> list[str]:
        """Expand multiple queries in batch."""
        return [self.expand(q) for q in queries]


# ── HybridRetriever ──────────────────────────────────────────────────


def _merge_with_graph_results(
    results: list[ScoredHit],
    graph_results: list[dict[str, Any]],
) -> list[ScoredHit]:
    """Merge graph-enriched hits with vector hits using graph-boosted ranking.

    Graph hits come from exact entity matches in the knowledge graph, so
    they receive a boost that ranks them above weak similarity matches while
    remaining below strong ones. Results are deduplicated by ``doc_id``
    (higher score wins) and returned sorted by ``final_score`` descending.
    """
    by_doc: dict[str, ScoredHit] = {}
    for h in results:
        key = h.doc_id or f"vec:{id(h)}"
        if key not in by_doc or h.final_score > by_doc[key].final_score:
            by_doc[key] = h

    for gr in graph_results:
        content = gr.get("content", "")
        if not content:
            continue
        doc_id = gr.get("doc_id") or gr.get("id") or ""
        base = float(gr.get("score", 0.5))
        # Exact entity matches get a boost over weak vector similarity.
        boosted = min(1.0, base * 1.15 + 0.12)
        hit = ScoredHit(
            content=content,
            score=base,
            final_score=boosted,
            metadata={
                "relation": gr.get("relation", ""),
                "node_type": gr.get("neighbor_type", ""),
                "node_id": gr.get("neighbor_id", ""),
            },
            collection="graph",
            doc_id=doc_id,
        )
        key = doc_id or f"kg:{len(by_doc)}"
        if key not in by_doc or boosted > by_doc[key].final_score:
            by_doc[key] = hit

    return sorted(by_doc.values(), key=lambda s: -s.final_score)


class HybridRetriever:
    """Weighted fusion of ANN vector search and BM25-lite keyword search.

    Delegates to the M3 engine's ``three_pillar_search`` under the hood,
    then applies query expansion and relevance re-scoring.

    Usage:
        retriever = HybridRetriever()
        hits = await retriever.retrieve(
            query="honeypot token high tax",
            collection="scanner",
            top_k=10,
        )
    """

    def __init__(self, expander: QueryExpander | None = None, scorer: RelevanceScorer | None = None) -> None:
        self._expander = expander or QueryExpander()
        self._scorer = scorer or RelevanceScorer()

    async def retrieve(
        self,
        query: str,
        collection: str = "scanner",
        top_k: int = 5,
        min_similarity: float = 0.0,
        filters: dict[str, Any] | None = None,
        expand: bool = True,
        rerank: bool = True,
        candidate_k: int | None = None,
        rerank_tier: str = "deep",
    ) -> list[ScoredHit]:
        """Run hybrid retrieval with optional query expansion.

        Args:
            query: Natural-language search query.
            collection: RAG collection to search.
            top_k: Maximum results to return.
            min_similarity: Minimum similarity threshold (0-1).
            filters: Metadata filters (exact-match).
            expand: Whether to expand the query with synonyms (incl. bigram
                phrase expansion).
            rerank: Whether to rerank candidates with the Qwen3
                cross-encoder (Qwen3-Reranker-4B, local :8003) before returning.
                The underlying ``three_pillar_search`` ALREADY runs the
                mandatory cross-encoder rerank, so this pass only fires when
                the engine could not (single hit / reranker unreachable).
                Fail-open: if the reranker is unreachable the pre-rerank
                order is returned unchanged.
            candidate_k: Pre-rerank candidate pool size (defaults to
                ``top_k`` — behavior-preserving). When larger than ``top_k``
                the engine fetches ``candidate_k`` candidates, the reranker
                ranks them, and the result is truncated to ``top_k`` — so
                the cross-encoder sees a wider pool than the final answer.

        Returns:
            List of ScoredHit, sorted by relevance.
        """
        start = time.monotonic()

        with trace_span(
            "rag.retrieval.hybrid",
            {
                "openinference.span.kind": "RETRIEVER",
                "openinference.retriever.query": query,
                "retrieval.collection": collection,
                "retrieval.top_k": top_k,
                "retrieval.expand": expand,
                "retrieval.rerank": rerank,
            },
        ) as span:
            # 1. Optionally expand the query
            search_query = self._expander.expand(query) if expand else query

            # 2. Delegate to the M3 engine. ``candidate_k`` widens the pool
            # the mandatory engine rerank sees; the final ``top_k`` slice
            # happens after reranking below.
            from app.rag.engine import three_pillar_search

            engine_top_k = candidate_k or top_k
            raw = await three_pillar_search(
                query=search_query,
                collection=collection,
                top_k=engine_top_k,
                min_similarity=min_similarity,
                filters=filters,
            )

            # 3. Re-score and sort
            scored = self._scorer.rescore(raw, query)

            # 3a. The underlying three_pillar_search already runs the
            # MANDATORY cross-encoder rerank (engine._rerank_hits, fail-open)
            # and stamps each hit with metadata["rerank_score"]. Preserve that
            # canonical rerank order: re-sorting by a heuristic blend would
            # undo the cross-encoder's work. Only the legacy path that did not
            # rerank (single hit, reranker unreachable, or no engine) falls
            # through to the optional second pass below.
            engine_reranked = any((h.metadata or {}).get("rerank_score") is not None for h in scored)
            if engine_reranked:
                scored.sort(key=lambda s: -float((s.metadata or {}).get("rerank_score", 0.0)))
                for h in scored:
                    h.final_score = float((h.metadata or {}).get("rerank_score", h.final_score))

            # 3b. Graph-enhanced retrieval (best-effort): if the knowledge
            # graph is available and the query mentions entities, enrich
            # vector results with entity-verified neighbors (graph boost).
            try:
                from app.knowledge_graph.graph import KnowledgeGraph

                kg = KnowledgeGraph()
                entities = await kg.extract_entities(query)
                if entities:
                    graph_results = await kg.search_related(entities, limit=5)
                    if graph_results:
                        scored = _merge_with_graph_results(scored, graph_results)
                        span.set_attribute("retrieval.graph_results", len(graph_results))
                        span.set_attribute("retrieval.graph_boosted", True)
            except Exception as exc:
                log.debug("graph_enrich_skipped err=%s", exc)

            # 4. Optional cross-encoder rerank via Qwen3 reranker (fail-open).
            # Skipped when the engine's mandatory rerank already ran
            # (rerank_score present) — avoids a duplicate reranker call per
            # query. Graph-enriched hits added in step 3b ride along on the
            # engine rerank order (they carry an entity-match boost).
            if rerank and not engine_reranked and len(scored) > 1:
                scored = await self._apply_rerank(query, scored, top_k, tier=rerank_tier)

            # 4a. When a wider pre-rerank pool was requested, truncate to
            # the post-rerank ``top_k`` here — the reranker (engine or
            # Qwen3 reranker) has ranked the candidates, so the top_k slice keeps
            # the most relevant docs regardless of which path ran.
            if candidate_k is not None and candidate_k > top_k:
                scored = scored[:top_k]

            # 5. News-specific ranking boosts (freshness + entity overlap).
            # Applied after the cross-encoder so the reranker stays the
            # primary relevance signal while freshness/entity tilt the final
            # order. No-op for non-news collections.
            scored = apply_news_boosts(scored, query, collection=collection)

            took_ms = int((time.monotonic() - start) * 1000)
            log.info(
                "hybrid_retrieve",
                collection=collection,
                top_k=top_k,
                results=len(scored),
                took_ms=took_ms,
                expanded=expand,
                rerank=rerank,
            )
            span.set_attribute("openinference.retriever.documents", len(scored))
            span.set_attribute("retrieval.result_count", len(scored))

        return scored

    async def _apply_rerank(
        self,
        query: str,
        scored: list[ScoredHit],
        top_n: int,
        tier: str = "deep",
    ) -> list[ScoredHit]:
        """Rerank *scored* hits with the Qwen3 cross-encoder.

        Reorders hits by Qwen3 relevance (updating ``final_score``) and
        truncates to *top_n*. Fail-open: on any error or empty response the
        original ordering is returned unchanged. ``tier="live"`` and
        ``"deep"`` both route through the Qwen3-Reranker-4B server (:8003)
        — the 0.6B fast tier was retired 2026-08-29.
        """
        if len(scored) < 2:
            return scored
        try:
            docs = [(h.content or "")[:2000] for h in scored]
            reranked = await rerank_results(query, docs, top_n=len(scored), tier=tier)
            if not reranked:
                return scored
            ordered = reorder_by_relevance(scored, reranked, top_n)
            return ordered if ordered else scored
        except Exception as exc:
            log.debug("hybrid_rerank_failed err=%s", exc)
            return scored

    async def retrieve_multi(
        self,
        query: str,
        collections: list[str] | None = None,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> dict[str, list[ScoredHit]]:
        """Search multiple collections in parallel.

        Returns:
            Dict mapping collection name to its scored hits.
        """
        from app.rag.models import COLLECTIONS as DEFAULT_COLLECTIONS

        targets = collections or DEFAULT_COLLECTIONS
        results: dict[str, list[ScoredHit]] = {}

        for col in targets:
            try:
                hits = await self.retrieve(
                    query=query,
                    collection=col,
                    top_k=top_k,
                    min_similarity=min_similarity,
                )
                results[col] = hits
            except Exception as e:
                log.warning("multi_retrieve_failed", collection=col, error=str(e))
                results[col] = []

        return results


# ── Convenience instance ─────────────────────────────────────────────

_default_retriever = HybridRetriever()
_default_expander = QueryExpander()


async def search(
    query: str,
    collection: str = "scanner",
    top_k: int = 5,
) -> list[ScoredHit]:
    """Quick single-collection search (uses default retriever)."""
    return await _default_retriever.retrieve(
        query=query,
        collection=collection,
        top_k=top_k,
    )


def expand_query(query: str) -> str:
    """Quick query expansion (uses default expander)."""
    return _default_expander.expand(query)


async def _qdrant_search(
    vector: list[float],
    collection: str,
    limit: int = 10,
    score_threshold: float = 0.0,
) -> list[dict[str, Any]]:
    """Minimal Qdrant search — delegates to rugbrain HTTP API."""
    from app.integrations.rugbrain_client import rugbrain_search

    return await rugbrain_search(query="", collections=[collection], limit=limit)
