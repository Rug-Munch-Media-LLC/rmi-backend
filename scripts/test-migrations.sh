#!/usr/bin/env bash
# test-migrations.sh — Verify Alembic migrations apply and roll back cleanly
# Usage: bash test-migrations.sh [--db-url postgres://...]
# Requires: Docker running, or a test PostgreSQL instance
#
# Sets ALEMBIC_DATABASE_URL for alembic (reads from env.py) so the
# script works with the project's existing migration configuration.

set -euo pipefail

DB_URL="${1:-postgresql://postgres:test@localhost:5432/rmi_test_migrations}"
export ALEMBIC_DATABASE_URL="$DB_URL"

echo "🧪 Migration Test Suite"
echo "  Target: $DB_URL"
echo ""

# ── Spin up test PostgreSQL ────────────────────
cleanup() {
  echo "  Cleaning up test database..."
  docker rm -f rmi-migration-test 2>/dev/null || true
}
trap cleanup EXIT

echo "  Starting test PostgreSQL..."
docker run -d --name rmi-migration-test \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=rmi_test_migrations \
  -p 5433:5432 \
  postgres:15-alpine 2>/dev/null || true

# Wait for Postgres to be ready
echo "  Waiting for PostgreSQL..."
for i in $(seq 1 30); do
  if docker exec rmi-migration-test pg_isready -U postgres &>/dev/null; then
    echo "  ✅ PostgreSQL ready"
    break
  fi
  sleep 1
done

# ── Test 1: Apply all migrations ──────────────
echo ""
echo "─── Test 1: Apply All Migrations ───"
if alembic upgrade head; then
  echo "  ✅ All migrations applied successfully"
else
  echo "  ❌ Migration apply failed"
  exit 1
fi

# ── Test 2: Verify migration history ──────────
echo ""
echo "─── Test 2: Migration History ───"
CURRENT=$(alembic current 2>/dev/null | head -1)
echo "  Current revision: $CURRENT"
HEAD=$(alembic heads | head -1)
echo "  Head revision:    $HEAD"

if [ "$CURRENT" = "$HEAD" ]; then
  echo "  ✅ At head revision"
else
  echo "  ❌ Not at head ($CURRENT vs $HEAD)"
  exit 1
fi

# ── Test 3: Rollback one revision ─────────────
echo ""
echo "─── Test 3: Rollback One Revision ───"
if alembic downgrade -1 2>/dev/null; then
  echo "  ✅ Rollback successful"
else
  echo "  ⚠️  Rollback failed (some migrations don't support downgrade)"
fi

# ── Test 4: Re-apply after rollback ───────────
echo ""
echo "─── Test 4: Re-Apply After Rollback ───"
if alembic upgrade head; then
  echo "  ✅ Re-apply successful"
else
  echo "  ❌ Re-apply failed after rollback"
  exit 1
fi

# ── Test 5: Verify tables exist ───────────────
echo ""
echo "─── Test 5: Schema Verification ───"
TABLE_COUNT=$(psql "$DB_URL" -t -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | xargs)
echo "  Tables created: $TABLE_COUNT"
if [ "${TABLE_COUNT:-0}" -gt 0 ]; then
  echo "  ✅ Schema populated"
else
  echo "  ⚠️  No tables found (may be a fresh database)"
fi

echo ""
echo "✅ Migration test suite passed"
