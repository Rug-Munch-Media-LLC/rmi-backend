"""
RMI LangChain Integration
==========================
Provides RMI tools as LangChain tools for use in LangChain agents, chains, and workflows.

Usage:
    from rmi_langchain import RMIToolkit, create_rmi_agent

    # Get all RMI tools as LangChain tools
    toolkit = RMIToolkit(api_key="rmi_dev_...")
    tools = toolkit.get_tools()

    # Create an agent with RMI tools
    agent = create_rmi_agent(api_key="rmi_dev_...")
    result = agent.invoke({"input": "Scan this wallet for risks: 0xd8dA..."})

Author: RMI Development
Date: 2026-06-05
"""

import json
import os
from typing import Any

from pydantic import BaseModel, Field

try:
    from langchain_core.callbacks import CallbackManagerForToolRun
    from langchain_core.tools import BaseTool

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False

try:
    from rmi_sdk import RMI, ToolResult  # noqa: F401

    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


# ── RMI Tool Wrappers ─────────────────────────────────────────────


class RMIToolInput(BaseModel):
    """Base input schema for RMI tools."""

    address: str = Field(description="Wallet or token address to analyze")
    chain: str = Field(default="solana", description="Blockchain: solana, base, ethereum, bsc, etc.")


class ScanInput(RMIToolInput):
    """Input for wallet scan."""


class AuditInput(RMIToolInput):
    """Input for contract audit."""

    source_code: str | None = Field(default=None, description="Optional contract source code")


class WhaleInput(RMIToolInput):
    """Input for whale analysis."""


class ReputationInput(BaseModel):
    """Input for reputation score."""

    address: str = Field(description="Wallet or token address")


class HoneypotInput(RMIToolInput):
    """Input for honeypot check."""


class SentimentInput(BaseModel):
    """Input for sentiment analysis."""

    query: str = Field(description="Search query for sentiment analysis")


class RugProbabilityInput(RMIToolInput):
    """Input for rug probability."""


class CompositeScoreInput(RMIToolInput):
    """Input for composite score."""


class SmartMoneyInput(BaseModel):
    """Input for smart money analysis."""

    address: str = Field(description="Wallet address to find similar smart money wallets")


class MarketOverviewInput(BaseModel):
    """Input for market overview."""


class NarrativeInput(BaseModel):
    """Input for market narrative."""


class ForensicsInput(RMIToolInput):
    """Input for wallet forensics."""


