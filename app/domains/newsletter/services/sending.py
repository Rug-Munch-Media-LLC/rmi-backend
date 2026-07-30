"""Email sending — Postmark, SES, Mailgun providers."""

from __future__ import annotations

from app.core.errors import ValidationError
from app.core.http import get_http_client
from app.core.logging import get_logger
from app.domains.newsletter.services.helpers import get_setting

logger = get_logger(__name__)


async def send_email(
    provider: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    sub_id: str,
    nl_id: str,
) -> None:
    """Send an email via the configured provider (Postmark/SES/Mailgun)."""
    client = await get_http_client()

    if provider == "postmark":
        api_key = get_setting("postmark_api_key", "")
        if not api_key:
            raise ValidationError("Postmark API key not configured")

        await client.post(
            "https://api.postmarkapp.com/email",
            json={
                "From": get_setting("newsletter_from_email", "newsletter@rugmunch.io"),
                "To": to_email,
                "Subject": subject,
                "HtmlBody": html_body,
                "TextBody": text_body,
                "TrackOpens": True,
                "TrackLinks": "HtmlOnly",
                "Metadata": {"subscriber_id": sub_id, "newsletter_id": nl_id},
            },
            headers={
                "X-Postmark-Server-Token": api_key,
                "Accept": "application/json",
            },
            timeout=15.0,
        )
    elif provider == "ses":
        from_email = get_setting("newsletter_from_email", "newsletter@rugmunch.io")

        await client.post(
            "https://email.us-east-1.amazonaws.com/v2/email/outbound-emails",
            json={
                "FromEmailAddress": from_email,
                "Destination": {"ToAddresses": [to_email]},
                "Content": {
                    "Simple": {
                        "Subject": {"Data": subject},
                        "Body": {
                            "Html": {"Data": html_body},
                            "Text": {"Data": text_body},
                        },
                    },
                },
            },
            headers={
                "X-Api-Key": get_setting("aws_ses_key", ""),
            },
            timeout=15.0,
        )
    elif provider == "mailgun":
        domain = get_setting("mailgun_domain", "")
        api_key = get_setting("mailgun_api_key", "")

        if not domain or not api_key:
            raise ValidationError("Mailgun not configured")

        await client.post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", api_key),
            data={
                "from": get_setting("newsletter_from_email", f"newsletter@{domain}"),
                "to": to_email,
                "subject": subject,
                "html": html_body,
                "text": text_body,
                "o:tracking-opens": "yes",
                "o:tracking-clicks": "yes",
                "v:subscriber_id": sub_id,
                "v:newsletter_id": nl_id,
            },
            timeout=15.0,
        )


async def get_email_provider() -> str | None:
    """Determine which email provider is configured.

    Priority: Postmark > Mailgun > SES > None (email disabled).
    """
    if get_setting("postmark_api_key", ""):
        return "postmark"
    if get_setting("mailgun_api_key", ""):
        return "mailgun"
    if get_setting("aws_ses_key", ""):
        return "ses"
    return None
