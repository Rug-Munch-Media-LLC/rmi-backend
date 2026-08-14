"""Pure bundle/sniper signal computations (no I/O).

Extracted from ``bundle_detector.py`` during the de-godding round 5 pass:
behavior clustering, compute-budget analysis and risk scoring. All functions
here are deterministic pure helpers; the I/O-facing ``BundleDetector`` class
delegates to them. ``bundle_detector`` re-exports the public names so every
importer and test keeps working against the same objects.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.domains.scanners._bundle_models import BehaviorCluster

__all__ = [
    "analyze_compute_budgets",
    "behavior_cluster_from_indices",
    "behavior_confidence",
    "behavior_exact_groups",
    "behavior_size_groups",
    "behavior_time_groups",
    "calculate_risk",
    "cluster_by_behavior",
]


def behavior_size_groups(amounts: list[float]) -> list[list[int]]:
    """Group buyer indices by buy amount within ±1% tolerance."""
    if len(amounts) < 2:
        return []
    indexed = sorted(range(len(amounts)), key=lambda i: amounts[i])
    groups: list[list[int]] = []
    current: list[int] = []
    anchor = 0.0
    for idx in indexed:
        amt = amounts[idx]
        if amt <= 0:
            continue
        if not current:
            current = [idx]
            anchor = amt
        elif amt <= anchor * 1.01:
            current.append(idx)
        else:
            if len(current) >= 2:
                groups.append(current)
            current = [idx]
            anchor = amt
    if len(current) >= 2:
        groups.append(current)
    return groups


def behavior_time_groups(blocks: list[int]) -> list[list[int]]:
    """Group buyer indices by same block or 1-block adjacency."""
    if len(blocks) < 2:
        return []
    indexed = sorted(range(len(blocks)), key=lambda i: blocks[i])
    groups: list[list[int]] = []
    current: list[int] = []
    prev_block = 0
    for idx in indexed:
        blk = blocks[idx]
        if blk <= 0:
            continue
        if not current or blk - prev_block <= 1:
            current.append(idx)
        else:
            if len(current) >= 2:
                groups.append(current)
            current = [idx]
        prev_block = blk
    if len(current) >= 2:
        groups.append(current)
    return groups


def behavior_exact_groups(values: list[Any]) -> list[list[int]]:
    """Group buyer indices by identical non-empty value (dex / compute)."""
    by_value: dict[Any, list[int]] = defaultdict(list)
    for idx, val in enumerate(values):
        if val:
            by_value[val].append(idx)
    return [g for g in by_value.values() if len(g) >= 2]


def behavior_confidence(pattern: str, size: int, blocks: list[int]) -> float:
    """Confidence heuristic: pattern strength + cluster size + exact block."""
    base = {
        "same_size": 0.6,
        "same_time": 0.7,
        "same_dex": 0.5,
        "mixed": 0.85,
    }[pattern]
    # Larger lockstep groups are more suspicious
    base = min(0.95, base + 0.05 * max(0, size - 2))
    # Identical (not just adjacent) block is a stronger timing signal
    if blocks and len(set(blocks)) == 1:
        base = min(0.98, base + 0.05)
    return round(base, 3)


def behavior_cluster_from_indices(
    buyers: list[Any],
    indices: list[int],
    amounts: list[float],
    blocks: list[int],
    dexes: list[str],
    total_amount: float,
) -> BehaviorCluster | None:
    """Build a BehaviorCluster from a merged set of buyer indices."""
    wallets = [str(getattr(buyers[i], "address", "")) for i in indices]
    wallets = [w for w in wallets if w]
    if len(wallets) < 2:
        return None

    cluster_amounts = [amounts[i] for i in indices if amounts[i] > 0]
    cluster_blocks = [blocks[i] for i in indices if blocks[i] > 0]
    cluster_dexes = {dexes[i] for i in indices if dexes[i]}

    signals: set[str] = set()
    if cluster_amounts and (max(cluster_amounts) / min(cluster_amounts)) <= 1.01:
        signals.add("same_size")
    if cluster_blocks and (max(cluster_blocks) - min(cluster_blocks)) <= 1:
        signals.add("same_time")
    if len(cluster_dexes) == 1:
        signals.add("same_dex")

    if len(signals) >= 2:
        pattern = "mixed"
    elif signals:
        pattern = next(iter(signals))
    else:
        # Cluster formed via compute-budget similarity or a chain of
        # different signals — treat as a mixed-pattern coordination.
        pattern = "mixed"

    combined_amount = sum(amounts[i] for i in indices)
    combined_pct = round(combined_amount / total_amount * 100, 2)
    confidence = behavior_confidence(pattern, len(wallets), cluster_blocks)

    return BehaviorCluster(
        wallets=wallets,
        pattern=pattern,
        combined_percentage=combined_pct,
        confidence=confidence,
    )


def cluster_by_behavior(buyers: list[Any]) -> list[BehaviorCluster]:
    """Cluster wallets by identical trading patterns.

    Groups early buyers that share the same behavioral fingerprint across
    four dimensions: trade size (±1%), timing (same/1-block-adjacent), DEX
    route, and Solana compute budget / priority fee. Single-dimension groups
    are merged (union-find); each cluster's ``pattern`` is one of
    ``same_size``, ``same_time``, ``same_dex``, or ``mixed``.
    """
    if not buyers or len(buyers) < 2:
        return []

    amounts = [float(getattr(b, "buy_amount", 0.0) or 0.0) for b in buyers]
    blocks = [int(getattr(b, "first_buy_block", 0) or 0) for b in buyers]
    dexes = [str(getattr(b, "dex_route", "") or "").strip().lower() for b in buyers]
    compute_keys = [
        (
            int(getattr(b, "compute_units", 0) or 0),
            round(float(getattr(b, "priority_fee", 0.0) or 0.0), 6),
        )
        for b in buyers
    ]

    # Candidate single-dimension groups (lists of buyer indices)
    size_groups = behavior_size_groups(amounts)
    time_groups = behavior_time_groups(blocks)
    dex_groups = behavior_exact_groups(dexes)
    compute_groups = behavior_exact_groups([f"{cu}:{pf}" if (cu > 0 or pf > 0) else "" for cu, pf in compute_keys])

    groups = [g for g in (size_groups + time_groups + dex_groups + compute_groups) if len(g) >= 2]
    if not groups:
        return []

    # Union-find: merge overlapping single-dimension groups into clusters
    parent = list(range(len(buyers)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for group in groups:
        for member in group[1:]:
            union(group[0], member)

    merged: dict[int, list[int]] = defaultdict(list)
    for i in range(len(buyers)):
        merged[find(i)].append(i)

    total_amount = sum(amounts) or 1.0
    clusters: list[BehaviorCluster] = []
    for indices in merged.values():
        if len(indices) < 2:
            continue
        cluster = behavior_cluster_from_indices(buyers, indices, amounts, blocks, dexes, total_amount)
        if cluster:
            clusters.append(cluster)

    clusters.sort(key=lambda c: c.combined_percentage, reverse=True)
    return clusters


def analyze_compute_budgets(buyers: list[Any]) -> tuple[dict[str, int], int]:
    """Analyze Solana compute budget patterns to identify bot networks.

    Key signals:
    - Casual traders: variable compute, random priority fees
    - Bot operators: identical compute units, fixed priority fee
    - Sniper bots: max compute (1.4M CU), max priority fee
    """
    patterns: dict[str, int] = {}
    jito_count = 0

    for buyer in buyers:
        cu = getattr(buyer, "compute_units", 0)
        pf = getattr(buyer, "priority_fee", 0.0)
        is_jito = getattr(buyer, "is_jito", False)

        if is_jito:
            jito_count += 1

        # Group by compute budget pattern
        if cu > 1_300_000:  # Max compute = sniper
            pattern = "max_compute_sniper"
        elif cu > 200_000 and pf < 0.001:  # Fixed low fee = bot
            pattern = "fixed_fee_bot"
        elif cu < 200_000:  # Normal trader
            pattern = "normal_trader"
        else:
            pattern = "standard_bot"

        patterns[pattern] = patterns.get(pattern, 0) + 1

    return patterns, jito_count


def calculate_risk(
    deployer_funded: list[Any],
    same_block: list[Any],
    behavior_clusters: list[Any],
    jito_count: int,
    bundled_pct: float,
    graph_score: int = 0,
) -> tuple[int, str, list[str]]:
    """Calculate overall bundle risk score and warnings."""
    score = 0
    warnings = []

    # Graph-based fraud patterns (fan-out / cycles / in-degree / temporal)
    if graph_score > 0:
        score += min(20, graph_score // 5)
        warnings.append(f"GRAPH_FRAUD: graph pattern score {graph_score}/100")

    # Deployer-funded wallets (strongest signal)
    if deployer_funded:
        fund_pct = sum(b.buy_amount for b in deployer_funded) / max(bundled_pct, 0.01) * 100
        score += min(50, len(deployer_funded) * 10)
        warnings.append(
            f"DEPLOYER_FUNDED: {len(deployer_funded)} wallets funded by deployer ({fund_pct:.1f}% of supply)"
        )

    # Same-block snipers
    if same_block:
        score += min(30, len(same_block) * 5)
        warnings.append(f"SAME_BLOCK: {len(same_block)} wallets bought in deployment block")

    # Jito bundles
    if jito_count > 0:
        score += min(20, jito_count * 5)
        warnings.append(f"JITO_BUNDLE: {jito_count} wallets used Jito tips")

    # Behavior clusters
    if behavior_clusters:
        # Clusters combining multiple patterns (e.g. same_size + same_time)
        # are very high confidence — raise the contribution cap 15 → 25.
        strong = [
            c
            for c in behavior_clusters
            if (c.pattern == "mixed" and c.confidence >= 0.8)
            or (c.pattern in ("same_size", "same_time") and c.confidence >= 0.7)
        ]
        base_pts = min(15, len(behavior_clusters) * 5)
        if strong:
            score += min(25, base_pts + len(strong) * 5)
            warnings.append(
                f"BEHAVIOR_CLUSTER: {len(behavior_clusters)} coordinated groups "
                f"({len(strong)} high-confidence size/timing clusters)"
            )
        else:
            score += base_pts
            warnings.append(f"BEHAVIOR_CLUSTER: {len(behavior_clusters)} coordinated wallet groups detected")

    # Bundled percentage
    if bundled_pct > 20:
        score += 20
        warnings.append(f"BUNDLED_SUPPLY: {bundled_pct:.1f}% of supply in bundled wallets")
    elif bundled_pct > 10:
        score += 10

    score = min(100, score)

    if score >= 70:
        level = "CRITICAL"
    elif score >= 45:
        level = "HIGH"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"

    return score, level, warnings
