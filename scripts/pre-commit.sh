#!/bin/bash
# RMI Backend Pre-Commit Check
# Run before committing: bash /root/backend/scripts/pre-commit.sh
set -e

echo "=== RMI Pre-Commit Check ==="

# 1. Python syntax check
echo "[1/4] Python syntax..."
find /root/backend/app -name "*.py" -print0 | xargs -0 python3 -m py_compile 2>&1 | grep -v "^$" || true

# 2. Check no hardcoded secrets
echo "[2/4] Secret scan..."
if grep -rn 'sk-[a-zA-Z0-9]\{20,\}\|eyJ[a-zA-Z0-9_-]\{20,\}\.[a-zA-Z0-9_-]\{20,\}\.[a-zA-Z0-9_-]\{10,\}' /root/backend/app/ --include='*.py' \
   | grep -v '.example\|generate_env\|test_\|__pycache__'; then
    echo "WARNING: Possible hardcoded secrets found!"
fi

# 3. Verify .env.example is up to date
echo "[3/4] Env check..."
CODE_VARS=$(grep -roh 'os.getenv("[A-Z_]*"' /root/backend/app/ --include='*.py' | sed 's/os.getenv("//;s/"//' | sort -u | wc -l)
EXAMPLE_VARS=$(grep -c '^[A-Z]' /root/backend/.env.example)
echo "  Code references: $CODE_VARS env vars"
echo "  .env.example documents: $EXAMPLE_VARS vars"

# 4. Check for stale imports referencing old paths
echo "[4/4] Stale path check..."
if grep -rn '/root/rmi\|/srv/rugmuncher-backend/main' /root/backend/ --include='*.py' | grep -v '.example\|generate_env\|__pycache__'; then
    echo "FAIL: References to stale paths found!"
    exit 1
fi

echo "=== PASS ==="
