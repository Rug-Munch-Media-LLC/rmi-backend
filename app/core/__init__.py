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
"""
