# RMI Backend — Rug Munch Intelligence

> The intelligence engine behind [rugmunch.io](https://rugmunch.io): scan any
> token or wallet across 21 networks (20 EVM-family chains including Tron, plus
> Solana), detect rugs before
> they happen, and pay per call via x402.

**Docs:** `/docs` (Swagger) | `/redoc` (ReDoc) | `/openapi.json`

## What It Is

FastAPI monolith serving the RMI crypto intelligence platform — **token risk scanning, wallet forensics, market data, Telegram bot, MCP agent tools, and x402-gated premium endpoints** across **21 networks (20 EVM-family chains including Tron, plus Solana)**.

| Interface | Protocol | Description |
|-----------|----------|-------------|
| HTTP API | REST + WebSocket | Public and authenticated endpoints |
| Telegram Bot | Polling/Webhook | Bot commands and alerts |
| MCP Tools | JSON-RPC / SSE | Agent tool interfaces |
| DataBus | Unified data layer | Multi-provider data aggregation |

## Feature Flags

| Feature | Status | Notes |
|---------|--------|-------|
| Token Security Scanning | ✅ Production | Multi-module risk analysis |
| Wallet Forensics | ✅ Production | Address labeling and risk scoring |
| Market Data Pipeline | ✅ Production | Live prices, OHLCV, social |
| AI Reports | ✅ Production | LLM-powered investigation |
| x402 Monetization | ✅ Production | Pay-per-call crypto payments |
| Alert WebSockets | ✅ Production | Real-time threat alerts |
| RAG Search | ✅ Production | Vector + BM25 hybrid |
| DeFi Detection | ✅ Production | MEV, flash loans, oracle |

## Quick Start

```bash
# Clone
git clone https://github.com/Rug-Munch-Media-LLC/rmi-backend.git
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
[Traffic] → Gateway → Application
  ├── Databases: Postgres · ClickHouse · Redis · Neo4j · Qdrant · DuckDB
  ├── Background: Task queue · Workflow engine · Scheduler
  ├── AI: Local LLM · Model router · Tracing
  └── External: 30+ blockchain and data provider APIs
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details.

## API Documentation

| Resource | Location |
|----------|----------|
| Swagger UI | `/docs` (when running) |
| ReDoc | `/redoc` (when running) |
| OpenAPI Spec | `/openapi.json` (when running) |
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
| Multi-source fusion risk scoring | Higher accuracy through ensembling | ADR-004 |
| Redis hot + Postgres cold | Performance vs cost optimization | ADR-003 |

## Project Map

```
app/
├── core/            # Cross-cutting: config, middleware, auth, logging
├── domains/         # Domain modules (DDD): auth, scanner, token, wallet, etc.
├── databus/         # Multi-provider data layer
├── routers/         # Legacy route modules (migrating to domains/)
├── middleware/      # Custom middleware: cost tracking, validation
├── models/          # SQLAlchemy ORM models
├── mcp/             # MCP tool definitions
├── scanners/        # Token risk scan modules
├── security/        # AI guardrails
├── services/        # Business logic services
├── detectors/       # DeFi attack detectors
├── factory.py       # FastAPI app factory
├── mount.py         # Route mounting
├── config.py        # Pydantic settings
└── main.py          # Entry point
```

## Related Repositories

| Repo | Purpose | Status |
|------|---------|--------|
| `rmi-frontend` | React 19 + Vite customer dashboard | Active development |
| `rmi-mcp-x402` | x402 payment-gated MCP server | Active development |
| `rugbrain` | Canonical RAG engine (scam/entity/security intel) | Live |
| `rmi-rag` | Legacy RAG pipeline — **archived**, superseded by `rugbrain` | Archived |
| `rmi-contracts` | Smart contracts for x402 | Audited |
| `fleet-infra` | Infrastructure as code | Production |
| `rmi-local-services` | Local dev environment | Active |
| `walletpress` | Wallet management | Production |

## Deployment

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for full guide.

For self-hosting, build the Docker image and run with Docker Compose.
Refer to `docker-compose.yml` for service configuration. All secrets
should be provided via environment variables or Docker secrets.

## Open-Core Model

This repo follows the open-core model. The publicly visible scanner modules,
API patterns, and database schemas are open source. The core admin backend,
advanced scoring algorithms and production infrastructure remain private.

See [OPEN-CORE.md](OPEN-CORE.md) for details.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and security practices.

## Observability

- **Metrics:** Prometheus endpoint at `/metrics`
- **Traces:** OpenTelemetry
- **Logs:** Structured JSON via structlog
- **Alerts:** Configurable alerting

## License

Source Available — Rug Munch Media LLC. See [LICENSE](LICENSE).

## FAQ

See [docs/FAQ.md](docs/FAQ.md) for frequently asked questions.
