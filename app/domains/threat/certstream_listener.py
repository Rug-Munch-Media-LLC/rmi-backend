"""T12 - CertStream phishing domain monitor (G04 FIX adjacent).

Per MINIMAX_M3_TASKS.md T12. Watches Certificate Transparency logs in real
time for domains that spoof crypto brands (MetaMask, Ledger, Coinbase,
etc). When a match is found, fires a Telegram alert via the existing
bot and persists the domain to Postgres `threat_domains`.

Run as a background task in lifespan.py. If CertStream is down, log and
continue - never break startup.

Why this matters: phishing sites clone crypto brands and steal wallet
seeds. CT logs show new domains BEFORE they go live, giving a 48h lead
time to take them down.

Usage:
  from app.domains.threat.certstream_listener import start_listener
  asyncio.create_task(start_listener())        # in lifespan
  python3 -m app.domains.threat.certstream_listener --duration 300   # CLI
"""

from __future__ import annotations
from typing import Any

import asyncio
import json
import logging
import os
import signal
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Brand patterns (loaded once at import) ──────────────────────────
_BRAND_PATTERNS_PATH = Path(__file__).parent / "brand_patterns.json"


def load_brand_patterns() -> list[str]:
    """Load brand pattern list. Returns lowercased list for matching."""
    try:
        with _BRAND_PATTERNS_PATH.open() as f:
            data = json.load(f)
        patterns = [str(p).lower() for p in data.get("brands", []) if p]
        logger.info("certstream_brands_loaded count=%d", len(patterns))
        return patterns
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.debug("load_brand_patterns_error", exc_info=True)
        logger.warning("certstream_brands_load_failed err=%s", exc)
        return []


_BRAND_PATTERNS: list[str] = load_brand_patterns()


# ── Domain matching ────────────────────────────────────────────────
def match_brand(domain: str, brands: list[str] | None = None) -> str | None:
    """Return the matched brand name if `domain` looks like a phishing
    clone of a known crypto brand. Returns None if no match.

    Heuristic: brand substring appears in the domain's main label,
    but the domain is NOT the brand's official primary domain (we
    allow obvious legit variants like "metamask.io" but flag
    "metamask-secure-claim.com").
    """
    if not domain:
        return None
    d = domain.lower().strip()
    # strip trailing dot
    if d.endswith("."):
        d = d[:-1]
    # take the main label (e.g. "metamask-secure" from "a.b.metamask-secure.com")
    parts = d.split(".")
    if len(parts) < 2:
        return None
    main = parts[-2] if len(parts) >= 2 else parts[0]
    patterns = brands or _BRAND_PATTERNS
    for brand in patterns:
        # Strip non-alphanum for fuzzy match
        main_clean = "".join(c for c in main if c.isalnum())
        brand_clean = "".join(c for c in brand if c.isalnum())
        if not brand_clean:
            continue
        if brand_clean in main_clean and main_clean != brand_clean:
            # Phishing variant - not the official domain
            return brand
    return None


