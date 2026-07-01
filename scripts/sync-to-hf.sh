#!/bin/bash
# sync-to-hf.sh — Push backend docs/specs to HuggingFace
# Run after: git push origin main
#
# Usage: ./sync-to-hf.sh
#
# Policy: When we push to one, we push to the other. Both org and personal.
#
# HF repos:
#   rugmunch/rug-munch-intelligence (org)
#   cryptorugmunch/rug-munch-intelligence (personal)

set -e

HF_TOKEN="${HF_TOKEN:-$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')}"

if [ -z "$HF_TOKEN" ]; then
    echo "ERROR: No HF token found. Set HF_TOKEN env var or save to ~/.cache/huggingface/token"
    echo "Generate token at: https://huggingface.co/settings/tokens (need write access)"
    exit 1
fi

BACKEND_DIR="${1:-/root/backend}"
REPOS="rugmunch/rug-munch-intelligence cryptorugmunch/rug-munch-intelligence"

echo "=== Syncing to HuggingFace ==="
echo "Source: $BACKEND_DIR"

# Fetch live specs from our own server
echo "Fetching live specs from rugmunch.io..."
mkdir -p /tmp/hf-sync

curl -s -H "User-Agent: RMI-Sync/1.0" https://rugmunch.io/openapi.json > /tmp/hf-sync/openapi.json
curl -s -H "User-Agent: RMI-Sync/1.0" https://rugmunch.io/.well-known/x402 > /tmp/hf-sync/x402-discovery.json
curl -s -H "User-Agent: RMI-Sync/1.0" https://rugmunch.io/api/v1/x402/tools-catalog > /tmp/hf-sync/tools-catalog.json

echo "Fetched specs. Uploading to HF..."

for repo_id in $REPOS; do
    echo "  Syncing to $repo_id..."
    python3 -c "
from huggingface_hub import HfApi
import os, sys

token = '$HF_TOKEN'
api = HfApi(token=token)
repo_id = '$repo_id'

files = {
    '/tmp/hf-sync/openapi.json': 'openapi.json',
    '/tmp/hf-sync/x402-discovery.json': 'x402-discovery.json',
    '/tmp/hf-sync/tools-catalog.json': 'tools-catalog.json',
    '$BACKEND_DIR/README.md': 'backend-readme.md',
    '$BACKEND_DIR/hf-model-card.md': 'README.md',
}

for local, remote in files.items():
    try:
        api.upload_file(
            path_or_fileobj=local,
            path_in_repo=remote,
            repo_id=repo_id,
            repo_type='model',
        )
        print(f'    {remote} uploaded')
    except Exception as e:
        print(f'    {remote}: {e}')
        sys.exit(1)

print(f'  {repo_id} synced successfully')
"
done

echo "=== Sync complete ==="
echo "Org:      https://huggingface.co/rugmunch/rug-munch-intelligence"
echo "Personal: https://huggingface.co/cryptorugmunch/rug-munch-intelligence"
