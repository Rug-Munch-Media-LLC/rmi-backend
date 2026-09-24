#!/usr/bin/env bash
# Block methodology/infra literals in COMMIT MESSAGES.
#
# The file-content gate (pre-commit-methodology-check.sh) scans staged files.
# A commit message is equally permanent in history and was NOT being scanned,
# so `gopass show -o rmi/...` or an internal hostname could be published in a
# PUBLIC repo with a clean-looking commit. This closes that path using the same
# uncommitted pattern set.
set -uo pipefail

MSG_FILE="${1:-}"
[ -z "$MSG_FILE" ] || [ ! -f "$MSG_FILE" ] && exit 0

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
PATTERNS="$ROOT/.methodology-patterns"
[ -f "$PATTERNS" ] || exit 0

if grep -qE -f "$PATTERNS" "$MSG_FILE" 2>/dev/null; then
  echo "❌ COMMIT MESSAGE BLOCKED: methodology/infra literal detected."
  echo "   The message is permanent history, including in public mirrors."
  echo "   Rephrase it (refer to services by role, not by address/path)."
  exit 1
fi
exit 0
