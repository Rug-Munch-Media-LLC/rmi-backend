"""Syndicate clustering engine — pure graph, no I/O.

Builds a KOL-coordination graph from three evidence sources and clusters its
connected components into syndicates (the "Solana Syndicate Pattern",
docs/SHILL-TRACKING-METHODOLOGY.md §5):

1. **co_shill_graph** — KOL x token bipartite calls projected onto KOL-KOL
   edges weighted by Jaccard (shared tokens / union) AND PMI (co-occurrence
   vs. chance), with weight decay by age of the co-occurrence window.
2. **shared_funding_graph** — KOLs whose attributed wallets share a common
   funder (``creator_funded`` / ``scam_funding_source`` fan-in).
3. **shared_deployer_graph** — KOLs whose wallets bought into tokens deployed
   by the same deployer wallet (``sybil_deployer`` fan-in).

Graphs are merged with :func:`union_graph`, decomposed with
:func:`connected_components`, and clustered by :func:`cluster_syndicates`,
which applies the **repeated-pair rule** (≥ ``min_pair_tokens`` distinct
co-shilled tokens) plus a weight threshold: above it a cluster is a
*detected* syndicate, below it a *candidate* in watch mode.

All functions are pure and deterministic (stdlib only: ``collections``,
``math``, ``itertools``, ``hashlib``). No I/O, no network, no logging deps.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from typing import Any

from app.core.logging import get_logger

logger = get_logger("syndicate_clustering")

# ═══════════════════════════════════════════════════════════════════════
# Small helpers (mirror shill_detector normalization; no logging deps)
# ═══════════════════════════════════════════════════════════════════════


def _to_ts(value: Any) -> float:
    """Parse a timestamp into epoch seconds; 0.0 when unparseable.

    Accepts int/float epoch (seconds; milliseconds when > 1e11) and ISO-8601
    strings (with optional trailing Z).
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        if value > 1e11:  # unix milliseconds
            return value / 1000.0
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() and len(s) >= 9:
            try:
                return float(s)
            except ValueError as e:
                logger.warning(f"_to_ts failed: {e}")
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError as e:
            logger.warning(f"_to_ts failed: {e}")
        try:
            return float(s)
        except ValueError as e:
            logger.warning(f"_to_ts failed: {e}")
    return 0.0


def _normalize_handle(value: Any) -> str:
    """Normalize a KOL identity: strip, drop leading '@', lowercase."""
    return str(value or "").strip().lstrip("@").lower()


def _normalize_address(value: Any) -> str:
    """Normalize an address for matching (EVM case-insensitive, base58 exact)."""
    s = str(value or "").strip()
    return s.lower() if s.startswith("0x") else s


def _normalize_token(value: Any) -> str:
    """Normalize a token id: contract addresses lowercased, else exact."""
    return _normalize_address(value)


def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pmi(shared: int, size_a: int, size_b: int, total_tokens: int) -> float:
    """Pointwise mutual information of co-occurrence, in bits.

    PMI(A, B) = log2( P(A∩B) / (P(A)·P(B)) )
              = log2( (c/T) / ( (|A|/T)·(|B|/T) ) )
              = log2( c·T / (|A|·|B|) )

    where ``c`` = shared tokens and ``T`` = distinct tokens across all KOLs.
    Negative values mean co-occurrence is *below* chance; they are reported
    raw on the edge but contribute 0 to the weight (see
    :func:`co_shill_graph`). Returns 0.0 when the ratio is not positive.
    """
    if shared <= 0 or size_a <= 0 or size_b <= 0 or total_tokens <= 0:
        return 0.0
    ratio = (shared * total_tokens) / (size_a * size_b)
    return float(math.log2(ratio))


def _age_decay(age_h: float, window_hours: float) -> float:
    """Exponential recency decay: 1.0 at age 0 → e^-1 at age = window_hours."""
    window = max(1e-9, float(window_hours))
    return math.exp(-max(0.0, age_h) / window)


def _syndicate_id(members: list[str]) -> str:
    """Stable syndicate id from the sorted member list (deterministic)."""
    key = "|".join(sorted(members))
    return "synd-" + hashlib.sha256(key.encode()).hexdigest()[:10]


# ═══════════════════════════════════════════════════════════════════════
# Graph builders
# ═══════════════════════════════════════════════════════════════════════


