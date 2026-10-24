"""Acceptance rules for upstream icon URLs.

Only absolute ``https`` image URLs are ever returned to a client: no
``data:``, no ``javascript:``, no plaintext ``http``, no ``.svg`` (SVG can
carry script), no private/link-local hosts, no whitespace or quote injection.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_MAX_LEN = 2048
_BLOCKED_SUFFIXES = (".svg", ".svgz")
_BLOCKED_NAMES = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})
_FORBIDDEN_CHARS = frozenset(' \n\r\t"\'<>')


def _blocked_host(host: str) -> bool:
    """Reject loopback/private/link-local targets (SSRF guard)."""
    if host in _BLOCKED_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def acceptable_icon_url(url: str | None) -> bool:
    """True only for an absolute https URL we are willing to hand to a browser."""
    if not url:
        return False
    candidate = str(url).strip()
    if not candidate or len(candidate) > _MAX_LEN:
        return False
    if any(char in candidate for char in _FORBIDDEN_CHARS):
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    if not host or _blocked_host(host):
        return False
    if any(parsed.path.lower().endswith(suffix) for suffix in _BLOCKED_SUFFIXES):
        return False
    return bool(parsed.path or parsed.query)
