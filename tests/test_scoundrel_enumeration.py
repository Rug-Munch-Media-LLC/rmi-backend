"""Unit tests for the scoundrel-enumeration expansion engine.

Pure graph BFS over synthetic transfer graphs — no network, no DB, no I/O.
Covers:
- transfer-graph construction + fan-in / fan-out helpers
- BFS expansion: funder -> deployer -> co-funded wallet chain
- depth caps, breadth caps, visited-set dedup
- dust-threshold filtering
- funding-source ranking order
- bidirectional wallet-first expansion through KOL wallets
- event emission (candidate, not detected)
"""

from __future__ import annotations

from app.databus.scoundrel_enumeration import (
    EnumerationNode,
    build_transfer_graph,
    emit_events,
    expand,
    fan_in,
    fan_out,
    rank_funders,
    wallet_first_expand,
)


def _chain_graph() -> dict:
    """seed <- f1 (funding); f1 -> deployer; f2 -> deployer; f2 -> co_wallet.

    Chain under test: seed --funded-by--> f1 --pays--> deployer, and
    deployer's funder f2 also pays co_wallet => co-funded syndicate peer.
    """
    return build_transfer_graph(
        [
            {"from": "f1", "to": "seed", "amount": 5.0, "ts": 100},
            {"from": "f1", "to": "deployer", "amount": 3.0, "ts": 101},
            {"from": "f2", "to": "deployer", "amount": 1.5, "ts": 102},
            {"from": "f2", "to": "co_wallet", "amount": 2.0, "ts": 103},
        ]
    )


# ── Graph construction + fan helpers ───────────────────────────────


def test_build_transfer_graph_normalizes_and_indexes() -> None:
    graph = build_transfer_graph(
        [
            {"from": "0xAAA", "to": "0xbbb", "amount": 1.0, "ts": 10},
            {"from": "0xBBB", "to": "0xccc", "amount": 2.0, "ts": 11},
            {"from": "0xAAA", "to": "0xBBB", "amount": 0.5, "ts": 12},
            {"from": "0xddd", "to": "0xeee", "amount": 3.0},  # no ts -> 0.0
            {"from": "", "to": "0xzzz", "amount": 9.0},  # malformed -> skipped
        ]
    )
    assert "0xaaa" in graph["nodes"]
    assert "0xbbb" in graph["nodes"]
    assert "0xzzz" not in graph["nodes"]
    assert len(graph["edges"]) == 4

    inbound = fan_in(graph, "0xBBB")
    assert {e["from"] for e in inbound} == {"0xaaa"}
    assert {e["amount"] for e in inbound} == {1.0, 0.5}

    outbound = fan_out(graph, "0xaaa")
    assert {e["to"] for e in outbound} == {"0xbbb"}
    assert len(outbound) == 2


def test_fan_helpers_case_insensitive_and_missing_safe() -> None:
    graph = _chain_graph()
    assert fan_in(graph, "SEED") == [{"from": "f1", "to": "seed", "amount": 5.0, "ts": 100}]
    assert fan_out(graph, "F1") == [
        {"from": "f1", "to": "seed", "amount": 5.0, "ts": 100},
        {"from": "f1", "to": "deployer", "amount": 3.0, "ts": 101},
    ]
    assert fan_in(graph, "0xmissing") == []
    assert fan_out(graph, "0xmissing") == []


# ── Expansion: the chain ───────────────────────────────────────────


def test_expand_finds_funder_deployer_cofunded_chain() -> None:
    result = expand("seed", _chain_graph())
    by_kind: dict[str, set[str]] = {}
    for node in result["nodes"]:
        by_kind.setdefault(node.kind, set()).add(node.node_id)

    # Seed + funders + deployer + co-funded wallet all surfaced.
    assert "seed" in by_kind["wallet"]
    assert {"f1", "f2"} == by_kind["funder"]
    assert "deployer" in by_kind["deployer"]
    assert "co_wallet" in by_kind["wallet"]

    # Edge kinds carry the semantics of each hop.
    edges = {(e["from"], e["to"], e["kind"]) for e in result["edges"]}
    assert ("f1", "seed", "funding") in edges
    assert ("f1", "deployer", "deploy") in edges
    assert ("f2", "co_wallet", "cofund") in edges
    assert result["frontier_drained"] is True