def co_shill_graph(calls: list[dict[str, Any]], *, window_hours: float = 48) -> dict[str, Any]:
    """Project KOL x token calls onto a weighted KOL-KOL co-shill graph.

    Args:
        calls: list of ``{kol, token, ts}`` dicts. The caller is expected to
            pre-filter to calls within 48h of token launch and within the
            30-day evaluation window (methodology §5.1) — this function only
            reasons over what it is given.
        window_hours: recency-decay time constant; an edge's weight decays as
            ``exp(-age_h / window_hours)`` where ``age_h`` is the time since
            the pair's most recent co-shilled-token call.

    Returns ``{"nodes": [...], "edges": [...]}``. Every edge carries the
    required ``a, b, jaccard, pmi, weight`` fields plus evidence fields
    (``token_count``, ``shared_tokens``, ``decay``, ``window_age_h``).
    ``weight = jaccard * (1 + max(pmi, 0)) * decay`` — Jaccard is the base
    similarity, positive PMI (co-occurrence above chance) amplifies it, and
    recency decays it. Edges only exist for pairs sharing ≥1 token.
    """
    window_hours = max(0.0, float(window_hours))
    kol_tokens: dict[str, set[str]] = defaultdict(set)
    token_last_ts: dict[tuple[str, str], float] = {}  # (kol, token) -> last call ts

    for call in calls or []:
        kol = _normalize_handle(call.get("kol") or call.get("kol_id") or call.get("handle"))
        token = _normalize_token(call.get("token") or call.get("token_address") or call.get("ca"))
        ts = _to_ts(call.get("ts") or call.get("timestamp") or call.get("called_at"))
        if not kol or not token or ts <= 0:
            continue
        kol_tokens[kol].add(token)
        key = (kol, token)
        if ts > token_last_ts.get(key, 0.0):
            token_last_ts[key] = ts

    now = max(token_last_ts.values(), default=0.0)
    total_tokens = len({t for tokens in kol_tokens.values() for t in tokens})

    nodes = sorted(kol_tokens)
    edges: list[dict[str, Any]] = []
    for a, b in combinations(nodes, 2):
        shared = kol_tokens[a] & kol_tokens[b]
        if not shared:
            continue
        jaccard = len(shared) / len(kol_tokens[a] | kol_tokens[b])
        pmi = _pmi(len(shared), len(kol_tokens[a]), len(kol_tokens[b]), total_tokens)

        # Pair recency = most recent call on any shared token by either KOL.
        recency = 0.0
        for token in shared:
            recency = max(recency, token_last_ts.get((a, token), 0.0), token_last_ts.get((b, token), 0.0))
        age_h = (now - recency) / 3600.0 if now else 0.0
        decay = _age_decay(age_h, window_hours)
        weight = jaccard * (1.0 + max(pmi, 0.0)) * decay

        edges.append(
            {
                "a": a,
                "b": b,
                "jaccard": round(jaccard, 6),
                "pmi": round(pmi, 6),
                "weight": round(weight, 6),
                "token_count": len(shared),
                "shared_tokens": sorted(shared),
                "decay": round(decay, 6),
                "window_age_h": round(age_h, 3),
            }
        )

    return {"nodes": nodes, "edges": edges}


def shared_funding_graph(kol_wallets: list[dict[str, Any]], funders: dict[str, Any]) -> dict[str, Any]:
    """Edge between two KOLs whose attributed wallets share a common funder.

    Args:
        kol_wallets: rows with ``kol`` (or ``kol_id``/``handle``) and
            ``wallet`` (or ``address``).
        funders: wallet address → list of funder entries. Each entry is a
            funder address string, or a dict with ``address``/``funder`` and
            optional ``amount_usd`` (from scoundrel enumeration).

    Edge weight = number of shared funders + shared funding USD / 1000
    (``count + usd/1000``). A pair with no common funder gets no edge.
    """
    wallet_kol: dict[str, str] = {}
    for row in kol_wallets or []:
        kol = _normalize_handle(row.get("kol") or row.get("kol_id") or row.get("handle"))
        wallet = _normalize_address(row.get("wallet") or row.get("address"))
        if kol and wallet:
            wallet_kol.setdefault(wallet, kol)

    # wallet -> {funder -> total usd}
    wallet_funder_usd: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for wallet, entries in (funders or {}).items():
        w = _normalize_address(wallet)
        if not w:
            continue
        for entry in entries or []:
            if isinstance(entry, dict):
                f = _normalize_address(entry.get("address") or entry.get("funder"))
                amount = _to_float(entry.get("amount_usd") or entry.get("amount"))
            else:
                f = _normalize_address(entry)
                amount = 0.0
            if f:
                wallet_funder_usd[w][f] += max(0.0, amount)

    # Aggregate per KOL so multi-wallet KOLs count each funder once.
    kol_funder_usd: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for wallet, funders_map in wallet_funder_usd.items():
        owner = wallet_kol.get(wallet)
        if not owner:
            continue
        for funder, amount in funders_map.items():
            kol_funder_usd[owner][funder] += amount

    edges: list[dict[str, Any]] = []
    for a, b in combinations(sorted(kol_funder_usd), 2):
        common = set(kol_funder_usd[a]) & set(kol_funder_usd[b])
        if not common:
            continue
        usd = sum(kol_funder_usd[a][f] + kol_funder_usd[b][f] for f in common)
        edges.append(
            {
                "a": a,
                "b": b,
                "weight": round(len(common) + usd / 1000.0, 6),
                "funder_count": len(common),
                "shared_usd": round(usd, 2),
                "shared_funders": sorted(common),
            }
        )

    nodes = sorted(wallet_kol.values())
    return {"nodes": nodes, "edges": edges}


