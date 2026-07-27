# Startup Order (Example Pattern)

> This document shows the general pattern for dependency-ordered service startup.
> It is an **example only** — adapt to your own infrastructure.

## Layered Startup Sequence

A typical multi-service deployment follows this dependency order:

```
Layer 1:  DNS / Service Discovery
            │
Layer 2:  Cache Layer        [Redis / Memcached]
            │
            ▼
          Primary Database   [PostgreSQL]
            │
            ▼
          Analytics Store    [ClickHouse / TimescaleDB]
            │
            ▼
          Auxiliary Stores   [Neo4j, Qdrant, etc. — optional]
            │
Layer 3:  Object Storage     [MinIO / S3 — optional]
            │
          Event Stream       [Redpanda / Kafka — optional]
            │
Layer 4:  Application Server [FastAPI / Gunicorn]
            │
          Background Workers [Celery / Temporal — optional]
            │
Layer 5:  Monitoring Stack   [Prometheus + Grafana — optional]
```

## Health Check Endpoints

| Endpoint | Type | Purpose |
|----------|------|---------|
| `GET /live` | Liveness | Process is alive (no dependencies checked) |
| `GET /ready` | Readiness | Dependencies reachable |
| `GET /health` | Full health | All configured stores operational |

## Security Considerations

- Containers should run with minimal capabilities (`--cap-drop=ALL`)
- Use `--read-only` root filesystem where possible
- Enable `no-new-privileges` security option
- All inter-service communication should use TLS
- Secrets provided via environment variables or Docker secrets (never baked into images)

## Verification

After deployment, verify in order:
1. All containers/services are running (no restart loops)
2. Core dependencies are reachable (database, cache)
3. Application health endpoint returns 200
4. Security headers are present on responses

---

*This is an example pattern. Your production deployment configuration belongs in a private repository.*
