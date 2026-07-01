# ops/ — Server-side operations scripts

Scripts that run on **netcup** (not in the FastAPI container). Deploy via:

```bash
scp scripts/ops/backup.sh        root@netcup:/root/scripts/backup.sh
scp scripts/ops/restore_test.sh  root@netcup:/root/scripts/restore_test.sh
ssh netcup "chmod +x /root/scripts/*.sh"
```

## backup.sh

Daily backup of all RMI data + system configs to `/backups/<timestamp>/`.

**What it backs up:**
1. Postgres (`postgres.sql.gz`)
2. Neo4j (`neo4j.dump`)
3. Qdrant (`qdrant/<collection>/<snapshot>`)
4. MinIO (`minio/`)
5. GlitchTip Postgres (`glitchtip.sql.gz`)
6. **System configs** (`etc_root.tar.gz`) — DR-critical, ~19 MB, 2440 files:
   - `/etc/prometheus/` — alert rules, prometheus.yml, alertmanager configs
   - `/root/scripts/` — this backup script, restore_test, cron health check
   - `/root/.hermes/` — Hermes gateway config (NOT sessions — too large)
   - `/root/.ssh/` — SSH keys for git/auth
   - `/root/.bashrc`, `/root/.profile` — shell config
   - `/root/backend/.env` — backend secrets (gopass-backed in source)
7. Old backups rotated (>7 days deleted)
8. ntfy notification on success

**Cadence:** Runs via cron (daily, see `crontab -l` on netcup).

**Restore test:** `/root/scripts/restore_test.sh` spins up test Postgres on alt port 15432, restores, verifies table count ≥ 10, samples row counts, now also validates `etc_root.tar.gz` contains `etc/prometheus/prometheus.yml`.

## restore_test.sh

Monthly restore-test cron (1st of month) that proves a backup can actually be restored, not just written.

**What it checks:**
1. Postgres table count ≥ 10 (fail if too few)
2. Row counts on `tokens`, `wallets`, `news_items` tables
3. `etc_root.tar.gz` contains `etc/prometheus/prometheus.yml`
4. `etc_root.tar.gz` file count > 1000 (catches truncated backups)
5. Notifies via ntfy topic `rmi-critical` on failure, `rmi-info` on success

## Cron health check (cron_health_check.py)

Python watchdog that monitors cron job health and auto-restarts failed crons.

## T10 (RMIV5) — Backup /etc + /root

Implemented in `backup.sh` step 6 (etc_root tarball). Verified 2026-06-22:
- Tarball: 19 MB, 2440 files
- Validates `etc/prometheus/prometheus.yml` exists
- Excludes: `.cache`, `.ollama`, `.hermes/sessions`, `.cargo`, `.rustup`
- Restore test validates tarball contents before declaring success

**Bug found and fixed 2026-06-22:**
Original script tried to back up `/etc/caddy/Caddyfile` which does not exist on
netcup (we use nginx, not caddy). The validation silently passed because grep
returned nothing. Fixed to validate `/etc/prometheus/prometheus.yml` instead,
which actually exists and is the file we need for DR.