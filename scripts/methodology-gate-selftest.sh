#!/usr/bin/env bash
# Proves the methodology gate still catches a published method. CI runs this so
# a gate that silently stopped detecting cannot stay green (the antipattern
# gates have the same self-test discipline).
set -euo pipefail
GATE="${GATE:-scripts/check-methodology.py}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/selftest/app"
cat >"$tmp/selftest/app/weights.py" <<'PY'
WEIGHTS = {
    "liquidity_depth": 0.40,
    "holder_concentration": 0.35,
    "dev_wallet_history": 0.25,
}
PY
printf '{"public_repos": ["selftest"]}\n' >"$tmp/policy.json"
if python3 "$GATE" --repo "$tmp/selftest" --policy "$tmp/policy.json" >"$tmp/out" 2>&1; then
    echo "FAIL: gate PASSED a planted weight table — it is decorative"
    sed -n '1,10p' "$tmp/out"
    exit 1
fi
grep -q "weight-map" "$tmp/out" || {
    echo "FAIL: blocked for the wrong reason:"
    sed -n '1,10p' "$tmp/out"
    exit 1
}
echo "ok: planted weight table blocked by rule weight-map"
