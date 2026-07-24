# RMI Backend — Design Patterns & Architecture Guide

> **Version:** 2026.07.24 | **Last updated:** 2026-07-24  
> **Audience:** All developers working on the RMI backend  
> **Purpose:** Document architectural patterns, conventions, anti-patterns, and
> the rules that keep the codebase maintainable as it grows.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Layer Architecture](#2-layer-architecture)
3. [Domain-Driven Design](#3-domain-driven-design)
4. [Event-Driven Architecture](#4-event-driven-architecture)
5. [CQRS Pattern](#5-cqrs-pattern)
6. [Repository Pattern](#6-repository-pattern)
7. [Service Layer Pattern](#7-service-layer-pattern)
8. [Dependency Injection](#8-dependency-injection)
9. [Cross-Cutting Concerns](#9-cross-cutting-concerns)
10. [Anti-Patterns — What NOT to Do](#10-anti-patterns--what-not-to-do)
11. [Code Examples](#11-code-examples)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      API Layer (FastAPI)                      │
│  Routers → Depends(get_service) → domain.service.method()    │
├──────────────────────────────────────────────────────────────┤
│                   Application Layer                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐ │
│  │ Services  │  │ CQRS     │  │ EventBus │  │ Background   │ │
│  │ (BizLogic)│  │ Mediator │  │ pub/sub  │  │ Workers      │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘ │
├──────────────────────────────────────────────────────────────┤
│                   Domain Layer                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐         │
│  │ domain/     │  │ domain/     │  │ domain/      │  ...     │
│  │ token/      │  │ wallet/     │  │ scanner/     │         │
│  │  ├ models   │  │  ├ models   │  │  ├ models    │         │
│  │  ├ repo     │  │  ├ repo     │  │  ├ repo      │         │
│  │  └ service  │  │  └ service  │  │  └ service   │         │
│  └─────────────┘  └─────────────┘  └──────────────┘         │
├──────────────────────────────────────────────────────────────┤
│                   Core Layer (Cross-Cutting)                   │
│  events.py  cqrs.py  errors.py  logging.py  redis.py  auth   │
│  database.py  rate_limiter.py  tracing.py  middleware.py     │
├──────────────────────────────────────────────────────────────┤
│                   Infrastructure Layer                         │
│  PostgreSQL  ClickHouse  Neo4j  Redis  Qdrant  MinIO         │
└──────────────────────────────────────────────────────────────┘
```

**Key principle:** Dependencies point INWARD. Routers depend on services.
Services depend on repositories. The core layer has NO dependencies on
business domains.

---

## 2. Layer Architecture

### 2.1 The Dependency Rule

Source code dependencies MUST point INWARD. Nothing in the inner (core)
circle can know about something in the outer circle.

```
Routers → Services → Repositories → Core
                                    ↑
                              NO dependencies on domains/routers
```

### 2.2 Layer Responsibilities

| Layer | What Goes Here | What NEVER Goes Here |
|-------|---------------|---------------------|
| **Routers** (`app/routers/`, `app/api/`) | HTTP verbs, path params, query params, Depends injection, response serialization | Business logic, raw SQL, direct Redis calls, imports from other routers |
| **Domains** (`app/domains/*/`) | Models, services, repositories — pure business logic | FastAPI imports, HTTP concerns, imports from routers |
| **Services** (`app/domains/*/service.py`) | Business logic, orchestration, validation, event publishing | Database details, network calls, HTTP response handling |
| **Repositories** (`app/domains/*/repository.py`) | Data access, queries, CRUD, serialization to/from models | Business rules, validation, event publishing |
| **Core** (`app/core/`) | Auth, errors, logging, redis, events, config, DI | Any business domain import, domain model references |
| **Config** (`app/config.py`) | Environment parsing, typed settings | Runtime logic, business rules |

### 2.3 Module Layout per Domain

Every domain follows this standard layout:

```
domains/<name>/
├── __init__.py      # Public API, re-exports, health check registration
├── models.py        # Pydantic models for the domain
├── repository.py    # Data access (thin, no business logic)
└── service.py       # Business logic, orchestration, event publishing
```

Additional files allowed:
- `analyzer.py` — domain-specific analysis/heuristics
- `broadcaster.py` — WebSocket push helpers
- `router.py` — API routes (only if small domain, else in `app/routers/`)

---

## 3. Domain-Driven Design

### 3.1 Domain Isolation

Domains MUST NOT import from each other directly. Cross-domain communication
happens ONLY through:
1. **Event Bus** (`app.core.events`) — async pub/sub for state changes
2. **Dependency injection** — a service depends on another service's interface
3. **Shared core types** — models in `app.models` or `app.core`

### 3.2 Domain Boundaries — Rules

```python
# ✅ GOOD: Domain imports only core + its own modules
from app.core.base_service import BaseService
from app.core.logging import get_logger
from app.domains.token.models import TokenDetail
from app.domains.token.repository import TokenRepository

# ❌ BAD: Domain importing from another domain's internals
from app.domains.wallet.service import WalletService  # CROSS-DOMAIN COUPLING

# ✅ BETTER: Cross-domain via event bus
from app.core.events import get_event_bus, WalletUpdated

bus = get_event_bus()
await bus.publish(WalletUpdated(wallet_address=addr, change_type="label"))

# ❌ BAD: Domain importing from routers
from app.routers.x402_enforcement import TOOL_PRICES  # LAYER VIOLATION

# ✅ CORRECT: Move shared constants to a config or core module
from app.core.config import TOOL_PRICES
```

### 3.3 Identified Cross-Domain Violations

The following cross-domain imports were detected and SHOULD be refactored:

| Source | Imports From | Problem | Fix |
|--------|-------------|---------|-----|
| `domains/billing/x402/` | `routers.x402_enforcement` | Domain depends on router | Move `TOOL_PRICES`, `get_redis`, `check_trial` to `core` or a shared domain module |
| `domains/billing/x402/` | `routers._expanded_tools` | Domain depends on router | Move `ADDITIONAL_TOOLS` to a config/registry module |
| `domains/x402/` | `routers.x402_enforcement` | Domain depends on router | Move `verify_self_payment` into domain service |
| `domains/billing/x402/` | `routers.x402_dashboard` | Domain depends on router | Move `_persist_payment_to_supabase` into billing domain |

**Rule:** If two modules need the same constant, put it in the lowest layer
both can import from — usually `app/core/config.py` or a `domains/*/constants.py`.

---

## 4. Event-Driven Architecture

### 4.1 Motivation

Services used to call each other directly:
```python
# OLD: Direct coupling
class TokenService:
    async def scan(self, req):
        result = await self._analyzer.analyze(req)
        await alert_service.fire_event(...)      # tight coupling
        await news_service.notify(...)            # tight coupling
```

This created hidden dependencies and made testing difficult. The event bus
decouples producers from consumers:

```python
# NEW: Event-driven
class TokenService:
    async def scan(self, req):
        result = await self._analyzer.analyze(req)
        await get_event_bus().publish(
            ScanCompleted(
                token_address=req.address,
                chain=req.chain,
                scan_id=result.id,
                safety_score=result.safety_score,
            )
        )
        # Who listens? The TokenService doesn't care.
        # AlertService, NewsService, WebSocket all subscribe independently.
```

### 4.2 Standard Event Types

All events live in `app/core/events.py` and inherit from `BaseEvent`:

| Event | When Published | Payload |
|-------|---------------|---------|
| `ScanCompleted` | Token scan finishes | `token_address`, `chain`, `scan_id`, `safety_score`, `risk_level`, `risk_flags`, `modules_run` |
| `WalletUpdated` | Wallet data changes significantly | `wallet_address`, `change_type` (balance/label/cluster/portfolio), `diff` |
| `NewsIngested` | News article ingested & clustered | `article_id`, `source`, `title`, `entities`, `cluster_id` |
| `AlertTriggered` | Alert condition met | `alert_id`, `token_address`, `event_type`, `severity`, `payload` |
| `PaymentReceived` | x402 payment confirmed | `payment_id`, `chain`, `amount_usd`, `payer_address`, `tool_id` |

### 4.3 How to Use the Event Bus

**Publishing an event:**
```python
from app.core.events import get_event_bus, ScanCompleted

bus = get_event_bus()
await bus.publish(
    ScanCompleted(
        token_address="0x...",
        chain="solana",
        scan_id="scan_123",
        safety_score=85.0,
    )
)
```

**Subscribing to an event (at startup):**
```python
# In app/lifespan.py or a domain's startup hook
from app.core.events import get_event_bus, ScanCompleted

bus = get_event_bus()

async def on_scan_completed(event: ScanCompleted) -> None:
    """Notify WebSocket clients when a scan finishes."""
    await ws_manager.broadcast_scan(event.model_dump())

bus.subscribe(ScanCompleted, on_scan_completed)
```

**Registering core subscribers:**
```python
# In app/lifespan.py — already wired
from app.core.events import register_core_subscribers
register_core_subscribers()  # logs all events, ready for extension
```

### 4.4 When to Use Events vs Direct Calls

| Use Events | Use Direct Calls |
|-----------|-----------------|
| "When X happens, do Y" (fire-and-forget) | "I need the result of X to continue" |
| Multiple unrelated consumers | Single synchronous operation |
| Cross-domain notifications | Intra-domain orchestration |
| Broadcasting to WebSocket clients | Return value required by caller |

### 4.5 Event Rules — DOs and DON'Ts

**DO:**
- Publish events at the **end** of a service method, after all side effects succeed
- Use typed event classes (not raw dicts)
- Catch exceptions in subscribers — one subscriber failure must NOT block others
- Document what events a domain publishes in its `__init__.py`

**DON'T:**
- Publish events from repositories (that's the service's job)
- Subscribe to events in router modules (use lifespan hooks)
- Send sensitive data (passwords, API keys) in event payloads
- Publish events synchronously (always `await`)
- Rely on events for ACID transactions (events are at-most-once delivery)

### 4.6 Transport

Events use **Redis pub/sub** under the hood:
- Channel format: `rmi:event:{EventTypeName}`
- Serialization: JSON via `dataclasses.asdict()`
- Redis is optional — subscribers are called in-process regardless
- Cross-process listeners use `listen_forever()` coroutines (long-lived Redis
  connections, typically in worker processes)

---

## 5. CQRS Pattern

### 5.1 Motivation

CQRS separates operations that **change state** (commands) from operations
that **read state** (queries). This makes the system easier to reason about.

Use CQRS when:
- The operation has complex validation, multiple side effects, or audit requirements
- The read path and write path have different performance characteristics
- The operation needs to publish events after completion
- Simple CRUD through `BaseService` is insufficient

### 5.2 When to Use CQRS vs BaseService

| Pattern | When to Use | Example |
|---------|------------|---------|
| `BaseService` | Simple CRUD, thin business logic | Get token detail, list alerts, create label |
| CQRS Command | Complex write with side effects | Rescan token (triggers modules, publishes events, logs audit) |
| CQRS Query | Complex read with custom projection | Get wallet summary (multiple sources) |

### 5.3 Command Pattern

```python
from dataclasses import dataclass
from app.core.cqrs import Command, CommandResult, CommandHandler, get_mediator

# 1. Define the command
@dataclass
class RescanTokenCommand(Command):
    token_address: str
    chain: str
    force: bool = False

# 2. Define the handler
class RescanTokenHandler(CommandHandler[RescanTokenCommand]):
    def __init__(self, token_service: TokenService, event_bus: EventBus):
        self._token_service = token_service
        self._event_bus = event_bus

    async def handle(self, command: RescanTokenCommand) -> CommandResult:
        try:
            result = await self._token_service.scan(
                TokenScanRequest(
                    address=command.token_address,
                    chain=command.chain,
                )
            )
            await self._event_bus.publish(
                ScanCompleted(
                    token_address=command.token_address,
                    chain=command.chain,
                    scan_id=result.id,
                    safety_score=result.safety_score,
                )
            )
            return CommandResult(success=True, result={"scan_id": result.id})
        except Exception as e:
            return CommandResult(success=False, error=str(e))

# 3. Register at startup
get_mediator().register_command(RescanTokenCommand, RescanTokenHandler(token_service, event_bus))

# 4. Execute from anywhere
result = await get_mediator().execute(
    RescanTokenCommand(token_address="0x...", chain="solana")
)
```

### 5.4 Query Pattern

```python
from dataclasses import dataclass, field
from app.core.cqrs import Query, QueryHandler, get_mediator

@dataclass
class WalletSummaryQuery(Query):
    wallet_address: str
    include_portfolio: bool = True
    include_labels: bool = True
    include_clusters: bool = False

class WalletSummaryHandler(QueryHandler[WalletSummaryQuery, dict]):
    async def handle(self, query: WalletSummaryQuery) -> dict:
        result = {}
        if query.include_portfolio:
            result["portfolio"] = await portfolio_service.get(query.wallet_address)
        if query.include_labels:
            result["labels"] = await label_service.get_labels(query.wallet_address)
        return result

# Register + use
get_mediator().register_query(WalletSummaryQuery, WalletSummaryHandler())
summary = await get_mediator().fetch(
    WalletSummaryQuery(wallet_address="0x...")
)
```

### 5.5 CQRS Rules

**DO:**
- Keep handlers stateless (use constructor injection for dependencies)
- Return `CommandResult` from commands, typed data from queries
- Register handlers at startup, not in router modules
- Use commands for operations that publish events

**DON'T:**
- Put business logic in the command/query dataclass itself
- Use CQRS for trivial CRUD (use `BaseService`)
- Return domain models from commands (commands return success/failure)
- Mix commands and queries in the same handler

---

## 6. Repository Pattern

### 6.1 Motivation

Repositories abstract data access behind a clean interface. Services never
touch Redis, Postgres, or ClickHouse directly — they call repository methods.

### 6.2 Repository Structure

```python
# domains/token/repository.py
class TokenRepository:
    """Pure data access. No business logic. No FastAPI imports."""

    async def get_detail(self, address: str, chain: str) -> TokenDetail:
        """Fetch token metadata via Databus."""
        ...

    async def get_holders(self, address: str, chain: str, limit: int = 100) -> list[TokenHolder]:
        """Fetch top holders."""
        ...

    async def get_liquidity(self, address: str, chain: str) -> TokenLiquidity:
        """Fetch liquidity + pool info."""
        ...
```

### 6.3 Repository Rules

**✅ Repository is the ONLY place for data access code.**
```python
# GOOD: Service calls repository
class TokenService:
    async def get_detail(self, address, chain):
        return await self._repo.get_detail(address, chain)

# BAD: Service talks directly to Redis/Databus
class TokenService:
    async def get_detail(self, address, chain):
        client = get_databus_client()  # WRONG — this belongs in the repo
        return await client.get_token_info(address, chain=chain)
```

**✅ Raw SQL belongs in repositories, NOT in routers.**
```python
# GOOD: Repository encapsulates the query
class ScanResultRepository:
    async def get_recent(self, limit: int = 50) -> list[ScanResult]:
        async with get_db() as db:
            result = await db.execute(
                text("SELECT * FROM scan_results ORDER BY created_at DESC LIMIT :limit"),
                {"limit": limit},
            )
            return [ScanResult(**row) for row in result.mappings()]

# ❌ BAD: Router calls ClickHouse directly
@app.get("/scans")
async def list_scans():
    rows = ch.execute("SELECT * FROM scan_results LIMIT 50")  # LAYER VIOLATION
    return rows
```

**✅ Repositories return domain models, not raw dicts.**
```python
# GOOD
async def get_detail(self, address: str, chain: str) -> TokenDetail:

# BAD
async def get_detail(self, address: str, chain: str) -> dict[str, Any]:
```

### 6.4 Repository Anti-Patterns

| Anti-Pattern | Why It's Wrong | Fix |
|-------------|---------------|-----|
| Repository calls another repository | Creates circular dependencies | Service orchestrates multiple repos |
| Repository publishes events | Repos are data access, not business logic | Service publishes after repo returns |
| Repository does validation | Validation is business logic, belongs in service | Use Pydantic models in service layer |
| Router calls repository directly | Bypasses service layer, duplicates logic | Router calls service, service calls repo |

---

## 7. Service Layer Pattern

### 7.1 BaseService

All domain services SHOULD inherit from `BaseService[T]`:

```python
from app.core.base_service import BaseService
from app.core.logging import get_logger

log = get_logger(__name__)

class MyDomainService(BaseService[MyModel]):
    """Business logic for MyDomain."""

    def __init__(self, repo: MyRepository | None = None) -> None:
        self._repo = repo or MyRepository()

    async def list(self, **filters) -> list[MyModel]:
        return await self._repo.list_all()

    async def get(self, id_: str, **kwargs) -> MyModel | None:
        return await self._repo.get(id_)
```

### 7.2 Service Responsibilities

1. **Orchestration** — call repository methods in the right order
2. **Validation** — check business rules before calling repositories
3. **Event publishing** — publish events after successful mutations
4. **Logging** — log business-relevant operations
5. **Error handling** — raise `AppError` subclasses for business rule violations

### 7.3 Service Rules

**DO:**
- Accept dependencies via constructor injection (optional, with defaults)
- Use `Depends(get_service)` in routers for DI
- Log entry and exit of significant operations
- Publish events at the END of the method, after all side effects succeed
- Raise typed errors (`NotFoundError`, `ValidationError`, `ConflictError`)

**DON'T:**
- Import FastAPI in services
- Access `request` objects
- Handle HTTP concerns (cookies, headers, status codes)
- Call external APIs directly (use repositories)
- Use global state (pass dependencies explicitly)

---

## 8. Dependency Injection

### 8.1 Central DI Hub

All FastAPI dependencies are declared in `app/core/dependencies.py`:

```python
# app/core/dependencies.py
from app.core.logging import get_logger
from app.config import Settings, get_settings as _get_settings

def get_settings() -> Settings:
    return _get_settings()

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    from app.core.database import get_db as _get_db
    async for session in _get_db():
        yield session
```

### 8.2 Usage in Routers

```python
from app.core import get_settings, get_db
from app.config import Settings
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

@router.get("/example")
async def example(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
):
    ...
```

### 8.3 Override in Tests

```python
async def mock_get_db():
    yield test_session

app.dependency_overrides[get_db] = mock_get_db
```

---

## 9. Cross-Cutting Concerns

### 9.1 Logging (`app/core/logging.py`)

Use `structlog` with structured keys:
```python
from app.core.logging import get_logger

log = get_logger(__name__)
log.info("scan_completed", token=address[:12], score=result.safety_score)
```

### 9.2 Errors (`app/core/errors.py`)

Use the `AppError` hierarchy for business logic errors:
```python
from app.core.errors import NotFoundError, ValidationError, ConflictError

raise NotFoundError(f"Token {address} not found")
raise ValidationError("Invalid chain parameter")
raise ConflictError("Alert subscription already exists")
```

### 9.3 Event Bus (`app/core/events.py`)

See [§4 Event-Driven Architecture](#4-event-driven-architecture).

### 9.4 CQRS (`app/core/cqrs.py`)

See [§5 CQRS Pattern](#5-cqrs-pattern).

### 9.5 Health Checks

Each domain registers a health check in its `__init__.py`:
```python
from app.core import health as health_mod

async def _health_check() -> DomainHealth:
    ...

health_mod.register_health_check("my_domain", _health_check)
```

---

## 10. Anti-Patterns — What NOT to Do

These are the most common violations found in the codebase. NEVER do these.

### 10.1 ❌ Domain Imports a Router

```python
# WRONG — in domains/billing/x402/tier_resolver.py
from app.routers.developer_tier import check_developer_key as _check
```

**Why:** Routers are the outermost layer. Domains are inner layers.
Dependencies point INWARD only.

**Fix:** Move `check_developer_key` to a domain or core service. The
router should import from the domain, not vice versa.

### 10.2 ❌ Raw SQL in Routers

```python
# WRONG — in routers/chat.py
conn.execute("CREATE TABLE chat_messages (...)")
rows = conn.execute("SELECT * FROM chat_messages WHERE user_id = ?", (user_id,))
```

**Why:** Routers handle HTTP concerns. Data access belongs in repositories.

**Fix:** Create a repository class and move all SQL there. The router
calls `repository.list_by_user(user_id)`.

### 10.3 ❌ Services Calling Each Other Directly

```python
# WRONG — tight coupling
class TokenService:
    async def scan(self, req):
        ...
        await alert_service.fire_event(...)
        await news_service.notify(...)
```

**Why:** Creates hidden dependencies. Hard to test. Hard to change.

**Fix:** Publish events instead. Let other services subscribe independently.

### 10.4 ❌ Global Singletons Accessed Directly

```python
# WRONG
from app.core.redis import get_redis_async
r = get_redis_async()
await r.set("key", "value")

# BETTER — use Depends
@router.get("/example")
async def example(r: Any = Depends(get_redis)):
    await r.set("key", "value")
```

**Why:** Direct access makes testing impossible. `Depends` allows override.

### 10.5 ❌ Business Logic in Models

```python
# WRONG
class TokenDetail(BaseModel):
    name: str
    symbol: str

    def compute_risk_score(self) -> float:  # Business logic in model!
        ...
```

**Why:** Models represent data shape. Business logic belongs in services
or dedicated analyzers.

### 10.6 ❌ Using `os.getenv` in Non-Config Code

```python
# WRONG — in a service module
import os
api_key = os.getenv("API_KEY", "")
```

**Why:** Config is centralized in `app/config.py`. Scattered env reads
make configuration impossible to audit.

**Fix:** Import `from app.config import settings` and use `settings.api_key`.

### 10.7 ❌ Catching Bare `Exception`

```python
# WRONG
try:
    result = await api_call()
except Exception:
    pass  # Swallows everything silently

# BETTER — be specific
try:
    result = await api_call()
except (httpx.HTTPError, ConnectionError) as e:
    log.warning("api_call_failed", error=str(e))
    return fallback_value
```

### 10.8 ❌ Router Calls Repository Directly

```python
# WRONG — in router
from app.domains.token.repository import TokenRepository

repo = TokenRepository()
detail = await repo.get_detail(address, chain)
```

**Why:** Bypasses the service layer where business logic, validation,
and event publishing live.

**Fix:** Always go through the service.

---

## 11. Code Examples

### 11.1 Complete Domain Module

```python
# domains/widget/models.py
from pydantic import BaseModel, Field

class Widget(BaseModel):
    id: str
    name: str
    color: str
    size: int = Field(ge=0, le=100)

class CreateWidgetRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="blue")
    size: int = Field(default=50, ge=0, le=100)
```

```python
# domains/widget/repository.py
from app.core.logging import get_logger
from app.domains.widget.models import Widget

log = get_logger(__name__)

class WidgetRepository:
    """In-memory widget storage. Replace with real DB in production."""

    def __init__(self) -> None:
        self._widgets: dict[str, Widget] = {}

    async def list_all(self) -> list[Widget]:
        return list(self._widgets.values())

    async def get(self, widget_id: str) -> Widget | None:
        return self._widgets.get(widget_id)

    async def create(self, widget: Widget) -> None:
        self._widgets[widget.id] = widget

    async def delete(self, widget_id: str) -> bool:
        return self._widgets.pop(widget_id, None) is not None
```

```python
# domains/widget/service.py
from datetime import UTC, datetime

from app.core.base_service import BaseService
from app.core.events import get_event_bus
from app.core.logging import get_logger
from app.domains.widget.models import Widget, CreateWidgetRequest
from app.domains.widget.repository import WidgetRepository

log = get_logger(__name__)

class WidgetService(BaseService[Widget]):
    def __init__(self, repo: WidgetRepository | None = None) -> None:
        self._repo = repo or WidgetRepository()

    async def list(self, **filters) -> list[Widget]:
        return await self._repo.list_all()

    async def get(self, id_: str, **kwargs) -> Widget | None:
        return await self._repo.get(id_)

    async def create(self, data: CreateWidgetRequest, **kwargs) -> Widget:
        widget = Widget(
            id=f"wgt:{int(datetime.now(UTC).timestamp())}",
            name=data.name,
            color=data.color,
            size=data.size,
        )
        await self._repo.create(widget)
        log.info("widget_created", widget_id=widget.id, name=widget.name)
        return widget

    async def delete(self, id_: str, **kwargs) -> bool:
        return await self._repo.delete(id_)
```

### 11.2 Event Bus — Full Lifecycle

```python
# 1. Define event (in app/core/events.py or your domain)
from app.core.events import BaseEvent
from dataclasses import dataclass

@dataclass
class WidgetCreated(BaseEvent):
    widget_id: str
    name: str
    color: str

# 2. Publish (in domain service)
from app.core.events import get_event_bus

await get_event_bus().publish(
    WidgetCreated(widget_id=widget.id, name=widget.name, color=widget.color)
)

# 3. Subscribe (in app/lifespan.py or domain __init__.py)
from app.core.events import get_event_bus, WidgetCreated

async def on_widget_created(event: WidgetCreated) -> None:
    log.info("widget_created_event", widget_id=event.widget_id)

get_event_bus().subscribe(WidgetCreated, on_widget_created)
```

### 11.3 CQRS — Full Lifecycle

```python
# 1. Define command + handler
from dataclasses import dataclass
from app.core.cqrs import Command, CommandResult, CommandHandler

@dataclass
class DeleteWidgetCommand(Command):
    widget_id: str

class DeleteWidgetHandler(CommandHandler[DeleteWidgetCommand]):
    async def handle(self, command: DeleteWidgetCommand) -> CommandResult:
        try:
            deleted = await widget_service.delete(command.widget_id)
            if deleted:
                return CommandResult(success=True)
            return CommandResult(success=False, error="Widget not found")
        except Exception as e:
            return CommandResult(success=False, error=str(e))

# 2. Register
from app.core.cqrs import get_mediator
get_mediator().register_command(DeleteWidgetCommand, DeleteWidgetHandler())

# 3. Execute
result = await get_mediator().execute(DeleteWidgetCommand(widget_id="wgt:123"))
```

### 11.4 Router Wiring (Correct)

```python
# routers/widgets.py
from fastapi import APIRouter, Depends
from app.domains.widget.models import Widget, CreateWidgetRequest
from app.domains.widget.service import WidgetService

router = APIRouter(prefix="/widgets", tags=["widgets"])

async def get_widget_service() -> WidgetService:
    """Factory for dependency injection."""
    return WidgetService()

@router.get("/")
async def list_widgets(
    service: WidgetService = Depends(get_widget_service),
) -> list[Widget]:
    return await service.list()

@router.post("/", status_code=201)
async def create_widget(
    data: CreateWidgetRequest,
    service: WidgetService = Depends(get_widget_service),
) -> Widget:
    return await service.create(data)
```

---

## Appendix: Pattern Decision Flow

```
Starting a new feature?
│
├─ Is it a simple CRUD operation?
│   YES → Use BaseService + Repository pattern
│   NO  → ↓
│
├─ Does it MUTATE state with side effects?
│   YES → Use CQRS Command + Event Bus
│   NO  → ↓
│
├─ Does it READ from multiple sources?
│   YES → Use CQRS Query
│   NO  → Use a simple service method
│
└─ Any async reactions needed?
    YES → Publish events after the operation
    NO  → Done
```

---

## Appendix: Quick Reference

| Pattern | File Location | Base Class | When to Use |
|---------|--------------|------------|-------------|
| Service | `domains/*/service.py` | `BaseService[T]` | Business logic + orchestration |
| Repository | `domains/*/repository.py` | None (plain class) | Data access only |
| Event Bus | `core/events.py` | `BaseEvent` + `EventBus` | Async decoupled communication |
| CQRS Command | `core/cqrs.py` | `Command` + `CommandHandler[C]` | Complex writes with side effects |
| CQRS Query | `core/cqrs.py` | `Query` + `QueryHandler[Q, R]` | Complex reads from multiple sources |
| Mediator | `core/cqrs.py` | `Mediator` | Dispatch commands/queries to handlers |
| CRUD Router | `core/base_router.py` | `CRUDRouter` | Auto-generate CRUD endpoints |
| DI Hub | `core/dependencies.py` | — | Central FastAPI dependencies |
| Error Hierarchy | `core/errors.py` | `AppError` | Typed business errors |
| Config | `config.py` | `pydantic.BaseSettings` | All environment variables |
