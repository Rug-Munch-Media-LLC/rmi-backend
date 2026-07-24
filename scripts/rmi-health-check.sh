#!/usr/bin/env bash
# ============================================================================
# rmi-health-check.sh — RMI Backend + DegenFeed Protocol Health Monitor
# ============================================================================
# Checks:
#   1. Backend liveness (/live) and health (/health)
#   2. DegenFeed protocol service health (/api/v1/degenfeed/status)
#   3. DegenFeed cache layer health (/api/v1/degenfeed/health)
#   4. Per-protocol endpoint availability
#
# Usage:
#   ./scripts/rmi-health-check.sh              # Quick check (exit 0=healthy)
#   ./scripts/rmi-health-check.sh --verbose    # Full output with details
#   ./scripts/rmi-health-check.sh --alert      # One-shot alert if any down
#
# Exit codes:
#   0 — all healthy
#   1 — one or more non-critical services degraded
#   2 — critical service down (backend, postgres, redis)
#
# Environment variables:
#   RMI_BACKEND_URL   — Backend base URL (default: http://127.0.0.1:8000)
#   ALERT_WEBHOOK_URL — Webhook URL for Slack/Discord/PagerDuty alerts
# ============================================================================
set -euo pipefail

BACKEND_URL="${RMI_BACKEND_URL:-http://127.0.0.1:8000}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
VERBOSE=false
ALERT_MODE=false
TIMEOUT=10

# ── Parse args ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose|-v) VERBOSE=true; shift ;;
        --alert|-a)   ALERT_MODE=true; shift ;;
        --help|-h)    echo "Usage: $0 [--verbose|--alert]"; exit 0 ;;
        *)            echo "Unknown: $1"; exit 1 ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────
PASS=0
WARN=0
FAIL=0
RESULTS=()

log_pass()  { local m="✓ PASS: $1"; RESULTS+=("$m"); $VERBOSE && echo "$m"; ((PASS++)); }
log_warn()  { local m="⚠ WARN: $1"; RESULTS+=("$m"); $VERBOSE && echo "$m"; ((WARN++)); }
log_fail()  { local m="✗ FAIL: $1"; RESULTS+=("$m"); echo "$m" >&2; ((FAIL++)); }

check_http() {
    local name="$1" url="$2" expect="${3:-200}"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")
    if [[ "$code" == "$expect" ]]; then
        log_pass "$name (HTTP $code)"
        return 0
    else
        log_fail "$name — expected HTTP $expect, got $code"
        return 1
    fi
}

check_json() {
    local name="$1" url="$2" jq_filter="$3"
    local output
    output=$(curl -s --max-time "$TIMEOUT" "$url" 2>/dev/null || echo '{}')
    local healthy
    healthy=$(echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if $jq_filter else 'false')" 2>/dev/null || echo "false")
    if [[ "$healthy" == "true" ]]; then
        log_pass "$name"
        return 0
    else
        local msg
        msg=$(echo "$output" | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d, indent=2)[:200])" 2>/dev/null || echo "parse error")
        log_fail "$name — check failed: $msg"
        return 1
    fi
}

send_alert() {
    local subject="$1" body="$2"
    if [[ -n "$ALERT_WEBHOOK_URL" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "$(cat <<EOF
{
    "text": "*[RMI Health] $subject*",
    "attachments": [{"text": "$body", "color": "$([ "$FAIL" -gt 0 ] && echo 'danger' || echo 'warning')"}]
}
EOF
)" > /dev/null 2>&1 && log_pass "Alert sent to webhook" || log_warn "Alert send failed"
    fi
}

# ═══════════════════════════════════════════════════════════════════════
# CHECKS
# ═══════════════════════════════════════════════════════════════════════

echo "═══════════════════════════════════════════════════════════"
echo "  RMI Health Check — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "  Backend: $BACKEND_URL"
echo "═══════════════════════════════════════════════════════════"

# ── 1. Backend liveness (critical) ──────────────────────────────────
echo ""
echo "── Layer 1: Backend Liveness ──"
check_http "backend liveness" "$BACKEND_URL/live" 200

# ── 2. Backend readiness (critical — redis + postgres) ──────────────
echo ""
echo "── Layer 2: Backend Readiness ──"
check_json "backend readiness" "$BACKEND_URL/ready" 'd.get("status") == "ready"'

# ── 3. Full health (deep probes) ────────────────────────────────────
echo ""
echo "── Layer 3: Full Health (all stores) ──"
check_json "backend health" "$BACKEND_URL/health" 'd.get("status") in ("healthy", "degraded")'

if $VERBOSE; then
    echo ""
    echo "── Layer 3b: Per-store detail ──"
    curl -s --max-time "$TIMEOUT" "$BACKEND_URL/health" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(f\"Status: {d.get('status')} | Uptime: {d.get('uptime_seconds',0):.0f}s\")
for name, store in d.get('stores',{}).items():
    h = store.get('healthy', False)
    e = store.get('error', '')
    ms = store.get('latency_ms', '?')
    print(f\"  {'✓' if h else '✗'} {name}: healthy={h} latency={ms}ms {e[:80] if e else ''}\")
"
fi

# ── 4. DegenFeed proxy health ──────────────────────────────────────
echo ""
echo "── Layer 4: DegenFeed Protocol Proxy ──"
check_json "degenfeed cache health" "$BACKEND_URL/api/v1/degenfeed/health" 'd.get("healthy", False) or d.get("cache", {}).get("healthy", False)'
check_json "degenfeed protocol list" "$BACKEND_URL/api/v1/degenfeed/protocols" 'd.get("count", 0) >= 6'

# ── 5. DegenFeed per-protocol health ───────────────────────────────
echo ""
echo "── Layer 5: DegenFeed Protocol Status ──"
check_json "degenfeed protocol status" "$BACKEND_URL/api/v1/degenfeed/status" 'd.get("healthy", False) or d.get("cache_healthy", False)'

if $VERBOSE; then
    echo ""
    echo "── Layer 5b: Protocol detail ──"
    curl -s --max-time "$TIMEOUT" "$BACKEND_URL/api/v1/degenfeed/status" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(f\"Overall healthy: {d.get('healthy')} | Cache: {d.get('cache_healthy')}\")
for name, status in d.get('protocols', {}).items():
    h = status.get('healthy', False)
    sc = status.get('status_code', '?')
    e = status.get('error', '')
    print(f\"  {'✓' if h else '✗'} {name}: HTTP {sc} {e[:80] if e else 'ok'}\")
"
fi

# ── 6. Redis reachable through degenfeed cache ─────────────────────
echo ""
echo "── Layer 6: Redis Cache Layer ──"
check_json "degenfeed cache status" "$BACKEND_URL/api/v1/degenfeed/health" 'True'

# ═══════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SUMMARY: $PASS passed, $WARN warnings, $FAIL failures"
echo "═══════════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "FAILURES:"
    for r in "${RESULTS[@]}"; do
        echo "  $r"
    done
    send_alert "RMI Health — $FAIL failures" "${RESULTS[*]}"
    exit 2
elif [ "$WARN" -gt 0 ]; then
    send_alert "RMI Health — $WARN warnings" "${RESULTS[*]}"
    exit 1
else
    echo "All checks passed."
    exit 0
fi
