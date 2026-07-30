"""Template rendering — newsletter content rendering and formatting."""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


def render_newsletter_template(
    template_name: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Render a newsletter template with the given context.

    Supported templates:
      - "default": Standard newsletter layout with header, content, footer
      - "minimal": Plain content with minimal styling
      - "promotional": Marketing-style layout with call-to-action

    Args:
        template_name: Name of the template to render.
        context: Template variables (title, content_html, author, etc.).

    Returns:
        Rendered HTML string.
    """
    ctx = context or {}
    title = ctx.get("title", "Newsletter")
    content_html = ctx.get("content_html", "")
    preheader = ctx.get("preheader_text", "")

    templates: dict[str, str] = {
        "default": _default_template(title, content_html, preheader),
        "minimal": _minimal_template(title, content_html),
        "promotional": _promotional_template(title, content_html, ctx),
    }

    return templates.get(template_name, templates["default"])


def _default_template(title: str, content_html: str, preheader: str = "") -> str:
    """Default newsletter template with header, content, and footer."""
    preheader_html = (
        f'<div style="display:none;font-size:1px;color:#333;max-height:0;">{preheader}</div>' if preheader else ""
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
{preheader_html}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;">
<tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;">
<tr><td style="padding:30px 40px;background:linear-gradient(135deg,#667eea,#764ba2);">
<h1 style="margin:0;color:#fff;font-size:24px;">{title}</h1>
</td></tr>
<tr><td style="padding:30px 40px;color:#333;font-size:16px;line-height:1.6;">
{content_html}
</td></tr>
<tr><td style="padding:20px 40px;background:#fafafa;border-top:1px solid #eee;font-size:12px;color:#888;text-align:center;">
<p style="margin:0;">Powered by RugMunch Newsletter</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _minimal_template(title: str, content_html: str) -> str:
    """Minimal newsletter template."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#333;">
<h1 style="font-size:20px;margin:0 0 20px;">{title}</h1>
<div style="font-size:16px;line-height:1.6;">
{content_html}
</div>
</body></html>"""


def _promotional_template(title: str, content_html: str, ctx: dict[str, Any]) -> str:
    """Promotional/marketing newsletter template with CTA."""
    cta_text = ctx.get("cta_text", "Learn More")
    cta_url = ctx.get("cta_url", "#")
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;">
<tr><td align="center" style="padding:20px 10px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;">
<tr><td style="padding:40px 40px 20px;text-align:center;">
<h1 style="margin:0;font-size:28px;color:#333;">{title}</h1>
</td></tr>
<tr><td style="padding:20px 40px;color:#333;font-size:16px;line-height:1.6;">
{content_html}
</td></tr>
<tr><td style="padding:20px 40px 40px;text-align:center;">
<a href="{cta_url}" style="display:inline-block;padding:12px 32px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;text-decoration:none;border-radius:6px;font-size:16px;font-weight:600;">{cta_text}</a>
</td></tr>
<tr><td style="padding:20px 40px;background:#fafafa;border-top:1px solid #eee;font-size:12px;color:#888;text-align:center;">
<p style="margin:0;">Powered by RugMunch Newsletter</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def render_content_html(content_html: str, template: str = "default", **kwargs: Any) -> str:
    """Render content HTML wrapped in a newsletter template."""
    return render_newsletter_template(template, {"content_html": content_html, **kwargs})


def render_content_text(content_html: str) -> str:
    """Strip HTML tags to produce a plain-text version."""
    import re

    text = re.sub(r"<[^>]+>", " ", content_html)
    text = re.sub(r"\s+", " ", text).strip()
    return text