def test_expand_seed_not_in_graph_returns_seed_only() -> None:
    result = expand("ghost", _chain_graph())
    assert [n.node_id for n in result["nodes"]] == ["ghost"]
    assert [n.kind for n in result["nodes"]] == ["wallet"]
    assert result["edges"] == []
    assert result["frontier_drained"] is True


# ── Caps + visited dedup ───────────────────────────────────────────


def test_depth_cap_limits_hops() -> None:
    graph = build_transfer_graph(
        [
            {"from": "f1", "to": "seed", "amount": 5.0, "ts": 1},
            {"from": "f1", "to": "deployer", "amount": 3.0, "ts": 2},
            {"from": "f2", "to": "deployer", "amount": 1.5, "ts": 3},
            {"from": "f2", "to": "co_wallet", "amount": 2.0, "ts": 4},
        ]
    )
    result = expand("seed", graph, max_depth=2)
    ids = {n.node_id for n in result["nodes"]}
    # depth 0 seed, depth 1 funders; deployer at depth 2 allowed; the
    # co-funded wallet sits at depth 3 -> capped out.
    assert "seed" in ids
    assert "f1" in ids
    assert "deployer" in ids
    assert "co_wallet" not in ids


def test_breadth_cap_limits_funders_and_flags_frontier() -> None:
    graph = build_transfer_graph([{"from": f"f{i}", "to": "seed", "amount": float(i + 1), "ts": i} for i in range(5)])
    result = expand("seed", graph, max_breadth=2)
    funders = {n.node_id for n in result["nodes"] if n.kind == "funder"}
    assert funders == {"f4", "f3"}  # largest amounts first, cap at 2
    assert result["frontier_drained"] is False


def test_visited_dedup_never_reexpands() -> None:
    # Two deployers both funded by the same f1 -> f1 appears once.
    graph = build_transfer_graph(
        [
            {"from": "f1", "to": "seed", "amount": 5.0, "ts": 1},
            {"from": "f1", "to": "deployer_a", "amount": 3.0, "ts": 2},
            {"from": "f1", "to": "deployer_b", "amount": 2.0, "ts": 3},
        ]
    )
    result = expand("seed", graph)
    funders = [n for n in result["nodes"] if n.kind == "funder"]
    assert len(funders) == 1
    assert funders[0].node_id == "f1"
    # Co-funded peers discovered via deployer expansion do not duplicate
    # the seed or the funder.
    ids = {n.node_id for n in result["nodes"]}
    assert len(ids) == len(result["nodes"])


# ── Dust threshold ─────────────────────────────────────────────────


def test_dust_filters_small_transfers() -> None:
    graph = build_transfer_graph(
        [
            {"from": "big_funder", "to": "seed", "amount": 5.0, "ts": 1},
            {"from": "dust_funder", "to": "seed", "amount": 0.001, "ts": 2},
            {"from": "edge_funder", "to": "seed", "amount": 0.01, "ts": 3},  # == dust
        ]
    )
    result = expand("seed", graph, dust=0.01)
    funders = {n.node_id for n in result["nodes"] if n.kind == "funder"}
    assert funders == {"big_funder"}
    assert "dust_funder" not in funders
    assert "edge_funder" not in funders  # strictly above dust


# ── Ranking ────────────────────────────────────────────────────────


