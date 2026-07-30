"""Temporal worker entrypoint."""

from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker


async def main() -> None:
    temporal_host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    client = await Client.connect(temporal_host)
    worker = Worker(client, task_queue="rmi-backend-tasks", workflows=[], activities=[])
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
