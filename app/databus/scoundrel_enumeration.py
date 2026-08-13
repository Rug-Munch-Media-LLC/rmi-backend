"""Scoundrel-enumeration expansion engine (pure graph BFS).

Implements the "Expansion Loop" from the scoundrel-enumeration design:
BFS over the attribution (transfer) graph, wallet-first and bidirectional,
to surface *candidate* funding sources, deployers, and co-funded wallets
that may form a rug-pull syndicate.

The loop (steps 1-5):
1. From a seed wallet, collect **inbound funders** (fan-in, above the
   ``dust`` threshold) as funding-source candidates.
2. From each funder, follow **outbound** edges to **deployers** (wallets
   the funder paid / token deployers).
3. From deployers/funders, find **other wallets funded by the same
   source** = co-funded wallets (syndicate-member candidates).
4. Never re-expand a visited node; enforce ``max_depth`` and
   ``max_breadth`` caps so the frontier stays bounded.
5. The bidirectional variant (``wallet_first_expand``) starts from a scam
   wallet, links KOLs whose attributed wallets receive funding or share
   tokens, then expands.

The core is pure stdlib + dataclasses: no I/O, no network, no DB. Callers
feed a transfer graph and read back nodes/edges/events.

Everything here is **candidate generation** — ``emit_events`` marks
``detected=False``; syndicate *detection* is left to downstream
clustering.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# ── Data model ─────────────────────────────────────────────────────

#: Valid node kinds.
NODE_KINDS = ("kol", "wallet", "funder", "deployer")

#: Default caps — calibration flags live in the module docstring so
#: callers can pass explicit overrides; these are conservative defaults.
DEFAULT_MAX_DEPTH = 4
DEFAULT_MAX_BREADTH = 8
DEFAULT_DUST = 0.01


@dataclass(slots=True)
class EnumerationNode:
    """One node in the scoundrel-enumeration frontier.

    ``node_id`` is a normalized address (lower-cased by
    :func:`build_transfer_graph`). ``kind`` is one of
    ``"kol" | "wallet" | "funder" | "deployer"``. ``evidence`` holds
    human-readable provenance strings; ``tier`` defaults to ``"T2"``
    (mid-confidence) unless a caller assigns a stronger/weaker one.
    """

    node_id: str
    kind: str
    evidence: list[str] = field(default_factory=list)
    tier: str = "T2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "evidence": list(self.evidence),
            "tier": self.tier,
        }


# ── Graph construction ─────────────────────────────────────────────

#: Shape of one transfer edge as consumed by :func:`build_transfer_graph`.
#: ``amount`` and ``ts`` are floats; ``token`` is optional.
TransferEdge = dict[str, Any]

#: Shape of the adjacency index produced by :func:`build_transfer_graph`.
Graph = dict[str, Any]


def build_transfer_graph(transfers: list[TransferEdge]) -> Graph:
    """Build an adjacency index from ``[{from, to, amount, ts}, ...]``.

    Returns a graph dict::

        {
            "nodes": {addr, ...},          # every address seen
            "edges": [{from, to, amount, ts, token?}, ...],
            "out": {addr: [edge, ...]},    # outbound (fan-out)
            "in":  {addr: [edge, ...]},    # inbound  (fan-in)
            "tokens": {addr: {token, ...}} # token holdings per address
        }

    Addresses are normalized to lower-case. Malformed/partial edges are
    skipped (must have ``from`` and ``to``). ``amount`` defaults to 0.0
    and ``ts`` defaults to 0.0 when absent, so dust/freshness logic is
    total (no KeyError on sparse data).
    """
    graph: Graph = {
        "nodes": set(),
        "edges": [],
        "out": defaultdict(list),
        "in": defaultdict(list),
        "tokens": defaultdict(set),
    }
    for edge in transfers:
        src = str(edge.get("from") or "").strip().lower()
        dst = str(edge.get("to") or "").strip().lower()
        if not src or not dst:
            continue
        amount = _to_float(edge.get("amount"))
        ts = _to_float(edge.get("ts"))
        token = str(edge.get("token") or "").strip().lower() or None
        clean = {"from": src, "to": dst, "amount": amount, "ts": ts}
        if token:
            clean["token"] = token
        graph["edges"].append(clean)
        graph["nodes"].add(src)
        graph["nodes"].add(dst)

    for edge in graph["edges"]:
        graph["out"][edge["from"]].append(edge)
        graph["in"][edge["to"]].append(edge)
        if edge.get("token"):
            graph["tokens"][edge["from"]].add(edge["token"])
            graph["tokens"][edge["to"]].add(edge["token"])
    return graph


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── Fan-in / fan-out helpers ───────────────────────────────────────


def fan_in(graph: Graph, addr: str) -> list[TransferEdge]:
    """Inbound edges (funders -> ``addr``), newest last."""
    return list(graph.get("in", {}).get(addr.strip().lower(), []))


def fan_out(graph: Graph, addr: str) -> list[TransferEdge]:
    """Outbound edges (``addr`` -> recipients), newest last."""
    return list(graph.get("out", {}).get(addr.strip().lower(), []))


def _above_dust(edges: list[TransferEdge], dust: float) -> list[TransferEdge]:
    """Keep edges whose amount is strictly above ``dust``."""
    return [e for e in edges if e.get("amount", 0.0) > dust]


# ── The expansion loop (BFS) ───────────────────────────────────────


def expand(
    seed_wallet: str,
    graph: Graph,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_breadth: int = DEFAULT_MAX_BREADTH,
    dust: float = DEFAULT_DUST,
) -> dict[str, Any]:
    """BFS expansion from a seed wallet over the attribution graph.

    Steps (see module docstring): seed -> inbound funders -> outbound
    deployers -> co-funded wallets. Maintains a ``visited`` set, never
    re-expands a visited node, and enforces ``max_depth`` /
    ``max_breadth`` / ``dust`` caps.

    Returns ``{"nodes": [EnumerationNode, ...], "edges": [...],
    "frontier_drained": bool}``. ``frontier_drained`` is True when the
    BFS consumed the whole frontier without hitting a breadth cap
    (depth-cap termination is a normal bound, not a drain failure).
    """
    return _bfs([seed_wallet], graph, max_depth=max_depth, max_breadth=max_breadth, dust=dust)


def wallet_first_expand(
    scam_wallet: str,
    graph: Graph,
    kols: list[dict[str, Any]],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_breadth: int = DEFAULT_MAX_BREADTH,
    dust: float = DEFAULT_DUST,
) -> dict[str, Any]:
    """Bidirectional expansion from a scam wallet through KOL wallets.

    Accepts ``kols`` as ``[{"handle": str, "wallets": [addr, ...]}, ...]``.
    A KOL wallet is attributed when it either (a) appears in the graph
    (receives funding / is funded), or (b) shares a token with the scam
    wallet (``hold the same tokens``). Those attributed wallets seed the
    BFS together with the scam wallet, so the frontier expands in both
    directions. ``max_depth`` / ``max_breadth`` / ``dust`` override the
    same defaults as :func:`expand`.
    """
    scam_wallet = scam_wallet.strip().lower()
    seeds: list[str] = [scam_wallet]
    seed_meta: dict[str, dict[str, Any]] = {scam_wallet: {"kind": "wallet", "evidence": ["seed"]}}

    scam_tokens = set(graph.get("tokens", {}).get(scam_wallet, set()))
    for kol in kols or []:
        handle = str(kol.get("handle") or "").strip().lstrip("@")
        if not handle:
            continue
        for wallet in kol.get("wallets") or []:
            addr = str(wallet).strip().lower()
            if not addr:
                continue
            wallet_tokens = set(graph.get("tokens", {}).get(addr, set()))
            in_graph = addr in graph.get("nodes", set())
            shares_token = bool(scam_tokens & wallet_tokens)
            if not (in_graph or shares_token):
                continue
            if addr not in seed_meta:
                seeds.append(addr)
            evidence = [f"attributed_to_handle={handle}"]
            if shares_token:
                evidence.append("shares_token_with_seed")
            if addr in graph.get("in", {}):
                evidence.append("receives_funding")
            seed_meta[addr] = {"kind": "kol", "evidence": evidence, "handle": handle}

    return _bfs(
        seeds,
        graph,
        max_depth=max_depth,
        max_breadth=max_breadth,
        dust=dust,
        seed_meta=seed_meta,
    )


def _bfs(
    seeds: list[str],
    graph: Graph,
    *,
    max_depth: int,
    max_breadth: int,
    dust: float,
    seed_meta: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Shared BFS core used by :func:`expand` and :func:`wallet_first_expand`."""
    if max_depth < 0:
        max_depth = 0
    if max_breadth < 1:
        max_breadth = 1
    seed_meta = seed_meta or {}

    nodes: dict[str, EnumerationNode] = {}
    edges: list[dict[str, Any]] = []
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()
    truncated = False
    edge_keys: set[tuple[str, str, str]] = set()

    def _add_node(addr: str, kind: str, evidence: list[str], tier: str) -> None:
        if addr not in nodes:
            nodes[addr] = EnumerationNode(node_id=addr, kind=kind, evidence=list(evidence), tier=tier)

    def _add_edge(src: str, dst: str, kind: str, edge: TransferEdge) -> None:
        key = (src, dst, kind)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(
            {"from": src, "to": dst, "kind": kind, "amount": edge.get("amount", 0.0), "ts": edge.get("ts", 0.0)}
        )

    # Seed the frontier.
    for seed in seeds:
        seed = seed.strip().lower()
        if not seed:
            continue
        meta = seed_meta.get(seed, {})
        visited.add(seed)
        _add_node(seed, meta.get("kind", "wallet"), meta.get("evidence", ["seed"]), meta.get("tier", "T0"))
        queue.append((seed, 0))

    while queue:
        addr, depth = queue.popleft()
        if depth >= max_depth:
            continue  # depth cap: discovered, not expanded

        # Step 1: inbound funders (funding-source candidates).
        inbound = sorted(_above_dust(fan_in(graph, addr), dust), key=_edge_sort_key, reverse=True)
        if len(inbound) > max_breadth:
            truncated = True
        for edge in inbound[:max_breadth]:
            funder = edge["from"]
            if funder in visited:
                continue
            visited.add(funder)
            _add_node(funder, "funder", [f"funded {addr} {edge.get('amount', 0.0):g}"], "T1")
            _add_edge(funder, addr, "funding", edge)
            queue.append((funder, depth + 1))

        # Step 2: outbound deployers — only from funders.
        if nodes[addr].kind == "funder":
            outbound = sorted(_above_dust(fan_out(graph, addr), dust), key=_edge_sort_key, reverse=True)
            if len(outbound) > max_breadth:
                truncated = True
            for edge in outbound[:max_breadth]:
                deployer = edge["to"]
                if deployer in visited:
                    continue
                visited.add(deployer)
                _add_node(deployer, "deployer", [f"funded_by {addr} {edge.get('amount', 0.0):g}"], "T2")
                _add_edge(addr, deployer, "deploy", edge)
                queue.append((deployer, depth + 1))

        # Step 3: co-funded wallets — OTHER wallets funded by the same
        # source, discovered when expanding funders (source = itself) or
        # deployers (source = the deployer's inbound funders). Peers that
        # were already discovered (seed / deployers / funders) are skipped
        # via ``visited``, so no node is ever re-labeled or re-expanded.
        if nodes[addr].kind in ("funder", "deployer"):
            sources = [addr] if nodes[addr].kind == "funder" else [e["from"] for e in inbound]
            for source in sources[:max_breadth]:
                co_edges = sorted(_above_dust(fan_out(graph, source), dust), key=_edge_sort_key, reverse=True)
                if len(co_edges) > max_breadth:
                    truncated = True
                for edge in co_edges[:max_breadth]:
                    peer = edge["to"]
                    if peer == addr or peer in visited:
                        continue
                    visited.add(peer)
                    _add_node(peer, "wallet", [f"co_funded_by {source} {edge.get('amount', 0.0):g}"], "T3")
                    _add_edge(source, peer, "cofund", edge)
                    queue.append((peer, depth + 1))

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "frontier_drained": not truncated and not queue,
    }


