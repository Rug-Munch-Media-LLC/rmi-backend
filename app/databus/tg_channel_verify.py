"""One-time liveness probe for Telegram-channel handles in the ``tg_channels`` registry.

The registry (``app/databus/tg_channels.json``) carries several medium-confidence
handles flagged with ``[REVIEW]`` notes. Before the scraper trusts them, this
module probes each handle against Telegram's public web preview and records a
``live``/``dead`` verdict in ``tg_channels_status.json`` (adjacent to the registry).

Design
------
* ``probe_channel`` performs a non-redirecting ``GET https://t.me/{username}``
  through an injectable ``httpx.AsyncClient`` (no secrets, no API tokens).
* The verdict is driven by HTTP status **and** page markers:

  - 200 + a ``tgme_page``/``tgme_channel_info`` marker  -> live
  - 3xx redirect to the t.me **root** (dead-handle signal) -> dead
  - 3xx redirect to a username-specific preview or ``tg://resolve`` -> live
  - 404 or any other status -> dead
  - any transport/parsing exception -> ``live=False`` + ``error`` (fail-open,
    the function never raises)

* ``verify_registry`` probes only ``enabled`` channels under a small
  ``asyncio.Semaphore`` (default 5) so the sweep is rate-limit friendly.
* ``write_status`` persists per-channel verdicts next to ``tg_channels.json``
  and is fail-open (never raises on write errors).

This module makes no real network calls on import; it is exercised offline via
injected fake clients in ``tests/test_tg_channel_verify.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.databus import tg_channel_registry as tcr

TG_CHANNELS_FILE = tcr.TG_CHANNELS_FILE
TG_STATUS_FILE = os.path.join(os.path.dirname(__file__), "tg_channels_status.json")

TME_BASE = "https://t.me"
_PROBE_TIMEOUT = 15.0
_DEFAULT_CONCURRENCY = 5
_USER_AGENT = "RugMunch-TG-Verify/1.0"

# HTML markers present on a real Telegram channel public-preview page.
_CHANNEL_PAGE_MARKERS = ("tgme_page", "tgme_channel_info")

_CANONICAL_RE = re.compile(
    r"""rel=["']canonical["'][^>]*href=["']https://t\.me/(?:s/)?([A-Za-z0-9_]+)["']""",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_RESOLVE_RE = re.compile(r"resolve\?domain(?:=|%3[dD])([a-z0-9_]+)", re.IGNORECASE)


def _normalize_headers(raw: Any) -> dict[str, str]:
    """Coerce a response ``headers`` object (dict, httpx.Headers, ...) to a plain dict."""
    if raw is None:
        return {}
    items = raw.items() if hasattr(raw, "items") else []
    return {str(k).lower(): str(v) for k, v in items}


def _path_segments(url: str) -> list[str]:
    """Path segments of a URL (scheme/query/fragment stripped)."""
    path = url.split("//", 1)[-1]
    path = path[path.index("/") + 1 :] if "/" in path else ""
    path = path.split("?", 1)[0].split("#", 1)[0]
    return [seg for seg in path.split("/") if seg]


def _location_targets_username(location: str, normalized: str) -> bool:
    """True when a redirect Location still points at this channel (not the root)."""
    lowered = location.lower()
    resolve = _RESOLVE_RE.search(lowered)
    if resolve:
        return resolve.group(1).lstrip("@") == normalized
    segments = _path_segments(lowered)
    if not segments:
        return False  # redirect back to t.me root -> dead handle
    first = segments[0]
    if first == "s" and len(segments) > 1:
        first = segments[1]  # t.me/s/{username} public preview
    return first == normalized


def _extract_canonical_username(body: str) -> str | None:
    match = _CANONICAL_RE.search(body)
    return match.group(1) if match else None


def _extract_title(body: str) -> str | None:
    match = _TITLE_RE.search(body)
    title = match.group(1).strip() if match else ""
    return title or None


def _classify_response(
    normalized: str, status: int, headers: dict[str, str], body: str
) -> tuple[bool, str | None, str | None]:
    """Map an HTTP response to ``(live, resolved_username, error)``.

    The heuristic: a real channel preview answers 200 with ``tgme_*`` page
    markers; a dead handle redirects to the t.me root (or 404s); a valid but
    preview-disabled handle redirects to a username-specific URL or a
    ``tg://resolve`` link.
    """
    if 200 <= status < 300:
        if any(marker in body for marker in _CHANNEL_PAGE_MARKERS):
            resolved = _extract_canonical_username(body) or normalized
            return True, resolved, None
        return False, None, "200 response missing channel-preview markers"
    if 300 <= status < 400:
        location = headers.get("location", "") or ""
        if location and _location_targets_username(location, normalized):
            segments = _path_segments(location)
            resolved = segments[-1] if segments else normalized
            return True, resolved, None
        if location:
            return False, None, "redirected to t.me root (handle not found)"
        return False, None, "redirect without location header"
    if status == 404:
        return False, None, "HTTP 404 (handle not found)"
    return False, None, f"unexpected HTTP status {status}"


