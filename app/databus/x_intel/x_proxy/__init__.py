"""WebShare proxy rotation for the X/CT intelligence pipeline.

Public surface:

``x_http_client``          proxy-rotating httpx client factory (use this)
``get_proxy_candidates``   WebShare API list + env fallback, TTL cached
``health``                 process-wide :class:`ProxyHealth` breaker/snapshot
``ProxyHealth``            per-host counters, credential-free
``TokenBucket``            shared rate limiter
``RotatingProxyTransport`` the httpx transport doing the rotation

Secrets: the API key is read from ``WEBSHARE_API_KEY`` (gopass
``rmi/backend/webshare_api_key``) and is never logged, stored or returned.
Health records key on proxy host only.
"""

from __future__ import annotations

from app.databus.x_intel.x_proxy.client import health, x_http_client
from app.databus.x_intel.x_proxy.health import ProxyHealth
from app.databus.x_intel.x_proxy.ratelimit import TokenBucket
from app.databus.x_intel.x_proxy.source import get_proxy_candidates
from app.databus.x_intel.x_proxy.transport import RotatingProxyTransport

__all__ = [
    "ProxyHealth",
    "RotatingProxyTransport",
    "TokenBucket",
    "get_proxy_candidates",
    "health",
    "x_http_client",
]