def _edge_sort_key(edge: TransferEdge) -> tuple[float, float]:
    """Deterministic ordering: largest amount first, then newest."""
    return (edge.get("amount", 0.0), edge.get("ts", 0.0))


# ── Ranking ────────────────────────────────────────────────────────


def rank_funders(nodes: list[EnumerationNode], graph: Graph) -> list[dict[str, Any]]:
    """Rank funding sources by (total sent desc, distinct wallets desc, freshness).

    Only ``funder``-kind nodes are ranked. For each funder:

    - ``total_value`` — sum of outbound amounts
    - ``distinct_wallets`` — count of distinct outbound recipients
    - ``freshness`` — most recent ``ts`` among outbound edges

    Returns a list of dicts sorted by that key, most significant first.
    """
    funders = [n for n in nodes if n.kind == "funder"]
    rows: list[dict[str, Any]] = []
    for node in funders:
        out = fan_out(graph, node.node_id)
        total = sum(e.get("amount", 0.0) for e in out)
        distinct = len({e["to"] for e in out})
        last_ts = max((e.get("ts", 0.0) for e in out), default=0.0)
        rows.append(
            {
                "node_id": node.node_id,
                "kind": "funder",
                "tier": node.tier,
                "total_value": total,
                "distinct_wallets": distinct,
                "freshness": last_ts,
                "evidence": list(node.evidence),
            }
        )
    rows.sort(key=lambda r: (-r["total_value"], -r["distinct_wallets"], -r["freshness"]))
    return rows


