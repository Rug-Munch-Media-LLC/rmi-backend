"""x402 AI Framework Integrations - pay-per-call for any agent framework.

LangChain, CrewAI, AutoGen, and any Python framework that calls tools.
One pattern: wrap a tool call → deduct credits → execute → return.

pip install x402-frameworks
"""

from .autogen_adapter import x402AutoGen  # noqa: F401
from .crewai_adapter import x402CrewAI  # noqa: F401
from .langchain_adapter import x402LangChain  # noqa: F401
