"""Content sanitizer for bulletin board posts."""

from __future__ import annotations

import html
import re
from typing import ClassVar


class ContentSanitizer:
    """Sanitize user-generated content to prevent XSS."""

    ALLOWED_TAGS: ClassVar[set[str]] = {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "a",
        "img",
        "blockquote",
        "code",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "td",
        "th",
        "div",
        "span",
        "hr",
        "sub",
        "sup",
        "del",
        "ins",
    }

    ALLOWED_ATTRS: ClassVar[dict[str, list[str]]] = {
        "a": ["href", "title", "target"],
        "img": ["src", "alt", "title", "width", "height"],
        "div": ["class"],
        "span": ["class"],
        "code": ["class"],
        "pre": ["class"],
    }

    @staticmethod
    def sanitize(text: str) -> str:
        """Basic HTML sanitization — strips all HTML tags for safety."""
        if not text:
            return ""
        text = html.escape(text)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    @staticmethod
    def generate_slug(title: str) -> str:
        """Generate URL-friendly slug from title."""
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[-\s]+", "-", slug)
        return slug[:80]

    @staticmethod
    def generate_summary(content: str, max_length: int = 200) -> str:
        """Generate summary from content."""
        text = re.sub(r"<[^>]+>", "", content)
        text = text.replace("\n", " ").strip()
        if len(text) > max_length:
            text = text[:max_length].rsplit(" ", 1)[0] + "..."
        return text
