#!/usr/bin/env bash
# RAG regression eval gate (G3) — fails the build when RAG quality regresses.
#
# Usage:
#   bash scripts/rag-eval.sh            # --dry: deterministic unit gate (default)
#   bash scripts/rag-eval.sh --dry      # same: runs the rag_eval pytest marker
#   bash scripts/rag-eval.sh --live     # runs the real golden-set eval against
#                                       # live services (Redis/Qdrant/Infinity)
#   bash scripts/rag-eval.sh --live --top-k 5 --collection threat_intel_v2
#
# Exit codes:
#   0  thresholds met (or dry unit gate passed)
#   1  hit_rate or faithfulness fell below the regression floor
#   2  eval run failed (timeouts, missing services, script misuse)
set -euo pipefail

MODE="dry"
TOP_K="${RAG_EVAL_TOP_K:-5}"
COLLECTION="${RAG_EVAL_COLLECTION:-threat_intel_v2}"

log() { echo "[rag-eval] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
  --dry) MODE="dry" ;;
  --live) MODE="live" ;;
  --top-k)
    TOP_K="${2:?--top-k needs a value}"
    shift
    ;;
  --collection)
    COLLECTION="${2:?--collection needs a value}"
    shift
    ;;
  -h | --help)
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    log "unknown option: $1"
    exit 2
    ;;
  esac
  shift
done

if [[ "$MODE" == "dry" ]]; then
  # Deterministic unit gate: golden-set size, hit-rate proxy, thresholds,
  # and the mocked pipeline wiring. No services required; safe in CI.
  log "running dry unit gate (rag_eval marker)..."
  python3 -m pytest tests/unit/rag/test_rag_eval_gate.py tests/unit/rag/test_rag_rerank_budget.py -q --tb=short
  log "dry unit gate passed"
  exit 0
fi

# ── Live golden-set eval ──────────────────────────────────────────────
log "running live golden-set eval (collection=$COLLECTION top_k=$TOP_K)..."

PYTHON_OUT="$(
  python3 - "$TOP_K" "$COLLECTION" <<'PY'
import asyncio
import json
import sys

from app.rag.evaluation import (
    EVAL_FAITHFULNESS_MIN,
    EVAL_HIT_RATE_MIN,
    RUGBRAIN_GOLDEN_SET,
    evaluate_rag_pipeline,
)

top_k = int(sys.argv[1]) if len(sys.argv) > 1 else 5
collection = sys.argv[2] if len(sys.argv) > 2 else "threat_intel_v2"


async def main() -> int:
    result = await evaluate_rag_pipeline(
        test_set=RUGBRAIN_GOLDEN_SET,
        collection=collection,
    )
    metrics = result.get("metrics", {})
    print(
        json.dumps(
            {
                "metrics": metrics,
                "metric_source": result.get("metric_source", ""),
                "thresholds": {
                    "hit_rate_min": EVAL_HIT_RATE_MIN,
                    "faithfulness_min": EVAL_FAITHFULNESS_MIN,
                },
                "thresholds_met": result.get("thresholds_met", False),
                "threshold_failures": result.get("threshold_failures", []),
                "test_count": result.get("test_count", 0),
                "queries_run": result.get("queries_run", 0),
                "errors": result.get("errors", 0),
            },
            indent=2,
        )
    )
    return 0 if result.get("thresholds_met", False) else 1


sys.exit(asyncio.run(main()))
PY
)" || PYTHON_RC=$?

if [[ -z "${PYTHON_RC:-}" ]]; then
  PYTHON_RC=0
fi

log "eval result:"
echo "$PYTHON_OUT"

if [[ "$PYTHON_RC" -ne 0 ]]; then
  log "FAILED: one or more metrics fell below the regression threshold"
  exit 1
fi

log "PASS: all RAG regression thresholds met"
exit 0
