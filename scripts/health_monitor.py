#!/usr/bin/env python3
"""
RMI Health Monitoring Cron
============================
Checks: backend health, worker health (base + solana), Redis connectivity.
POSTs alerts to a webhook if anything is down.

Run via cron every 1-5 minutes:
  */2 * * * * cd /root/backend && python3 scripts/health_monitor.py >> /var/log/rmi_health.log 2>&1

Environment variables:
  HEALTH_WEBHOOK_URL  - webhook URL for alerts (Slack, Discord, PagerDuty, etc.)
  BACKEND_URL         - backend health endpoint (default: http://localhost:8000/health)
  REDIS_HOST          - Redis host (default: localhost)
  REDIS_PORT          - Redis port (default: 6379)
  REDIS_PASSWORD      - Redis password (default: empty)
  BASE_WORKER_URL     - Base worker health endpoint
  SOLANA_WORKER_URL   - Solana worker health endpoint

Author: RMI Development
Date: 2026-05-20
"""

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("health_monitor")

# ── Configuration ─────────────────────────────────────────────────

HEALTH_WEBHOOK_URL = os.getenv("HEALTH_WEBHOOK_URL", "")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/health")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
BASE_WORKER_URL = os.getenv("BASE_WORKER_URL", "http://localhost:8000/api/v1/x402/fallback/status")
SOLANA_WORKER_URL = os.getenv("SOLANA_WORKER_URL", "")
CHECK_TIMEOUT = 10  # seconds
ALERT_COOLDOWN = 300  # 5 minutes - don't re-alert for same service within this window

# Track last alert time per service to avoid spam
_last_alerts: dict = {}


def _load_cooldown_state():
    """Load alert cooldown state from file (survives restarts)."""
    try:
        state_file = "/tmp/rmi_health_cooldown.json"
        if os.path.exists(state_file):
            with open(state_file) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cooldown_state(state: dict):
    """Save alert cooldown state to file."""
    try:
        state_file = "/tmp/rmi_health_cooldown.json"
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def _should_alert(service: str) -> bool:
    """Check if we should send an alert (respects cooldown)."""
    now = time.time()
    state = _load_cooldown_state()
    last = state.get(service, 0)
    if now - last < ALERT_COOLDOWN:
        return False
    state[service] = now
    _save_cooldown_state(state)
    return True


# ── Health checks ──────────────────────────────────────────────────


def check_backend() -> dict:
    """Check backend /health endpoint."""
    try:
        req = urllib.request.Request(BACKEND_URL, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return {"service": "backend", "status": "healthy", "details": data}
    except Exception as e:
        return {"service": "backend", "status": "down", "error": str(e)[:200]}


def check_redis() -> dict:
    """Check Redis connectivity."""
    try:
        import redis

        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
        )
        r.ping()
        info = r.info("server")
        return {
            "service": "redis",
            "status": "healthy",
            "details": {
                "version": info.get("redis_version", "unknown"),
                "uptime_seconds": info.get("uptime_in_seconds", 0),
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_human": info.get("used_memory_human", "unknown"),
            },
        }
    except Exception as e:
        return {"service": "redis", "status": "down", "error": str(e)[:200]}


