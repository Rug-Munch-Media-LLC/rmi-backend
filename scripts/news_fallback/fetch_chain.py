"""Transport chain for sources the RSS/DataBus path cannot reach.

Order: direct GET (browser UA) -> FlareSolverr :8192 -> pry scrape.
Every stage returns the raw body; no parsing happens here.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import httpx

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
FS_URL = "http://127.0.0.1:8192/v1"
TIMEOUT = 25.0
MIN_BYTES = 400


@dataclass
class Fetched:
    """One fetch attempt result."""

    url: str
    body: str
    route: str
    status: int
    error: str = ""


def _direct(url: str) -> tuple[Fetched | None, str]:
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
    if resp.status_code < 400 and len(resp.text) >= MIN_BYTES:
        return Fetched(url, resp.text, "direct", resp.status_code), ""
    return None, f"direct_{resp.status_code}"


def _flaresolverr(url: str) -> tuple[Fetched | None, str]:
    payload = {"cmd": "request.get", "url": url, "maxTimeout": 45000}
    resp = httpx.post(FS_URL, json=payload, timeout=90.0)
    solution = (resp.json() or {}).get("solution") or {}
    body = solution.get("response") or ""
    status = int(solution.get("status") or 0)
    if status == 200 and len(body) >= MIN_BYTES:
        return Fetched(url, body, "flaresolverr", status), ""
    return None, f"flaresolverr_{status or 'no_solution'}"


def _pry(url: str) -> tuple[Fetched | None, str]:
    exe = shutil.which("pry")
    if not exe:
        return None, "pry_missing"
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        [exe, "open", url], capture_output=True, text=True, timeout=150, check=False
    )
    body = proc.stdout or ""
    if len(body) >= MIN_BYTES:
        return Fetched(url, body, "pry", 0), ""
    return None, "pry_empty"


def fetch(url: str, start: int = 0) -> Fetched:
    """Try each transport from ``start``; report the chain that failed if all miss."""
    errors: list[str] = []
    for stage in (_direct, _flaresolverr, _pry)[start:]:
        try:
            got, err = stage(url)
        except Exception as exc:  # transport failures are expected here
            got, err = None, f"{stage.__name__}:{type(exc).__name__}"
        if got is not None:
            return got
        errors.append(err)
    return Fetched(url, "", "blocked", 0, ";".join(errors))
