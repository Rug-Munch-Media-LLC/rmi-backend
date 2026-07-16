#!/usr/bin/env python3
"""Cost tracker poller.

Two modes:
  1. Poll LiteLLM /spend/logs (proxy mode)
  2. Standalone test mode (pushes sample data)

Usage:
    python scripts/cost_tracker.py              # poll LiteLLM
    python scripts/cost_tracker.py --test        # insert sample data
    python scripts/cost_tracker.py --summary     # print daily summary
"""

import argparse
import os

import httpx

CLICKHOUSE_HOST = os.getenv("CH_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CH_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CH_USER", "rmi")
CLICKHOUSE_PASSWORD = os.environ["CH_PASSWORD"]

LITELLM_URL = os.getenv("LITELLM_URL", "http://127.0.0.1:4000")
LITELLM_API_KEY = os.environ["LITELLM_API_KEY"]


def _ch_client():
    from clickhouse_driver import Client

    return Client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        user=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database="rmi",
        connect_timeout=5,
        send_receive_timeout=10,
    )


def poll_litellm():
    """Poll the LiteLLM /spend/logs endpoint and write to ClickHouse."""
    headers = {"Authorization": f"Bearer {LITELLM_API_KEY}"}
    try:
        resp = httpx.get(
            f"{LITELLM_URL}/spend/logs",
            headers=headers,
            params={"limit": 100},
            timeout=10,
        )
        if resp.status_code != 200:
            print(
                f"[cost_tracker] Watchdog poll failed: HTTP {resp.status_code} - {resp.text[:200]}"
            )
            return
        data = resp.json()
        if not data:
            return
    except httpx.RequestError as e:
        print(f"[cost_tracker] Watchdog connection error: {e}")
        return

    rows = []
    for entry in data if isinstance(data, list) else data.get("data", data.get("results", [])):
        rows.append(
            (
                entry.get("model", "unknown"),
                entry.get("metadata", {}).get("agent_name", "litellm-proxy"),
                int(entry.get("prompt_tokens", 0)),
                int(entry.get("completion_tokens", 0)),
                float(entry.get("cost", 0)),
                int(entry.get("response_time_ms", 0)),
                entry.get("status", "success"),
            )
        )

    if not rows:
        print("[cost_tracker] No spend data from LiteLLM")
        return

    try:
        client = _ch_client()
        client.execute(
            "INSERT INTO cost_attribution (model_name, agent_name, tokens_in, tokens_out, cost_usd, latency_ms, status) VALUES",
            rows,
        )
        client.disconnect()
        print(f"[cost_tracker] Inserted {len(rows)} cost rows from LiteLLM")
    except Exception as e:
        print(f"[cost_tracker] ClickHouse insert error: {e}")


def push_sample():
    """Insert sample cost data for testing."""
    samples = [
        ("deepseek-v4-flash", "agent-alpha", 450, 120, 0.00014, 320, "success"),
        ("gemini-2.5-pro", "agent-beta", 1200, 400, 0.00250, 890, "success"),
        ("mistral-small", "unknown", 300, 80, 0.00006, 210, "success"),
        ("deepseek-v4-pro", "agent-alpha", 800, 250, 0.00110, 550, "success"),
        ("groq-llama", "unknown", 200, 60, 0.00000, 180, "success"),
    ]
    try:
        client = _ch_client()
        client.execute(
            "INSERT INTO cost_attribution (model_name, agent_name, tokens_in, tokens_out, cost_usd, latency_ms, status) VALUES",
            samples,
        )
        client.disconnect()
        print(f"[cost_tracker] Inserted {len(samples)} sample rows")
    except Exception as e:
        print(f"[cost_tracker] Error: {e}")


def show_summary():
    """Print daily cost summary."""
    try:
        client = _ch_client()
        rows = client.execute(
            "SELECT toDate(timestamp) AS day, model_name, sum(cost_usd), sum(tokens_in + tokens_out), count() "
            "FROM rmi.cost_attribution "
            "WHERE timestamp >= now() - INTERVAL 7 DAY "
            "GROUP BY day, model_name ORDER BY day DESC",
        )
        total = client.execute(
            "SELECT sum(cost_usd), sum(tokens_in + tokens_out), count() "
            "FROM rmi.cost_attribution "
            "WHERE timestamp >= now() - INTERVAL 7 DAY",
        )
        client.disconnect()
    except Exception as e:
        print(f"[cost_tracker] Query error: {e}")
        return

    print(f"{'Date':<14} {'Model':<22} {'Cost $':<12} {'Tokens':<12} {'Calls':<8}")
    print("-" * 68)
    for r in rows:
        print(f"{r[0]!s:<14} {r[1]!s:<22} {round(r[2], 6):<12} {r[3]:<12} {r[4]:<8}")
    print("-" * 68)
    if total and total[0]:
        print(
            f"{'TOTAL':<14} {'':<22} {round(total[0][0], 6):<12} {total[0][1]:<12} {total[0][2]:<8}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI cost tracker")
    parser.add_argument("--test", action="store_true", help="Insert sample data")
    parser.add_argument("--summary", action="store_true", help="Show summary")
    args = parser.parse_args()

    if args.test:
        push_sample()
    elif args.summary:
        show_summary()
    else:
        poll_litellm()
