#!/bin/bash
# rmi-status — Rug Munch Intelligence System Status & Profit Checker
# Auto-loads on terminal start via .bashrc
# Fast (<2s): parallel checks, cached data, Supabase-backed earnings
# Usage: rmi-status [--watch]

API="${RMI_API_URL:-http://localhost:8000}"
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
ok()    { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn()  { printf "  ${YELLOW}⚠${NC} %s\n" "$1"; }
fail()  { printf "  ${RED}✗${NC} %s\n" "$1"; }
header(){ printf "\n${BOLD}${CYAN}━━━ %s ━━━${NC}\n" "$1"; }
money() { printf "${GREEN}\$%'.2f${NC}" "$1"; }

# ── HEADER ──
clear
printf "${BOLD}${CYAN}"
printf "╔══════════════════════════════════════════════════════╗\n"
printf "║   RUG MUNCH INTELLIGENCE — System Status            ║\n"
printf "║   %-50s ║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "╚══════════════════════════════════════════════════════╝${NC}\n"

# ── EARNINGS (from backend dashboard + Supabase fallback) ──
header "EARNINGS"

DASH=$(curl -s --max-time 3 "${API}/api/v1/x402/dashboard" 2>/dev/null)
if [ -n "$DASH" ]; then
  TOTAL=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); e=d.get('earnings',{}); print(e.get('total_earnings_usdc',0))" 2>/dev/null)
  TODAY=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); e=d.get('earnings',{}); print(e.get('today_earnings_usdc',0))" 2>/dev/null)
  PAYERS=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); e=d.get('earnings',{}); print(e.get('unique_payers',0))" 2>/dev/null)
  SB_TOTAL=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('supabase',{}).get('total_usdc',0))" 2>/dev/null)
  SB_COUNT=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('supabase',{}).get('persisted_count',0))" 2>/dev/null)
  BY_CHAIN=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); e=d.get('earnings',{}).get('earnings_by_chain',{}); print(' | '.join(f'{k}: \${v:,.0f}' for k,v in sorted(e.items(), key=lambda x:-x[1])[:4]))" 2>/dev/null)
  TRIALS=$(echo "$DASH" | python3 -c "import json,sys; d=json.load(sys.stdin); t=d.get('trials',{}); print(f\"{t.get('trials_today',0)} today / {t.get('trials_all_time',0)} all\")" 2>/dev/null)
  REDIS_OK=$(echo "$DASH" | python3 -c "import json,sys; print('yes' if json.load(sys.stdin).get('meta',{}).get('redis_available') else 'no')" 2>/dev/null)

  printf "  %-22s %s\n" "Revenue (All-Time):"   "$(money "${TOTAL:-0}")"
  printf "  %-22s %s\n" "Revenue (Today):"      "$(money "${TODAY:-0}")"
  printf "  %-22s %s\n" "Unique Payers:"        "${PAYERS:-0}"
  printf "  %-22s %s\n" "By Chain:"             "${BY_CHAIN:-?}"
  printf "  %-22s %s\n" "Supabase Stored:"      "\$${SB_TOTAL:-0} (${SB_COUNT:-0} records)"
  printf "  %-22s %s\n" "Trials:"               "${TRIALS:-?}"
  printf "  %-22s %s\n" "Redis:"                "${REDIS_OK}"
else
  warn "Backend dashboard unreachable"
fi

# ── SYSTEM HEALTH (parallel docker checks) ──
header "SYSTEM HEALTH"

# Check backend
BE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "${API}/health" 2>/dev/null)
[ "$BE" = "200" ] && ok "Backend API" || fail "Backend API (HTTP $BE)"

# Docker containers — fast inline checks
for c in rmi-backend rmi-redis rmi-worker rmi-n8n rmi-orchestrator rmi-cloudflare; do
  STATUS=$(docker inspect --format='{{.State.Status}}' "$c" 2>/dev/null)
  case "$STATUS" in
    running) ok "$c" ;;
    "")      warn "$c — not found" ;;
    *)       fail "$c — $STATUS" ;;
  esac
done

# ── GATEWAY WORKERS (fast, parallel) ──
header "GATEWAY WORKERS"

