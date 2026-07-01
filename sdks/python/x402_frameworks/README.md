# x402 AI Framework Integrations

Pay-per-call AI tools for every major framework.
No crypto knowledge required. One `pip install`, zero config.

## Installation

```bash
pip install x402-frameworks
```

## Usage

### LangChain

```python
from x402_frameworks import x402LangChain

x402 = x402LangChain(api_key="your-key", auto_pay=True)

@x402.wrap_tool("token_scan")
@tool
def scan_token(address: str) -> dict:
    """Scan a token for rug pull indicators."""
    return client.scan(address)

from langchain.agents import create_react_agent
agent = create_react_agent(llm, [scan_token], prompt)
# Payment handled automatically on every tool call
```

### CrewAI

```python
from x402_frameworks import x402CrewAI

x402 = x402CrewAI(api_key="your-key", max_budget_usd=50)

researcher = Agent(
    role="Token Researcher",
    tools=[x402.wrap("token_scan", scan_func)],
)
# Shared credit pool across all agents in the crew
```

### AutoGen

```python
from x402_frameworks import x402AutoGen

x402 = x402AutoGen(api_key="your-key")

analyst = AssistantAgent(
    name="TokenAnalyst",
    functions=[x402.as_function("token_scan")],
)
# AutoGen-compatible function specs with transparent payment
```

## How It Works

1. Developer wraps a tool function with `@x402.wrap_tool("tool_name")`
2. When the AI agent calls the tool, x402 checks:
   - Free trial available? → use it
   - Prepaid credits? → deduct
   - Auto-pay enabled? → pay from wallet
   - None? → raise `PaymentRequiredError` with invoice details
3. Tool executes, result returned to agent

## Supported Frameworks

| Framework | Adapter | Status |
|-----------|---------|--------|
| LangChain | `x402LangChain` | ✅ |
| CrewAI | `x402CrewAI` | ✅ |
| AutoGen | `x402AutoGen` | ✅ |
| Haystack | Coming soon | ⏳ |
| Semantic Kernel | Coming soon | ⏳ |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `X402_API_KEY` | `""` | API key for authenticated access |
| `X402_BASE_URL` | `https://mcp.rugmunch.io/mcp/x402` | x402 MCP endpoint |
| `X402_AUTO_PAY` | `false` | Auto-pay invoices from wallet |
| `X402_MAX_BUDGET` | `0` | Monthly budget cap (0 = unlimited) |
