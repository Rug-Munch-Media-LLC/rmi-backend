#!/bin/bash
# backup-rag.sh — Backup RAG vector data (Redis ANN + Qdrant snapshots)
# =====================================================================
# Per v4.0 RAG permanence requirement:
#   1. Redis ANN index: DUMP all rag:ann:* keys to backup file
#   2. Qdrant: snapshot all collections to /backups/rag/qdrant/
# 3. B2: sync to remote for disaster recovery
#
# Usage:
#   ./backup-rag.sh                    # Full backup
#   ./backup-rag.sh --qdrant-only      # Qdrant only
#   ./backup-rag.sh --redis-only       # Redis ANN only
#   ./backup-rag.sh --restore FILE     # Restore from backup (not yet implemented)
#
# Cron: 0 4 * * * (daily at 4AM)

set -euo pipefail

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/rag/${DATE}"
KEEP_DAYS=30
LOG="/var/log/backup-rag.log"
REDIS_ANNS_FILE="rag_ann_vectors.json"
QDRANT_BACKUP_DIR="${BACKUP_DIR}/qdrant"

mkdir -p "${BACKUP_DIR}" "${QDRANT_BACKUP_DIR}"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "${LOG}"; }

log "=== RAG Backup started: ${DATE} ==="

MODE="${1:-full}"