if LANGCHAIN_AVAILABLE and SDK_AVAILABLE:

    class RMITool(BaseTool):
        """Base class for RMI LangChain tools."""

        rmi_client: RMI

        def _run(
            self,
            run_manager: CallbackManagerForToolRun | None = None,
            **kwargs: Any,
        ) -> str:
            try:
                result = self.rmi_client.call(self.name, kwargs)
                return json.dumps(result.raw, indent=2, default=str)
            except Exception as e:
                return json.dumps({"error": str(e), "tool": self.name})

        async def _arun(
            self,
            run_manager: CallbackManagerForToolRun | None = None,
            **kwargs: Any,
        ) -> str:
            # Use sync version for now - async SDK available separately
            return self._run(run_manager=run_manager, **kwargs)

    class RMIScanTool(RMITool):
        name: str = "rmi_scan"
        description: str = "Scan a wallet address for security risks, scam patterns, and suspicious activity. Returns risk assessment, labels, and recommendations."
        args_schema: type[BaseModel] = ScanInput

    class RMIAuditTool(RMITool):
        name: str = "rmi_audit"
        description: str = "Audit a smart contract for vulnerabilities, rug pull indicators, and malicious code patterns. Returns security assessment."
        args_schema: type[BaseModel] = AuditInput

    class RMIWhaleTool(RMITool):
        name: str = "rmi_whale"
        description: str = "Analyze whale wallet activity, holdings, and trading patterns. Returns portfolio analysis and behavioral insights."
        args_schema: type[BaseModel] = WhaleInput

    class RMIReputationTool(RMITool):
        name: str = "rmi_reputation"
        description: str = "Get a 0-100 reputation/trust score for a wallet or token address. Combines labels, scam databases, and on-chain behavior."
        args_schema: type[BaseModel] = ReputationInput

    class RMIHoneypotTool(RMITool):
        name: str = "rmi_honeypot"
        description: str = "Check if a token is a honeypot - tokens you can buy but cannot sell. Returns honeypot status and risk factors."
        args_schema: type[BaseModel] = HoneypotInput

    class RMISentimentTool(RMITool):
        name: str = "rmi_sentiment"
        description: str = "Get market sentiment analysis for a token, project, or keyword. Returns sentiment score and community mood."
        args_schema: type[BaseModel] = SentimentInput

    class RMIRugProbabilityTool(RMITool):
        name: str = "rmi_rug_probability"
        description: str = (
            "Get rug pull probability score (0-100) for a token. Predicts likelihood of rug pull in next 24 hours."
        )
        args_schema: type[BaseModel] = RugProbabilityInput

    class RMICompositeScoreTool(RMITool):
        name: str = "rmi_composite_score"
        description: str = "Get a composite buy/sell/avoid score for a token. Combines reputation, rug probability, market health, narrative sentiment, and MEV exposure."
        args_schema: type[BaseModel] = CompositeScoreInput

    class RMISmartMoneyTool(RMITool):
        name: str = "rmi_smart_money"
        description: str = "Find profitable traders similar to this wallet. Returns ranked list of smart money wallets with PnL and follow-worthiness scores."
        args_schema: type[BaseModel] = SmartMoneyInput

    class RMIMarketOverviewTool(RMITool):
        name: str = "rmi_market_overview"
        description: str = "Get current market overview - prices, volumes, trends across major chains and tokens."
        args_schema: type[BaseModel] = MarketOverviewInput

    class RMINarrativeTool(RMITool):
        name: str = "rmi_narrative"
        description: str = "Get current market narrative - what is the market saying RIGHT NOW? Returns trending narratives, sentiment, and community focus."
        args_schema: type[BaseModel] = NarrativeInput

    class RMIForensicsTool(RMITool):
        name: str = "rmi_forensics"
        description: str = (
            "Run deep wallet forensics - transaction history, fund flows, connected addresses, and risk analysis."
        )
        args_schema: type[BaseModel] = ForensicsInput

    # ── Toolkit ────────────────────────────────────────────────────

    class RMIToolkit:
        """RMI tools as a LangChain toolkit."""

        def __init__(self, api_key: str | None = None, **kwargs):
            self.api_key = api_key or os.environ.get("RMI_API_KEY")
            self.rmi = RMI(api_key=self.api_key, **kwargs)

        def get_tools(self) -> list[BaseTool]:
            """Get all RMI tools as LangChain tools."""
            return [
                RMIScanTool(rmi_client=self.rmi),
                RMIAuditTool(rmi_client=self.rmi),
                RMIWhaleTool(rmi_client=self.rmi),
                RMIReputationTool(rmi_client=self.rmi),
                RMIHoneypotTool(rmi_client=self.rmi),
                RMISentimentTool(rmi_client=self.rmi),
                RMIRugProbabilityTool(rmi_client=self.rmi),
                RMICompositeScoreTool(rmi_client=self.rmi),
                RMISmartMoneyTool(rmi_client=self.rmi),
                RMIMarketOverviewTool(rmi_client=self.rmi),
                RMINarrativeTool(rmi_client=self.rmi),
                RMIForensicsTool(rmi_client=self.rmi),
            ]

    def create_rmi_agent(
        api_key: str | None = None,
        model: str = "gpt-4",
        **kwargs,
    ):
        """Create a LangChain agent with RMI tools.

        Args:
            api_key: RMI API key.
            model: LLM model to use.
            **kwargs: Additional arguments for RMI client.

        Returns:
            Configured LangChain agent executor.
        """
        try:
            from langchain.agents import AgentExecutor, create_tool_calling_agent
            from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
            from langchain_openai import ChatOpenAI

            toolkit = RMIToolkit(api_key=api_key, **kwargs)
            tools = toolkit.get_tools()

            llm = ChatOpenAI(model=model, temperature=0)

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        """You are a crypto security analyst powered by Rug Munch Intelligence (RMI).
You have access to 230+ crypto intelligence tools for:
- Wallet scanning and forensics
- Smart contract auditing
- Whale tracking and analysis
- Rug pull prediction
- Market sentiment and narratives
- Reputation scoring

Always use the appropriate tool for the user's query. If a tool returns no data, try a different approach.
Provide clear, actionable recommendations based on the tool results.""",
                    ),
                    MessagesPlaceholder("chat_history", optional=True),
                    ("human", "{input}"),
                    MessagesPlaceholder("agent_scratchpad"),
                ]
            )

            agent = create_tool_calling_agent(llm, tools, prompt)
            return AgentExecutor(agent=agent, tools=tools, verbose=True)

        except ImportError as e:
            raise ImportError(
                "langchain and langchain_openai are required: pip install langchain langchain-openai"
            ) from e

    def create_crewai_rmi_tools(api_key: str | None = None, **kwargs):
        """Create CrewAI-compatible tools.

        Usage:
            from rmi_langchain import create_crewai_rmi_tools
            from crewai import Agent, Task, Crew

            tools = create_crewai_rmi_tools(api_key="rmi_dev_...")

            analyst = Agent(
                role="Crypto Security Analyst",
                goal="Analyze crypto wallets and tokens for security risks",
                backstory="Expert in detecting rugs, honeypots, and scams",
                tools=tools,
                verbose=True,
            )
        """
        toolkit = RMIToolkit(api_key=api_key, **kwargs)
        return toolkit.get_tools()
