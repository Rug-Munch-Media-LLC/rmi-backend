"""Newsletter services — sending, subscriber, templates, scheduling modules."""

from __future__ import annotations

from app.domains.newsletter.services.constants import PLATFORM_FEE_PCT, SUBSCRIPTION_TIERS
from app.domains.newsletter.services.helpers import get_setting, slugify
from app.domains.newsletter.services.scheduling import (
    compute_creator_analytics,
    compute_delivery_status,
    compute_newsletter_info_analytics,
)
from app.domains.newsletter.services.sending import (
    get_email_provider,
    send_email,
    verify_crypto_payment,
)
from app.domains.newsletter.services.service import NewsletterService
from app.domains.newsletter.services.subscribers import (
    count_subscribers,
    export_subscribers_csv,
    get_segment_subscribers,
    import_subscribers_batch,
    list_subscribers,
    subscriber_growth,
)
from app.domains.newsletter.services.templates import (
    render_content_html,
    render_content_text,
    render_newsletter_template,
)

__all__ = [
    "PLATFORM_FEE_PCT",
    "SUBSCRIPTION_TIERS",
    "NewsletterService",
    "compute_creator_analytics",
    "compute_delivery_status",
    "compute_newsletter_info_analytics",
    "count_subscribers",
    "export_subscribers_csv",
    "get_email_provider",
    "get_segment_subscribers",
    "get_setting",
    "import_subscribers_batch",
    "list_subscribers",
    "render_content_html",
    "render_content_text",
    "render_newsletter_template",
    "send_email",
    "slugify",
    "subscriber_growth",
    "verify_crypto_payment",
]
