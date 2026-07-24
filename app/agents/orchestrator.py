"""Agent orchestrator — routes tasks to specialist agents."""

from __future__ import annotations
from .specialists import AGENTS
from typing import Any

class AgentOrchestrator:
    """Routes tasks to the right specialist agent."""

    def __init__(self) -> None:
        self.agents = {name: factory() for name, factory in AGENTS.items()}

    async def run_pipeline(self, task: str, pipeline: list[str] | None = None) -> Any:
        """Run a pipeline of agents on a task."""
        if pipeline is None:
            pipeline = ["analyst"]

        results = []
        for agent_name in pipeline:
            agent = self.agents.get(agent_name)
            if agent:
                result = agent.ask(task)
                results.append({"agent": agent_name, "result": result})
        return results