def shared_deployer_graph(kol_wallets: list[dict[str, Any]], deployers: dict[str, Any]) -> dict[str, Any]:
    """Edge between KOLs whose wallets bought tokens from the same deployer.

    Args:
        kol_wallets: rows with ``kol`` (or ``kol_id``/``handle``), ``wallet``
            (or ``address``) and ``tokens`` (or ``tokens_bought``) — the
            token ids the wallet bought into.
        deployers: deployer wallet → list of token ids it deployed.

    Edge weight = number of distinct (deployer, token) co-buy incidences.
    """
    token_deployers: dict[str, set[str]] = defaultdict(set)
    for deployer, tokens in (deployers or {}).items():
        d = _normalize_address(deployer)
        if not d:
            continue
        for token in tokens or []:
            tn = _normalize_token(token)
            if tn:
                token_deployers[tn].add(d)

    # kol -> set of (deployer, token) incidences across all its wallets.
    kol_incidences: dict[str, set[tuple[str, str]]] = defaultdict(set)
    kol_nodes: set[str] = set()
    for row in kol_wallets or []:
        kol = _normalize_handle(row.get("kol") or row.get("kol_id") or row.get("handle"))
        if not kol:
            continue
        kol_nodes.add(kol)
        for token in row.get("tokens") or row.get("tokens_bought") or []:
            tn = _normalize_token(token)
            if tn and tn in token_deployers:
                for d in token_deployers[tn]:
                    kol_incidences[kol].add((d, tn))

    edges: list[dict[str, Any]] = []
    for a, b in combinations(sorted(kol_incidences), 2):
        shared = kol_incidences[a] & kol_incidences[b]
        if not shared:
            continue
        edges.append(
            {
                "a": a,
                "b": b,
                "weight": round(float(len(shared)), 6),
                "incidence_count": len(shared),
                "shared_deployers": sorted({d for d, _ in shared}),
                "shared_tokens": sorted({t for _, t in shared}),
            }
        )

    return {"nodes": sorted(kol_nodes), "edges": edges}


# ═══════════════════════════════════════════════════════════════════════
# Merge + decomposition
# ═══════════════════════════════════════════════════════════════════════


def union_graph(*graphs: dict[str, Any]) -> dict[str, Any]:
    """Merge graph dicts: union nodes, sum edge weights for shared pairs.

    Additional edge metadata is merged deterministically: list fields
    (``shared_tokens``, ``shared_funders``, ``shared_deployers``) are
    concatenated and deduped; scalar evidence (``token_count``, ``jaccard``,
    ``pmi``) keeps the maximum value seen across merged edges.
    """
    nodes: set[str] = set()
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    for graph in graphs:
        for node in graph.get("nodes", []) or []:
            nodes.add(str(node))
        for edge in graph.get("edges", []) or []:
            a, b = str(edge.get("a") or ""), str(edge.get("b") or "")
            if not a or not b:
                continue
            key = (min(a, b), max(a, b))
            cur = edges.get(key)
            if cur is None:
                cur = {"a": key[0], "b": key[1], "weight": 0.0}
                edges[key] = cur
            cur["weight"] += _to_float(edge.get("weight"))
            for lst_key in ("shared_tokens", "shared_funders", "shared_deployers"):
                values = edge.get(lst_key)
                if values:
                    cur.setdefault(lst_key, set()).update(str(v) for v in values)
            for sc_key in ("token_count", "jaccard", "pmi"):
                value = edge.get(sc_key)
                if value is not None:
                    cur[sc_key] = max(float(cur.get(sc_key) or 0.0), float(value))

    result_edges: list[dict[str, Any]] = []
    for key in sorted(edges):
        edge = dict(edges[key])
        for lst_key in ("shared_tokens", "shared_funders", "shared_deployers"):
            if lst_key in edge:
                edge[lst_key] = sorted(edge.pop(lst_key))
        edge["weight"] = round(edge["weight"], 6)
        result_edges.append(edge)

    return {"nodes": sorted(nodes), "edges": result_edges}


