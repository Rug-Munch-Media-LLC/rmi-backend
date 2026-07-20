# RMI Backend

**Repo:** `git.rugmunch.io/RugMunchMedia/rmi-backend`
**Deploy:** `/root/backend/` on Talos
**Container:** `rmi-backend`
**Health:** `http://127.0.0.1:8000/health` (currently unhealthy — Redis host not resolving)

## What it is
FastAPI backend for the RMI platform. Serves token risk scans, wallet forensics, market data, Telegram bot commands, MCP agent tools, and x402-gated premium endpoints.

## Status
Production, but degraded. The container runs and responds, but `/health` reports unhealthy because the Redis hostname `rmi-redis:6379` is not resolving inside the container network.

## Quick start (local)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/unit/ -x -q
```

## Deploy
```bash
ssh netcup
cd /root/backend/
docker compose pull
docker compose up -d rmi-backend
```

## Fleet dependencies
| Service | Address | Status |
|---|---|---|
| Postgres | `rmi-postgres:5432` | Up |
| Redis | `rmi-redis:6379` | Failing to resolve |
| Qdrant | `127.0.0.1:6333` | Down (stale IP bind) |
| Neo4j | `127.0.0.1:7687` | Up |
| ClickHouse | `127.0.0.1:8123` | Up |

## Current issues
- 8.61% test coverage
- 17 `time.sleep()`, 1,713 f-string logs, 399 `print()` calls
- Largest file `app/routers/x402_tools.py` is 212 KB
- CI gates use `continue-on-error: true` and `|| true`
- 5 Telegram tokens leaked in git history

## Docs
- `PRODUCT.md` — charter and scope
- `ARCHITECTURE.md` — components and data flow
- `ROADMAP.md` — next 90 days
- `TODO.md` — current tasks
- `docs/adr/` — Architecture Decision Records (6 ADRs covering DataBus, fusion, x402, CI gates)
- `docs/CODING_STANDARDS.md` — Python/TS coding standards, testing rules, git conventions
- `CONTRIBUTING.md` — how to contribute, development flow, quality gate

## Standards
This repo follows [Rug Munch Media standards](https://git.rugmunch.io/RugMunchMedia/standards).
