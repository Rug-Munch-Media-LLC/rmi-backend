"""Live health probe for the free chat pool (one small request per lane)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Final

import httpx

if TYPE_CHECKING:
    from app.assistant_gateway.free_pool.spec import PoolSpec, ProviderSpec

PING: Final = "Reply with exactly: OK"
QUOTA_HEADERS: Final = ("rate", "quota", "limit", "remaining", "retry-after")


def _request(spec: ProviderSpec, key: str, prompt: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Build (url, headers, payload) for the provider's wire format."""
    if spec.kind == "gemini":
        url = f"{spec.base_url.rstrip('/')}/models/{spec.model}:generateContent"
        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 64},
        }
        return url, headers, payload
    url = f"{spec.base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 rugmunch-fleet/1.0",
    }
    payload = {
        "model": spec.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 64,
    }
    return url, headers, payload


def _answer(spec: ProviderSpec, body: dict[str, Any]) -> str:
    if spec.kind == "gemini":
        parts = (body.get("candidates") or [{}])[0].get("content", {}).get("parts") or [{}]
        return str(parts[0].get("text") or "").strip()
    message = (body.get("choices") or [{}])[0].get("message") or {}
    return str(message.get("content") or message.get("reasoning") or "").strip()


async def probe_provider(
    spec: ProviderSpec,
    client: httpx.AsyncClient | None = None,
    secrets_dir: str | None = None,
) -> dict[str, Any]:
    """One live call. Always returns a row; unusable lanes carry a reason."""
    key = spec.api_key(secrets_dir)
    if not key:
        return {
            "provider": spec.id,
            "model": spec.model,
            "ok": False,
            "status": None,
            "seconds": None,
            "reason": f"missing key ({spec.secret_env})",
            "quota": {},
        }
    url, headers, payload = _request(spec, key, PING)
    started = time.monotonic()
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=spec.timeout)
    try:
        response = await client.post(url, headers=headers, json=payload)
        elapsed = round(time.monotonic() - started, 2)
        quota = {
            k.lower(): v for k, v in response.headers.items() if any(token in k.lower() for token in QUOTA_HEADERS)
        }
        try:
            body = response.json()
        except ValueError:
            body = {}
        answer = _answer(spec, body) if response.status_code == 200 else ""
        return {
            "provider": spec.id,
            "model": spec.model,
            "ok": response.status_code == 200,
            "status": response.status_code,
            "seconds": elapsed,
            "answer": answer[:80],
            "reason": "" if response.status_code == 200 else str(body)[:160],
            "quota": quota,
        }
    except httpx.HTTPError as exc:
        return {
            "provider": spec.id,
            "model": spec.model,
            "ok": False,
            "status": None,
            "seconds": round(time.monotonic() - started, 2),
            "reason": f"{type(exc).__name__}: {exc}"[:160],
            "quota": {},
        }
    finally:
        if own_client:
            await client.aclose()


async def probe_pool(spec: PoolSpec, *, concurrency: int = 4, secrets_dir: str | None = None) -> dict[str, Any]:
    """Probe enabled lanes in parallel; report healthy order and totals."""
    import asyncio

    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=150.0) as client:

        async def run(provider: ProviderSpec) -> dict[str, Any]:
            async with semaphore:
                return await probe_provider(provider, client, secrets_dir)

        rows = await asyncio.gather(*(run(p) for p in spec.enabled()))
    healthy = [row["provider"] for row in rows if row["ok"]]
    return {
        "pool": spec.name,
        "probed": len(rows),
        "healthy": healthy,
        "exhausted": not healthy,
        "fail_closed_code": spec.fail_closed_code,
        "results": rows,
    }