def check_base_worker() -> dict:
    """Check Base x402 worker/gateway health."""
    try:
        req = urllib.request.Request(BASE_WORKER_URL, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return {"service": "base_worker", "status": "healthy", "details": data}
    except Exception as e:
        return {"service": "base_worker", "status": "down", "error": str(e)[:200]}


def check_solana_worker() -> dict:
    """Check Solana x402 worker/gateway health."""
    if not SOLANA_WORKER_URL:
        return {
            "service": "solana_worker",
            "status": "not_configured",
            "details": "SOLANA_WORKER_URL not set",
        }
    try:
        req = urllib.request.Request(SOLANA_WORKER_URL, method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return {"service": "solana_worker", "status": "healthy", "details": data}
    except Exception as e:
        return {"service": "solana_worker", "status": "down", "error": str(e)[:200]}


# ── Alert dispatch ──────────────────────────────────────────────────


def send_alert(results: list):
    """POST alert to webhook for any down services."""
    if not HEALTH_WEBHOOK_URL:
        logger.warning("HEALTH_WEBHOOK_URL not set - skipping alert dispatch")
        return

    down_services = [r for r in results if r.get("status") == "down"]
    if not down_services:
        return  # All healthy

    # Filter by cooldown
    alertable = [s for s in down_services if _should_alert(s["service"])]
    if not alertable:
        logger.info(f"{len(down_services)} service(s) down but in alert cooldown")
        return

    # Build alert payload
    alert_lines = []
    for s in alertable:
        alert_lines.append(f"**{s['service']}** is DOWN: {s.get('error', 'unknown error')}")

    # Support multiple webhook formats
    # Slack/Discord format
    payload = {
        "text": f"RMI Health Alert - {len(alertable)} service(s) down",
        "attachments": [
            {
                "title": "Service Health Alert",
                "color": "danger",
                "text": "\n".join(alert_lines),
                "footer": "RMI Health Monitor",
                "ts": int(time.time()),
            }
        ],
        # Also include raw data for custom webhooks
        "services": alertable,
        "all_results": results,
    }

    # Try sending
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            HEALTH_WEBHOOK_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Alert sent to webhook: HTTP {resp.status} - {len(alertable)} services down")
    except Exception as e:
        logger.error(f"Failed to send alert to webhook: {e}")


def send_recovery_alert(recovered: list):
    """Send recovery notification."""
    if not HEALTH_WEBHOOK_URL or not recovered:
        return

    # Clear cooldown for recovered services
    state = _load_cooldown_state()
    for s in recovered:
        service_name = s["service"]
        if service_name in state:
            del state[service_name]
    _save_cooldown_state(state)

    alertable = [s for s in recovered if _should_alert(f"{s['service']}_recovery")]
    if not alertable:
        return

    lines = [f"**{s['service']}** is back UP" for s in alertable]
    payload = {
        "text": f"RMI Recovery - {len(alertable)} service(s) recovered",
        "attachments": [
            {
                "title": "Service Recovery",
                "color": "good",
                "text": "\n".join(lines),
                "footer": "RMI Health Monitor",
                "ts": int(time.time()),
            }
        ],
    }
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            HEALTH_WEBHOOK_URL,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10):
            logger.info(f"Recovery alert sent: {len(alertable)} services recovered")
    except Exception as e:
        logger.error(f"Failed to send recovery alert: {e}")


# ── Previous state tracking ─────────────────────────────────────────


def _load_prev_state():
    """Load previous health state for recovery detection."""
    try:
        state_file = "/tmp/rmi_health_state.json"
        if os.path.exists(state_file):
            with open(state_file) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_prev_state(state: dict):
    """Save current health state for recovery detection."""
    try:
        state_file = "/tmp/rmi_health_state.json"
        with open(state_file, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


# ── Main ────────────────────────────────────────────────────────────


def main():
    """Run all health checks and dispatch alerts."""
    # Support loading from any .env file
    if "--dotenv" in sys.argv:
        import dotenv

        idx = sys.argv.index("--dotenv")
        if idx + 1 < len(sys.argv):
            dotenv.load_dotenv(sys.argv[idx + 1])
        else:
            dotenv.load_dotenv()
    elif os.path.exists("/root/.env"):
        try:
            from dotenv import load_dotenv

            load_dotenv("/root/.env")
        except ImportError:
            pass

    # Re-read config after dotenv
    global HEALTH_WEBHOOK_URL, BACKEND_URL, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
    global BASE_WORKER_URL, SOLANA_WORKER_URL
    HEALTH_WEBHOOK_URL = os.getenv("HEALTH_WEBHOOK_URL", "")
    BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000/health")
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
    BASE_WORKER_URL = os.getenv("BASE_WORKER_URL", "http://localhost:8000/api/v1/x402/fallback/status")
    SOLANA_WORKER_URL = os.getenv("SOLANA_WORKER_URL", "")

    logger.info("Running health checks...")

    results = [
        check_backend(),
        check_redis(),
        check_base_worker(),
        check_solana_worker(),
    ]

    # Detect recoveries
    prev_state = _load_prev_state()
    current_state = {r["service"]: r["status"] for r in results}
    recovered = []
    for r in results:
        if r["status"] == "healthy" and prev_state.get(r["service"]) == "down":
            recovered.append(r)

    # Save current state
    _save_prev_state(current_state)

    # Log results
    for r in results:
        status = r["status"]
        if status == "healthy":
            logger.info(f"  {r['service']}: healthy")
        elif status == "not_configured":
            logger.info(f"  {r['service']}: not configured (skipped)")
        else:
            logger.error(f"  {r['service']}: DOWN - {r.get('error', 'unknown')}")

    # Send alerts for down services
    send_alert(results)

    # Send recovery alerts
    if recovered:
        send_recovery_alert(recovered)
        for r in recovered:
            logger.info(f"  {r['service']}: RECOVERED")

    # Print summary
    healthy = sum(1 for r in results if r["status"] == "healthy")
    total = len(results)
    down = sum(1 for r in results if r["status"] == "down")

    logger.info(f"Health summary: {healthy}/{total} healthy, {down} down")

    # Exit with error code if anything is down (useful for cron monitoring)
    if down > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
