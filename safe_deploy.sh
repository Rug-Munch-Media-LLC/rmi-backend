#!/bin/bash
# Safe RAG Deploy — NEVER deploys main.py
# Deploys only RAG app files that are safe to overwrite.
#
# SECURITY: This file is in a public repo. Set VPS_HOST via env var
# or SSH host alias — NEVER hardcode production IPs here.
#
# Usage:
#   VPS_HOST=root@your-server bash safe_deploy.sh
#   cd ~/rmi/backend && bash safe_deploy.sh

VPS="${VPS_HOST:?Set VPS_HOST env var (e.g. root@hostname) or edit this file locally}"
VPS_APP="/root/backend/app"

FILES=(
    # Core RAG (single source of truth)
    "app/crypto_embeddings.py"
    "app/ann_index.py"
    "app/rag_service.py"
    # Ingestion
    "app/rag_chunking.py"
    "app/rag_historical.py"
    "app/github_rag_feeder.py"
    # Evaluation & Feedback
    "app/rag_evaluation.py"
    "app/rag_feedback.py"
    # Storage & Backup
    "app/rag_permanence.py"
    "app/rag_gcloud_backup.py"
    # Agentic & Firehose
    "app/rag_agentic.py"
    "app/rag_firehose.py"
    # Endpoints (imported by main.py)
    "app/rag_endpoints.py"
    # Observability & Metrics
    "app/rag_observability.py"
    "app/rag_metrics_api.py"
    # Databus RAG
    "app/databus/rag_ingestion.py"
    "app/databus/rag_provider.py"
)

echo "=== Safe RAG Deploy ==="
echo "Deploying ${#FILES[@]} files (skipping main.py)"
echo ""

for f in "${FILES[@]}"; do
    if [ -f "$f" ]; then
        echo "  $f → VPS"
        scp "$f" "$VPS:$VPS_APP/${f#app/}" 2>/dev/null
    else
        echo "  SKIP $f (not found)"
    fi
done

echo ""
echo "Deploy complete. Restart backend:"
echo "  ssh $VPS 'docker restart rmi-backend'"
