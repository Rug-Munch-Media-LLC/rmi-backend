"""Temporal workflows for rmi-backend."""
from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

@workflow.defn
class TokenScannerWorkflow:
    @workflow.run
    async def run(self, chain: str, address: str) -> dict:
        return await workflow.execute_activity(
            "scan_token", args=[chain, address],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3))

@workflow.defn
class DatabusHealthCheckWorkflow:
    @workflow.run
    async def run(self, providers: list[str]) -> dict:
        results = {}
        for p in providers:
            results[p] = await workflow.execute_activity(
                "check_databus_provider", args=[p],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2))
        return results

@workflow.defn
class ChainReindexWorkflow:
    @workflow.run
    async def run(self, chain: str) -> dict:
        return await workflow.execute_activity(
            "reindex_ponder_chain", args=[chain],
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=1))
