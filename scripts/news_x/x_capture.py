"""Capture X's live profile-timeline GraphQL contract from a real browser session.

The query id and the feature set change with every web build, and a stale set
does not error: it returns `timeline_empty` or a media-only timeline. Guessing is
therefore banned — this module drives Chromium with the session cookies, opens a
real profile, and records the request the site itself makes.

Cookie *values* are never written or printed; `x-csrf-token` is recorded only as
a presence flag because it is the ct0 cookie.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import x_session

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "usertweets_live.json"
UA = x_session.UA
#: Both ops are recorded; the profile timeline is whichever X currently ships.
WANTED = ("UserOriginalsTimeline", "UserTweets")
KEEP_HEADERS = ("authorization", "x-twitter-auth-type", "x-twitter-active-user")


def _query(url: str) -> tuple[str, str, dict]:
    """(query_id, operation, params) from an /i/api/graphql/ URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    qid = parts[3] if len(parts) > 3 else ""
    op = parts[4] if len(parts) > 4 else ""
    return qid, op, {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _json(value: str) -> object:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


def _record(request, response) -> dict | None:
    """One captured request, cookie-free."""
    qid, op, params = _query(request.url)
    if op not in WANTED or not qid:
        return None
    headers = request.all_headers()
    rec: dict = {
        "query_id": qid,
        "operation": op,
        "variables": _json(params.get("variables", "{}")),
        "features": _json(params.get("features", "{}")),
        "field_toggles": _json(params.get("fieldToggles", "{}")),
        "headers": {k.lower(): headers[k] for k in KEEP_HEADERS if k in headers},
        "csrf_token_present": bool(headers.get("x-csrf-token")),
        "params_present": sorted(params),
    }
    if response is not None:
        try:
            rec["status"] = response.status
            rec["posts"] = response.text().count('"tweet_results"')
        except Exception as exc:  # browser closed mid-flight
            rec["status"] = 0
            rec["body_error"] = type(exc).__name__
    return rec


def _cookies(jar: dict[str, str]) -> list[dict]:
    """Playwright cookie objects from the two session cookies."""
    return [
        {
            "name": name,
            "value": value,
            "domain": ".x.com",
            "path": "/",
            "secure": True,
            "httpOnly": name == "auth_token",
            "expires": -1,
        }
        for name, value in jar.items()
        if value
    ]


def capture(handle: str = "lookonchain", timeout_ms: int = 45000) -> dict:
    """Open the profile in Chromium and return the captured contract."""
    from playwright.sync_api import sync_playwright

    jar = _cookies(x_session.jar())
    if not jar:
        return {"ok": False, "error": "no_session_cookies"}
    captured: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = browser.new_context(
            user_agent=UA, viewport={"width": 1366, "height": 900}, locale="en-US"
        )
        ctx.add_cookies(jar)
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()

        def on_response(resp) -> None:
            if "/i/api/graphql/" not in resp.url or not any(w in resp.url for w in WANTED):
                return
            rec = _record(resp.request, resp)
            if rec and not any(c["query_id"] == rec["query_id"] for c in captured):
                captured.append(rec)

        page.on("response", on_response)
        page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded", timeout=timeout_ms)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline and not captured:
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(1500)
        page.wait_for_timeout(1500)
        title = page.title()
        browser.close()

    if not captured:
        return {"ok": False, "error": "no_timeline_request", "title": title}
    best = max(captured, key=lambda c: int(c.get("posts") or 0))
    best = dict(best, ok=True, captured_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return best


def save(rec: dict) -> Path:
    """Write the fixture (directory created on demand)."""
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return FIXTURE


if __name__ == "__main__":
    import os

    out = capture(os.environ.get("X_CAPTURE_HANDLE", "lookonchain"))
    safe = {k: v for k, v in out.items() if k not in ("features", "variables", "field_toggles")}
    print(json.dumps(safe, indent=2))
    if out.get("ok"):
        print("fixture:", save(out))
