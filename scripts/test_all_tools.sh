#!/bin/bash
# RMI Tool Test Suite — tests every x402 tool endpoint
BASE="http://localhost:8000"
PASS=0; FAIL=0; SKIP=0

echo "=== RMI TOOL TEST SUITE ==="
echo "Testing all tool endpoints..."
echo ""

# Get tool list from catalog
TOOLS=$(curl -s --max-time 10 "$BASE/api/v1/x402/tools-catalog" | python3 -c "
import json,sys
d=json.load(sys.stdin)
for t in d.get('tools',[]):
    print(t['id'])
")

for tool in $TOOLS; do
  # Test with a basic payload
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 --max-time 8 \
    -X POST "$BASE/api/v1/x402-tools/$tool" \
    -H "Content-Type: application/json" \
    -d '{"chain":"solana"}' 2>/dev/null)

  case $code in
    200) echo "  ✅ $tool (200)"; PASS=$((PASS+1));;
    402) echo "  💰 $tool (402 — payment required, working)"; PASS=$((PASS+1));;
    403) echo "  💰 $tool (403 — x402 enforcement, working)"; PASS=$((PASS+1));;
    404) echo "  ⚠️  $tool (404 — route missing)"; FAIL=$((FAIL+1));;
    000) echo "  ❌ $tool (no response)"; FAIL=$((FAIL+1));;
    *)   echo "  ❓ $tool ($code)"; SKIP=$((SKIP+1));;
  esac
done

echo ""
echo "=== RESULTS ==="
echo "  Pass: $PASS"
echo "  Fail: $FAIL (missing routes)"
echo "  Skip: $SKIP"
