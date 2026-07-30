"""
Cross-Chain Trace — Main Analyzer
==================================
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from .bridge import _infer_bridge_destination, _is_bridge_contract
from .models import CrossChainEdge, CrossChainNode, CrossChainTraceReport
from .queries import _fetch_labels, _fetch_transfers
from .utils import _assign_role, _evm_address_portable

logger = get_logger("cross_chain_trace")


class CrossChainTraceAnalyzer:
    """
    Tracks fund flow across chains for a given wallet address.

    Uses chain-indexer ClickHouse data (73M EVM transfers) and
    wallet_labels entity database — all LOCAL, zero external API calls.
    """

    def __init__(self, max_transfers: int = 500, max_depth: int = 2) -> None:
        self.max_transfers = max_transfers
        self.max_depth = max_depth

    async def analyze(
        self,
        wallet_address: str,
        chain: str = "ethereum",
    ) -> CrossChainTraceReport:
        """Trace fund flow for a wallet address across chains."""
        addr = wallet_address.lower().strip()
        logger.info(
            "CrossChainTrace: tracing %s on %s (depth=%d, max_xfers=%d)",
            addr,
            chain,
            self.max_depth,
            self.max_transfers,
        )

        try:
            return await self._trace(addr, chain)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.error("CrossChainTrace failed for %s: %s", addr, exc)
            return CrossChainTraceReport(
                seed_address=addr,
                seed_chain=chain,
                error=str(exc)[:500],
            )

    async def _trace(
        self,
        address: str,
        chain: str,
    ) -> CrossChainTraceReport:
        # Step 1: Fetch all transfers for this address
        transfers = await _fetch_transfers(address, chain, self.max_transfers)

        # Step 2: Collect unique addresses + labels
        all_addresses: set[str] = set()
        for t in transfers:
            all_addresses.add(t["from_address"])
            all_addresses.add(t["to_address"])

        labels = await _fetch_labels(all_addresses) if all_addresses else {}

        # Step 3: For EVM addresses, also check other chains
        portable_chains = _evm_address_portable(address, chain)
        additional_transfers: list[dict[str, Any]] = []
        for other_chain in portable_chains[:3]:
            xfers = await _fetch_transfers(address, other_chain, min(200, self.max_transfers // 2))
            additional_transfers.extend(xfers)
            for t in xfers:
                all_addresses.add(t["from_address"])
                all_addresses.add(t["to_address"])

        all_transfers = transfers + additional_transfers

        if additional_transfers:
            labels = await _fetch_labels(all_addresses)

        # Step 4: Build nodes + edges
        seen_edges: set[tuple[str, str, str, int]] = set()
        nodes_map: dict[str, CrossChainNode] = {}
        edges_list: list[CrossChainEdge] = []
        bridge_hops: list[dict[str, Any]] = []
        known_entities: dict[str, str] = {}
        entity_flags: list[str] = []
        total_received = 0.0
        total_sent = 0.0
        chains_involved: set[str] = set()

        for t in all_transfers:
            edge_key = (t["from_address"], t["to_address"], t["chain"], t["block_number"])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            chains_involved.add(t["chain"])

            # Detect bridge interaction
            is_bridge = _is_bridge_contract(t["to_address"], t["chain"]) or _is_bridge_contract(
                t["from_address"], t["chain"]
            )
            bridge_dest = None
            if is_bridge:
                bridge_dest = _infer_bridge_destination(t["from_address"], t["to_address"], t["chain"])
                if bridge_dest:
                    bridge_hops.append(
                        {
                            "bridge_contract": (
                                t["to_address"]
                                if _is_bridge_contract(t["to_address"], t["chain"])
                                else t["from_address"]
                            ),
                            "source_chain": t["chain"],
                            "destination_chain": bridge_dest,
                            "tx_hash": t["tx_hash"],
                            "token_symbol": t.get("token_symbol"),
                            "value": t["value"],
                        }
                    )

            # Parse value
            try:
                val_float = float(t["value"]) / 1e18 if t["value"] else 0.0
            except (ValueError, TypeError):
                val_float = 0.0

            # Build edge
            edge = CrossChainEdge(
                from_address=t["from_address"],
                to_address=t["to_address"],
                chain=t["chain"],
                token_address=t["token_address"],
                token_symbol=t.get("token_symbol"),
                value=t["value"],
                tx_hash=t["tx_hash"],
                block_number=t["block_number"],
                block_timestamp=t["block_timestamp"],
                is_bridge=is_bridge,
                bridge_destination_chain=bridge_dest,
            )
            edges_list.append(edge)

            # Build/update nodes
            for addr_key, is_from in [(t["from_address"], True), (t["to_address"], False)]:
                if addr_key not in nodes_map:
                    lbl = labels.get(addr_key, {})
                    lbl_cat = lbl.get("label_category") if lbl else None
                    is_seed = addr_key == address
                    nodes_map[addr_key] = CrossChainNode(
                        address=addr_key,
                        chain=t["chain"],
                        label=lbl.get("label_name") if lbl else None,
                        label_category=lbl_cat,
                        role=_assign_role(addr_key, is_seed, lbl_cat),
                        is_sanctioned=lbl.get("is_sanctioned", False) if lbl else False,
                    )

                node = nodes_map[addr_key]
                node.tx_count += 1
                if is_from:
                    node.total_sent += val_float
                    total_sent += val_float
                else:
                    node.total_received += val_float
                    total_received += val_float

            # Track known entities
            for addr_key in (t["from_address"], t["to_address"]):
                lbl = labels.get(addr_key)
                if lbl and lbl.get("label_category"):
                    known_entities[addr_key] = lbl["label_name"] or lbl["label_category"]

        # Step 5: Build entity flags
        for addr_key, label_info in labels.items():
            cat = label_info.get("label_category", "")
            if cat in (
                "exchange",
                "mixer",
                "scam",
                "exploiter",
                "malicious",
                "ponzi",
                "sanctioned",
                "darknet_market",
                "phishing_scam",
                "ransomware",
            ):
                entity_flags.append(
                    f"Address {addr_key[:10]}...{addr_key[-6:]} "
                    f"is labeled as {cat}: {label_info.get('label_name', 'unknown')}"
                )

        # Step 6: Compute risk score
        risk_score = 0.0
        dangerous_labels = {
            "scam",
            "exploiter",
            "malicious",
            "ponzi",
            "phishing_scam",
            "ransomware",
            "mixer",
            "sanctioned",
            "darknet_market",
            "money_laundering",
        }
        dangerous_count = sum(1 for addr, lbl in labels.items() if lbl.get("label_category") in dangerous_labels)
        risk_score += min(dangerous_count * 15, 60)

        if bridge_hops:
            risk_score += 10

        if total_received > 1000:
            risk_score += 5
        if total_received > 100000:
            risk_score += 10

        risk_score = min(risk_score, 100.0)

        if risk_score < 25:
            risk_level = "low"
        elif risk_score < 50:
            risk_level = "moderate"
        elif risk_score < 75:
            risk_level = "high"
        else:
            risk_level = "critical"

        # Step 7: Serialize to report
        node_dicts = [
            {
                "address": n.address,
                "chain": n.chain,
                "label": n.label,
                "label_category": n.label_category,
                "role": n.role,
                "total_received": round(n.total_received, 4),
                "total_sent": round(n.total_sent, 4),
                "tx_count": n.tx_count,
                "is_sanctioned": n.is_sanctioned,
            }
            for n in nodes_map.values()
        ]

        edge_dicts = [
            {
                "from_address": e.from_address,
                "to_address": e.to_address,
                "chain": e.chain,
                "token_address": e.token_address,
                "token_symbol": e.token_symbol,
                "value": e.value,
                "tx_hash": e.tx_hash,
                "block_number": e.block_number,
                "block_timestamp": e.block_timestamp,
                "is_bridge": e.is_bridge,
                "bridge_destination_chain": e.bridge_destination_chain,
            }
            for e in edges_list
        ]

        return CrossChainTraceReport(
            seed_address=address,
            seed_chain=chain,
            nodes=node_dicts,
            edges=edge_dicts,
            bridge_hops=bridge_hops,
            entity_flags=entity_flags,
            known_entities=known_entities,
            total_volume_received=round(total_received, 4),
            total_volume_sent=round(total_sent, 4),
            chains_involved=sorted(chains_involved),
            unique_counterparties=len(nodes_map) - 1,
            risk_score=round(risk_score, 1),
            risk_level=risk_level,
        )
