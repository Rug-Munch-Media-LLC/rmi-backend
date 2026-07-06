"""API versioning router for RMI Backend."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="", tags=["api-versioning"])

DEPRECATED_PATHS: dict[str, str] = {}


@router.get("/api/version")
async def api_version():
    """Return current API version info."""
    return {
        "versions": {"v1": {"status": "stable"}, "v2": {"status": "planned"}},
        "current": "v1",
        "docs": "/docs",
    }


@router.get("/api")
async def api_root():
    """API root with version links."""
    return {
        "message": "RugMunch Intelligence API",
        "versions": {"v1": "/api/v1/", "v2": "/api/v2/ (planned)"},
        "docs": {"swagger": "/docs", "redoc": "/redoc", "openapi": "/openapi.json"},
        "health": "/health",
        "metrics": "/metrics",
    }


def register_deprecation(path: str, new_path: str) -> None:
    """Register a deprecated path for sunset headers."""
    DEPRECATED_PATHS[path] = new_path


def setup_deprecation_middleware(app):
    """Add deprecation headers middleware to the FastAPI app."""

    @app.middleware("http")
    async def _deprecation(request: Request, call_next):
        path = request.url.path
        if path in DEPRECATED_PATHS:
            response = await call_next(request)
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = "Sat, 01 Jan 2027 00:00:00 GMT"
            response.headers["Link"] = f'<{DEPRECATED_PATHS[path]}>; rel="successor-version"'
            return response
        return await call_next(request)
