"""RMI Backend - Structured logging with structlog + request ID tracking."""

import logging
import sys

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog with JSON output for production, console for dev."""
    is_prod = level == "INFO"  # JSON in prod, colored console in debug

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer() if not is_prod else structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set root logger level
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, level))
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for a module."""
    return structlog.get_logger(name)
