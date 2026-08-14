"""
_ops_intel — chain/indexer status, alert dispatch, backend stats queries.

Extracted from ``admin_ops`` (degodding 2026-08-14).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from app.domains.telegram.adminbot._ops_core import _err, _ok, _run_subprocess


def chain_status() -> dict[str, Any]:
    """Get LiteIndexer chain sync status from Docker."""
    # Check for running chain indexer containers
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "--filter",
            "name=chain-indexer",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
        ],
        timeout=10,
    )
    if result["ok"] and result["output"].strip():
        return result

    # Fallback: check for any container with 'chain' or 'indexer' in name
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout=10,
    )
    # Filter for chain-related containers
    lines = ["📡 *Chain Indexer Status*\n"]
    found = False
    for line in result["output"].splitlines():
        if any(
            kw in line.lower()
            for kw in ["chain", "indexer", "lighthouse", "heimdall", "bor", "geth", "reth", "lodestar"]
        ):
            lines.append(f"  {line}")
            found = True
    if not found:
        lines.append("  No chain indexer containers detected.")
        lines.append("\n💡 Tip: Run `/search chain` or check with `/docker ps`")
    return _ok("\n".join(lines))


def send_alert(message: str, level: str = "info") -> dict[str, Any]:
    """Dispatch an alert to the configured admin chat via the running bot."""
    # Alerts are sent via the health endpoint or directly through the bot
    # This function creates a JSON payload for the Redis queue
    from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID

    if not message:
        return _err("Message is required")

    # Try to push to Redis queue for the bot to pick up
    try:
        import json

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        if not redis_password:
            for secret_file in ("/run/secrets/redis_password", "/srv/secrets/redis_password"):
                try:
                    with open(secret_file) as f:
                        redis_password = f.read().strip()
                        break
                except (FileNotFoundError, OSError):
                    continue

        if redis_password and "://" in redis_url and "@" not in redis_url:
            parts = redis_url.split("://", 1)
            redis_url = f"{parts[0]}://:{redis_password}@{parts[1]}"

        import redis as sync_redis

        # Force RESP2 protocol to avoid HELLO/AUTH ordering issue
        # with redis-py 8.x + DragonflyDB/Redis 7.x password auth.
        client = sync_redis.from_url(redis_url, protocol=2)
        payload = json.dumps(
            {
                "level": level.upper(),
                "title": "🔔 Admin Alert",
                "body": message,
                "source": "admin_bot",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            }
        )
        client.rpush("rag:telegram_queue", payload)
        client.close()
        return _ok(f"✅ Alert queued for chat {ADMIN_CHAT_ID}", duration=0.0)
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(f"Redis unavailable: {e}")


def rag_stats() -> dict[str, Any]:
    """Get RAG collection sizes and embedder status from the backend."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        r = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/rag/v2/stats",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return _ok(json.dumps(data))
        return _err(f"RAG stats returned {r.status_code}")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


def news_feed(limit: int = 5) -> dict[str, Any]:
    """Get latest rated news articles from the backend news feed."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        r = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/internal/news-feed",
            params={"channel": "news", "limit": limit, "min_score": 0},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return _ok(json.dumps(data.get("items", [])))
        return _err(f"News feed returned {r.status_code}")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


def scan_stats() -> dict[str, Any]:
    """Get scanner usage and own_data vs external breakdown."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        metrics = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/scanner/metrics",
            timeout=10,
        )
        sufficiency = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/scanner/sufficiency",
            timeout=10,
        )

        result = {}
        if metrics.status_code == 200:
            result["metrics"] = metrics.json()
        if sufficiency.status_code == 200:
            result["sufficiency"] = sufficiency.json()

        return _ok(json.dumps(result)) if result else _err("No scanner data available")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


def indexer_status() -> dict[str, Any]:
    """Get LiteIndexer chain-by-chain status from Docker, organized by chain."""
    # Check docker ps for chain-indexer containers with detailed info
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=chain-indexer",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}",
        ],
        timeout=10,
    )

    if result["ok"] and result["output"].strip():
        lines = ["📡 *Chain Indexer Status*\n"]
        for line in result["output"].splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                name, status, image = parts[0], parts[1], parts[2]
                ports = parts[3] if len(parts) > 3 else ""
                status_emoji = "✅" if "Up" in status else "🔴" if "Exited" in status else "⏳"
                name_clean = name.replace("chain-indexer-", "")
                lines.append(f"{status_emoji} `{name_clean}`")
                lines.append(f"   Status: {status}")
                if image != "<none>":
                    lines.append(f"   Image: {image}")
                if ports:
                    lines.append(f"   Ports: {ports}")
                lines.append("")
        if len(lines) <= 2:
            lines.append("  No chain-indexer containers found (docker ps -a).")
        return _ok("\n".join(lines))

    # Fallback: check all running containers for indexer/chain names
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout=10,
    )
    lines = ["📡 *Chain Indexer Status*\n"]
    found = False
    for line in result["output"].splitlines():
        if any(
            kw in line.lower()
            for kw in ["chain", "indexer", "lighthouse", "geth", "reth", "heimdall", "bor", "lodestar"]
        ):
            lines.append(f"  {line}")
            found = True
    if not found:
        lines.append("  No chain indexer containers detected.")
    return _ok("\n".join(lines))
