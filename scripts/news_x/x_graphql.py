"""X profile timelines over the web GraphQL API, using this host's session.

The contract (query id + 40 feature flags + field toggles) is captured, not
guessed — see `x_graphql_features`. Two ops are used: UserByScreenName (id and
followers) and UserOriginalsTimeline (the profile "posts" timeline).

Why that matters: X renamed the timeline op. `UserTweets` no longer exists in
the web bundle, so any id for it returns either an empty timeline or a
media-only one, never an error. A call that comes back `timeline_empty` /
`query_not_found` therefore triggers `discover()` — one browser capture, one
retry — instead of a silent miss.

Politeness stays in http_get (<=1 req/s/host); this module only owns auth,
pagination and parsing. Never raises at the caller: (posts, error).
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

import httpx
import x_session
from http_get import Limiter
from model import Post
from x_graphql_features import GRAPHQL, contract
from x_graphql_parse import bottom_cursor, parse_timeline, profile

TIMEOUT = 35.0
MAX_PAGES = 3
DISCOVER_ON = ("timeline_empty", "query_not_found", "http_404")


def _q(value: object) -> str:
    """Query-string encoding: plain strings quoted, objects as compact JSON."""
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return quote(str(text), safe="")


def _call(
    qid: str,
    op: str,
    params: dict[str, object],
    handle: str,
    limiter: Limiter | None = None,
    proxy: str | None = None,
) -> tuple[dict, str]:
    """One GraphQL GET. Returns (payload, error-code); never raises."""
    head, err = x_session.headers(handle)
    if err:
        return {}, err
    query = "&".join(f"{k}={_q(v)}" for k, v in params.items())
    url = f"{GRAPHQL}/{qid}/{op}?{query}"
    if limiter is not None and not limiter.wait(url):
        return {}, "host_parked"
    kwargs: dict[str, object] = {"timeout": TIMEOUT, "follow_redirects": True}
    if proxy:
        kwargs["proxy"] = proxy
    try:
        resp = httpx.get(url, headers=head, **kwargs)
    except httpx.HTTPError as exc:
        return {}, type(exc).__name__
    if resp.status_code in (401, 403):
        return {}, f"auth_http_{resp.status_code}"
    if resp.status_code == 404:
        return {}, "query_not_found"
    if resp.status_code == 429:
        return {}, "rate_limited"
    if resp.status_code >= 400:
        return {}, f"http_{resp.status_code}"
    try:
        payload = resp.json()
    except ValueError:
        return {}, "bad_json"
    if payload.get("errors") and not payload.get("data"):
        message = str((payload["errors"] or [{}])[0].get("message") or "")[:120]
        return payload, "not_found" if "not found" in message.lower() else message
    return payload, ""


def user(handle: str, limiter: Limiter | None = None, proxy: str | None = None) -> dict | None:
    """Resolve a screen name to id/followers; None when the account is gone."""
    fields = contract()
    params = {
        "variables": json.dumps({"screen_name": handle, "withGrokTranslatedBio": False}),
        "features": json.dumps(fields["features"]),
        "fieldToggles": json.dumps({"withAuxiliaryUserLabels": False}),
    }
    payload, _err = _call(
        str(fields["user_query_id"]), "UserByScreenName", params, handle, limiter, proxy
    )
    result = ((payload.get("data") or {}).get("user") or {}).get("result") or {}
    if not result:
        return None
    return profile(result, handle)


def _instructions(payload: dict) -> list:
    """Timeline instructions from either the timeline or timeline_v2 shape."""
    result = ((payload.get("data") or {}).get("user") or {}).get("result") or {}
    timeline = (result.get("timeline") or result.get("timeline_v2") or {}).get("timeline") or {}
    return timeline.get("instructions") or []


def _pages(
    uid: str,
    handle: str,
    count: int,
    limiter: Limiter | None,
    proxy: str | None,
) -> tuple[list[Post], str]:
    """Walk bottom cursors until `count` posts or MAX_PAGES."""
    fields = contract()
    posts: list[Post] = []
    seen: set[str] = set()
    cursor = ""
    err = ""
    for _page in range(MAX_PAGES):
        variables = dict(fields["variables"])
        variables.update({"userId": uid, "count": min(20, max(count, 20))})
        if cursor:
            variables["cursor"] = cursor
        params = {
            "variables": json.dumps(variables),
            "features": json.dumps(fields["features"]),
            "fieldToggles": json.dumps(fields["field_toggles"]),
        }
        payload, err = _call(
            str(fields["query_id"]), str(fields["operation"]), params, handle, limiter, proxy
        )
        if err:
            break
        instructions = _instructions(payload)
        for post in parse_timeline(instructions, handle):
            if post.dedupe_key not in seen:
                seen.add(post.dedupe_key)
                posts.append(post)
        cursor = bottom_cursor(instructions)
        if len(posts) >= count or not cursor:
            break
    if posts:
        return posts, ""
    return [], err or "timeline_empty"


def discover(handle: str = "lookonchain") -> dict:
    """Re-capture the contract from a live browser session and persist it."""
    import x_capture

    record = x_capture.capture(handle)
    if record.get("ok"):
        x_capture.save(record)
    return record


def timeline(
    handle: str,
    limiter: Limiter | None = None,
    proxy: str | None = None,
    count: int = 40,
) -> tuple[list[Post], str]:
    """Timeline for one handle as Post objects; (posts, error) never raises."""
    lim = limiter or Limiter()
    proxy = proxy or os.environ.get("X_INGEST_PROXY") or None
    account = user(handle, lim, proxy)
    if not account:
        return [], "unknown_handle"
    uid = str(account.get("user_id") or "")
    if not uid:
        return [], "no_user_id"
    posts, err = _pages(uid, handle, count, lim, proxy)
    if posts or err not in DISCOVER_ON or os.environ.get("X_NO_DISCOVER"):
        return posts, err
    record = discover(handle)
    if not record.get("ok"):
        return [], f"{err}:discover_{record.get('error', 'failed')}"
    return _pages(uid, handle, count, lim, proxy)
