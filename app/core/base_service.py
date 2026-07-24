"""Base service — standardised async service pattern.

All domain services should inherit from ``BaseService`` to ensure
consistent patterns:
  - Async methods with typed signatures
  - Standard CRUD interface (list, get, create, update, delete)
  - Structured logging using get_logger
  - Consistent error handling via AppError hierarchy

Usage::

    from app.core.base_service import BaseService
    from app.core.logging import get_logger

    class MyService(BaseService):
        async def list(self, **filters) -> list[MyModel]:
            ...
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from app.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger

T = TypeVar("T")

log = get_logger(__name__)

class BaseService(Generic[T]):
    """Standard async service interface.

    Subclasses must implement concrete CRUD methods or override
    the default implementations.
    """

    # ── CRUD (override in subclasses) ─────────────────────────────────────

    async def list(self, **filters: Any) -> list[T]:
        """List all resources matching *filters*.

        Override in subclass. Default raises NotImplementedError.
        """
        raise NotImplementedError

    async def get(self, id_: str, **kwargs: Any) -> T | None:
        """Get a single resource by ID.

        Returns None if not found. Override in subclass.
        """
        raise NotImplementedError

    async def create(self, data: Any, **kwargs: Any) -> T:
        """Create a new resource.

        Raise ValidationError if input is invalid.
        Raise ConflictError if a duplicate exists.
        Override in subclass.
        """
        raise NotImplementedError

    async def update(self, id_: str, data: Any, **kwargs: Any) -> T | None:
        """Update an existing resource.

        Return the updated resource, or None if not found.
        Override in subclass.
        """
        raise NotImplementedError

    async def delete(self, id_: str, **kwargs: Any) -> bool:
        """Delete a resource by ID.

        Return True if deleted, False if not found.
        Override in subclass.
        """
        raise NotImplementedError

    # ── Error helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _not_found(id_: str) -> NotFoundError:
        """Raise a standardised 404 error."""
        return NotFoundError(f"Resource not found: {id_}")

    @staticmethod
    def _conflict(detail: str = "Resource already exists") -> ConflictError:
        """Raise a standardised 409 error."""
        return ConflictError(detail)

    @staticmethod
    def _validation(detail: str = "Invalid input") -> ValidationError:
        """Raise a standardised 400 error."""
        return ValidationError(detail)
