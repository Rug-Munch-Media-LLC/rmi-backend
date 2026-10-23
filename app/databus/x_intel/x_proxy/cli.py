"""CLI probe for the X proxy tier.

    python3 -m app.databus.x_intel.x_proxy.cli            # 3 live probes
    python3 -m app.databus.x_intel.x_proxy.cli --probes 5 --url https://x.com/nasa

Resolves the proxy list (WebShare API, then env fallback), issues N live
requests through the rotating transport, and prints the health snapshot.
Only proxy HOSTS and outcome counters are printed — never credentials, never
the proxy URLs themselves. Exit code 0 when every probe returned < 400.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

from app.databus.x_intel.x_proxy.client import health, x_http_client
from app.databus.x_intel.x_proxy.source import get_proxy_candidates

DEFAULT_URL = "https://x.com/"


def main(argv: list[str] | None = None) -> int:
    """Run ``--probes`` live requests and report credential-free health."""
    parser = argparse.ArgumentParser(prog="x_proxy", description=__doc__)
    parser.add_argument("--probes", type=int, default=3, help="live requests to issue")
    parser.add_argument("--url", default=DEFAULT_URL, help="target URL")
    args = parser.parse_args(argv)

    async def _run() -> int:
        proxies = await get_proxy_candidates(force=True)
        hosts = [health.host_of(p) for p in proxies]
        print(f"proxies={len(proxies)} hosts={hosts}")
        ok = 0
        async with x_http_client(timeout=20.0) as client:
            for index in range(max(args.probes, 1)):
                try:
                    response = await client.get(args.url)
                except httpx.HTTPError as exc:
                    print(f"probe{index + 1} error={type(exc).__name__}")
                    continue
                print(f"probe{index + 1} status={response.status_code} bytes={len(response.content)}")
                if response.status_code < 400:
                    ok += 1
        print(json.dumps(health.snapshot(), indent=2, sort_keys=True))
        return 0 if ok == max(args.probes, 1) else 1

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
