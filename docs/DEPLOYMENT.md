# RMI Backend — Deployment Guide

> **Host:** Talos (netcup) — 12 CPU, 32 GB RAM, ~193 GB SSD  
> **OS:** Ubuntu Linux  
> **Containers:** 93 total across ~20 Docker Compose files  
> **Last updated:** 2026-07-23

---

## 1. Architecture Overview

```mermaid
graph TB
    LB[Traffic] --> RMI[rmi-backend<br/>Port 8000]
    RMI --> PG[(rmi-postgres<br/>Port 5432)]
    RMI --> CH[(rmi-clickhouse<br/>Port 8123)]
    RMI --> NEO[(rmi-neo4j<br/>Port 7687)]
    RMI --> QD[(rmi-qdrant<br/>Port 6333)]
    RMI --> DRAG[(rmi-redis/dragonfly<br/>Port 6379)]
    RMI --> MIN[(rmi-minio<br/>Port 9092)]
    
    PG -.->|Pooled| PGB[rmi-pgbouncer<br/>Port 6432]
    RMI --> DNS[rmi-dns-proxy<br/>dnsmasq]
```

The RMI backend is a **single Docker container** deployed via Docker Compose on Talos. It depends on 6 infrastructure containers (databases, cache, object storage) that are deployed separately via `docker-compose.infrastructure.yml`.

---

## 2. Environment Variables

### Required

| Variable | Description | Source |
|----------|-------------|--------|
| `ENVIRONMENT` | `prod`, `staging`, or `dev` | `.env.secrets` |
| `DATABASE_URL` | Postgres async connection string | `.env.secrets` |
| `POSTGRES_PASSWORD` | Postgres password | `.env.secrets` |
| `CLICKHOUSE_PASSWORD` | ClickHouse password | `.env.secrets` |
| `REDIS_URL` | Redis/DragonflyDB URL | `.env.secrets` |
| `RMI_AUTH_TOKEN` | API key for internal auth | `.env.secrets` |
| `ADMIN_API_KEY` | Admin endpoint API key | `.env.secrets` |
| `JWT_SECRET` | HMAC secret for JWT (>= 32 chars) | `.env.secrets` |
| `CORS_ORIGINS` | JSON array of allowed origins | Hardcoded in compose |

### Database Connections

| Variable | Example Value |
|----------|--------------|
| `DATABASE_URL` | `postgresql+asyncpg://rmi:${PASSWORD}@rmi-postgres:5432/rmi` |
| `POSTGRES_HOST` | `rmi-postgres` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_USER` | `rmi` |
| `POSTGRES_DB` | `rmi` |
| `CLICKHOUSE_URL` | `http://rmi:${PASSWORD}@rmi-clickhouse:8123` |
| `CLICKHOUSE_HOST` | `rmi-clickhouse` |
| `CLICKHOUSE_PORT` | `8123` |
| `CLICKHOUSE_USER` | `rmi` |
| `CLICKHOUSE_DATABASE` | `rmi` |
| `REDIS_URL` | `redis://rmi-redis:6379/0` |
| `REDIS_HOST` | `rmi-redis` |
| `QDRANT_URL` | `http://rmi-qdrant:6333` |
| `NEO4J_URI` | `bolt://rmi-neo4j:7687` |
| `MINIO_URL` | `http://rmi-minio:9000` |

### Runtime

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port |
| `LOG_LEVEL` | `info` | Logging verbosity |
| `OTEL_ENABLED` | `false` | OpenTelemetry tracing |
| `OLLAMA_URL` | `http://[INTERNAL_HOST]:11434` | Local LLM endpoint |
| `ETH_RPC_URL` | `http://127.0.0.1:8545` | Local Ethereum RPC |

### JWT

| Variable | Description | Required for RS256 |
|----------|-------------|-------------------|
| `JWT_SECRET` | HMAC fallback secret (>= 32 chars) | ✅ Always |
| `JWT_ALGORITHM` | `RS256` (default) or `HS256` | — |
| `JWT_PRIVATE_KEY` | RSA PEM private key | ✅ RS256 only |
| `JWT_PUBLIC_KEY` | RSA PEM public key | ✅ RS256 only |

---

## 3. Docker Compose Layout

### Production Compose Files (on Talos)

