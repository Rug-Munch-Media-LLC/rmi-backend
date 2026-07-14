"""Temporal worker entrypoint."""
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(client, task_queue="rmi-backend-tasks",
        workflows=[], activities=[])
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