# ── Event emission ─────────────────────────────────────────────────


def emit_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Map expansion nodes to candidate events.

    Event types:

    - ``kol.funding_source``  — a funder that paid an expanded wallet
    - ``kol.wallet_attributed`` — a KOL wallet or deployer surfaced by the
      expansion (carries ``handle`` when known)
    - ``kol.syndicate_member`` — a co-funded wallet (same payer as another
      node: syndicate-member candidate)

    All events are **candidates**: ``candidate=True`` and
    ``detected=False``. Syndicate *detection* (clustering) is deliberately
    left downstream.
    """
    events: list[dict[str, Any]] = []
    for node in result.get("nodes", []):
        base: dict[str, Any] = {
            "node_id": node.node_id,
            "kind": node.kind,
            "tier": node.tier,
            "evidence": list(node.evidence),
            "candidate": True,
            "detected": False,
        }
        if node.kind == "funder":
            base["type"] = "kol.funding_source"
            events.append(base)
        elif node.kind in ("deployer", "kol"):
            base["type"] = "kol.wallet_attributed"
            for ev in node.evidence:
                if ev.startswith("attributed_to_handle="):
                    base["handle"] = ev.split("=", 1)[1]
                    break
            events.append(base)
        elif node.kind == "wallet" and any(ev.startswith("co_funded_by ") for ev in node.evidence):
            base["type"] = "kol.syndicate_member"
            events.append(base)
        # Plain seed wallets produce no event.
    return events
