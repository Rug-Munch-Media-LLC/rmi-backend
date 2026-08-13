"""Pure unit tests for the syndicate clustering engine.

Covers the three graph builders, graph merge/decomposition, hub scoring and
the repeated-pair / weight-threshold clustering rules (candidate vs.
detected) from docs/SHILL-TRACKING-METHODOLOGY.md §5.
"""

from __future__ import annotations

import math

import pytest

from app.databus import syndicate_clustering as sc


def _call(kol: str, token: str, ts: float = 1_700_000_000) -> dict:
    return {"kol": kol, "token": token, "ts": ts}


def test_co_shill_graph_jaccard_pmi_weight() -> None:
    """2 KOLs sharing 3 of 5 tokens → jaccard 0.6, pmi < 0, weight 0.6."""
    calls = [
        _call("a", "t1"),
        _call("a", "t2"),
        _call("a", "t3"),
        _call("a", "u1"),
        _call("b", "t1"),
        _call("b", "t2"),
        _call("b", "t3"),
        _call("b", "u2"),
    ]
    graph = sc.co_shill_graph(calls)
    assert graph["nodes"] == ["a", "b"]
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["a"] == "a"
    assert edge["b"] == "b"
    assert edge["jaccard"] == pytest.approx(3 / 5)
    # PMI = log2(c*T / (|A|*|B|)) = log2(3*5 / (4*4)) — below chance → negative.
    assert edge["pmi"] == pytest.approx(round(math.log2(15 / 16), 6))
    assert edge["token_count"] == 3
    assert edge["decay"] == pytest.approx(1.0)
    # weight = jaccard * (1 + max(pmi, 0)) * decay — negative PMI contributes 0.
    assert edge["weight"] == pytest.approx(0.6)


def test_co_shill_graph_positive_pmi_amplifies_weight() -> None:
    """Exact-same token with a large corpus → PMI > 0 boosts weight above 1."""
    calls = [
        _call("a", "t1"),
        _call("b", "t1"),
        _call("c", "t2"),
        _call("d", "t3"),
        _call("e", "t4"),
    ]
    graph = sc.co_shill_graph(calls)
    assert graph["nodes"] == ["a", "b", "c", "d", "e"]
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["jaccard"] == pytest.approx(1.0)
    # PMI = log2(1*4 / (1*1)) = 2 bits.
    assert edge["pmi"] == pytest.approx(2.0)
    assert edge["weight"] == pytest.approx(3.0)


def test_co_shill_graph_window_decay() -> None:
    """Older shared-token calls decay the edge weight below raw jaccard."""
    calls = [
        {"kol": "a", "token": "t1", "ts": 1000.0},
        {"kol": "a", "token": "u1", "ts": 5000.0},
        {"kol": "b", "token": "t1", "ts": 2000.0},
        {"kol": "b", "token": "u2", "ts": 5000.0},
    ]
    graph = sc.co_shill_graph(calls)
    edge = graph["edges"][0]
    age_h = 3000.0 / 3600.0  # most recent shared-token call is 50min before now
    assert edge["window_age_h"] == pytest.approx(round(age_h, 3))
    assert edge["decay"] == pytest.approx(round(math.exp(-age_h / 48.0), 6))
    # weight < raw jaccard (1/3) because of decay; pmi negative → no boost.
    assert edge["weight"] == pytest.approx(round((1 / 3) * math.exp(-age_h / 48.0), 6))


def test_co_shill_graph_no_shared_tokens() -> None:
    graph = sc.co_shill_graph([_call("a", "t1"), _call("b", "t2")])
    assert graph["nodes"] == ["a", "b"]
    assert graph["edges"] == []


def test_shared_funding_graph_forms_edge() -> None:
    kol_wallets = [
        {"kol": "kolA", "wallet": "wA"},
        {"kol": "kolB", "wallet": "wB"},
        {"kol": "kolC", "wallet": "wC"},
    ]
    funders = {
        "wA": [{"address": "f1", "amount_usd": 500}, {"address": "f2", "amount_usd": 200}],
        "wB": [{"address": "f1", "amount_usd": 300}],
        "wC": [{"address": "f9", "amount_usd": 10}],
    }
    graph = sc.shared_funding_graph(kol_wallets, funders)
    assert graph["nodes"] == ["kola", "kolb", "kolc"]
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["a"] == "kola"
    assert edge["b"] == "kolb"
    assert edge["shared_funders"] == ["f1"]
    assert edge["funder_count"] == 1
    assert edge["shared_usd"] == pytest.approx(800.0)
    # weight = count + usd/1000 = 1 + 0.8
    assert edge["weight"] == pytest.approx(1.8)


