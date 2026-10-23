"""Multi-method X/Twitter data fetching for the CT intelligence pipeline.

Methods: xurl CLI (OAuth), cookie-based scraping, DuckDuckGo web scraping.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import urllib.parse
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.x_intel.x_proxy.client import x_http_client

logger = get_logger("x_intel")

# Fail-open error set for X/Twitter fetching (xurl CLI, cookie scrape, web
# scrape): transports (OSError covers builtin ConnectionError, socket and
# file-IO failures; TimeoutError covers subprocess and asyncio timeouts;
# httpx.HTTPError covers all httpx transport/request errors) plus
# payload-shape errors from parsing JSON/HTML (ValueError incl.
# json.JSONDecodeError, KeyError, TypeError, AttributeError). Sites backed
# by the xurl CLI add subprocess.SubprocessError explicitly.
X_FETCH_FAIL_OPEN_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    TimeoutError,
    httpx.HTTPError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
)

# xurl-backed block: subprocess.TimeoutExpired joins the shared set
# (FileNotFoundError for a missing binary is already OSError).
XURL_FETCH_FAIL_OPEN_ERRORS: tuple[type[BaseException], ...] = (
    *X_FETCH_FAIL_OPEN_ERRORS,
    subprocess.SubprocessError,
)


async def _xurl_search(query: str, count: int = 20) -> list[dict[str, Any]]:
    """Search X via xurl CLI (OAuth)."""
    try:
        result = subprocess.run(
            ["xurl", "search", query, "-n", str(count), "--auth", "oauth2"],
            capture_output=True,
            text=True,
            timeout=20,
            shell=False,
        )
        if result.returncode != 0:
            return []

        posts = []
        for line in result.stdout.strip().split("\n"):
            try:
                data = json.loads(line)
                if isinstance(data, dict) and "text" in data:
                    posts.append(data)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "text" in item:
                            posts.append(item)
            except json.JSONDecodeError:
                continue
        return posts
    except XURL_FETCH_FAIL_OPEN_ERRORS as e:
        logger.debug("xurl_search_failed error=%r query=%r", e, query)
        return []


async def _xurl_user_timeline(handle: str, count: int = 10) -> list[dict[str, Any]]:
    """Get recent posts from a specific user via xurl."""
    try:
        result = subprocess.run(
            ["xurl", "/2/users/by/username/" + handle.lstrip("@"), "--auth", "oauth2"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        if result.returncode != 0:
            return []

        user_data = json.loads(result.stdout)
        user_id = user_data.get("data", {}).get("id", "")

        if not user_id:
            return []

        result2 = subprocess.run(
            [
                "xurl",
                f"/2/users/{user_id}/tweets",
                "--auth",
                "oauth2",
                "-d",
                json.dumps({"max_results": count, "tweet.fields": "created_at,public_metrics"}),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )

        if result2.returncode != 0:
            return []

        data = json.loads(result2.stdout)
        posts = data.get("data", []) if isinstance(data, dict) else []

        return [
            {
                "text": p.get("text", ""),
                "author": handle,
                "created_at": p.get("created_at", ""),
                "likes": p.get("public_metrics", {}).get("like_count", 0),
                "retweets": p.get("public_metrics", {}).get("retweet_count", 0),
                "replies": p.get("public_metrics", {}).get("reply_count", 0),
                "url": f"https://x.com/{handle}/status/{p['id']}",
                "id": p["id"],
            }
            for p in posts
        ]

    except XURL_FETCH_FAIL_OPEN_ERRORS as e:
        logger.debug("xurl_timeline_failed handle=%r error=%r", handle, e)
        return []


async def _cookie_scrape_x(handles: list[str]) -> list[dict[str, Any]]:
    """Attempt cookie-based scraping via saved browser session.

    If Tailscale is connected to user's machine, we can pull cookies
    from their browser session for authenticated access.
    """
    # Check for saved cookies
    cookie_file = os.path.expanduser("~/.x_cookies.json")
    if not os.path.exists(cookie_file):
        return []

    try:
        with open(cookie_file) as f:
            cookies = json.load(f)

        posts = []
        async with x_http_client(timeout=15, cookies=cookies) as c:
            for handle in handles[:5]:  # Limit to avoid rate limiting
                r = await c.get(
                    f"https://x.com/{handle}",
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120"},
                )
                if r.status_code == 200:
                    # Extract embedded tweet data from page
                    # X embeds JSON in __NEXT_DATA__ script tag
                    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            timeline = data.get("props", {}).get("pageProps", {}).get("timeline", {}).get("entries", [])
                            for entry in timeline[:10]:
                                tweet = entry.get("content", {}).get("tweet", {})
                                if tweet:
                                    posts.append(
                                        {
                                            "text": tweet.get("full_text", ""),
                                            "author": handle,
                                            "likes": tweet.get("favorite_count", 0),
                                            "retweets": tweet.get("retweet_count", 0),
                                            "url": f"https://x.com/{handle}/status/{tweet.get('id_str', '')}",
                                        }
                                    )
                        except X_FETCH_FAIL_OPEN_ERRORS as e:
                            logger.warning("x_next_data_parse_failed handle=%r error=%r", handle, e)
        return posts
    except X_FETCH_FAIL_OPEN_ERRORS as e:
        logger.debug("cookie_scrape_failed error=%r", e)
        return []


async def _web_scrape_x(handles: list[str], count_per_handle: int = 3) -> list[dict[str, Any]]:
    """Fallback: Web scrape X via DuckDuckGo search + embed extraction."""
    posts = []
    async with x_http_client(
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    ) as client:
        for handle in handles[:10]:
            try:
                search_q = f"site:x.com {handle}"
                r = await client.get(f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_q)}")
                if r.status_code != 200:
                    continue

                urls = list(
                    set(
                        re.findall(
                            rf"https://x\.com/{handle.lstrip('@')}/status/\d+",
                            r.text,
                            re.IGNORECASE,
                        )
                    )
                )

                for url in urls[:count_per_handle]:
                    try:
                        r2 = await client.get(url)
                        if r2.status_code == 200:
                            desc_match = re.search(r'<meta property="og:description" content="(.*?)"', r2.text)
                            if desc_match:
                                text = html.unescape(desc_match.group(1))
                                text = re.sub(rf"^{handle}:\s*", "", text, flags=re.IGNORECASE).strip()

                                posts.append(
                                    {
                                        "text": text,
                                        "author": handle,
                                        "url": url,
                                        "likes": 0,
                                        "retweets": 0,
                                        "replies": 0,
                                        "created_at": "",
                                        "id": url.split("/")[-1],
                                    }
                                )
                    except X_FETCH_FAIL_OPEN_ERRORS:
                        continue
            except X_FETCH_FAIL_OPEN_ERRORS as e:
                logger.debug("web_scrape_failed handle=%r error=%r", handle, e)

    return posts
