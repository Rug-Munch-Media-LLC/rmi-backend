"""Timeline readers: the transports that actually return posts.

Primary  pry        our self-hosted reader (127.0.0.1:8005) — rendered markdown
Fallback brightdata Bright Data Web Unlocker CLI — rendered markdown WITH ids
Legacy   syndication / nitter / jina / flaresolverr
         kept for completeness; syndication 429s and the nitter mirrors are dead,
         so none of them is in the default chain.

Every reader is a GET/POST of a public page: no cookies, no auth tokens, no
auth bypass. Readers return (posts, error) and never raise.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import httpx
import x_graphql
from http_get import MIN_BYTES, Limiter, get
from markdown_parse import parse_markdown
from model import Post, parse_nitter, parse_syndication

PRY_URL = os.environ.get("PRY_URL", "http://127.0.0.1:8005/v1/scrape")
PRY_TIMEOUT = 120.0
BDATA_TIMEOUT = 180
FLAIR = 8192
SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
READERS = ("https://r.jina.ai/https://x.com/{handle}",)
_BDATA_NOISE = ("Scraping ", "⚠", "Retrying")


def graphql(handle: str, limiter: Limiter | None = None, proxy: str | None = None) -> tuple[list[Post], str]:
    """Guest-token GraphQL timeline (primary): ids, timestamps, no cookies."""
    return x_graphql.timeline(handle, limiter, proxy)


def pry(handle: str, _limiter: Limiter | None = None, _proxy: str | None = None) -> tuple[list[Post], str]:
    """Rendered markdown from the self-hosted Pry service (API key required)."""
    key = os.environ.get("PRY_API_KEY", "")
    if not key:
        return [], "no_pry_api_key"
    try:
        resp = httpx.post(
            PRY_URL,
            json={"url": f"https://x.com/{handle}"},
            headers={"X-API-Key": key},
            timeout=PRY_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return [], type(exc).__name__
    if resp.status_code == 402:
        return [], "payment_required"
    if resp.status_code >= 400:
        return [], f"http_{resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        return [], "bad_json"
    markdown = str(((payload.get("data") or {}).get("markdown")) or "")
    if not payload.get("success") or len(markdown) < MIN_BYTES:
        return [], "empty"
    posts = parse_markdown(handle, markdown)
    for post in posts:
        post.route = "pry"
    return posts, "" if posts else "pry:no_posts"


def brightdata(handle: str, _limiter: Limiter | None = None, _proxy: str | None = None) -> tuple[list[Post], str]:
    """Rendered markdown via the Bright Data Web Unlocker CLI (fallback)."""
    exe = shutil.which("bdata") or shutil.which("brightdata")
    if not exe:
        return [], "bdata_missing"
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            [exe, "scrape", f"https://x.com/{handle}", "-f", "markdown"],
            capture_output=True,
            text=True,
            timeout=BDATA_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"bdata:{type(exc).__name__}"
    body = "\n".join(
        line for line in proc.stdout.splitlines() if not line.startswith(_BDATA_NOISE)
    )
    if len(body) < MIN_BYTES:
        return [], f"bdata_empty_rc{proc.returncode}"
    posts = parse_markdown(handle, body)
    for post in posts:
        post.route = "brightdata"
    return posts, "" if posts else f"bdata_no_posts_rc{proc.returncode}"


def syndication(handle: str, limiter: Limiter, proxy: str | None = None) -> tuple[list[Post], str]:
    """X's own syndication timeline (id-complete, but 429s from this egress)."""
    status, body, err = get(SYNDICATION.format(handle=handle), limiter, proxy)
    if not body:
        return [], err or f"http_{status}"
    posts = parse_syndication(handle, body)
    return posts, "" if posts else "syndication_empty"


def nitter(handle: str, limiter: Limiter, proxy: str | None = None) -> tuple[list[Post], str]:
    """Opt-in nitter mirrors (all dead as of 2026-10; kept behind X_NITTER_MIRRORS)."""
    last = "no_mirrors"
    for mirror in os.environ.get("X_NITTER_MIRRORS", "").split():
        status, body, err = get(f"https://{mirror}/{handle}", limiter, proxy)
        posts = parse_nitter(handle, body) if body else []
        if posts:
            return posts, ""
        last = err or f"http_{status}"
    return [], last


def jina(handle: str, limiter: Limiter, proxy: str | None = None) -> tuple[list[Post], str]:
    """r.jina.ai reader (no ids, no timestamps)."""
    first = ""
    for template in READERS:
        _status, body, err = get(template.format(handle=handle), limiter, proxy)
        posts = parse_markdown(handle, body) if body else []
        if posts:
            return posts, ""
        first = first or err
    return [], first or "jina_empty"


def flaresolverr(url: str) -> tuple[int, str, str]:
    """Last resort for a Cloudflare interstitial (documented fleet path)."""
    try:
        resp = httpx.post(
            f"http://127.0.0.1:{FLAIR}/v1",
            json={"cmd": "request.get", "url": url, "maxTimeout": 60000},
            timeout=90.0,
        )
        solution = (resp.json() or {}).get("solution") or {}
        body = str(solution.get("response") or "")
        if int(solution.get("status") or 0) == 200 and len(body) >= MIN_BYTES:
            return 200, body, ""
        return int(solution.get("status") or 0), "", "flaresolverr_empty"
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return 0, "", f"flaresolverr:{type(exc).__name__}"


#: Stage name -> reader. Order is chosen by fetch.DEFAULT_STAGES.
STAGES = {
    "graphql": graphql,
    "pry": pry,
    "brightdata": brightdata,
    "syndication": syndication,
    "nitter": nitter,
    "jina": jina,
}
