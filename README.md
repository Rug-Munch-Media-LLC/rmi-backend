# rmi-backend

The FastAPI service behind rugmunch.io: it turns a token or wallet address into a
risk verdict, a label set and the evidence rows behind them, and serves the same
data to the web app, the Telegram bot and MCP clients.

**Status:** production · **Licence:** Rug Munch Intelligence Source Available 1.0 — see [LICENSE](LICENSE), this is not an open-source licence.

Canonical: https://git.rugmunch.io/RugMunchMedia/rmi-backend

## Quickstart

Python 3.11+. Postgres and Redis are needed for anything past a bare boot.

```bash
git clone https://git.rugmunch.io/RugMunchMedia/rmi-backend.git
cd rmi-backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set DATABASE_URL, REDIS_URL, JWT_SECRET
uvicorn app.main:app --reload --port 8000
```

`make dev` is the same server with the project's own flags. Swagger sits at
`http://localhost:8000/docs`.

`pip install -r requirements.txt` is the supported install. Do **not** use
`pip install -e .`: `pyproject.toml` pins the scanner engine to a commit SHA in a
private Forgejo repository, so that path needs credentials for `git.rugmunch.io`
and will fail for anyone outside the org.

Compose is the deployment path, not a zero-config demo — it refuses to start
without secrets:

```bash
export DATABASE_PASSWORD=... JWT_SECRET=... REDIS_PASSWORD=...
docker compose up -d
```

## What it does

- **Risk verdicts.** `POST` scan endpoints under `/api/v1` return a score, the
  category breakdown, explicit flags, and the evidence that produced each flag.
- **Wallet and contract forensics.** `/api/v1/address-profiler`,
  `/api/v1/contract-analyzer` and `/api/v1/community-forensics` resolve
  counterparties, deployer history and cluster membership.
- **One data layer.** `app/databus/` fans a request out to a long list of
  third-party providers and self-hosted nodes with per-provider rate limits and
  caching, so a scan is one call from the router's point of view.
- **Agents as clients.** `app/api/v1/mcp/router.py` exposes the same data as MCP
  tools, and `/api/v1/chat` answers from it, so an agent is a client of the API
  rather than a second implementation.
- **Bot endpoints.** `/api/v1/bot` is the route group the Telegram bot calls; the
  bot itself is a separate process in its own repository.

## How it is put together

```
uvicorn app.main:app
  └── app/factory.py:create_app()      app assembly, lifespan, middleware
        ├── app/routers/               ~/api/v1/* route modules
        ├── app/databus/               provider fan-out, cache, rate limits
        ├── app/agents/                tool + agent plumbing
        └── app/adapters/              storage and chain clients
```

`app/main.py` is a thin shim so `uvicorn app.main:app` stays the one launch path.

## Configuration

Everything is environment-driven; `.env.example` is the full list. The ones that
change behaviour:

| Variable | Purpose |
|---|---|
| `ENVIRONMENT` | `development` / `production` switches strictness |
| `DATABASE_URL`, `ALEMBIC_DATABASE_URL` | Postgres, and the same URL for migrations |
| `REDIS_URL`, `REDIS_CACHE_URL` | queue/cache; separate logical DBs recommended |
| `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT` | analytics and label lookups |
| `JWT_SECRET`, `JWT_EXPIRE_MINUTES` | auth tokens |
| `CORS_ORIGINS` | comma-separated allowlist, no wildcard in production |
| `RATE_LIMIT_PER_MINUTE` | default per-key limit |
| `EMBEDDER_URL` | embedding lane used by retrieval routes |
| `OPENROUTER_API_KEY`, `LITELLM_API_BASE` | optional LLM lanes for AI routes |
| `PORT` | uvicorn port, default 8000 |

## Known limits

- The scanner engine is a private dependency. The API layer is readable; the
  scoring logic is not, and the vendored copy is pinned by SHA for that reason.
- Startup needs Postgres and Redis. There is no in-memory test mode that gives
  you a working `/docs` page with real data.
- `make test` covers units and needs no external services; `make test-all` needs
  the full stack.

## Development

```bash
make install     # dependencies
make lint        # ruff
make typecheck   # mypy
make test        # pytest with coverage
make quality     # lint + typecheck + security + test
```

## Licence

Rug Munch Intelligence Source Available License 1.0. Read [LICENSE](LICENSE)
before you plan to use this in anything you distribute.