| File | Path | Services |
|------|------|----------|
| Infrastructure | `/srv/rmi-infra/docker-compose.infrastructure.yml` | Postgres, ClickHouse, Neo4j, Qdrant, DragonflyDB, MinIO, Prometheus, Grafana |
| Backend | `/srv/rmi-infra/docker-compose.backend.yml` | rmi-backend, dns-proxy |
| Forgejo | `/srv/rmi-infra/docker-compose.forgejo.yml` | Git hosting |
| Quality | `/srv/rmi-infra/docker-compose.quality.yml` | SonarQube, Uptime Kuma |
| Temporal | `/srv/rmi-infra/temporal-docker-compose.yml` | Temporal workflow engine |
| LiteLLM | `/srv/rmi-infra/litellm-docker-compose.yml` | LLM proxy |

### Backend Service Definition

```yaml
rmi-backend:
  build:
    context: /srv/work/repos/rmi-backend
    dockerfile: Dockerfile.multistage
  image: backend-rmi-backend:latest
  container_name: rmi-backend
  restart: unless-stopped
  networks:
    rmi_network:
      aliases: [rmi-backend, backend]
  entrypoint: ["/tini", "--"]
  command: ["python", "-u", "main.py"]
  env_file:
    - .env.secrets
  ports:
    - "8000:8000"
  deploy:
    resources:
      limits:
        memory: 2g
        cpus: '2'
  cap_drop: ALL
  security_opt:
    - no-new-privileges:true
  read_only: true
  tmpfs:
    - /tmp:noexec,nosuid,size=64m
  logging:
    driver: json-file
    options:
      max-size: "50m"
      max-file: "3"
```

---

## 4. Build & Deploy

### Development

```bash
cd /srv/work/repos/rmi-backend

# Install dependencies
pip install -e ".[dev]"
pip install -r requirements.txt

# Start dev server with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
make test        # unit tests
make test-all    # all tests including integration

# Quality gates
make lint        # ruff check
make format      # ruff format
make typecheck   # mypy
make security    # bandit scan
make ci          # full CI pipeline
```

### Production Build & Deploy

#### Standard Deploy

```bash
cd /srv/work/repos/rmi-backend

# Build multi-stage image
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .

# Restart container with new image
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend

# Verify
docker ps --filter name=rmi-backend
docker inspect rmi-backend --format='{{.State.Health.Status}}'
```

#### Quick Restart (no rebuild)

```bash
docker restart rmi-backend
```

#### Safe RAG-Only Deploy

For deploying RAG file changes without rebuilding the full image:

```bash
bash /srv/work/repos/rmi-backend/safe_deploy.sh
docker restart rmi-backend
```

#### Full Stack Deploy (backend + infrastructure)

```bash
# Ensure infrastructure is running
docker compose -f /srv/rmi-infra/docker-compose.infrastructure.yml up -d

# Wait for all databases to be healthy
docker compose -f /srv/rmi-infra/docker-compose.infrastructure.yml ps

# Apply migrations
docker exec rmi-backend alembic upgrade head

# Deploy backend
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend
```

---

## 5. Database Migrations

```bash
# Apply all pending migrations
docker exec rmi-backend alembic upgrade head

# Check current migration version
docker exec rmi-backend alembic current

# View migration history
docker exec rmi-backend alembic history

# Roll back one migration
docker exec rmi-backend alembic downgrade -1

# Create a new migration (requires local alembic)
alembic revision --autogenerate -m "description"
```

ClickHouse schema is applied separately:

```bash
docker cp infra/clickhouse_schema.sql rmi-clickhouse:/tmp/
docker exec rmi-clickhouse clickhouse-client --queries-file /tmp/clickhouse_schema.sql
```

---

## 6. Health Checks

### Container-Level Health Check

Built into `Dockerfile`:
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1
```

### Manual Verification

```bash
# Basic health
curl http://localhost:8000/health
# → {"status": "ok"}

# Liveness probe
curl http://localhost:8000/live
# → 200 OK (always, if process is alive)

# Readiness probe (checks DB connectivity)
curl http://localhost:8000/ready
# → 200 OK if all DBs reachable

# Full API status
curl http://localhost:8000/api/v1/status

# Check Docker health status
docker inspect rmi-backend --format='{{.State.Health.Status}}'
# → healthy

# View recent logs
docker logs rmi-backend --tail 50
```

### Dependent Service Checks

```bash
# Check all databases
docker ps --filter network=rmi_network
docker exec rmi-postgres pg_isready -U rmi
docker exec rmi-clickhouse clickhouse-client --query "SELECT 1"
docker exec rmi-redis redis-cli PING
```

---

## 7. Monitoring

### Prometheus Metrics

```bash
# View raw metrics
curl http://localhost:8000/metrics

