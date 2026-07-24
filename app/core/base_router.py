"""Base router mixin — common CRUD patterns for domain routers.

Usage:
    from app.core.base_router import CRUDRouter
    from app.domains.my_service import MyService
    from fastapi import Depends

    class MyDomainRouter(CRUDRouter):
        def __init__(self):
            super().__init__(prefix="/my-domain", tags=["my-domain"])
            self._register_routes()

        def get_service(self) -> MyService:
            return MyService()
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from fastapi import APIRouter, Depends

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger

log = get_logger(__name__)

# ── Types ────────────────────────────────────────────────────────────────

T = TypeVar("T", covariant=True)

class ServiceProtocol(Protocol[T]):
    """Minimal service interface expected by CRUDRouter.

    Domain services should implement these methods. Each is optional;
    CRUDRouter only calls methods that exist on the service.
    """

    async def list(self, **filters: Any) -> list[T]: ...
    async def get(self, id_: str, **filters: Any) -> T: ...
    async def create(self, data: Any, **kwargs: Any) -> T: ...
    async def update(self, id_: str, data: Any, **kwargs: Any) -> T: ...
    async def delete(self, id_: str, **kwargs: Any) -> bool: ...

# ── Base class ────────────────────────────────────────────────────────────

class CRUDRouter(APIRouter):
    """Base router with auto-registered CRUD endpoints.

    Subclasses must implement ``get_service()``. Override ``_register_routes()``
    to customize which endpoints are created.

    Example::

        class MyRouter(CRUDRouter):
            def __init__(self) -> None:
                super().__init__(prefix="/items", tags=["items"])
                self._register_routes()

            def get_service(self) -> MyService:
                return MyService()

        router = MyRouter()
    """

    def get_service(self) -> Any:
        """Override this to return the domain service instance."""
        raise NotImplementedError

    # ── Route registration ────────────────────────────────────────────────

    def _register_routes(self) -> None:
        """Register standard CRUD endpoints.

        Override this method and call individual ``_add_*`` methods
        to customize which endpoints are created.
        """
        self._add_list()
        self._add_get()
        self._add_create()
        self._add_update()
        self._add_delete()

    def _add_list(self) -> None:
        """Register GET / — list all resources."""

        @self.get("/")
        async def list_(
            svc: Any = Depends(self._get_service_dep),  # type: ignore[misc]
        ) -> list[Any]:
            try:
                results = await svc.list()
                return results
            except Exception:
                log.exception("list_failed")
                raise

    def _add_get(self) -> None:
        """Register GET /{id} — get a single resource."""

        @self.get("/{id_}")
        async def get_(
            id_: str,
            svc: Any = Depends(self._get_service_dep),  # type: ignore[misc]
        ) -> Any:
            try:
                item = await svc.get(id_)
                if item is None:
                    raise NotFoundError(f"Resource not found: {id_}")
                return item
            except NotFoundError:
                raise
            except Exception:
                log.exception("get_failed id=%s", id_)
                raise

    def _add_create(self) -> None:
        """Register POST / — create a resource."""

        @self.post("/")
        async def create_(
            data: Any,
            svc: Any = Depends(self._get_service_dep),  # type: ignore[misc]
        ) -> Any:
            try:
                result = await svc.create(data)
                return result
            except ValidationError:
                raise
            except Exception:
                log.exception("create_failed")
                raise

    def _add_update(self) -> None:
        """Register PUT /{id} — update a resource."""

        @self.put("/{id_}")
        async def update_(
            id_: str,
            data: Any,
            svc: Any = Depends(self._get_service_dep),  # type: ignore[misc]
        ) -> Any:
            try:
                item = await svc.update(id_, data)
                if item is None:
                    raise NotFoundError(f"Resource not found: {id_}")
                return item
            except NotFoundError:
                raise
            except Exception:
                log.exception("update_failed id=%s", id_)
                raise

    def _add_delete(self) -> None:
        """Register DELETE /{id} — delete a resource."""

        @self.delete("/{id_}")
        async def delete_(
            id_: str,
            svc: Any = Depends(self._get_service_dep),  # type: ignore[misc]
        ) -> dict[str, str]:
            try:
                ok = await svc.delete(id_)
                if not ok:
                    raise NotFoundError(f"Resource not found: {id_}")
                return {"status": "deleted"}
            except NotFoundError:
                raise
            except Exception:
                log.exception("delete_failed id=%s", id_)
                raise

    # ── Internal ──────────────────────────────────────────────────────────

    async def _get_service_dep(self) -> Any:
        """FastAPI dependency that returns the service instance."""
        return self.get_service()
