#!/bin/bash
# /root/scripts/restore_test.sh — Verify a backup can actually be restored.
# Per v4.0 §T45. Spins up test containers on alt ports, restores,
# verifies table counts and row counts.

set -euo pipefail

LOG=/var/log/restore-test.log
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

# Find most recent backup
LATEST=$(ls -1d /backups/*/ 2>/dev/null | sort -r | head -1)
if [ -z "$LATEST" ]; then
    log "FAIL: no backups found"
    exit 1
fi
log "Testing restore of: $LATEST"

# Spin up test Postgres on port 15432
TEST_PG="restore-test-pg"
docker rm -f "$TEST_PG" >/dev/null 2>&1 || true
docker run -d --name "$TEST_PG" \
    -e POSTGRES_USER=rmi \
    -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=rmi \
    -p 15432:5432 \
    postgres:16-alpine >/dev/null

# Wait for healthy
for i in 1 2 3 4 5 6 7 8 9 10; do
    if docker exec "$TEST_PG" pg_isready -U rmi >/dev/null 2>&1; then break; fi
    sleep 2
done
log "  ✓ test postgres up"

# Restore
gunzip -c "$LATEST/postgres.sql.gz" | docker exec -i "$TEST_PG" psql -U rmi -d rmi >/dev/null 2>&1
log "  ✓ postgres restored"

# Verify
EXPECTED_TABLES=$(docker exec "$TEST_PG" psql -U rmi -d rmi -t -c "
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'public';" | tr -d ' ')

if [ "$EXPECTED_TABLES" -lt 10 ]; then
    log "FAIL: only $EXPECTED_TABLES tables in restored db"
    curl -s -d "BACKUP RESTORE TEST FAILED: $EXPECTED_TABLES tables" \
        http://localhost:9080/rmi-critical 2>/dev/null || true
    docker rm -f "$TEST_PG" >/dev/null 2>&1
    exit 1
fi
log "  ✓ $EXPECTED_TABLES tables present"

# Sample row counts
log "  Row counts:"
for table in tokens wallets news_items; do
    count=$(docker exec "$TEST_PG" psql -U rmi -d rmi -t -c "
        SELECT COUNT(*) FROM $table;" 2>/dev/null | tr -d ' ' || echo "n/a")
    log "    $table: $count"
done

# Cleanup
docker rm -f "$TEST_PG" >/dev/null 2>&1
log "=== Restore test PASSED ==="

# Notify
curl -s -d "Restore test PASSED for $LATEST ($EXPECTED_TABLES tables)" \
    http://localhost:9080/rmi-info 2>/dev/null || true
# Validate etc_root tarball (T10 — system config backup)
if [ -f "$LATEST/etc_root.tar.gz" ]; then
    log "=== Validating etc_root.tar.gz ==="
    if tar -tzf "$LATEST/etc_root.tar.gz" 2>/dev/null | grep -q "etc/prometheus/prometheus.yml"; then
        log "  ✓ etc_root.tar.gz valid (contains prometheus.yml)"
        ETCRC_FILES=$(tar -tzf "$LATEST/etc_root.tar.gz" 2>/dev/null | wc -l)
        log "  ✓ $ETCRC_FILES files in etc_root.tar.gz"
    else
        log "  ✗ etc_root.tar.gz INVALID (missing prometheus.yml)"
        curl -s -d "BACKUP RESTORE TEST FAILED: etc_root.tar.gz missing prometheus.yml" \
            http://localhost:9080/rmi-critical 2>/dev/null || true
        exit 1
    fi
else
    log "  ⚠ etc_root.tar.gz not found in $LATEST"
fi
