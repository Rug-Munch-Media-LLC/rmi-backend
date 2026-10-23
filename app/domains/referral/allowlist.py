"""Partner allowlist resolution — the only place outbound referral URLs are built."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.domains.referral.constants import CODE_PATTERN, PARTNER_TARGETS, SURFACES, TIERS

_CODE_RE = re.compile(CODE_PATTERN)
_ALLOWED_HOSTS: frozenset[str] = frozenset(urlsplit(url).hostname or "" for url in PARTNER_TARGETS.values())


class UnknownPartnerError(ValueError):
    """Partner is not in the allowlist."""


class InvalidAttributionError(ValueError):
    """Code, surface or tier failed validation."""


def validate_code(code: str) -> str:
    """Reject anything but an opaque referral slug (no URLs, no traversal)."""
    if not isinstance(code, str) or not _CODE_RE.match(code):
        raise InvalidAttributionError("invalid referral code")
    return code


def validate_surface(surface: str) -> str:
    if surface not in SURFACES:
        raise InvalidAttributionError("unknown source surface")
    return surface


def validate_tier(tier: str | None) -> str:
    from app.domains.referral.constants import DEFAULT_TIER

    candidate = (tier or DEFAULT_TIER).strip().lower()
    if candidate not in TIERS:
        raise InvalidAttributionError("unknown tier")
    return candidate


def resolve_partner_url(partner: str, code: str) -> str:
    """Build the redirect target for an allowlisted partner.

    Unknown partner -> UnknownPartnerError (404 at the edge). The result is
    re-checked for scheme/host immediately before being returned, so a bad
    constant can never become an open redirect.
    """
    template = PARTNER_TARGETS.get(partner)
    if template is None:
        raise UnknownPartnerError(partner)
    safe_code = validate_code(code)
    url = template.replace("{code}", safe_code)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _ALLOWED_HOSTS:
        raise UnknownPartnerError(partner)
    return url