# Key metrics:
#   rmi_http_requests_total{method,path,status}
#   rmi_http_request_duration_ms{path}
#   rmi_db_pool_size
#   rmi_cache_hit_ratio
```

### Grafana Dashboards

Available at `http://localhost:3000` (port-forwarded from internal host)

| Dashboard | Contents |
|-----------|----------|
| RMI Backend | Request rate, latency, error rate by route |
| Infrastructure | CPU, memory, disk, network per host |
| Database | Postgres connections, query time, cache hit ratio |
| Scanner | Scan volume, detection rates, risk distribution |

### Logging

```bash
# Follow live logs
docker logs rmi-backend -f

# Last 5 minutes
docker logs rmi-backend --since 5m

# Search for errors
docker logs rmi-backend 2>&1 | grep -i error

# Export logs to file
docker logs rmi-backend > /tmp/rmi-backend.log
```

### Alerts

Alerts evaluated by Prometheus, routed via Alertmanager:

| Alert | Threshold | Action |
|-------|-----------|--------|
| High error rate | > 5% errors in 5min | n8n webhook → notification |
| High latency | p99 > 5s | n8n webhook |
| Container down | Health check fails 3x | PagerDuty |
| DB connection pool | > 80% used | Email alert |
| Disk space | < 10% free | Email alert |

---

## 8. Rollback Procedures

### Docker Image Rollback

```bash
# Option 1: Revert to specific tag
docker tag backend-rmi-backend:previous backend-rmi-backend:latest
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend

# Option 2: Run specific old image
docker run -d --name rmi-backend-rollback \
    --network rmi_network \
    backend-rmi-backend:2026.06.21

# Option 3: Rebuild from git history
cd /srv/work/repos/rmi-backend
git checkout <previous-stable-commit>
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend
```

### Database Rollback

```bash
# Alembic downgrade (one step)
docker exec rmi-backend alembic downgrade -1

# Or target specific revision
docker exec rmi-backend alembic downgrade <revision_id>

# Restore from backup
/root/scripts/restore-postgres.sh <backup-file>
```

### Full Stack Restart

```bash
# Restart backend only
docker compose -f /srv/rmi-infra/docker-compose.backend.yml restart rmi-backend

# Restart all core services
docker compose -f /srv/rmi-infra/docker-compose.infrastructure.yml restart
docker compose -f /srv/rmi-infra/docker-compose.backend.yml restart

# Full stack restart (if DB needs recovery)
docker compose -f /srv/rmi-infra/docker-compose.infrastructure.yml down
docker compose -f /srv/rmi-infra/docker-compose.infrastructure.yml up -d
# Wait for DB health, then:
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d
```

---

## 9. Troubleshooting

### Common Issues

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Connection refused` to rmi-postgres | Postgres not ready | Wait for `pg_isready`, check compose |
| `JWT_SECRET` error on startup | Missing env var | Check `.env.secrets` file |
| `CORS_ORIGINS` error in production | CORS not configured | Set specific origins, not `["*"]` |
| High memory usage | Scan cache growing | Check `memory_limit`, reduce cache TTL |
| Slow ClickHouse queries | Missing partition pruning | Verify `ORDER BY` matches query pattern |
| x402 payment fails | Facilitator down | Check `GET /api/v1/x402/facilitator-health` |

### Debug Commands

```bash
# Check container resource usage
docker stats rmi-backend

# Inspect environment
docker exec rmi-backend env | grep -v PASSWORD | sort

# Check if port is listening
netstat -tlnp | grep 8000

# DNS resolution test
docker exec rmi-backend getent hosts rmi-postgres

# SSL cert check
echo | openssl s_client -connect localhost:443 2>/dev/null | openssl x509 -noout -dates
```

---

## 10. Security Checklist

Before deploying to production, verify:

- [ ] `CORS_ORIGINS` is set to specific domains, not `["*"]`
- [ ] `ADMIN_API_KEY` is configured (not empty)
- [ ] `RMI_AUTH_TOKEN` is configured (not empty)
- [ ] `JWT_SECRET` is >= 32 characters and unique per environment
- [ ] `ENVIRONMENT=prod` (not "dev" or "staging")
- [ ] Container runs as non-root user (rmi)
- [ ] `read_only: true` is set on the container
- [ ] `cap_drop: ALL` is applied
- [ ] Secrets file `.env.secrets` is not in git
- [ ] Database backups are running and verified
- [ ] Health checks pass
- [ ] Prometheus metrics are accessible
- [ ] `OTEL_ENABLED` is set appropriately for the environment
