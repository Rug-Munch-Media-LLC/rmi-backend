"""Agent orchestrator — routes tasks to specialist agents."""

from .specialists import AGENTS


class AgentOrchestrator:
    """Routes tasks to the right specialist agent."""

    def __init__(self):
        self.agents = {name: factory() for name, factory in AGENTS.items()}

    async def run_pipeline(self, task: str, pipeline: list[str] | None = None):
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