def test_shared_funding_graph_no_common_funder() -> None:
    kol_wallets = [{"kol": "kolA", "wallet": "wA"}, {"kol": "kolB", "wallet": "wB"}]
    funders = {"wA": ["f1"], "wB": ["f2"]}
    graph = sc.shared_funding_graph(kol_wallets, funders)
    assert graph["edges"] == []


def test_shared_deployer_graph_forms_edges() -> None:
    deployers = {"D1": ["t1", "t2"], "D2": ["t3"]}
    kol_wallets = [
        {"kol": "kolA", "wallet": "wA", "tokens": ["t1"]},
        {"kol": "kolB", "wallet": "wB", "tokens": ["t1", "t3"]},
        {"kol": "kolC", "wallet": "wC", "tokens": ["t3"]},
    ]
    graph = sc.shared_deployer_graph(kol_wallets, deployers)
    assert graph["nodes"] == ["kola", "kolb", "kolc"]
    assert len(graph["edges"]) == 2
    by_pair = {(e["a"], e["b"]): e for e in graph["edges"]}
    assert by_pair[("kola", "kolb")]["weight"] == pytest.approx(1.0)
    assert by_pair[("kola", "kolb")]["shared_deployers"] == ["D1"]
    assert by_pair[("kolb", "kolc")]["weight"] == pytest.approx(1.0)
    assert by_pair[("kolb", "kolc")]["shared_deployers"] == ["D2"]


def test_union_graph_sums_weights_and_merges_metadata() -> None:
    co_shill = sc.co_shill_graph(
        [
            _call("a", "t1"),
            _call("a", "t2"),
            _call("a", "t3"),
            _call("b", "t1"),
            _call("b", "t2"),
            _call("b", "t3"),
        ]
    )
    funding = sc.shared_funding_graph(
        [{"kol": "kolA", "wallet": "wA"}, {"kol": "kolB", "wallet": "wB"}],
        {"wA": ["f1"], "wB": ["f1"]},
    )
    # Map the funding graph's kols onto the co-shill graph's nodes.
    funding["edges"][0]["a"], funding["edges"][0]["b"] = "a", "b"
    funding["nodes"] = ["a", "b"]

    merged = sc.union_graph(co_shill, funding)
    assert merged["nodes"] == ["a", "b"]
    assert len(merged["edges"]) == 1
    edge = merged["edges"][0]
    assert edge["weight"] == pytest.approx(1.0 + 1.0)  # co-shill 1.0 + funding 1.0
    assert edge["token_count"] == 3  # max keeps co-shill evidence
    assert edge["jaccard"] == pytest.approx(1.0)
    assert edge["shared_funders"] == ["f1"]


def test_union_graph_empty() -> None:
    assert sc.union_graph() == {"nodes": [], "edges": []}
    assert sc.union_graph({"nodes": ["a"], "edges": []}, {}) == {"nodes": ["a"], "edges": []}


def test_connected_components() -> None:
    graph = {
        "nodes": ["a", "b", "c", "d", "e"],
        "edges": [{"a": "a", "b": "b", "weight": 1}, {"a": "b", "b": "c", "weight": 1}],
    }
    assert sc.connected_components(graph) == [["a", "b", "c"], ["d"], ["e"]]


def test_hub_score_star() -> None:
    graph = {
        "nodes": ["a", "b", "c", "d"],
        "edges": [{"a": "a", "b": "b"}, {"a": "a", "b": "c"}, {"a": "a", "b": "d"}],
    }
    assert sc.hub_score(graph) == {"a": 3, "b": 1, "c": 1, "d": 1}


def test_cluster_syndicates_detected_triangle() -> None:
    """3 KOLs shilling the same 3 tokens → detected syndicate."""
    calls = [
        _call("a", "t1"),
        _call("a", "t2"),
        _call("a", "t3"),
        _call("b", "t1"),
        _call("b", "t2"),
        _call("b", "t3"),
        _call("c", "t1"),
        _call("c", "t2"),
        _call("c", "t3"),
    ]
    graph = sc.co_shill_graph(calls)
    syndicates = sc.cluster_syndicates(graph)
    assert len(syndicates) == 1
    synd = syndicates[0]
    assert synd["members"] == ["a", "b", "c"]
    assert synd["status"] == "detected"
    assert synd["weight"] == pytest.approx(3.0)  # 3 edges x 1.0
    assert synd["hub"] == "a"  # all degree 2 → lexicographic tie-break
    assert len(synd["evidence"]) == 3
    assert all(e["token_count"] == 3 for e in synd["evidence"])
    assert synd["syndicate_id"].startswith("synd-")


