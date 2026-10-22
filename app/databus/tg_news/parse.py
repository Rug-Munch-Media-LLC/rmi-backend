"""Parse Telegram public-preview HTML (``t.me/s/<channel>``) into post records.

The public web preview needs no auth and renders the newest ~20 messages of a
public channel with stable ``tgme_widget_message_*`` classes. One primary
function: :func:`parse_channel_page`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

_VIEWS_RE = re.compile(r"([\d.,]+)\s*([KMB])?", re.IGNORECASE)
_SCALE = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_WS_RE = re.compile(r"\s+")


@dataclass(slots=True)
class ChannelPage:
    """One rendered preview page: channel metadata + posts (oldest first)."""

    channel: str
    title: str | None = None
    subscribers: int | None = None
    description: str | None = None
    posts: list[dict[str, Any]] = field(default_factory=list)


def parse_count(raw: str | None) -> int | None:
    """Parse an abbreviated Telegram counter: ``"99.8K"`` -> 99800."""
    if not raw:
        return None
    cleaned = raw.replace("\xa0", " ").replace(" ", "").replace(",", ".")
    match = _VIEWS_RE.search(cleaned)
    if not match:
        return None
    suffix = (match.group(2) or "").upper()
    try:
        return round(float(match.group(1)) * _SCALE[suffix])
    except ValueError:
        return None


def _clean(node: Any) -> str:
    return _WS_RE.sub(" ", node.get_text(" ", strip=True)).strip()


def _forward_from(block: Any, text_el: Any, channel: str) -> str | None:
    """Forward source: the hidden name when present, else a t.me link in the text."""
    if block is None:
        return None
    name_el = block.select_one(".tgme_widget_message_forwarded_from_name")
    hidden = _clean(name_el) if name_el else ""
    if hidden:
        return hidden.lstrip("@")
    for anchor in text_el.find_all("a", href=True) if text_el else []:
        parts = anchor["href"].rstrip("/").split("/")
        handle = parts[-1] if parts else ""
        if "t.me" in anchor["href"] and handle and handle.lower() != channel.lower():
            return handle.lstrip("@")
    return "unknown"


def parse_channel_page(html: str, channel: str) -> ChannelPage:
    """Turn one ``t.me/s/<channel>`` page into a :class:`ChannelPage`."""
    soup = BeautifulSoup(html, "html.parser")
    page = ChannelPage(channel=channel)
    title_el = soup.select_one(".tgme_channel_info_header_title")
    page.title = _clean(title_el) if title_el else None
    desc_el = soup.select_one(".tgme_channel_info_description")
    page.description = _clean(desc_el) if desc_el else None
    for counter in soup.select(".tgme_channel_info_counter"):
        if "subscriber" in _clean(counter).lower():
            page.subscribers = parse_count(_clean(counter.select_one(".counter_value") or counter))
            break

    for wrap in soup.select(".tgme_widget_message_wrap"):
        msg = wrap.select_one(".tgme_widget_message[data-post]")
        if msg is None:
            continue
        data_post = str(msg.get("data-post", ""))
        if "/" not in data_post:
            continue
        owner, raw_id = data_post.rsplit("/", 1)
        try:
            message_id = int(raw_id)
        except ValueError:
            continue
        text_el = msg.select_one(".tgme_widget_message_text")
        text = _clean(text_el) if text_el else ""
        time_el = msg.select_one(".tgme_widget_message_date time")
        views_el = msg.select_one(".tgme_widget_message_views")
        links = [
            urljoin("https://t.me/s/", str(a["href"])) for a in (text_el.find_all("a", href=True) if text_el else [])
        ]
        page.posts.append(
            {
                "channel": owner,
                "message_id": message_id,
                "timestamp": time_el.get("datetime") if time_el else None,
                "text": text,
                "views": parse_count(_clean(views_el)) if views_el else None,
                "forward_from": _forward_from(msg.select_one(".tgme_widget_message_forwarded_from"), text_el, owner),
                "links": links,
                "url": f"https://t.me/{owner}/{message_id}",
            }
        )
    page.posts.sort(key=lambda p: p["message_id"])
    return page
