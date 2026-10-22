"""Cohort rollup and the premium bundle graph, on fixtures.

Both modules answer "who is this, and how much do they hold". The tests pin
the two properties that make the answer usable: a cohort we could not see is
``known=False`` with zeros (not silently equal to an empty cohort), and supply
share is ``None`` when there is no denominator rather than ``0%``.
"""

from __future__ import annotations

from app.domains.premium.bundle_graph import build_bundle_graph
from app.domains.premium.cohorts import COHORTS, cohort_rollup

HOLDERS = [
    {"wallet": "TeamWallet", "pct": 18.0, "realized_sol": 0.0},
    {"wallet": "InsiderOne", "pct": 6.0, "realized_sol": 12.5, "bundled": True},
    {"wallet": "InsiderTwo", "pct": 4.0, "realized_sol": 3.0, "bundled": True},
    {"wallet": "SniperOne", "pct": 2.5, "realized_sol": 41.0},
    {"wallet": "Retail", "pct": 1.0},
]

FIRST_BUYERS = [
    {"wallet": "InsiderOne", "rank": 1, "pct": 7.0},
    {"wallet": "InsiderTwo", "rank": 2, "pct": 5.0},
    {"wallet": "SniperOne", "rank": 4, "pct": 3.0},
    {"wallet": "Retail", "rank": 60, "pct": 1.0},
]

LABELS = {
    "TeamWallet": {"is_team": True},
    "InsiderOne": {"is_insider": True},
    "InsiderTwo": {"cohort": "deployer"},
    "KOLWallet": {"handle": "degen_anna", "pnl_sol": 88.5},
}


def test_three_cohorts_are_always_reported_in_order() -> None:
    rollup = cohort_rollup(HOLDERS, FIRST_BUYERS, LABELS)

    assert tuple(rollup["cohorts"]) == COHORTS
    assert COHORTS == ("team", "insiders", "snipers")


def test_explicit_labels_beat_behavioural_inference() -> None:
    cohorts = cohort_rollup(HOLDERS, FIRST_BUYERS, LABELS)["cohorts"]

    assert cohorts["team"]["count"] == 1
    assert cohorts["insiders"]["count"] == 2
    # SniperOne is rank 4 and unlabelled, so rank is the only evidence.
    assert cohorts["snipers"]["count"] == 1


def test_cohort_rows_carry_the_five_comparable_numbers() -> None:
    insiders = cohort_rollup(HOLDERS, FIRST_BUYERS, LABELS)["cohorts"]["insiders"]

    assert insiders == {
        "count": 2,
        "bundled_n": 2,
        "initial_buy_pct": 12.0,
        "current_pct": 10.0,
        "realized_sol": 15.5,
        "known": True,
    }


def test_unlabelled_non_early_wallet_is_not_forced_into_a_cohort() -> None:
    cohorts = cohort_rollup(HOLDERS, FIRST_BUYERS, LABELS)["cohorts"]

    total = sum(row["count"] for row in cohorts.values())
    assert total == 4  # Retail is deliberately excluded


def test_empty_cohort_is_zeroes_and_known_false() -> None:
    cohorts = cohort_rollup([{"wallet": "Retail", "pct": 1.0}], [], {})["cohorts"]

    assert cohorts["team"] == {
        "count": 0,
        "bundled_n": 0,
        "initial_buy_pct": 0.0,
        "current_pct": 0.0,
        "realized_sol": 0.0,
        "known": False,
    }


def test_kol_rows_come_from_identity_labels_with_an_x_link() -> None:
    kol = cohort_rollup(HOLDERS, FIRST_BUYERS, LABELS)["kol"]

    assert kol == [
        {
            "handle": "degen_anna",
            "x_url": "https://x.com/degen_anna",
            "pnl_sol": 88.5,
        }
    ]


def test_graph_without_edges_has_zero_confidence_and_singleton_clusters() -> None:
    graph = build_bundle_graph("MintA", "solana", ["W1", "W2"], [], total_supply=1_000.0)

    assert graph["wallet_count"] == 2
    assert [cluster["size"] for cluster in graph["clusters"]] == [1, 1]
    assert graph["confidence"] == 0.0
    assert graph["bundle_pct_of_supply"] == 0.0


def test_a_three_hop_chain_is_one_cluster_not_three_pairs() -> None:
    edges = [
        {"from": "W1", "to": "W2", "kind": "funding", "evidence_tx": "tx1"},
        {"from": "W2", "to": "W3", "kind": "funding", "evidence_tx": "tx2"},
    ]

    graph = build_bundle_graph("MintA", "solana", [], edges, total_supply=1_000.0)

    assert graph["wallet_count"] == 3
    assert len(graph["clusters"]) == 1
    assert graph["clusters"][0]["wallets"] == ["W1", "W2", "W3"]


def test_supply_pct_is_none_without_a_denominator_but_real_with_one() -> None:
    edges = [{"from": w, "to": "W4", "kind": "co_buy", "evidence_tx": "tx"} for w in ("W1", "W2", "W3")]
    holdings = {w: {"balance": 10.0} for w in ("W1", "W2", "W3")}

    unknown = build_bundle_graph("MintA", "solana", [], edges, holdings=holdings)
    known = build_bundle_graph("MintA", "solana", [], edges, holdings=holdings, total_supply=100.0)

    assert unknown["clusters"][0]["supply_pct"] is None
    assert unknown["bundle_pct_of_supply"] == 0.0
    assert known["clusters"][0]["supply_pct"] == 30.0
    assert known["bundle_pct_of_supply"] == 30.0


def test_confidence_rewards_evidence_diversity_and_cluster_size() -> None:
    # 1.0 evidenced + two kinds (funding, co_buy) + cluster of 3 == 1.0
    strong = build_bundle_graph(
        "MintA",
        "solana",
        [],
        [
            {"from": "W1", "to": "W2", "kind": "funding", "evidence_tx": "tx1"},
            {"from": "W2", "to": "W3", "kind": "co_buy", "evidence_tx": "tx2"},
        ],
    )
    # Same shape, no evidence_tx and a single kind: 0.3 + 0.0 + 0.3
    weak = build_bundle_graph(
        "MintA",
        "solana",
        [],
        [
            {"from": "W1", "to": "W2", "kind": "funding", "evidence_tx": None},
            {"from": "W2", "to": "W3", "kind": "funding", "evidence_tx": None},
        ],
    )

    assert strong["confidence"] == 1.0
    assert weak["confidence"] == 0.45


def test_unproven_edges_are_labelled_unknown_and_self_links_are_dropped() -> None:
    graph = build_bundle_graph(
        "MintA",
        "solana",
        [],
        [
            {"from": "W1", "to": "W1", "kind": "funding", "evidence_tx": "self"},
            {"from": "W1", "to": "W2", "kind": "telepathy", "evidence_tx": "tx1"},
            {"from": "", "to": "W3", "kind": "funding", "evidence_tx": "tx2"},
        ],
    )

    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["kind"] == "unknown"
    assert graph["wallet_count"] == 2
