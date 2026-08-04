#!/usr/bin/env bash
# Install the ChatGuardrails module to /srv/scripts so the deployed chat
# services (chat-rag-bridge.py etc.) can import it.
#
# Canonical source: app/security/chat_guardrails.py
# Deploy target:    /srv/scripts/chat_guardrails.py
#
# The deployed filename uses an underscore (not the hyphen in the original
# spec) because Python cannot `import` a module whose name contains a hyphen.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/app/security/chat_guardrails.py"
DST="/srv/scripts/chat_guardrails.py"
LOG="${CHAT_GUARDRAILS_LOG:-/var/log/chat-guardrails.log}"

if [[ ! -f "$SRC" ]]; then
    echo "error: source module not found: $SRC" >&2
    exit 1
fi

mkdir -p /srv/scripts
install -m 0640 -o root -g root "$SRC" "$DST"

# Pre-create the guardrail log (0600, root-only).
if [[ "$LOG" != "/dev/null" && "$LOG" != "-" ]]; then
    touch "$LOG"
    chmod 0600 "$LOG"
fi

echo "installed: $DST"
echo "log file:  $LOG"
echo
echo "To wire into chat-rag-bridge.py, see: docs/chat-guardrails-bridge-hook.md"