def test_cluster_syndicates_candidate_pair() -> None:
    """2 KOLs sharing 3+ tokens within 30d → candidate (component < 3)."""
    calls = [
        _call("a", "t1"),
        _call("a", "t2"),
        _call("a", "t3"),
        _call("b", "t1"),
        _call("b", "t2"),
        _call("b", "t3"),
    ]
    graph = sc.co_shill_graph(calls)
    syndicates = sc.cluster_syndicates(graph)
    assert len(syndicates) == 1
    synd = syndicates[0]
    assert synd["members"] == ["a", "b"]
    assert synd["status"] == "candidate"
    assert synd["hub"] == "a"


def test_cluster_syndicates_detect_weight_threshold() -> None:
    calls = [
        _call("a", "t1"),
        _call("a", "t2"),
        _call("a", "t3"),
        _call("b", "t1"),
        _call("b", "t2"),
        _call("b", "t3"),
        _call("c", "t1"),
        _call("c", "t2"),
        _call("c", "t3"),
    ]
    graph = sc.co_shill_graph(calls)
    # weight sums to 3.0; raising the bar above it demotes to candidate.
    syndicates = sc.cluster_syndicates(graph, detect_weight=4.0)
    assert syndicates[0]["status"] == "candidate"
    # ...and keeping it below it stays detected.
    assert sc.cluster_syndicates(graph, detect_weight=2.5)[0]["status"] == "detected"


def test_cluster_syndicates_min_pair_tokens_threshold() -> None:
    calls = [
        _call("a", "t1"),
        _call("a", "t2"),
        _call("a", "t3"),
        _call("b", "t1"),
        _call("b", "t2"),
        _call("b", "t3"),
        _call("c", "t1"),
        _call("c", "t2"),
        _call("c", "t3"),
    ]
    graph = sc.co_shill_graph(calls)
    # Pairs share 3 tokens; demanding 4 fails the repeated-pair rule.
    syndicates = sc.cluster_syndicates(graph, min_pair_tokens=4)
    assert syndicates[0]["status"] == "candidate"


def test_cluster_syndicates_funding_only_candidate() -> None:
    """Weight + size OK but no token evidence → candidate (no token rule)."""
    kol_wallets = [
        {"kol": "kolA", "wallet": "wA"},
        {"kol": "kolB", "wallet": "wB"},
        {"kol": "kolC", "wallet": "wC"},
    ]
    funders = {"wA": ["f1", "f3"], "wB": ["f1", "f2"], "wC": ["f2", "f3"]}
    graph = sc.shared_funding_graph(kol_wallets, funders)
    syndicates = sc.cluster_syndicates(graph)
    assert len(syndicates) == 1
    synd = syndicates[0]
    assert synd["members"] == ["kola", "kolb", "kolc"]
    assert synd["weight"] == pytest.approx(3.0)
    assert synd["status"] == "candidate"


def test_cluster_syndicates_hub_and_id_determinism() -> None:
    graph = {
        "nodes": ["a", "b", "c", "d"],
        "edges": [
            {"a": "a", "b": "b", "weight": 1.0, "token_count": 3},
            {"a": "a", "b": "c", "weight": 1.0, "token_count": 3},
            {"a": "a", "b": "d", "weight": 1.0, "token_count": 3},
        ],
    }
    first = sc.cluster_syndicates(graph)
    second = sc.cluster_syndicates(graph)
    assert first[0]["hub"] == "a"  # degree 3 beats degree 1
    assert first[0]["syndicate_id"] == second[0]["syndicate_id"]
    assert len(first[0]["syndicate_id"]) == 5 + 10  # "synd-" + 10 hex chars
    assert first[0]["members"] == ["a", "b", "c", "d"]


def test_cluster_syndicates_single_node_dropped() -> None:
    graph = {"nodes": ["loner"], "edges": []}
    assert sc.cluster_syndicates(graph) == []
