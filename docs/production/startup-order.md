# Production Startup Order — rmi-backend

> Strict dependency ordering for production deployments.
> Applies to: Docker Compose, Docker Swarm, bare-metal systemd.

## Layered Startup Sequence

```
Layer 1:  DNS                     [Docker DNS / systemd-resolved]
            │
Layer 2:  Redis                   [Cache + rate-limiter state]
            │
            ▼
          PostgreSQL              [Primary data store]
            │
            ▼
          ClickHouse              [Analytics / time-series]
            │
            ▼
          Neo4j / Memgraph        [Knowledge graph — optional]
            │
            ▼
          Qdrant                  [Vector store — optional]
            │
Layer 3:  MinIO                   [S3-compatible storage — optional]
            │
          Redpanda                [Event streaming — optional]
            │
Layer 4:  rmi-backend (web)       [FastAPI / Gunicorn]
            │
          rmi-worker (temporal)   [Background worker — optional]
            │
Layer 5:  Prometheus + Grafana   [Monitoring — optional]
```

## Docker Compose (recommended)

```bash
# 1. Set environment
cp .env.production.example .env
# Edit .env with real secrets from gopass

# 2. Deploy with security hardening
docker compose --env-file .env up -d

# 3. Verify each layer
curl -f http://localhost:6379/ && echo "Redis OK"
pg_isready -h localhost -U rmi -d rmi && echo "PostgreSQL OK"
curl -sf http://localhost:8123/ping && echo "ClickHouse OK"

# 4. Verify application health
curl -f http://localhost:8000/live     # liveness (process alive)
curl -f http://localhost:8000/ready    # readiness (deps reachable)
curl -f http://localhost:8000/health   # full health (all stores)
```

## Docker Run (without Compose)

```bash
# Layer 2: Databases
docker run -d --name rmi-redis \
  --cap-drop=ALL --cap-add=SETGID,SETUID \
  --security-opt=no-new-privileges \
  redis:7-alpine redis-server --requirepass "${REDIS_PASSWORD}" --appendonly yes

docker run -d --name rmi-postgres \
  --cap-drop=ALL --cap-add=CHOWN,DAC_OVERRIDE,SETGID,SETUID \
  --security-opt=no-new-privileges \
  -e POSTGRES_PASSWORD="${DATABASE_PASSWORD}" postgres:16-alpine

# Layer 4: Application
docker run -d --name rmi-backend \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /tmp \
  --tmpfs /dev/shm \
  -p 8000:8000 \
  rmi-backend:latest
```

## Security Flags

Every container MUST run with these flags unless it explicitly needs capabilities:

| Flag | Purpose | Exceptions |
|------|---------|-----------|
| `--read-only` | Read-only root filesystem | Services that write to local disk (PostgreSQL, Redis with AOF) mount volumes |
| `--cap-drop=ALL` | Remove ALL Linux capabilities | Only add back what's strictly needed |
| `--security-opt=no-new-privileges` | Prevent privilege escalation | None |
| `--tmpfs /tmp` | Writable temp directory | Use fixed size: `--tmpfs /tmp:size=100M` |
| `--tmpfs /dev/shm` | Shared memory for gunicorn workers | Size via `--tmpfs /dev/shm:size=256M` |

## Health Check Endpoints

| Endpoint | Type | Checks | Response |
|----------|------|--------|----------|
| `GET /live` | Liveness | Process alive, no deps | 200: `{"status":"alive"}` |
| `GET /ready` | Readiness | Redis + PostgreSQL reachable | 200: `{"status":"ready"}`, 503: `{"status":"not_ready"}` |
| `GET /health` | Full health | All configured stores | 200/503 with per-store status |

## Retry Configuration

The docker-compose.yml uses `depends_on` with `condition: service_healthy` for
hard dependencies. Each service has:
- `start_period`: grace time before health checks begin
- `interval`: how often to check
- `timeout`: max time per check
- `retries`: consecutive failures before marking unhealthy

See the `docker-compose.yml` for exact values per service.

## Verification Checklist

After deployment, run:

```bash
# 1. All containers running
docker compose ps

# 2. No containers restarting
docker compose ps | grep -c "Restarting" || echo "0 restarting — good"

# 3. Backend liveness + readiness
curl -sf http://localhost:8000/live && echo "LIVENESS OK"
curl -sf http://localhost:8000/ready && echo "READINESS OK"

# 4. Full health
curl -s http://localhost:8000/health | python3 -m json.tool

# 5. Rate limiting active
curl -sI http://localhost:8000/health | grep -i x-ratelimit

# 6. Security headers
curl -sI http://localhost:8000/ | grep -iE "x-content-type-options|x-frame-options|strict-transport-security"

# 7. Prometheus metrics
curl -s http://localhost:8000/metrics | head -20
```
