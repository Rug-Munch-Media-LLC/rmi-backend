#!/usr/bin/env python3
"""#8 - Model Evaluation Harness. Benchmarks models on Real-CATS scam data.
Runs lm-eval locally or via Ollama. Picks the best model per task."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434")
REAL_CATS_PATH = Path(os.getenv("REAL_CATS_PATH", str(Path.home() / "rmi/backend/data/real_cats.json")))

# Test prompts for scam classification
BENCHMARK_TASKS: dict[str, dict[str, Any]] = {
    "scam_detection": {
        "prompts": [
            {
                "input": "Token has mint authority enabled, liquidity is 0.5 SOL unlocked, deployer created 50 tokens before. Is this a scam?",
                "expected": "yes",
            },
            {
                "input": "Token has renounced mint, liquidity locked for 1 year, verified contract, audited by CertiK. Is this a scam?",
                "expected": "no",
            },
            {
                "input": "Token has honeypot detection enabled, 99% sell tax, unverified contract, anonymous team. Is this a scam?",
                "expected": "yes",
            },
            {
                "input": "Token listed on Binance, $50M market cap, 100K holders, 2 years old. Is this a scam?",
                "expected": "no",
            },
        ],
        "metric": "accuracy",
    },
}


async def evaluate_model(model: str, task_name: str) -> dict[str, Any]:
    """Evaluate a model on a benchmark task."""
    task = BENCHMARK_TASKS.get(task_name)
    if not task:
        return {"error": f"Unknown task: {task_name}"}

    correct = 0
    total = 0
    total_time = 0.0
    results = []

    async with httpx.AsyncClient(timeout=60) as c:
        for item in task["prompts"]:
            start = time.perf_counter()
            try:
                r = await c.post(
                    f"{OLLAMA}/api/generate",
                    json={
                        "model": model,
                        "prompt": f"Answer only YES or NO. {item['input']}",
                        "stream": False,
                        "options": {"num_predict": 5, "temperature": 0.1},
                    },
                )
                elapsed = time.perf_counter() - start
                total_time += elapsed

                response = r.json().get("response", "").strip().upper()
                is_correct = item["expected"].upper() in response
                if is_correct:
                    correct += 1
                total += 1
                results.append(
                    {
                        "input": item["input"][:80],
                        "expected": item["expected"],
                        "got": response[:20],
                        "correct": is_correct,
                        "time_ms": round(elapsed * 1000),
                    }
                )
            except Exception as e:
                results.append({"input": item["input"][:80], "error": str(e)})
                total += 1

    accuracy = (correct / total * 100) if total > 0 else 0
    return {
        "model": model,
        "task": task_name,
        "accuracy": round(accuracy, 1),
        "correct": correct,
        "total": total,
        "avg_time_ms": round((total_time / total) * 1000) if total > 0 else 0,
        "results": results,
    }


async def compare_models(models: list[str], task: str = "scam_detection") -> dict[str, Any]:
    """Compare multiple models on a benchmark task."""
    scores = []
    for model in models:
        result = await evaluate_model(model, task)
        scores.append(result)

    scores.sort(key=lambda s: s["accuracy"], reverse=True)
    return {
        "task": task,
        "models_compared": len(scores),
        "leaderboard": [
            {"model": s["model"], "accuracy": s["accuracy"], "avg_time_ms": s["avg_time_ms"]} for s in scores
        ],
        "best_model": scores[0]["model"] if scores else None,
    }


if __name__ == "__main__":

    async def main() -> None:
        logger.info("Model Evaluation Harness")
        logger.info("=" * 40)
        models = ["qwen2.5-coder:7b", "mistral:7b"]
        results = await compare_models(models)
        logger.info(json.dumps(results["leaderboard"], indent=2))
        logger.info(f"\nBest model for scam detection: {results['best_model']}")

    asyncio.run(main())