# ── Telegram alert (best-effort, never blocks) ─────────────────────
async def _telegram_alert(domain: str, brand: str, issuer: str) -> bool:
    """Send a Telegram alert. Returns True on success. Never raises."""
    try:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")
        if not bot_token or not chat_id:
            return False
        import httpx

        text = (
            f"⚠️ Phishing domain registered: {domain}\n"
            f"Brand: {brand}\n"
            f"Issuer: {issuer}\n"
            f"Issued: {datetime.now(UTC).isoformat()}\n"
            f"48h window to take down"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            return r.status_code == 200
    except (httpx.HTTPError, ValueError, TypeError, ConnectionError, OSError, TimeoutError) as exc:
        logger.debug("_telegram_alert_error", exc_info=True)
        logger.debug("telegram_alert_skipped err=%s", exc)
        return False


# ── Postgres persistence (lazy import) ──────────────────────────────
_THREAT_DOMAINS_SCHEMA = """
CREATE TABLE IF NOT EXISTS threat_domains (
  domain TEXT PRIMARY KEY,
  brand_matched TEXT,
  issued_at TIMESTAMPTZ,
  issuer TEXT,
  first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS threat_domains_first_seen_idx
  ON threat_domains (first_seen DESC);
CREATE INDEX IF NOT EXISTS threat_domains_brand_idx
  ON threat_domains (brand_matched);
"""


async def _ensure_schema() -> bool:
    try:
        import asyncpg

        from app.core.db_pool import PG_URL

        # Strip SQLAlchemy driver suffix (e.g. +asyncpg) for asyncpg.connect()
        _pg_dsn = PG_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(_pg_dsn)
        try:
            await conn.execute(_THREAT_DOMAINS_SCHEMA)
        finally:
            await conn.close()
        return True
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.debug("_ensure_schema_error", exc_info=True)
        logger.warning("threat_domains_schema_failed err=%s", exc)
        return False


async def _persist_domain(domain: str, brand: str, issued_at: datetime | None, issuer: str) -> bool:
    try:
        import asyncpg

        from app.core.db_pool import PG_URL

        # Strip SQLAlchemy driver suffix (e.g. +asyncpg) for asyncpg.connect()
        _pg_dsn = PG_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncpg.connect(_pg_dsn)
        try:
            await conn.execute(
                """
                INSERT INTO threat_domains (domain, brand_matched, issued_at, issuer)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (domain) DO NOTHING
                """,
                domain,
                brand,
                issued_at,
                issuer,
            )
        finally:
            await conn.close()
        return True
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        logger.debug("_persist_domain_error", exc_info=True)
        logger.debug("threat_domains_persist_failed domain=%s err=%s", domain, exc)
        return False


# ── CertStream WebSocket listener ──────────────────────────────────
# Public CertStream (CaliDog) - the canonical free CT feed.
CERTSTREAM_URL = os.getenv("CERTSTREAM_URL", "wss://certstream.calidog.io/")


async def _process_message(msg: dict) -> int:
    """Process one CertStream message. Returns # of threats found (0 or 1+)."""
    data_type = msg.get("message_type") or msg.get("type")
    if data_type not in ("certificate_update", "heartbeat"):
        return 0
    if data_type == "heartbeat":
        return 0

    leaf = msg.get("data", {}).get("leaf_cert", {}) or {}
    domains = set()
    # CertStream v2 puts all SANs here
    sans = leaf.get("all_domains") or []
    for d in sans:
        if d and isinstance(d, str) and not d.startswith("*"):
            domains.add(d.lower().strip())
    # Some payloads also have subject CN
    subject = (leaf.get("subject") or {}).get("CN", "")
    if subject and not subject.startswith("*"):
        domains.add(subject.lower().strip())

    issuer = msg.get("data", {}).get("chain", [{}])[0].get("subject", {}).get("O", "") or "unknown"
    issued_at = None
    not_before = leaf.get("not_before")
    if not_before:
        try:
            issued_at = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
        except (ConnectionError, OSError, TimeoutError):
            issued_at = datetime.now(UTC)
    if issued_at is None:
        issued_at = datetime.now(UTC)

    threats = 0
    for d in domains:
        brand = match_brand(d)
        if brand:
            threats += 1
            logger.warning(
                "certstream_threat_detected domain=%s brand=%s issuer=%s",
                d,
                brand,
                issuer,
            )
            await _persist_domain(d, brand, issued_at, issuer)
            await _telegram_alert(d, brand, issuer)
    return threats


async def _run_listener(stop_event: asyncio.Event) -> None:
    """Connect to CertStream and consume messages until stop_event."""
    try:
        import websockets
    except ImportError:
        logger.warning("certstream_skipped err=websockets_not_installed")
        return

    await _ensure_schema()
    backoff = 1.0
    while not stop_event.is_set():
        try:
            async with websockets.connect(CERTSTREAM_URL, ping_interval=20) as ws:
                logger.info("certstream_connected url=%s", CERTSTREAM_URL)
                backoff = 1.0
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except TimeoutError:
                        # send a ping to keep the connection alive
                        with suppress(Exception):
                            await ws.send("ping")
                        continue
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await _process_message(msg)
        except asyncio.CancelledError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.debug("_run_listener_error", exc_info=True)
            logger.warning("certstream_disconnected err=%s reconnecting_in=%.1fs", exc, backoff)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            backoff = min(backoff * 2, 60.0)


# ── Public API ─────────────────────────────────────────────────────
async def start_listener() -> asyncio.Task | None:
    """Start the CertStream listener as a background task.

    Returns the Task so caller can cancel on shutdown. Returns None if
    websockets is not installed or another instance is already running.
    """
    if os.getenv("CERTSTREAM_ENABLED", "1") != "1":
        logger.info("certstream_disabled env=CERTSTREAM_ENABLED=0")
        return None
    try:
        import websockets  # noqa: F401 — availability probe
    except ImportError:
        logger.info("certstream_skipped err=websockets_not_installed")
        return None
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _run_listener(stop_event),
        name="certstream-listener",
    )
    # Stash the stop event on the task so shutdown can set it
    task._rmi_stop_event = stop_event  # type: ignore[attr-defined]
    return task


async def stop_listener(task: asyncio.Task | None) -> None:
    """Stop a running CertStream listener task."""
    if task is None or task.done():
        return
    stop_event = getattr(task, "_rmi_stop_event", None)
    if stop_event is not None:
        stop_event.set()
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(task, timeout=5.0)


# ── CLI for ad-hoc testing ─────────────────────────────────────────
def _cli() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="CertStream phishing monitor (CLI)")
    parser.add_argument("--duration", type=int, default=300, help="seconds to listen (0 = forever)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def _main() -> None:
        stop = asyncio.Event()
        if args.duration > 0:

            async def _trigger() -> None:
                await asyncio.sleep(args.duration)
                stop.set()

            asyncio.create_task(_trigger())

        await _run_listener(stop)

    if args.duration == 0:
        # Forever mode: install SIGINT handler
        def _sigint(*_a: object) -> None:
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _sigint)
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[cli] stopped", file=sys.stderr)


if __name__ == "__main__":
    _cli()

__all__ = [
    "CERTSTREAM_URL",
    "load_brand_patterns",
    "match_brand",
    "start_listener",
    "stop_listener",
]
