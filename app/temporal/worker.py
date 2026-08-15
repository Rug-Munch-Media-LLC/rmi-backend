"""Temporal worker entrypoint.

Runs the rmi-backend Temporal worker on ``TEMPORAL_HOST`` (default
``localhost:7233``). Registers the workflows/activities defined in
``app.temporal.workflows`` and ``app.temporal.activities``:

  Workflows:  TokenScannerWorkflow, DatabusHealthCheckWorkflow, ChainReindexWorkflow
  Activities: scan_token, check_databus_provider, reindex_ponder_chain, send_telegram_alert

NOTE: the activities are currently placeholder implementations that return
stub results — they must be wired to the real scanner/data-bus/chain-indexer
calls before the worker is run in production.
"""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from app.temporal.activities import (
    check_databus_provider,
    reindex_ponder_chain,
    scan_token,
    send_telegram_alert,
)
from app.temporal.workflows import (
    ChainReindexWorkflow,
    DatabusHealthCheckWorkflow,
    TokenScannerWorkflow,
)


async def main() -> None:
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    client = await Client.connect(temporal_host)
    worker = Worker(
        client,
        task_queue="rmi-backend-tasks",
        workflows=[TokenScannerWorkflow, DatabusHealthCheckWorkflow, ChainReindexWorkflow],
        activities=[scan_token, check_databus_provider, reindex_ponder_chain, send_telegram_alert],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
