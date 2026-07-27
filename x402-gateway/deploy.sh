#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RMI x402 Gateway — Unified Deploy Script v2
# ═══════════════════════════════════════════════════════════════
# Deploys both x402 Workers from canonical source directories.
# Tools are auto-synced from backend catalog — no more hardcoded lists.
#
# Usage:
#   ./deploy.sh                    # Deploy both
#   ./deploy.sh base               # Deploy x402-base only
#   ./deploy.sh solana             # Deploy x402-sol only
#   ./deploy.sh verify             # Verify both workers post-deploy
#
# Env vars:
#   CLOUDFLARE_API_TOKEN — Workers Scripts:Edit permission
#   CLOUDFLARE_ACCOUNT_ID  — 8f9bd9165c1250b426c66dc1967deefd
# ═══════════════════════════════════════════════════════════════

set -eo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

BASE_DIR="/path/to/x402-gateway-base"
SOL_DIR="/path/to/x402-gateway/solana"
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-8f9bd9165c1250b426c66dc1967deefd}"
TOKEN="${CLOUDFLARE_API_TOKEN:-}"

if [ -z "$TOKEN" ]; then
    echo -e "${RED}ERROR: CLOUDFLARE_API_TOKEN not set${NC}"
    echo "Run:  export CLOUDFLARE_API_TOKEN=cfat_xxx..."
    echo "Get one at: https://dash.cloudflare.com/profile/api-tokens"
    echo "Needs: Workers Scripts:Edit permission on account ${ACCOUNT_ID}"
    exit 1
fi

deploy_worker() {
    local name=$1 dir=$2
    echo -e "${CYAN}════════════════════════════════════════════${NC}"
    echo -e "${CYAN}Deploying ${name} from ${dir}...${NC}"
    echo -e "${CYAN}════════════════════════════════════════════${NC}"

    if [ ! -d "$dir" ]; then
        echo -e "${RED}ERROR: ${dir} not found${NC}"
        return 1
    fi

    cd "$dir"
    CLOUDFLARE_API_TOKEN="$TOKEN" \
    CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID" \
        npx wrangler deploy 2>&1 | grep -E "Uploaded|Deployed|Current Version|ERROR|✘"

    local rc=${PIPESTATUS[0]}
    if [ $rc -eq 0 ]; then
        echo -e "${GREEN}✓ ${name} deployed successfully${NC}"
    else
        echo -e "${RED}✗ ${name} deploy failed (may be rate-limited, retry in 60s)${NC}"
        return $rc
    fi
}

verify_worker() {
    local name=$1 url=$2
    echo -n "  Verifying ${name}... "
    local health
    health=$(curl -sf "${url}/health" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'{d[\"status\"]} | rmi:{d[\"tools\"][\"rmi_paid\"]} mcp:{d[\"tools\"][\"mcp\"]}')" 2>/dev/null || echo "FAIL")
    if [ "$health" != "FAIL" ]; then
        echo -e "${GREEN}${health}${NC}"
    else
        echo -e "${RED}DOWN${NC}"
    fi

    echo -n "  Discovery (networks)... "
    local nets
    nets=$(curl -sf "${url}/.well-known/x402" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('supportedNetworks',[])))" 2>/dev/null || echo "0")
    if [ "$nets" -ge 13 ]; then
        echo -e "${GREEN}${nets} chains${NC}"
    else
        echo -e "${RED}${nets} chains (expected 13)${NC}"
    fi

    echo -n "  Trial enforcement... "
    local trial
    trial=$(curl -sf "${url}/tools/sentiment" -X POST -H "Content-Type: application/json" -d '{"query":"test"}' 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('trial_remaining','no-trial'))" 2>/dev/null || echo "FAIL")
    echo -e "${CYAN}trial_remaining=${trial}${NC}"
}

case "${1:-both}" in
    base)
        deploy_worker "x402-base" "$BASE_DIR"
        ;;
    solana|sol)
        deploy_worker "x402-sol" "$SOL_DIR"
        ;;
    verify)
        echo -e "${CYAN}Verifying both workers...${NC}"
        verify_worker "x402-base" "https://x402-base.cryptorugmuncher.workers.dev"
        verify_worker "x402-sol"  "https://x402-sol.cryptorugmuncher.workers.dev"
        ;;
    both|"")
        deploy_worker "x402-base" "$BASE_DIR"
        echo ""
        deploy_worker "x402-sol" "$SOL_DIR"
        echo ""
        echo -e "${CYAN}Post-deploy verification:${NC}"
        verify_worker "x402-base" "https://x402-base.cryptorugmuncher.workers.dev"
        verify_worker "x402-sol"  "https://x402-sol.cryptorugmuncher.workers.dev"
        ;;
    *)
        echo "Usage: $0 [base|solana|both|verify]"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}════════════════════════════════════════════${NC}"
echo -e "${GREEN}Deploy complete${NC}"
echo -e "  Backend catalog: https://mcp.rugmunch.io/api/v1/x402-tools/catalog"
echo -e "  x402-base:       https://x402-base.cryptorugmuncher.workers.dev"
echo -e "  x402-sol:        https://x402-sol.cryptorugmuncher.workers.dev"
echo -e "${GREEN}════════════════════════════════════════════${NC}"
