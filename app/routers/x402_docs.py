"""x402 documentation endpoints - embedded docs site + sandbox mode."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app.core.errors import ValidationError

router = APIRouter(tags=["x402-Docs"])

DOCS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>x402 - Pay-per-Call AI Tools</title>
<style>
:root{--bg:#0a0a0f;--fg:#e0e0e0;--accent:#4a6cf7;--green:#10b981;--mono:'SF Mono','Fira Code',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--fg);line-height:1.7;padding:40px 24px}
.container{max-width:800px;margin:0 auto}
h1{font-size:28px;margin-bottom:8px}
h2{font-size:20px;margin:32px 0 12px;color:var(--accent)}
h3{font-size:16px;margin:24px 0 8px}
p{color:#94a3b8;margin-bottom:12px;font-size:14px}
code{font-family:var(--mono);background:#1a1a2e;padding:2px 6px;border-radius:4px;font-size:13px;color:var(--green)}
pre{background:#1a1a2e;padding:16px;border-radius:8px;overflow:auto;font-size:13px;line-height:1.5;margin:12px 0;color:var(--green)}
.badge{display:inline-block;background:var(--accent);color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-right:6px}
.badge-green{background:var(--green)}
.mcp-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0}
.mcp-card{background:#1a1a2e;padding:16px;border-radius:8px;border:1px solid #2a2a3e}
.mcp-card h4{font-size:14px;margin-bottom:4px;color:var(--fg)}
.mcp-card p{font-size:12px;color:#666}
@media(max-width:600px){.mcp-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="container">

<h1>⚡ x402</h1>
<p>Pay-per-call AI tools. Send USDC, get intelligence. No subscription. No commitment.</p>

<div style="display:flex;gap:16px;margin:24px 0">
<div><span class="badge badge-green">274+ tools</span></div>
<div><span class="badge">7 chains</span></div>
<div><span class="badge">MCP-native</span></div>
<div><span class="badge">Free trials</span></div>
</div>

<h2>Quickstart (30 seconds)</h2>
<pre>
# 1. List available tools
curl https://mcp.rugmunch.io/mcp/x402 \\
  -H "Content-Type: application/json" \\
  -d '{"method":"tools/list"}'

# 2. Create invoice for a tool
curl https://mcp.rugmunch.io/mcp/x402 \\
  -d '{"method":"tools/call","params":{"name":"x402_create_invoice","arguments":{"tool":"token_scan"}}}'

# 3. Pay with USDC (Base or Solana)
#    Send the amount to the pay_to address from the invoice

# 4. Verify payment
curl https://mcp.rugmunch.io/mcp/x402 \\
  -d '{"method":"tools/call","params":{"name":"x402_verify_payment","arguments":{"tx_hash":"0x...","tool":"token_scan"}}}'
</pre>

<h2>MCP Integration</h2>
<p>Every major AI tool can discover and use x402 natively:</p>

<div class="mcp-grid">
<div class="mcp-card">
<h4>Claude Code</h4>
<p>Add to claude.json: <code>mcpServers.x402.url = "https://mcp.rugmunch.io/mcp/x402"</code></p>
</div>
<div class="mcp-card">
<h4>Cursor</h4>
<p>Add .cursor/mcp.json: <code>@x402 list_tools</code> in chat</p>
</div>
<div class="mcp-card">
<h4>opencode</h4>
<p>Add opencode.json: <code>mcp.x402.url = "https://mcp.rugmunch.io/mcp/x402"</code></p>
</div>
<div class="mcp-card">
<h4>aider</h4>
<p>Run with: <code>aider --mcp-servers x402=https://mcp.rugmunch.io/mcp/x402</code></p>
</div>
</div>

<h2>Python SDK</h2>
<pre>
pip install x402-sdk

from x402_sdk import x402
from typing import Any
client = x402()
tools = client.list_tools(category="intelligence")
invoice = client.create_invoice(tool="token_scan")
# Pay on-chain → verify
result = client.verify_payment(tx_hash="0x...", tool="token_scan")
</pre>

<h2>Supported Chains</h2>
<pre>
Base      USDC: 0x833589fcd6edb6e08f35c603c69b1e1c4b8e7a8b
Solana    USDC: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
Ethereum  USDC: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
BNB Chain USDC: 0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d
TRON      USDC: TEkxiTehnzSmSe2XqrBj4w32RUN966rdz
</pre>

<h2>Pricing</h2>
<pre>
Category        Price Range     Free Trials
Intelligence    $0.01-$0.15     2-3 per tool
Premium         $0.10-$0.30     1 per tool
Monitoring      $0.02-$0.05     2-3 per tool
Analysis        $0.05-$0.10     2 per tool
Bundles         $0.20-$0.35     1 per bundle
</pre>

<h2>Discovery Endpoint</h2>
<pre>
GET /.well-known/x402
→ Returns service metadata, supported chains, pricing, facilitator list
</pre>

<p style="margin-top:40px;font-size:12px;color:#666">
x402 - Rug Munch Media LLC · Wyoming, USA · MIT License
</p>

</div>
</body>
</html>
"""


@router.get("/x402/docs")
async def x402_docs_page() -> Any:
    """HTML documentation for the x402 payment system."""
    return HTMLResponse(content=DOCS_PAGE)


@router.post("/x402/sandbox/create-invoice")
async def sandbox_create_invoice(tool: str = Query(...), mode: str = Query("test")) -> Any:
    """Sandbox mode - create a test invoice without real payment.

    Use ?mode=test to get a fake invoice for integration testing.
    The invoice includes a mock payment that self-verifies.
    """
    from app.domains.x402.middleware import create_invoice

    invoice = create_invoice(tool, "sandbox_user")
    if mode == "test":
        invoice["mode"] = "test"
        invoice["test_instructions"] = (
            "SANDBOX MODE: Use tx_hash '0xSANDBOX_' + any 50 hex chars to verify without real payment. "
            "Example: 0xSANDBOX_abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )
    return invoice


@router.post("/x402/sandbox/verify")
async def sandbox_verify_payment(tx_hash: str = Query(...), tool: str = Query(...)) -> dict[str, bool]:
    """Sandbox mode - verify a test payment.

    Accepts any tx_hash starting with '0xSANDBOX_' as valid.
    Returns mock verification result for integration testing.
    """
    if tx_hash.startswith("0xSANDBOX_"):
        return {
            "verified": True,
            "tx_hash": tx_hash[:20] + "...",
            "tool": tool,
            "amount_usd": 0.01,
            "chain": "base (sandbox)",
            "mode": "test",
            "note": "SANDBOX: Payment verified. This was a test - no real USDC transferred.",
        }
    raise ValidationError("Invalid sandbox tx_hash. Use 0xSANDBOX_ prefix.")
