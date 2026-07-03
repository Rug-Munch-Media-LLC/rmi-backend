# RMI Backend — Product Charter

**Product:** Rug Munch Intelligence (RMI) Backend
**Repo:** `git.rugmunch.io/RugMunchMedia/rmi-backend`
**Deploy path:** `/root/backend/` on Talos
**Container:** `rmi-backend`
**Health:** `http://127.0.0.1:8000/health`
**Status:** Production / degraded

## Problem
Crypto users and platforms need a single, fast, machine-readable source of token risk, wallet labels, and on-chain intelligence. Existing tools are fragmented, expensive, or closed.

## User
- Telegram users via `@rugmunchbot`
- RMI web frontend at `https://rugmunch.io`
- AI agents via MCP
- API consumers via x402 pay-per-call

## Scope
- FastAPI backend with token scanner, wallet forensics, market data, alerts
- DataBus: unified data plane across 30+ providers
- Telegram bot commands: `/scan`, `/portfolio`, `/simulate`, `/chart`, `/watch`
- MCP server for agent tool calling
- x402 payment gating for premium endpoints

## Anti-scope
- No frontend code (that lives in `rmi-frontend`)
- No wallet generation (that lives in `walletpress`)
- No web scraping engine (that lives in `pryscraper`)
- No social protocol clients (that lives in `degenfeed-web`)

## Success criteria
- `/health` returns 200
- CI gates are authoritative
- Test coverage >60%
- All MCP tools have Pydantic input/output schemas
- No production file >500 lines

## Current reality
- 990 routes across modular routers
- Health is **unhealthy** because `rmi-redis:6379` does not resolve
- 8.61% test coverage
- 17 `time.sleep()`, 1,713 f-string logs, 399 `print()` calls
- Largest file `app/routers/x402_tools.py` is 212 KB

## Fleet dependencies
- Postgres `rmi-postgres:5432`
- Redis `rmi-redis:6379` (currently failing)
- Qdrant `127.0.0.1:6333` (currently down due to stale IP bind)
- Neo4j `127.0.0.1:7687`
- ClickHouse `127.0.0.1:8123`

## Standards
This repo follows [Rug Munch Media standards](https://git.rugmunch.io/RugMunchMedia/standards).
