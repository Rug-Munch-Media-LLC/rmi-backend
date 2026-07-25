# Release Checklist — Production Deployment

> **Owner:** DevOps SRE  
> **Last updated:** 2026-07-25  
> **Applies to:** rmi-backend production releases on Talos

---

## Pre-Launch Verification (1 week before)

### ☐ 1. Code Review & Quality Gates

- [ ] All PRs merged to main
- [ ] `make quality` passes locally
- [ ] `make test-all` passes (all unit + integration tests)
- [ ] `make coverage` ≥ 60%
- [ ] `bandit -r app/ -ll` — zero high-severity findings
- [ ] `gitleaks detect` — zero secret findings
- [ ] No `print()`, `time.sleep()`, or f-string logs in new code
- [ ] No files > 500 lines in new/changed code
- [ ] CHANGELOG.md updated with all changes

### ☐ 2. Dependency Audit

- [ ] `pip audit` or `pip-audit` — zero known CVEs in Python deps
- [ ] Renovate/Dependabot PRs reviewed and merged
- [ ] `pip install -r requirements.txt --dry-run` — no conflicts
- [ ] Verify all optional deps in `pyproject.toml` are current

### ☐ 3. Security Verification

- [ ] CORS_ORIGINS configured (not `["*"]` in production)
- [ ] CSP headers verified (nginx + middleware)
- [ ] Rate limiting tested (nginx 100r/s, middleware per-path)
- [ ] JWT secrets rotated (if >60 days since last rotation)
- [ ] API keys rotated (if >60 days since last rotation)
- [ ] .env.secrets reviewed for stale/expired keys
- [ ] Cloudflare WAF rules reviewed and up to date

### ☐ 4. Infrastructure Check

- [ ] All databases healthy (Postgres, ClickHouse, Redis, Neo4j, Qdrant)
- [ ] Disk space ≥ 20% free on Talos
- [ ] Memory available ≥ 4GB for rmi-backend container
- [ ] SSL certificates valid (check expiry dates)
- [ ] Backup ran successfully in last 24 hours
- [ ] `docker-compose.infrastructure.yml` services all running
- [ ] Monitoring stack: Prometheus, Grafana, Loki all healthy

---

## Launch Day

### ☐ 5. Pre-Deploy

- [ ] Notify team in Telegram #deploy channel
- [ ] Tag release in git: `git tag v2026.XX.YY && git push --tags`
- [ ] Create database backup:
  ```bash
  docker exec rmi-postgres pg_dump -U rmi -d rmi > /backups/pre-release-$(date +%Y%m%d).sql
  ```
- [ ] Warm up Docker build cache:
  ```bash
  docker pull backend-rmi-backend:latest
  ```

### ☐ 6. Build

- [ ] Build multi-stage image:
  ```bash
  docker build -f Dockerfile.multistage \
    -t backend-rmi-backend:v2026.XX.YY \
    -t backend-rmi-backend:latest .
  ```
- [ ] Run Trivy scan on image:
  ```bash
  trivy image backend-rmi-backend:latest \
    --severity CRITICAL,HIGH \
    --ignore-unfixed
  ```
- [ ] Verify zero CRITICAL/HIGH vulnerabilities (or document exceptions)

### ☐ 7. Deploy

- [ ] Apply database migrations:
  ```bash
  docker compose -f /srv/rmi-infra/docker-compose.backend.yml run --rm \
    rmi-backend alembic upgrade head
  ```
- [ ] Deploy new image:
  ```bash
  docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend
  ```
- [ ] Verify container started:
  ```bash
  docker ps --filter name=rmi-backend
  docker inspect rmi-backend --format='{{.State.Health.Status}}'
  ```
- [ ] Check health endpoint:
  ```bash
  curl -f http://localhost:8000/health
  curl -f http://localhost:8000/ready
  ```
- [ ] Check logs for startup errors:
  ```bash
  docker logs rmi-backend --tail 50
  ```

### ☐ 8. Smoke Tests

- [ ] HTTP API responds (200 on `/health`, `/version`)
- [ ] Auth endpoints work (register, login, refresh)
- [ ] Token scan endpoint returns valid results
- [ ] Wallet query returns data
- [ ] MCP tools respond
- [ ] x402 payment flow works (sandbox mode)
- [ ] Rate limiting headers present on responses
- [ ] Security headers present (CSP, HSTS, XFO)
- [ ] WebSocket alerts connect successfully

---

## Post-Launch Monitoring (first 24 hours)

### ☐ 9. Immediate (first hour)

- [ ] Error rate: monitor `/metrics` for 5xx spikes
- [ ] P99 latency below 5s
- [ ] Database connection pool usage < 80%
- [ ] Memory usage stable (< 3GB RSS)
- [ ] CPU usage stable (< 80%)
- [ ] No crash loops (container restart count = 0)

### ☐ 10. First 24 Hours

- [ ] Monitor all Grafana dashboards:
  - [ ] RMI Backend (request rate, latency, error rate)
  - [ ] Infrastructure (CPU, memory, disk, network)
  - [ ] Database (connections, query time, cache hit ratio)
  - [ ] Scanner (volume, detection rates, risk distribution)
- [ ] Verify log aggregation (Loki receiving JSON logs)
- [ ] Check all cron jobs ran successfully
- [ ] Check backup completed successfully (daily 04:00)
- [ ] Monitor Telegram bot responsiveness
- [ ] Verify x402 payment processing
- [ ] Alert if:
  - Error rate > 5% in 5-minute window
  - P99 latency > 5s
  - Container health check fails
  - Database pool > 80% used
  - Disk < 10% free

### ☐ 11. 48-Hour Follow-Up

- [ ] Review error logs for new patterns
- [ ] Check Sentry/GlitchTip for new exceptions
- [ ] Survey team for any issues
- [ ] Update release notes / CHANGELOG if hotfixes were needed
- [ ] Document lessons learned in `RELEASE_LOG.md`

---

## Rollback Procedure

If the release causes issues:

### Quick Rollback (image revert)
```bash
# Option 1: Restart previous image
docker tag backend-rmi-backend:previous backend-rmi-backend:latest
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend

# Option 2: Rebuild from git tag
git checkout v2026.XX.YY-prior
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend
```

### Database Rollback
```bash
# Alembic downgrade (one step)
docker exec rmi-backend alembic downgrade -1

# Or restore from backup
/root/scripts/restore-postgres.sh /backups/pre-release-$(date +%Y%m%d).sql
```

---

## Quick Reference Commands

```bash
# Build
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .

# Deploy
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend

# Health check
curl -f http://localhost:8000/health
docker inspect rmi-backend --format='{{.State.Health.Status}}'

# Logs
docker logs rmi-backend --tail 100 -f

# Migration
docker exec rmi-backend alembic upgrade head

# Rollback
docker exec rmi-backend alembic downgrade -1

# Metrics
curl http://localhost:8000/metrics | grep -E 'rmi_http_requests_total|rmi_http_request_duration'
```

---

*Last updated: 2026-07-25 · Maintained by DevOps SRE*