class _UnionFind:
    """Hand-rolled union-find (stdlib only — no external graph libs)."""

    def __init__(self, nodes: list[str]) -> None:
        self.parent: dict[str, str] = {n: n for n in nodes}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != x:  # path compression
            nxt = self.parent[x]
            self.parent[x] = root
            x = nxt
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def connected_components(graph: dict[str, Any]) -> list[list[str]]:
    """Connected components over graph nodes/edges (union-find).

    Returns a list of member lists, each sorted; components are sorted by
    size descending, then by first member (deterministic). Isolated nodes
    form components of size 1.
    """
    nodes = [str(n) for n in graph.get("nodes", []) or []]
    uf = _UnionFind(nodes)
    for edge in graph.get("edges", []) or []:
        a, b = str(edge.get("a") or ""), str(edge.get("b") or "")
        if a in uf.parent and b in uf.parent:
            uf.union(a, b)

    members: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        members[uf.find(node)].append(node)

    result = [sorted(m) for m in members.values()]
    result.sort(key=lambda c: (-len(c), c[0]))
    return result


# ═══════════════════════════════════════════════════════════════════════
# Hub scoring + syndicate clustering
# ═══════════════════════════════════════════════════════════════════════


def hub_score(graph: dict[str, Any]) -> dict[str, int]:
    """Degree centrality per node: number of distinct neighbors."""
    degree: dict[str, int] = defaultdict(int)
    for node in graph.get("nodes", []) or []:
        degree[str(node)] = 0
    for edge in graph.get("edges", []) or []:
        a, b = str(edge.get("a") or ""), str(edge.get("b") or "")
        if a in degree and b in degree:
            degree[a] += 1
            degree[b] += 1
    return dict(sorted(degree.items()))


def cluster_syndicates(
    graph: dict[str, Any],
    *,
    min_pair_tokens: int = 3,
    min_component_size: int = 3,
    detect_weight: float = 0.6,
) -> list[dict[str, Any]]:
    """Cluster graph components into syndicates (repeated-pair rule).

    For each connected component with ≥2 members:
    - ``weight`` = sum of internal edge weights.
    - ``token_rule`` = at least one internal edge shares ≥ ``min_pair_tokens``
      distinct tokens (the repeated-pair rule: ≥3 tokens within 30d).
    - ``status`` = "detected" iff ``weight ≥ detect_weight`` AND component
      size ≥ ``min_component_size`` AND ``token_rule``; otherwise
      "candidate" (watch mode, methodology §5.3).
    - ``hub`` = highest-degree node; ties broken by summed incident weight,
      then lexicographic (deterministic).
    - ``syndicate_id`` = stable hash of the sorted member list.

    Components of size 1 are not syndicates and are dropped. Result is
    sorted by weight descending, then first member.
    """
    edges = list(graph.get("edges", []) or [])
    syndicates: list[dict[str, Any]] = []

    for members in connected_components(graph):
        if len(members) < 2:
            continue
        member_set = set(members)
        internal = [e for e in edges if str(e.get("a")) in member_set and str(e.get("b")) in member_set]
        weight = sum(_to_float(e.get("weight")) for e in internal)
        token_rule = any(int(e.get("token_count") or 0) >= min_pair_tokens for e in internal)
        status = (
            "detected" if weight >= detect_weight and len(members) >= min_component_size and token_rule else "candidate"
        )
        syndicates.append(
            {
                "syndicate_id": _syndicate_id(members),
                "members": members,
                "hub": _pick_hub(members, internal),
                "weight": round(weight, 6),
                "status": status,
                "evidence": _evidence_of(internal),
            }
        )

    syndicates.sort(key=lambda s: (-s["weight"], s["members"][0]))
    return syndicates


def _pick_hub(members: list[str], internal: list[dict[str, Any]]) -> str | None:
    """Highest-degree node; ties broken by incident weight, then lexicographic."""
    if not members:
        return None
    degree: dict[str, int] = defaultdict(int)
    weight_sum: dict[str, float] = defaultdict(float)
    for edge in internal:
        a, b = str(edge.get("a") or ""), str(edge.get("b") or "")
        w = _to_float(edge.get("weight"))
        degree[a] += 1
        degree[b] += 1
        weight_sum[a] += w
        weight_sum[b] += w
    return sorted(members, key=lambda m: (-degree[m], -weight_sum[m], m))[0]


def _evidence_of(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Edge evidence sorted by weight desc, then endpoints (deterministic)."""
    evidence: list[dict[str, Any]] = []
    for edge in sorted(edges, key=lambda e: (-_to_float(e.get("weight")), str(e.get("a")), str(e.get("b")))):
        item: dict[str, Any] = {
            "a": str(edge.get("a")),
            "b": str(edge.get("b")),
            "weight": round(_to_float(edge.get("weight")), 6),
        }
        for key in (
            "token_count",
            "jaccard",
            "pmi",
            "decay",
            "window_age_h",
            "shared_tokens",
            "shared_funders",
            "shared_deployers",
        ):
            if key in edge:
                item[key] = edge[key]
        evidence.append(item)
    return evidence