# Check Solana gateway
SOL_H=$(curl -s --max-time 3 "https://sol.rugmunch.io/health" 2>/dev/null)
if [ -n "$SOL_H" ]; then
  SOL_RMI=$(echo "$SOL_H" | python3 -c "import json,sys; d=json.load(sys.stdin); t=d.get('tools',{}); print(t.get('rmi_paid',0)+t.get('mcp',0))" 2>/dev/null)
  ok "Solana Gateway — ${SOL_RMI:-?} tools"
else
  fail "Solana Gateway"
fi

# Check Base gateway
BASE_H=$(curl -s --max-time 3 "https://base.rugmunch.io/health" 2>/dev/null)
if [ -n "$BASE_H" ]; then
  BASE_T=$(echo "$BASE_H" | python3 -c "import json,sys; d=json.load(sys.stdin); t=d.get('tools',{}); print(t.get('rmi_paid',0)+t.get('mcp',0))" 2>/dev/null)
  ok "Base Gateway — ${BASE_T:-?} tools"
else
  fail "Base Gateway"
fi

# MCP Router
MCP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "https://mcp-router.rugmunch.io/tools" 2>/dev/null)
[ "$MCP" = "200" ] && ok "MCP Router" || warn "MCP Router (HTTP $MCP)"

# ── RESOURCES ──
header "RESOURCES"

# Disk
DISK=$(df -h / | tail -1 | awk '{printf "%s used (%s/%s)", $5, $3, $2}')
printf "  ${GREEN}●${NC} Disk:  %s\n" "$DISK"

# Memory
MEM=$(free -h | awk '/^Mem:/ {printf "%s free / %s total", $4, $2}')
printf "  ${GREEN}●${NC} Memory: %s\n" "$MEM"

# CPU (quick snapshot)
CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1 2>/dev/null)
printf "  ${GREEN}●${NC} CPU:    %s%% used\n" "${CPU:-?}"

# ── KEY URLS ──
header "ENDPOINTS"
printf "  Solana:  ${CYAN}https://sol.rugmunch.io${NC}\n"
printf "  Base:    ${CYAN}https://base.rugmunch.io${NC}\n"
printf "  MCP Docs:${CYAN}https://sol.rugmunch.io/mcp-x402-docs${NC}\n"

# ── WALLET FACTORY ──
header "WALLET FACTORY (25+ chains)"
WALLET_API=$(curl -s --max-time 2 "http://localhost:8000/api/v1/wallets/stats" 2>/dev/null)
if [ -n "$WALLET_API" ]; then
  CHAINS=$(echo "$WALLET_API" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('chains_supported','?'))" 2>/dev/null)
  GEN=$(echo "$WALLET_API" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('generation_count','?'))" 2>/dev/null)
  ok "Wallet Factory Active — ${CHAINS:-?} chains, ${GEN:-0} generated"
else
  warn "Wallet Factory — backend not responding"
fi

# Vault status
VAULT="/root/.rmi/wallets/vault.json"
if [ -f "$VAULT" ]; then
  VAULT_COUNT=$(python3 -c "import json; d=json.load(open('$VAULT')); print(d.get('_meta',{}).get('total_wallets',0))" 2>/dev/null)
  printf "  ${GREEN}●${NC} Vault:   %s wallets stored\\n" "${VAULT_COUNT:-?}"
else
  printf "  ${YELLOW}●${NC} Vault:   empty (generate wallets at /api/v1/wallets/generate)\\n"
fi

# Payment wallets
printf "  ${GREEN}●${NC} EVM:     ${CYAN}0x1E3AC...905C9${NC}\n"
printf "  ${GREEN}●${NC} Solana:  ${CYAN}Gix4P9...MpFzv${NC}\n"
printf "  ${GREEN}●${NC} TRON:    ${CYAN}TYFBXi...eyZu8${NC}\n"
printf "  ${GREEN}●${NC} Bitcoin: ${CYAN}1DkQAf...aM3Md${NC}\n"
printf "  Backend: ${CYAN}${API}${NC}\n"
printf "  Website: ${CYAN}https://rugmunch.io${NC}\n"

echo ""
printf "${BOLD}${CYAN}Run 'rmi-status' anytime. Add --watch for 30s refresh.${NC}\n"
echo ""
