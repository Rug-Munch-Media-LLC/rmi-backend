"""Newsletter services — sending, subscriber, payment modules."""

from __future__ import annotations

from app.domains.newsletter.services.constants import PLATFORM_FEE_PCT, SUBSCRIPTION_TIERS
from app.domains.newsletter.services.helpers import get_setting, slugify
from app.domains.newsletter.services.sending import get_email_provider, send_email

__all__ = [
    "PLATFORM_FEE_PCT",
    "SUBSCRIPTION_TIERS",
    "get_email_provider",
    "get_setting",
    "send_email",
    "slugify",
]