def test_rank_funders_orders_by_total_distinct_freshness() -> None:
    graph = build_transfer_graph(
        [
            # big: total 9, distinct 2, oldest
            {"from": "big", "to": "w1", "amount": 6.0, "ts": 10},
            {"from": "big", "to": "w2", "amount": 3.0, "ts": 11},
            # fresh: total 9, distinct 2, newest -> wins tiebreak
            {"from": "fresh", "to": "w3", "amount": 5.0, "ts": 99},
            {"from": "fresh", "to": "w4", "amount": 4.0, "ts": 100},
            # narrow: total 9, distinct 1
            {"from": "narrow", "to": "w5", "amount": 9.0, "ts": 50},
        ]
    )
    nodes = [
        EnumerationNode(node_id="big", kind="funder"),
        EnumerationNode(node_id="fresh", kind="funder"),
        EnumerationNode(node_id="narrow", kind="funder"),
        EnumerationNode(node_id="w1", kind="wallet"),  # non-funder ignored
    ]
    ranked = rank_funders(nodes, graph)
    assert [r["node_id"] for r in ranked] == ["fresh", "big", "narrow"]
    assert ranked[0]["total_value"] == 9.0
    assert ranked[0]["distinct_wallets"] == 2
    assert ranked[0]["freshness"] == 100


# ── Bidirectional wallet-first expansion ───────────────────────────


def test_wallet_first_expand_finds_kol_via_funding_and_token() -> None:
    graph = build_transfer_graph(
        [
            {"from": "funder", "to": "scam", "amount": 5.0, "ts": 1},
            {"from": "funder", "to": "kol_wallet", "amount": 2.0, "ts": 2},
            {"from": "funder", "to": "deployer", "amount": 3.0, "ts": 3},
            # token held by scam + another kol wallet
            {"from": "scam", "to": "x", "amount": 1.0, "ts": 4, "token": "TOK"},
            {"from": "kol_token_wallet", "to": "y", "amount": 1.0, "ts": 5, "token": "TOK"},
        ]
    )
    kols = [
        {"handle": "alpha", "wallets": ["kol_wallet"]},
        {"handle": "beta", "wallets": ["kol_token_wallet"]},
        {"handle": "gamma", "wallets": ["unrelated"]},  # not in graph -> skipped
    ]
    result = wallet_first_expand("scam", graph, kols)
    kol_nodes = {n.node_id: n for n in result["nodes"] if n.kind == "kol"}
    assert "kol_wallet" in kol_nodes
    assert any(ev.startswith("attributed_to_handle=alpha") for ev in kol_nodes["kol_wallet"].evidence)
    assert "kol_token_wallet" in kol_nodes
    assert any(ev.startswith("attributed_to_handle=beta") for ev in kol_nodes["kol_token_wallet"].evidence)
    assert "unrelated" not in result["nodes"]

    events = emit_events(result)
    attributed = [e for e in events if e["type"] == "kol.wallet_attributed" and e.get("handle")]
    handles = {e.get("handle") for e in attributed}
    assert handles == {"alpha", "beta"}


def test_wallet_first_expand_accepts_no_kols() -> None:
    result = wallet_first_expand("scam", _chain_graph(), kols=[])
    assert result["nodes"]  # still expands from the scam wallet alone


# ── Event emission ─────────────────────────────────────────────────


def test_emit_events_maps_node_kinds_to_candidate_events() -> None:
    result = expand("seed", _chain_graph())
    events = emit_events(result)
    by_type: dict[str, list[dict]] = {}
    for ev in events:
        by_type.setdefault(ev["type"], []).append(ev)

    funder_events = by_type["kol.funding_source"]
    assert {ev["node_id"] for ev in funder_events} == {"f1", "f2"}
    assert all(ev["candidate"] is True for ev in funder_events)
    assert all(ev["detected"] is False for ev in funder_events)

    attributed = by_type["kol.wallet_attributed"]
    assert {ev["node_id"] for ev in attributed} == {"deployer"}

    syndicate = by_type["kol.syndicate_member"]
    assert {ev["node_id"] for ev in syndicate} == {"co_wallet"}


def test_emit_events_skips_seed_wallet() -> None:
    result = expand("seed", _chain_graph())
    events = emit_events(result)
    assert all(ev["node_id"] != "seed" for ev in events)


# ── Node dataclass surface ─────────────────────────────────────────


def test_enumeration_node_defaults_and_to_dict() -> None:
    node = EnumerationNode(node_id="0xabc", kind="funder")
    assert node.tier == "T2"
    assert node.evidence == []
    d = node.to_dict()
    assert d == {"node_id": "0xabc", "kind": "funder", "evidence": [], "tier": "T2"}
