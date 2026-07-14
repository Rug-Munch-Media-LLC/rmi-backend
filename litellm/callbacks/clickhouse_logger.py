"""LiteLLM custom callback that logs model spend to ClickHouse.

Mount this file into the LiteLLM container at /app/callbacks/ and add
to config.yaml:

    litellm_settings:
        callbacks: ["callbacks.clickhouse_logger"]
"""

import os

CLICKHOUSE_HOST = os.getenv("CH_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CH_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CH_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CH_PASSWORD", "RMI_PROD_CLICKHOUSE_2026")


def log_success_event(
    kwargs,
    response_obj,
    start_time,
    end_time,
):
    """Called after a successful LLM API call."""
    try:
        model = kwargs.get("model", "unknown")
        agent = kwargs.get("metadata", {}).get("agent_name", "litellm-proxy")
        completion_start = kwargs.get("completion_start_time", start_time)

        usage = getattr(response_obj, "usage", None)
        if usage is None:
            return

        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        latency = (end_time - start_time) * 1000 if end_time and start_time else 0

        from clickhouse_driver import Client

        client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database="rmi",
            connect_timeout=5,
            send_receive_timeout=10,
        )
        client.execute(
            "INSERT INTO cost_attribution (model_name, agent_name, tokens_in, tokens_out, latency_ms) VALUES",
            [(model, agent, tokens_in, tokens_out, int(latency))],
        )
        client.disconnect()
    except Exception:
        pass


def log_failure_event(
    kwargs,
    response_obj,
    start_time,
    end_time,
):
    """Called after a failed LLM API call."""
    try:
        model = kwargs.get("model", "unknown")
        agent = kwargs.get("metadata", {}).get("agent_name", "litellm-proxy")

        from clickhouse_driver import Client

        client = Client(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database="rmi",
            connect_timeout=5,
            send_receive_timeout=10,
        )
        client.execute(
            "INSERT INTO cost_attribution (model_name, agent_name, tokens_in, tokens_out, latency_ms, status) VALUES",
            [(model, agent, 0, 0, 0, "error")],
        )
        client.disconnect()
    except Exception:
        pass