# ── 1. Redis ANN Index Backup ──────────────────────────────────────
backup_redis_ann() {
  log "Backing up Redis ANN index..."

  # Try to get Redis from Docker or local
  REDIS_CLI=""
  if command -v redis-cli &>/dev/null; then
    REDIS_CLI="redis-cli"
  elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q rmi-redis; then
    REDIS_CLI="docker exec rmi-redis redis-cli"
  elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q rmi-dragonfly; then
    REDIS_CLI="docker exec rmi-dragonfly redis-cli"
  fi

  if [ -z "${REDIS_CLI}" ]; then
    log "  ⚠ No Redis/DragonflyDB found — skipping Redis ANN backup"
    return 1
  fi

  # Discover all rag:ann:*:docs keys (one per collection)
  ANN_KEYS=$(${REDIS_CLI} KEYS 'rag:ann:*:docs' 2>/dev/null || true)
  if [ -z "${ANN_KEYS}" ]; then
    log "  ⚠ No rag:ann:*:docs keys found — nothing to backup"
    return 0
  fi

  BACKUP_JSON="${BACKUP_DIR}/${REDIS_ANNS_FILE}"
  echo "{" >"${BACKUP_JSON}"
  FIRST=true
  KEY_COUNT=0

  for KEY in ${ANN_KEYS}; do
    COLLECTION=$(echo "${KEY}" | sed 's/rag:ann://;s/:docs//')
    if [ "${FIRST}" = false ]; then
      echo "," >>"${BACKUP_JSON}"
    fi
    FIRST=false

    # Dump ALL fields from the hash
    ENTRIES=$(${REDIS_CLI} HGETALL "${KEY}" 2>/dev/null || true)
    # Convert flat HGETALL output to JSON object
    echo "\"${COLLECTION}\": {" >>"${BACKUP_JSON}"

    # Read flat key-value pairs
    readarray -t KV_PAIRS <<<"${ENTRIES}"
    INNER_FIRST=true
    for ((i = 0; i < ${#KV_PAIRS[@]}; i += 2)); do
      DOC_ID="${KV_PAIRS[$i]}"
      DOC_DATA="${KV_PAIRS[$((i + 1))]}"
      if [ -z "${DOC_ID}" ]; then
        continue
      fi
      if [ "${INNER_FIRST}" = false ]; then
        echo "," >>"${BACKUP_JSON}"
      fi
      INNER_FIRST=false
      # JSON-escape the values
      ESCAPED=$(echo "${DOC_DATA}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo "\"${DOC_DATA}\"")
      echo "\"${DOC_ID}\": ${ESCAPED}" >>"${BACKUP_JSON}"
      KEY_COUNT=$((KEY_COUNT + 1))
    done
    echo "}" >>"${BACKUP_JSON}"

    # Also back up metadata key
    META_KEY="rag:ann:${COLLECTION}:meta"
    META_DATA=$(${REDIS_CLI} GET "${META_KEY}" 2>/dev/null || true)
    if [ -n "${META_DATA}" ]; then
      echo "${META_DATA}" >"${BACKUP_DIR}/rag_ann_${COLLECTION}_meta.json"
    fi
  done

  echo "}" >>"${BACKUP_JSON}"

  # Validate JSON
  if python3 -c "import json; json.load(open('${BACKUP_JSON}'))" 2>/dev/null; then
    SIZE=$(du -h "${BACKUP_JSON}" | cut -f1)
    log "  ✓ Redis ANN backup: ${SIZE} (${KEY_COUNT} vectors across ${COLLECTION} collections)"
  else
    log "  ⚠ Redis ANN backup JSON invalid — check ${BACKUP_JSON}"
    rm -f "${BACKUP_JSON}"
    return 1
  fi
}

# ── 2. Qdrant Snapshot Backup ──────────────────────────────────────
backup_qdrant() {
  log "Backing up Qdrant snapshots..."

  # Discover Qdrant endpoint
  QDRANT_URL=""
  if curl -s -o /dev/null -w '%{http_code}' http://localhost:6333/collections 2>/dev/null | grep -q '^2'; then
    QDRANT_URL="http://localhost:6333"
  elif docker ps --format '{{.Names}}' 2>/dev/null | grep -q rmi-qdrant; then
    QDRANT_URL="http://rmi-qdrant:6333"
  fi

  if [ -z "${QDRANT_URL}" ]; then
    log "  ⚠ No Qdrant endpoint found — skipping Qdrant backup"
    return 1
  fi

  # List all collections
  COLLECTIONS=$(curl -s "${QDRANT_URL}/collections" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for c in d.get('result', {}).get('collections', []):
        print(c['name'])
except Exception:
    pass
" 2>/dev/null || true)

  if [ -z "${COLLECTIONS}" ]; then
    log "  ⚠ No Qdrant collections found"
    return 0
  fi

  SNAPSHOT_COUNT=0
  for COLLECTION in ${COLLECTIONS}; do
    # Create snapshot via API
    SNAP_RESULT=$(curl -s -X POST "${QDRANT_URL}/collections/${COLLECTION}/snapshots" 2>/dev/null)
    SNAP_NAME=$(echo "${SNAP_RESULT}" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('result', {}).get('name', ''))
except Exception:
    pass
" 2>/dev/null || true)

    if [ -z "${SNAP_NAME}" ]; then
      log "  ⚠ Failed to create snapshot for ${COLLECTION}"
      continue
    fi

    # Determine snapshot path (depends on Docker vs bare-metal)
    SNAP_SRC=""
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q rmi-qdrant; then
      SNAP_SRC="/qdrant/snapshots/${COLLECTION}/${SNAP_NAME}"
      mkdir -p "${QDRANT_BACKUP_DIR}/${COLLECTION}"
      if docker cp "rmi-qdrant:${SNAP_SRC}" "${QDRANT_BACKUP_DIR}/${COLLECTION}/${SNAP_NAME}" 2>/dev/null; then
        SNAPSHOT_COUNT=$((SNAPSHOT_COUNT + 1))
        SIZE=$(du -h "${QDRANT_BACKUP_DIR}/${COLLECTION}/${SNAP_NAME}" | cut -f1)
        log "  ✓ ${COLLECTION}: ${SNAP_NAME} (${SIZE})"
      else
        log "  ⚠ Failed to copy ${COLLECTION} snapshot"
      fi
    else
      # Bare-metal: snapshot is local
      log "  ✓ ${COLLECTION}: snapshot created (${SNAP_NAME})"
      SNAPSHOT_COUNT=$((SNAPSHOT_COUNT + 1))
    fi
  done

  log "  ✓ Qdrant: ${SNAPSHOT_COUNT} collections snapshotted"
}

# ── 3. R2 Sync ──────────────────────────────────────────────────
sync_to_r2() {
  log "Syncing RAG backup to B2..."
  if command -v rclone &>/dev/null; then
    if rclone ls b2-crypt:backups/rag/ 2>/dev/null >/dev/null; then
      rclone sync "${BACKUP_DIR}" "b2-crypt:backups/rag/${DATE}" --progress 2>&1 | tee -a "${LOG}"
      log "  ✓ Synced to b2-crypt:backups/rag/${DATE}"
    else
      log "  ⚠ b2-crypt:backups/rag/ not accessible — skipping B2 sync"
    fi
  else
    log "  ⚠ rclone not found — skipping B2 sync"
  fi
}

# ── 4. Cleanup old backups ──────────────────────────────────────────
rotate_backups() {
  log "Rotating old RAG backups (keep ${KEEP_DAYS} days)..."
  find /backups/rag -maxdepth 1 -type d -mtime +${KEEP_DAYS} -exec rm -rf {} \; 2>/dev/null || true
  REMAINING=$(find /backups/rag -maxdepth 1 -type d 2>/dev/null | wc -l)
  TOTAL_SIZE=$(du -sh /backups/rag 2>/dev/null | cut -f1)
  log "  ${REMAINING} backups remaining, total size: ${TOTAL_SIZE}"
}

# ── Main ────────────────────────────────────────────────────────────
case "${MODE}" in
--redis-only)
  backup_redis_ann
  ;;
--qdrant-only)
  backup_qdrant
  ;;
--restore)
  log "Restore mode not yet implemented"
  exit 1
  ;;
full | *)
  backup_redis_ann
  echo "" >>"${LOG}"
  backup_qdrant
  echo "" >>"${LOG}"
  sync_to_r2
  echo "" >>"${LOG}"
  rotate_backups
  ;;
esac

log "=== RAG Backup complete: ${BACKUP_DIR} ==="
echo "${BACKUP_DIR}" >/tmp/last_rag_backup_dir.txt

# Notify ntfy if available
if curl -s -o /dev/null -w '%{http_code}' http://localhost:9080 2>/dev/null | grep -q '^2'; then
  BACKUP_SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
  curl -s -d "RAG backup complete: ${BACKUP_DIR} (${BACKUP_SIZE})" \
    http://localhost:9080/rmi-backups 2>/dev/null || true
fi
