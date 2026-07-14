"""Base agent class with Instructor structured output support."""

import instructor
from openai import OpenAI


class BaseAgent:
    """Unified agent base with Instructor for structured outputs.

    Usage:
        agent = BaseAgent(name="scanner", model="deepseek-chat")
        result = agent.ask("Scan token 0x...", response_model=TokenRiskReport)
    """

    def __init__(
        self,
        name: str,
        model: str = "deepseek-chat",
        api_base: str = "http://localhost:4000/v1",
        api_key: str | None = None,
    ):
        self.name = name
        self.model = model
        self.client = instructor.from_openai(OpenAI(base_url=api_base, api_key=api_key))

    def ask(self, prompt: str, response_model=None, **kwargs):
        """Ask the agent — returns structured output if response_model provided."""
        if response_model:
            return self.client.chat.completions.create(
                model=self.model,
                response_model=response_model,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        return self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
