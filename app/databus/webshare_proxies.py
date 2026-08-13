"""Residential proxy list loader (Webshare rotation).

Loads the Webshare residential proxy rotation list used by
:mod:`app.databus.proxy_scraper` for 429/403 rate-limit escape.

Resolution priority:

1. ``WEBSHARE_PROXY_LIST`` env — space/newline/comma separated proxy URLs
2. ``WEBSHARE_PROXY_URL`` env — a single proxy URL
3. gopass ``rmi/infra/webshare_proxy_list`` — best-effort subprocess read,
   cached per process (TTL) so we do not shell out on every lookup

Credentials are never hardcoded and never logged. Log lines carry only the
source and the count of proxies; the URLs themselves (which embed
``user:pass@``) are never written to the log.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Final

from app.core.logging import get_logger

log = get_logger(__name__)

GOPASS_PROXY_PATH: Final[str] = "rmi/infra/webshare_proxy_list"
_PROXY_URL_SCHEMES: Final[tuple[str, ...]] = ("http://", "https://", "socks5://", "socks5h://")
_GOPASS_CACHE_TTL: Final[float] = 300.0  # re-read after 5 min in case credentials rotate

# Module-level memoization of the gopass read (deliberate; the env path is
# cheap and always re-checked).
_gopass_cache: list[str] | None = None
_gopass_cache_at: float = 0.0


def parse_proxy_list(raw: str) -> list[str]:
    """Parse a space / newline / comma separated list of proxy URLs."""
    entries: list[str] = []
    for chunk in raw.replace(",", " ").split():
        chunk = chunk.strip()
        if chunk.startswith(_PROXY_URL_SCHEMES):
            entries.append(chunk)
    return entries


def _load_from_gopass() -> list[str]:
    """Best-effort cached ``gopass show -o rmi/infra/webshare_proxy_list`` read.

    Returns ``[]`` when gopass is missing, times out, errors, or the secret is
    empty. Never logs proxy URLs.
    """
    global _gopass_cache, _gopass_cache_at  # module memoization
    now = time.monotonic()
    if _gopass_cache is not None and now - _gopass_cache_at < _GOPASS_CACHE_TTL:
        return _gopass_cache
    try:
        # nosec: fixed gopass path, shell=False, operator-controlled — not user input.
        result = subprocess.run(  # noqa: S603
            ["gopass", "show", "-o", GOPASS_PROXY_PATH],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            _gopass_cache = parse_proxy_list(result.stdout)
            _gopass_cache_at = now
            return _gopass_cache
        log.debug("webshare_gopass_failed", path=GOPASS_PROXY_PATH, returncode=result.returncode)
    except FileNotFoundError:
        log.debug("webshare_gopass_missing", hint="gopass not installed")
    except subprocess.TimeoutExpired:
        log.debug("webshare_gopass_timeout", path=GOPASS_PROXY_PATH)
    except Exception as exc:  # pragma: no cover — defensive
        log.debug("webshare_gopass_error", path=GOPASS_PROXY_PATH, error=str(exc)[:120])
    return []


def load_proxies() -> list[str]:
    """Return the configured residential proxy list.

    Priority: ``WEBSHARE_PROXY_LIST`` env → ``WEBSHARE_PROXY_URL`` env →
    gopass ``rmi/infra/webshare_proxy_list``. Returns ``[]`` when no source is
    available so the proxy tier degrades gracefully.
    """
    listed = os.getenv("WEBSHARE_PROXY_LIST", "").strip()
    if listed:
        parsed = parse_proxy_list(listed)
        if parsed:
            log.info("webshare_proxies_loaded", source="env_list", count=len(parsed))
            return parsed
    single = os.getenv("WEBSHARE_PROXY_URL", "").strip()
    if single and single.startswith(_PROXY_URL_SCHEMES):
        log.info("webshare_proxies_loaded", source="env_url", count=1)
        return [single]
    gopass_proxies = _load_from_gopass()
    if gopass_proxies:
        log.info("webshare_proxies_loaded", source="gopass", count=len(gopass_proxies))
    return gopass_proxies


def clear_proxy_cache() -> None:
    """Drop the cached gopass result (used by tests / after credential rotation)."""
    global _gopass_cache, _gopass_cache_at  # module memoization
    _gopass_cache = None
    _gopass_cache_at = 0.0
