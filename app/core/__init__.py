"""Cross-cutting concerns. NO business logic lives here.

Modules (populated by parallel DeepSeek tasks DS-1..DS-10):
    - config: pydantic-settings (DS-1)
    - logging: structlog + correlation IDs (DS-2)
    - errors: AppError hierarchy + handlers (DS-3)
    - redis: async client + Depends (DS-4)
    - db: async SQLAlchemy session (DS-5)
    - auth: JWT decode + role guards (DS-6)
    - lifespan: startup/shutdown hooks (DS-7)
    - middleware: CORS, rate limit, correlation ID (DS-8)
    - websocket: connection manager (DS-9)
    - tracing: OpenTelemetry + Langfuse v4 (DS-10)
    - dependencies: central DI hub (REFACTOR-2026-Q3)
    - base_router: CRUD router mixin (REFACTOR-2026-Q3)
    - base_service: standardised async service interface (REFACTOR-2026-Q3)
    - events: EventBus, typed events, pub/sub (2026-07-24)
    - cqrs: Command/Query separation, Mediator (2026-07-24)
"""

from __future__ import annotations

# Re-export base classes
from app.core.base_router import CRUDRouter
from app.core.base_service import BaseService
from app.core.cqrs import (
    Command,
    CommandHandler,
    CommandResult,
    Mediator,
    Query,
    QueryHandler,
    get_mediator,
    reset_mediator,
)

# Re-export DI providers so callers can ``from app.core import get_settings``
from app.core.dependencies import (
    close_http_client,
    get_chain_client,
    get_client_ip,
    get_db,
    get_evm_client,
    get_http_client,
    get_pryscraper_client,
    get_rate_limiter,
    get_redis,
    get_settings,
    get_token_scanner,
    get_wallet_scanner,
)

# Re-export event bus + CQRS
from app.core.events import (
    AlertTriggered,
    BaseEvent,
    EventBus,
    NewsIngested,
    PaymentReceived,
    ScanCompleted,
    WalletUpdated,
    get_event_bus,
    reset_event_bus,
)

__all__ = [
    "get_settings",
    "get_db",
    "get_http_client",
    "get_redis",
    "get_chain_client",
    "get_evm_client",
    "get_rate_limiter",
    "get_token_scanner",
    "get_wallet_scanner",
    "get_pryscraper_client",
    "get_client_ip",
    "close_http_client",
    "CRUDRouter",
    "BaseService",
    # Event bus
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
    "BaseEvent",
    "ScanCompleted",
    "WalletUpdated",
    "NewsIngested",
    "AlertTriggered",
    "PaymentReceived",
    # CQRS
    "Mediator",
    "get_mediator",
    "reset_mediator",
    "Command",
    "Query",
    "CommandResult",
    "CommandHandler",
    "QueryHandler",
]
