# RMI Backend — Rug Munch Intelligence

**Repo:** `git.rugmunch.io/RugMunchMedia/rmi-backend`
**Deploy:** `/root/backend/` on Talos | **Container:** `rmi-backend`
**Health:** `http://127.0.0.1:8000/health`
**Docs:** `/docs` (Swagger) | `/redoc` (ReDoc) | `/openapi.json`

## What It Is

FastAPI monolith serving the RMI crypto intelligence platform — **token risk scanning, wallet forensics, market data, Telegram bot, MCP agent tools, and x402-gated premium endpoints** across **96+ blockchains**.

| Service | Routes | Protocols | Active Users |
|---------|--------|-----------|-------------|
| HTTP API | 780+ routes | REST + WebSocket + MCP | 1,000+ MAU |
| Telegram Bot | 40+ commands | `@rugmunchbot` | 500+ users |
| MCP Tools | 221 tools | JSON-RPC / SSE | 50+ agents |
| DataBus Chains | 89 providers | Unified data layer | All services |

## Feature Flags

| Feature | Status | Notes |
|---------|--------|-------|
| Token Security Scanning | ✅ Production | 21 SENTINEL modules |
| Wallet Forensics | ✅ Production | 39.4M labeled addresses |
| Market Data Pipeline | ✅ Production | Live prices, OHLCV, social |
| AI Reports | ✅ Production | LLM-powered investigation |
| x402 Monetization | ✅ Production | Pay-per-call crypto payments |
| Alert WebSockets | ✅ Production | Real-time threat alerts |
| RAG Search | ✅ Production | Vector + BM25 hybrid |
| DeFi Detection | ✅ Production | MEV, flash loans, oracle |

## Quick Start

```bash
# Clone
git clone https://git.rugmunch.io/RugMunchMedia/rmi-backend.git
cd rmi-backend

# Local dev
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Run quality gate
make quality

# Run tests
make test          # unit tests
make test-all      # all tests
```

## Architecture (5-minute overview)

```
[Traffic] → nginx:8080 → rmi-backend:8000
  ├── Databases: Postgres · ClickHouse · Redis · Neo4j · Qdrant · DuckDB
  ├── Background: Celery · Temporal · Cron
  ├── AI: Ollama · LiteLLM · Langfuse
  └── External: 30+ provider APIs (Helius, Birdeye, CoinGecko, etc.)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details.

## API Documentation

| Resource | Location |
|----------|----------|
| Swagger UI | `https://api.rugmunch.io/docs` |
| ReDoc | `https://api.rugmunch.io/redoc` |
| OpenAPI Spec | `https://api.rugmunch.io/openapi.json` |
| API Reference | [`docs/API.md`](docs/API.md) |
| x402 API Reference | [`docs/x402_API_REFERENCE.md`](docs/x402_API_REFERENCE.md) |
| MCP User Guide | [`docs/MCP-USER-GUIDE.md`](docs/MCP-USER-GUIDE.md) |
| Tools Reference | [`docs/TOOLS-REFERENCE.md`](docs/TOOLS-REFERENCE.md) |

## Key Design Decisions

| Decision | Rationale | ADR |
|----------|-----------|-----|
| Monolith over microservices | Team size, iteration speed, DB complexity | ADR-002 |
| DataBus as single data layer | Consistent access, monitoring, caching | ADR-001 |
| x402 payment-gated access | Crypto-native monetization | ADR-005 |
| Multi-source fusion risk scoring | Higher accuracy, 92%+ confidence | ADR-004 |
| Redis hot + Postgres cold | Performance vs cost optimization | ADR-003 |

## Project Map

```
app/
├── core/            # Cross-cutting: config, middleware, auth, logging
├── domains/         # Domain modules (DDD): auth, scanner, token, wallet, etc.
├── databus/         # 89 data provider chains
├── routers/         # Legacy route modules (migrating to domains/)
├── middleware/      # Custom middleware: cost tracking, validation
├── models/          # SQLAlchemy ORM models
├── mcp/             # MCP tool definitions
├── scanners/        # 21 SENTINEL scan modules
├── security/        # AI guardrails
├── services/        # Business logic services
├── detectors/       # DeFi attack detectors
├── factory.py       # FastAPI app factory
├── mount.py         # Route mounting (53 routers)
├── config.py        # Pydantic settings (350+ vars)
└── main.py          # Entry point (28 lines, by design)
```

## Related Repositories

| Repo | Purpose | Status |
|------|---------|--------|
| `rmi-frontend` | Vue 3 / Nuxt customer dashboard | Active development |
| `rmi-mcp-x402` | x402 payment-gated MCP server | Active development |
| `rmi-rag` | RAG pipeline + vector store | Active development |
| `rmi-contracts` | Smart contracts for x402 | Audited |
| `fleet-infra` | Infrastructure as code | Production |
| `rmi-local-services` | Local dev environment | Active |
| `walletpress` | Wallet management | Production |

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for full guide.

```bash
# Production deploy on Talos
ssh netcup
cd /srv/work/repos/rmi-backend
docker build -f Dockerfile.multistage -t backend-rmi-backend:latest .
docker compose -f /srv/rmi-infra/docker-compose.backend.yml up -d rmi-backend
```

## Security

See [SECURITY.md](SECURITY.md) for:
- Vulnerability reporting (security@rugmunch.io)
- Our security stack (pre-commit hooks, gitleaks, SAST, dependency scanning)
- Container hardening standards

## Observability

- **Metrics:** Prometheus at `/metrics`
- **Traces:** OpenTelemetry → Langfuse
- **Logs:** Structured JSON via structlog → Loki
- **Alerts:** Grafana dashboards + PagerDuty

## License

Proprietary — Rug Munch Media LLC. See [LICENSE](LICENSE).
