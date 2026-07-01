"""A/B Testing Framework — split traffic, measure accuracy, auto-promote winners."""

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/ab-testing", tags=["ab-testing"])

_experiments: dict[str, dict] = {}
_results: dict[str, list] = {}


@router.post("/experiment")
async def create_experiment(
    name: str,
    variants: str = Query(..., description="Comma-separated variant names, e.g. 'prompt_v1,prompt_v2'"),
    traffic_split: str = Query("50,50", description="Traffic split percentages"),
    metric: str = Query("accuracy", description="Metric to evaluate"),
):
    """Create a new A/B test experiment."""
    variant_list = [v.strip() for v in variants.split(",")]
    split_list = [int(s.strip()) for s in traffic_split.split(",")]

    if len(variant_list) != len(split_list):
        return {"error": "Variants and splits must have same length"}
    if sum(split_list) != 100:
        return {"error": "Traffic splits must sum to 100"}

    exp_id = hashlib.md5(name.encode()).hexdigest()[:8]
    _experiments[exp_id] = {
        "id": exp_id,
        "name": name,
        "variants": variant_list,
        "traffic_split": split_list,
        "metric": metric,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
    }
    _results[exp_id] = []
    return _experiments[exp_id]


@router.get("/assign/{experiment_id}")
async def assign_variant(experiment_id: str, user_id: str = "anonymous"):
    """Assign a user to a variant deterministically (same user always gets same variant)."""
    if experiment_id not in _experiments:
        return {"variant": "control", "note": "Experiment not found"}

    exp = _experiments[experiment_id]
    # Deterministic assignment based on user_id hash
    h = int(hashlib.md5(f"{experiment_id}:{user_id}".encode()).hexdigest(), 16)
    bucket = h % 100

    cumulative = 0
    for variant, split in zip(exp["variants"], exp["traffic_split"], strict=False):
        cumulative += split
        if bucket < cumulative:
            return {"experiment": experiment_id, "variant": variant, "user": user_id}

    return {"experiment": experiment_id, "variant": exp["variants"][-1], "user": user_id}


@router.post("/record/{experiment_id}")
async def record_result(experiment_id: str, variant: str, correct: bool = False):
    """Record a result for an A/B test variant."""
    if experiment_id not in _experiments:
        return {"error": "Experiment not found"}

    _results[experiment_id].append(
        {
            "variant": variant,
            "correct": correct,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    # Check if we have enough data to declare a winner (30+ samples per variant)
    results = _results[experiment_id]
    variant_counts = {}
    variant_correct = {}
    for r in results:
        v = r["variant"]
        variant_counts[v] = variant_counts.get(v, 0) + 1
        if r["correct"]:
            variant_correct[v] = variant_correct.get(v, 0) + 1

    # Check if all variants have enough samples
    min_samples = 30
    all_ready = all(count >= min_samples for count in variant_counts.values())

    if all_ready:
        scores = {v: variant_correct.get(v, 0) / variant_counts[v] for v in variant_counts}
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        improvement = scores[best] - scores[worst]

        result = {
            "experiment": experiment_id,
            "status": "complete" if improvement > 0.05 else "inconclusive",
            "winner": best if improvement > 0.05 else None,
            "scores": {v: round(s, 3) for v, s in scores.items()},
            "samples": variant_counts,
            "confidence": round(improvement * 100, 1),
        }

        if improvement > 0.05:
            _experiments[experiment_id]["status"] = "complete"
            _experiments[experiment_id]["winner"] = best

        return result

    return {
        "experiment": experiment_id,
        "status": "collecting",
        "samples": variant_counts,
        "min_needed": min_samples,
    }


@router.get("/experiments")
async def list_experiments():
    return {
        "experiments": list(_experiments.values()),
        "active": sum(1 for e in _experiments.values() if e["status"] == "active"),
    }


@router.get("/experiment/{experiment_id}")
async def get_experiment(experiment_id: str):
    if experiment_id not in _experiments:
        return {"error": "Experiment not found"}
    exp = _experiments[experiment_id]
    results = _results.get(experiment_id, [])
    return {
        **exp,
        "total_trials": len(results),
        "results": _summarize_results(results, exp["variants"]),
    }


def _summarize_results(results: list, variants: list) -> dict:
    counts = dict.fromkeys(variants, 0)
    correct = dict.fromkeys(variants, 0)
    for r in results:
        v = r["variant"]
        if v in counts:
            counts[v] += 1
            if r["correct"]:
                correct[v] += 1
    return {
        v: {"trials": counts[v], "correct": correct[v], "accuracy": round(correct[v] / max(counts[v], 1), 3)}
        for v in variants
    }