async def probe_channel(username: str, client: Any | None = None) -> dict[str, Any]:
    """Probe one Telegram handle against the public web preview.

    Returns ``{username, live, resolved_username, title, error}``. Fail-open:
    never raises; any exception yields ``live=False`` plus ``error``. An
    injected ``client`` (``httpx.AsyncClient``-like) is used as-is; when
    ``None`` a short-lived client is created and closed.
    """
    normalized = tcr.channel_dedup_key(username)
    if not normalized:
        return {
            "username": username,
            "live": False,
            "resolved_username": None,
            "title": None,
            "error": "empty username",
        }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        )
    assert client is not None
    try:
        response = await client.get(f"{TME_BASE}/{normalized}", follow_redirects=False)
        status = int(getattr(response, "status_code", 0) or 0)
        headers = _normalize_headers(getattr(response, "headers", None))
        body = str(getattr(response, "text", "") or "")
        live, resolved, error = _classify_response(normalized, status, headers, body)
        return {
            "username": username,
            "live": live,
            "resolved_username": resolved,
            "title": _extract_title(body) if live else None,
            "error": error,
        }
    except Exception as exc:  # fail-open: report dead, never propagate
        return {
            "username": username,
            "live": False,
            "resolved_username": None,
            "title": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if own_client:
            assert client is not None
            await client.aclose()


async def verify_registry(
    channels: list[dict[str, Any]],
    client: Any | None = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Probe every ``enabled`` channel, rate-limit friendly.

    Returns ``{verified: [...], dead: [...], summary: {total, live, dead}}``.
    Each result preserves the channel's ``username``, ``kind``, ``reliable``
    and ``notes`` alongside the probe verdict.
    """
    enabled = [c for c in channels if c.get("enabled", True) is True]
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        )

    async def _probe(entry: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await probe_channel(str(entry.get("username", "")), client=client)
        result["username"] = entry.get("username")
        result["kind"] = entry.get("kind")
        result["reliable"] = entry.get("reliable", False)
        result["notes"] = entry.get("notes", "")
        return result

    try:
        results = await asyncio.gather(*(_probe(e) for e in enabled), return_exceptions=True)
    finally:
        if own_client:
            assert client is not None
            await client.aclose()

    verified: list[dict[str, Any]] = []
    dead: list[dict[str, Any]] = []
    for result in results:
        if isinstance(result, Exception):
            dead.append(
                {
                    "username": None,
                    "live": False,
                    "kind": None,
                    "reliable": None,
                    "notes": None,
                    "error": f"{type(result).__name__}: {result}",
                }
            )
            continue
        assert isinstance(result, dict)
        if result.get("live"):
            verified.append(result)
        else:
            dead.append(result)

    summary = {"total": len(enabled), "live": len(verified), "dead": len(dead)}
    return {"verified": verified, "dead": dead, "summary": summary}


def write_status(results: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Write per-channel verdicts next to ``tg_channels.json``.

    Produces ``{version, updated, generated_at, summary, channels}`` where each
    channel carries ``{username, live, probed_at, kind, reliable, notes, error}``.
    Fail-open: on any write/serialization error the returned dict gains an
    ``error`` key instead of raising.
    """
    if path is None:
        path = TG_STATUS_FILE
    generated_at = datetime.now(UTC).isoformat()
    entries = [*results.get("verified", []), *results.get("dead", [])]
    channels = [
        {
            "username": entry.get("username"),
            "live": bool(entry.get("live")),
            "probed_at": entry.get("probed_at") or generated_at,
            "kind": entry.get("kind"),
            "reliable": entry.get("reliable"),
            "notes": entry.get("notes"),
            "error": entry.get("error"),
        }
        for entry in entries
    ]
    payload: dict[str, Any] = {
        "version": "1.0",
        "updated": date.today().isoformat(),
        "generated_at": generated_at,
        "summary": results.get("summary", {}),
        "channels": channels,
    }
    try:
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m app.databus.tg_channel_verify --probe [--limit N]``."""
    parser = argparse.ArgumentParser(
        prog="tg_channel_verify",
        description="Probe enabled Telegram-channel handles for liveness and write tg_channels_status.json.",
    )
    parser.add_argument("--probe", action="store_true", help="Probe enabled channels and write status")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of enabled channels probed (0/absent = all)",
    )
    args = parser.parse_args(argv)
    if not args.probe:
        parser.print_help()
        return 2

    channels = tcr.enabled_channels()
    if args.limit and args.limit > 0:
        channels = channels[: args.limit]

    results = asyncio.run(verify_registry(channels))
    payload = write_status(results)
    summary = results["summary"]
    print(f"total={summary['total']} live={summary['live']} dead={summary['dead']}")
    for entry in results["dead"]:
        print(f"  DEAD {entry.get('username')!r}: {entry.get('error') or 'not found'}")
    if payload.get("error"):
        print(f"status write failed: {payload['error']}", file=sys.stderr)
        return 1
    print(f"status written to {TG_STATUS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
