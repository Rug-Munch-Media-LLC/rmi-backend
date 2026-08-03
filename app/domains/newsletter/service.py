"""DEPRECATED — Import from app.domains.newsletter.services instead.

This file is kept for backward compatibility. All functionality has been
moved to the `app.domains.newsletter.services` package.
"""

from __future__ import annotations

from app.domains.newsletter.services import (
    PLATFORM_FEE_PCT,
    SUBSCRIPTION_TIERS,
    NewsletterService,
    compute_creator_analytics,
    compute_delivery_status,
    compute_newsletter_info_analytics,
    count_subscribers,
    export_subscribers_csv,
    get_email_provider,
    get_segment_subscribers,
    get_setting,
    import_subscribers_batch,
    list_subscribers,
    render_content_html,
    render_content_text,
    render_newsletter_template,
    send_email,
    slugify,
    subscriber_growth,
    verify_crypto_payment,
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
