"""Cross-check co-pushed tokens against the live RMI scanner."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("RMI_API_BASE", "http://127.0.0.1:8000")
SCAN_PATH = "/api/v2/scanner/token/scan?scan_mode=basic"


def _call(path: str, body: dict | None = None, timeout: int = 25) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"content-type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read()[:300].decode("utf-8", "ignore")}
    except Exception as e:
        return 0, {"error": str(e)}


def check_co_pushed(cops: list[dict], n: int = 5, fallback: list[dict] | None = None) -> dict:
    """Scan up to n tokens (co-pushed first, then top shill); queue the rest."""
    status, _ = _call("/health", timeout=5)
    healthy = status == 200
    cands, seen = [], set()
    for c in cops:
        if not c.get("ca") or c["ca"] in seen:
            continue
        seen.add(c["ca"])
        cands.append({**c, "source": "co_push"})
        if len(cands) >= n:
            break
    for e in sorted(fallback or [], key=lambda x: (-x["trust"], x["ts"])):
        if len(cands) >= n + 4:
            break
        if not e.get("ca") or e["ca"] in seen:
            continue
        seen.add(e["ca"])
        cands.append({"ticker": e["ticker"], "ca": e["ca"], "chain": e.get("chain"),
                      "channels": [e["channel"]], "window_start": e["ts"],
                      "source": "top_shill"})
    checked, queued = [], []
    for c in cands:
        if len(checked) >= n:
            break
        item = {"ticker": c["ticker"], "ca": c["ca"], "chain": c.get("chain") or "solana",
                "channels": c["channels"], "pushed_at": c["window_start"],
                "source": c.get("source", "co_push")}
        if not healthy:
            queued.append({**item, "reason": "scanner_unhealthy"})
            continue
        if item["chain"] == "evm":
            queued.append({**item, "reason": "chain_unresolved",
                           "detail": "EVM address with no chain hint in message"})
            continue
        code, body = _call(SCAN_PATH, {"address": item["ca"], "chain": item["chain"]})
        attempts = 1
        if code == 200 and (not body.get("name") or not body.get("price_usd")):
            time.sleep(1.5)  # metadata fetch is flaky on first hit
            code, body = _call(SCAN_PATH, {"address": item["ca"], "chain": item["chain"]})
            attempts = 2
        if code != 200:
            queued.append({**item, "reason": f"http_{code}", "detail": str(body)[:200]})
            continue
        degraded = not body.get("name") or not body.get("price_usd")
        checked.append({**item, "attempts": attempts, "safety_score": body.get("safety_score"),
                        "risk_level": body.get("risk_level"),
                        "risk_flags": body.get("risk_flags", [])[:6],
                        "confidence": body.get("confidence"),
                        "name": body.get("name"), "price_usd": body.get("price_usd"),
                        "degraded": degraded,
                        "usable": not degraded})
    return {"scanner_healthy": healthy, "endpoint": BASE + SCAN_PATH,
            "checked": checked, "queued": queued}


def write_queue(queued: list[dict], path: str) -> None:
    """Persist unscreened tokens for a later pass."""
    if not queued:
        return
    try:
        existing = json.load(open(path))
    except (OSError, ValueError):
        existing = []
    json.dump(existing + queued, open(path, "w"), indent=2)
