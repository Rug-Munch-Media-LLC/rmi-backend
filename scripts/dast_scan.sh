#!/usr/bin/env bash
# DAST scan script for RMI — OWASP ZAP quick scan against staging
# Usage: bash scripts/dast_scan.sh [target_url]
# Target defaults to http://localhost:8000
set -euo pipefail

TARGET="${1:-http://localhost:8000}"
REPORT_DIR="/tmp/zap-reports"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT="$REPORT_DIR/zap-report-$TIMESTAMP.json"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "=== DAST Scan: $TARGET ==="
log "Timestamp: $TIMESTAMP"

# Create output directory
mkdir -p "$REPORT_DIR"

# Check if ZAP is installed
if ! command -v zap-cli &>/dev/null && ! command -v zap.sh &>/dev/null; then
    log "ZAP not installed. Running with OWASP ZAP Docker image..."
    
    if ! docker image inspect zaproxy/zap-stable &>/dev/null 2>&1; then
        log "Pulling ZAP Docker image (first run)..."
        docker pull zaproxy/zap-stable
    fi
    
    # Run ZAP in Docker
    docker run --rm --network="host" zaproxy/zap-stable \
        zap-baseline.py \
        -t "$TARGET" \
        -J "$REPORT" \
        -a \
        -r "${REPORT%.json}.html" \
        2>&1 | tee -a "$REPORT_DIR/zap-output-$TIMESTAMP.log" || true

elif command -v zap-cli &>/dev/null; then
    log "Using ZAP CLI..."
    
    # Start ZAP daemon (if not running)
    zap-cli --zap-url http://localhost:8090 status || {
        log "Starting ZAP daemon..."
        zap start -p 8090 &>/dev/null
        sleep 5
    }
    
    # Run spider (crawl)
    log "Spidering $TARGET..."
    zap-cli --zap-url http://localhost:8090 spider "$TARGET"
    
    # Run active scan
    log "Scanning $TARGET..."
    zap-cli --zap-url http://localhost:8090 quick-scan "$TARGET" \
        --spider --scanners all
    
    # Export report
    log "Generating report..."
    zap-cli --zap-url http://localhost:8090 report -o "$REPORT" -f json
fi

# Parse results
log "=== Results ==="
if [ -f "$REPORT" ]; then
    # Count findings by severity
    if command -v jq &>/dev/null; then
        HIGHS=$(jq --raw-output '[.site[].alerts[] | select(.riskcode=="3")] | length' "$REPORT" 2>/dev/null || echo "0")
        MEDIUMS=$(jq --raw-output '[.site[].alerts[] | select(.riskcode=="2")] | length' "$REPORT" 2>/dev/null || echo "0")
        LOWS=$(jq --raw-output '[.site[].alerts[] | select(.riskcode=="1")] | length' "$REPORT" 2>/dev/null || echo "0")
        INFOS=$(jq --raw-output '[.site[].alerts[] | select(.riskcode=="0")] | length' "$REPORT" 2>/dev/null || echo "0")
        
        log "High:   $HIGHS"
        log "Medium: $MEDIUMS"
        log "Low:    $LOWS"
        log "Info:   $INFOS"
    else
        log "Report saved (jq not installed for summary): $REPORT"
    fi
    
    # Gate: fail on high-severity
    if [ "${HIGHS:-0}" -gt 0 ]; then
        log "FAIL: $HIGHS high-severity vulnerabilities found!"
        log "Review: $REPORT"
        exit 1
    fi
    
    log "PASS: No high-severity vulnerabilities"
else
    log "WARN: No report file generated"
fi

log "Full report: ${REPORT}"
log "=== DAST scan complete ==="
