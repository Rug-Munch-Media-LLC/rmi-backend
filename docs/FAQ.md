# RMI Backend — FAQ

**What is this?** The reference backend of the Rug Munch Intelligence platform: FastAPI crypto-intelligence API — token scanning, wallet intelligence, market data via pluggable DataBus providers.

**Is this the production codebase?** It is the open reference implementation. The managed platform adds advanced detection engines and operational infrastructure on top.

**What databases does it need?** Postgres (primary), Redis (cache/queues); optional Neo4j, Qdrant, ClickHouse for graph/vector/analytics features.

**How do I configure providers?** Every DataBus provider reads keys from environment variables at runtime. Copy `.env.example` names into your secret store — never commit credentials.

**How do I add a data provider?** Implement the provider interface under `app/databus/`, register it, add contract tests. Free-tier-first ordering is enforced by the unified provider.

**How do I run tests?** `make test` (pytest). Lint: `make lint` (ruff + mypy strict).

**Scaling?** Stateless API pods behind a LB; heavy jobs go through the workers queue. See docs/ for the deployment guide.
