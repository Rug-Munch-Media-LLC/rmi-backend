#!/usr/bin/env bash
# sbom-verify.sh — Verify SBOM completeness and integrity
# Usage: bash sbom-verify.sh [path-to-sbom.json]
#
# Validates that an SBOM file is well-formed, non-empty, and contains
# all required CycloneDX fields. Blocks on failure — bad SBOM = broken build.

set -euo pipefail

SBOM_FILE="${1:-sbom.spdx.json}"

if [ ! -f "$SBOM_FILE" ]; then
  echo "❌ SBOM file not found: $SBOM_FILE"
  echo "   Generate with: syft scan . -o cyclonedx-json > sbom.spdx.json"
  exit 1
fi

echo "🔍 SBOM Verification — $(basename "$SBOM_FILE")"
echo ""

# Check 1: Valid JSON
if ! python3 -c "import json; json.load(open('$SBOM_FILE'))" 2>/dev/null; then
  echo "  ❌ Invalid JSON"
  exit 1
fi
echo "  ✅ Valid JSON"

# Check 2: Contains components
COMPONENT_COUNT=$(python3 -c "
import json
d = json.load(open('$SBOM_FILE'))
print(len(d.get('components', [])))
" 2>/dev/null || echo "0")
echo "  ✅ Components: $COMPONENT_COUNT"

if [ "$COMPONENT_COUNT" = "0" ]; then
  echo "  ❌ SBOM has zero components — regeneration needed"
  exit 1
fi

# Check 3: File size (must be > 1KB — not empty)
if command -v stat &>/dev/null; then
  SIZE=$(stat -c%s "$SBOM_FILE" 2>/dev/null || stat -f%z "$SBOM_FILE" 2>/dev/null || echo 0)
else
  SIZE=$(wc -c <"$SBOM_FILE" 2>/dev/null || echo 0)
fi
if [ "$SIZE" -lt 1024 ]; then
  echo "  ❌ SBOM too small ($SIZE bytes) — likely empty or corrupt"
  exit 1
fi
echo "  ✅ Size: $SIZE bytes"

# Check 4: Contains required CycloneDX fields
python3 -c "
import json, sys
d = json.load(open('$SBOM_FILE'))
required = ['bomFormat', 'specVersion', 'components']
for field in required:
    if field not in d:
        print(f'  ❌ Missing required field: {field}')
        sys.exit(1)
print('  ✅ All required fields present')
" || exit 1

# Check 5: specVersion is valid
python3 -c "
import json, sys
d = json.load(open('$SBOM_FILE'))
spec = d.get('specVersion', '')
if not spec.startswith('1.'):
    print(f'  ❌ Unsupported specVersion: {spec}')
    sys.exit(1)
print(f'  ✅ specVersion: {spec}')
" || exit 1

echo ""
echo "✅ SBOM verification passed — $COMPONENT_COUNT components, $SIZE bytes"
