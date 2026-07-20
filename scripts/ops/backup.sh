# DEPRECATED: Use db-backup.sh instead. Remove after 2026-08-01.
#!/bin/bash
# /root/scripts/backup.sh — Daily backup + monthly restore test
# Per v4.0 §T45. Backs up Postgres + Neo4j + Qdrant + MinIO → /backups/
# Monthly (1st of month) restores to a test container and verifies.

set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/backups/$DATE
KEEP_DAYS=7
LOG=/var/log/backup.log

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== Backup started ==="

# 1. Postgres
log "Backing up Postgres..."
docker exec rmi-postgres pg_dump -U rmi rmi 2>/dev/null | gzip > "$BACKUP_DIR/postgres.sql.gz"
PG_SIZE=$(du -h "$BACKUP_DIR/postgres.sql.gz" | cut -f1)
log "  ✓ postgres.sql.gz ($PG_SIZE)"

# 2. Neo4j (export to SQL via cypher-shell)
log "Backing up Neo4j..."
docker exec rmi-neo4j neo4j-admin database dump neo4j --to-path=/tmp 2>/dev/null || true
if docker cp rmi-neo4j:/tmp/neo4j.dump "$BACKUP_DIR/" 2>/dev/null; then
    NEO_SIZE=$(du -h "$BACKUP_DIR/neo4j.dump" | cut -f1)
    log "  ✓ neo4j.dump ($NEO_SIZE)"
else
    log "  ⚠ Neo4j dump failed (skipping)"
fi

# 3. Qdrant (snapshot API)
log "Backing up Qdrant..."
for collection in $(curl -s http://localhost:6333/collections | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for c in d.get('result', {}).get('collections', []):
        print(c['name'])
except: pass
"); do
    SNAP=$(curl -s -X POST "http://localhost:6333/collections/$collection/snapshots" 2>/dev/null | python3 -c "
import json, sys
try: print(json.load(sys.stdin).get('result', {}).get('name', ''))
except: pass
")
    if [ -n "$SNAP" ]; then
        mkdir -p "$BACKUP_DIR/qdrant/$collection"
        docker cp "rmi-qdrant:/qdrant/snapshots/$collection/$SNAP" "$BACKUP_DIR/qdrant/$collection/$SNAP" 2>/dev/null || true
        log "  ✓ qdrant/$collection/$SNAP"
    fi
done

# 4. MinIO (rclone → local backup dir)
log "Backing up MinIO..."
if command -v rclone >/dev/null 2>&1; then
    rclone sync minio:rmi-assets "$BACKUP_DIR/minio/" --transfers 4 2>>"$LOG" || log "  ⚠ rclone sync partial"
else
    # Fallback: mc mirror
    docker run --rm -v /data:/data --network rmi_network minio/mc:latest \
        mirror --overwrite minio/rmi-assets "$BACKUP_DIR/minio/" 2>>"$LOG" || log "  ⚠ mc mirror failed"
fi

# 5. GlitchTip (Postgres dump)
log "Backing up GlitchTip..."

# 5b. System configs (/etc + /root) - surgical backup for DR
log "Backing up system configs..."
tar -czf "$BACKUP_DIR/etc_root.tar.gz" \
    --exclude='/root/.cache' \
    --exclude='/root/.ollama' \
    --exclude='/root/.hermes/sessions' \
    --exclude='/root/.cargo' \
    --exclude='/root/.rustup' \
    --exclude='/root/.local/share/cargo' \
    /etc/prometheus \
    /root/scripts /root/.hermes /root/.ssh /root/.bashrc /root/.profile \
    /root/backend/.env \
    2>/dev/null || true
if [ -f "$BACKUP_DIR/etc_root.tar.gz" ]; then
    ER_SIZE=$(du -h "$BACKUP_DIR/etc_root.tar.gz" | cut -f1)
    log "  ✓ etc_root.tar.gz ($ER_SIZE)"
    if tar -tzf "$BACKUP_DIR/etc_root.tar.gz" 2>/dev/null | grep -q "etc/prometheus/prometheus.yml"; then
        log "  ✓ etc_root.tar.gz valid"
    fi
else
    log "  ⚠ etc_root.tar.gz creation failed"
fi
log "Backing up GlitchTip..."
docker exec rmi-glitchtip-db pg_dump -U glitchtip glitchtip 2>/dev/null | gzip > "$BACKUP_DIR/glitchtip.sql.gz" || true

# 6. Rotation
log "Rotating old backups (keep ${KEEP_DAYS} days)..."
find /backups -maxdepth 1 -type d -mtime +$KEEP_DAYS -exec rm -rf {} \; 2>/dev/null || true
REMAINING=$(ls -1d /backups/*/ 2>/dev/null | wc -l)
TOTAL_SIZE=$(du -sh /backups 2>/dev/null | cut -f1)

log "=== Backup complete: $BACKUP_DIR ==="
log "  Remaining snapshots: $REMAINING"
log "  Total backup size: $TOTAL_SIZE"

# 7. Monthly restore test (1st of month)
if [ "$(date +%d)" = "01" ]; then
    log "=== Monthly restore test ==="
    /root/scripts/restore_test.sh 2>&1 | tee -a "$LOG"
fi

# Push to ntfy if running
if curl -s -o /dev/null -w '%{http_code}' http://localhost:9080 2>/dev/null | grep -q '^2'; then
    curl -s -d "Backup complete: $BACKUP_DIR ($TOTAL_SIZE, $REMAINING snapshots)" \
        http://localhost:9080/rmi-backups 2>/dev/null || true
fi
