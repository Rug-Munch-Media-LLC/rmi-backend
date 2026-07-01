#!/bin/bash
# scripts/docker_cleanup.sh — Netcup Docker cleanup script
set -euo pipefail

LOG=/var/log/docker-cleanup.log

log() {
    echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"
}

log "=== Docker Cleanup ==="

# 1. Remove exited containers
log "Pruning exited containers..."
docker container prune -f 2>/dev/null | tee -a "$LOG"

# 2. Remove dangling images (untagged)
log "Pruning dangling images..."
docker image prune -f 2>/dev/null | tee -a "$LOG"

# 3. Remove unused images (not referenced by any container)
log "Pruning unused images..."
docker image prune -a -f 2>/dev/null | tee -a "$LOG"

# 4. Remove build cache
log "Pruning build cache..."
docker builder prune -a -f 2>/dev/null | tee -a "$LOG"

# 5. Remove unused volumes (BE CAREFUL — only unused)
log "Pruning unused volumes..."
docker volume prune -f 2>/dev/null | tee -a "$LOG"

# 6. Report
log "=== After cleanup ==="
docker system df | tee -a "$LOG"
df -h / | tee -a "$LOG"
free -m | tee -a "$LOG"
log "=== Cleanup complete ==="
