"""RugMunch Intelligence API Docs - written for normal developers.

No blockchain knowledge required. No crypto wallet needed. No USDC.
The product is token intelligence and scam detection. The payment
infrastructure (x402) is invisible backend plumbing.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import os

router = APIRouter(tags=["API-Docs"])

SEO_KEYWORDS = "x402 protocol, crypto intelligence API, blockchain token scanner, rug pull detection, USDC payment gateway, crypto scam detection API, web3 security API, on-chain analysis API, token risk assessment, wallet forensics API, blockchain API pay-per-call, MCP crypto tools, DeFi security API, smart contract scam detector"

DEMO_API_KEY = os.getenv("RMI_FREE_API_KEY", "sk-your-free-api-key")

def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="keywords" content="{SEO_KEYWORDS}">
<meta name="description" content="Crypto intelligence API for token scanning, wallet analysis, and scam detection. Powered by x402 protocol. Pay with credit card or USDC. No blockchain knowledge needed.">
<title>{title} - RugMunch Intelligence API</title>
<style>
:root{{--bg:#0a0a0f;--fg:#e0e0e0;--accent:#4a6cf7;--green:#10b981;--border:#1e1e2e;--mono:'SF Mono','Fira Code',monospace;--font:system-ui,sans-serif}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:var(--font);background:var(--bg);color:var(--fg);line-height:1.7}}
.header{{border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;gap:12px}}
.header h1{{font-size:18px;font-weight:600}}
.header .tag{{background:var(--accent);color:#fff;font-size:10px;padding:2px 8px;border-radius:4px;font-weight:600}}
.container{{max-width:800px;margin:0 auto;padding:32px 24px}}
h2{{font-size:22px;margin:32px 0 12px}}
h3{{font-size:16px;margin:24px 0 8px;color:var(--accent)}}
p{{color:#94a3b8;margin-bottom:12px;font-size:14px;line-height:1.7}}
code{{font-family:var(--mono);background:#1a1a2e;padding:2px 6px;border-radius:3px;font-size:13px;color:var(--green)}}
pre{{background:#1a1a2e;padding:16px 20px;border-radius:8px;overflow:auto;font-size:13px;line-height:1.6;margin:12px 0;border:1px solid var(--border)}}
pre .comment{{color:#666}}
.btn{{display:inline-block;background:var(--accent);color:#fff;padding:10px 24px;border-radius:6px;font-weight:600;font-size:14px;text-decoration:none;margin:8px 4px}}
.btn:hover{{opacity:0.9}}
.btn-green{{background:var(--green)}}
.step{{background:#1a1a2e;border:1px solid var(--border);border-radius:8px;padding:20px;margin:16px 0}}
.step .num{{display:inline-block;background:var(--accent);color:#fff;width:24px;height:24px;border-radius:12px;text-align:center;line-height:24px;font-size:12px;font-weight:700;margin-right:8px}}
.step strong{{font-size:15px}}
.step p{{margin:8px 0 0 20px}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid var(--border)}}
th{{color:#94a3b8;font-weight:500;font-size:12px;text-transform:uppercase}}
td{{font-family:var(--mono);font-size:12px}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{text-decoration:underline}}
.note{{background:#1a1a2e;border-left:3px solid var(--accent);padding:12px 16px;margin:12px 0;font-size:13px;color:#94a3b8}}
.footer{{text-align:center;padding:32px;color:#666;font-size:12px;border-top:1px solid var(--border);margin-top:32px}}
</style>
</head>
<body>
<div class="header">
<h1>RugMunch Intelligence API</h1>
<span class="tag">BETA</span>
<span style="font-size:13px;color:#666;margin-left:auto">docs.rugmunch.io</span>
</div>
<div class="container">
{body}
<div class="footer">Rug Munch Media LLC · Wyoming, USA · docs@rugmunch.io</div>
</div>
</body>
</html>"""

@router.get("/docs", summary="Docs Landing", description="Landing page for the x402 API documentation portal.")
@router.get("/docs/v2/landing")
async def landing_page() -> Any:
    """Landing page - the product is token intelligence, not x402."""
    return HTMLResponse(
        content=_page(
            "RugMunch Intelligence API",
            """
<h2>Token Intelligence for Every Developer</h2>
<p style="font-size:16px;margin-bottom:24px">
Scan tokens for rug pulls. Analyze wallets for risk. Detect scams before they steal.
No blockchain knowledge needed. Just an API call. Built on the <strong>x402 protocol</strong> -
the open standard for pay-per-call blockchain API access with USDC settlement.
</p>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:24px 0">
<div style="background:#1a1a2e;border:1px solid var(--border);border-radius:8px;padding:20px">
<h3 style="color:var(--fg);margin:0 0 8px">🔍 Token Scanning</h3>
<p style="font-size:13px;color:#94a3b8;margin:0">Detect honeypots, rug pulls, fake liquidity, wash trading, and social engineering in any token contract.</p>
</div>
<div style="background:#1a1a2e;border:1px solid var(--border);border-radius:8px;padding:20px">
<h3 style="color:var(--fg);margin:0 0 8px">👛 Wallet Analysis</h3>
<p style="font-size:13px;color:#94a3b8;margin:0">Score any wallet for risk, trace fund flows, identify scam connections, and detect suspicious patterns.</p>
</div>
<div style="background:#1a1a2e;border:1px solid var(--border);border-radius:8px;padding:20px">
<h3 style="color:var(--fg);margin:0 0 8px">📊 Market Intelligence</h3>
<p style="font-size:13px;color:#94a3b8;margin:0">Real-time token prices, holder concentration, liquidity changes, and whale movement alerts.</p>
</div>
<div style="background:#1a1a2e;border:1px solid var(--border);border-radius:8px;padding:20px">
<h3 style="color:var(--fg);margin:0 0 8px">🚨 Alert Pipeline</h3>
<p style="font-size:13px;color:#94a3b8;margin:0">Get webhook notifications when tokens get flagged, liquidity drops, or suspicious activity detected.</p>
</div>
</div>

<div style="background:#1a1a2e;border-left:3px solid var(--green);padding:16px 20px;margin:24px 0">
<p style="margin:0;font-size:14px"><strong>Free tier:</strong> 100 API calls free every month. No credit card required.<br>
<strong>Pay as you go:</strong> $0.01-$0.05 per call after that. Pay with any credit card.</p>
</div>

<div style="text-align:center;margin:32px 0">
<a href="https://api.rugmunch.io/docs/v2/quickstart" class="btn btn-green">Get Started Free →</a>
<a href="https://api.rugmunch.io/docs/v2/api-reference" class="btn">API Reference</a>
</div>

<h3>Works With Any Language</h3>
<pre>curl -X POST https://api.rugmunch.io/v1/token/scan \\
  -H "X-API-Key: your-key" \\
  -H "Content-Type: application/json" \\
  -d '{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "chain": "ethereum"}'

<span class="comment"># → {"risk_score": 12, "risk_level": "low", "liquidity_usd": 50000000}</span></pre>

<div class="note">
<strong>No crypto wallet? No problem.</strong> Pay with a credit card or USDC.
The x402 protocol handles USDC settlement on Base, Solana, Ethereum, and Polygon.
Your API key is all you need. We handle the blockchain.
</div>
""",
        )
    )

@router.get("/docs/v2/quickstart", summary="Quickstart", description="Quickstart guide for the x402 API. Shows how to get started with token scanning, wallet analysis, and x402 payments.")
async def quickstart() -> Any:
    return HTMLResponse(
        content=_page(
            "Quickstart",
            f"""
<h2>Quickstart - Try It in 30 Seconds</h2>
<p>No crypto wallet needed. No USDC. Just a credit card or free API key.</p>

<div class="step">
<span class="num">1</span>
<strong>Get a free API key</strong>
<p>Send a POST request with your email. We'll send you a key instantly.</p>
<pre>curl https://api.rugmunch.io/v1/auth/signup \\
  -H "Content-Type: application/json" \\
  -d '{{"email": "you@example.com"}}'
<span class="comment"># Response: {{"api_key": "{DEMO_API_KEY}...", "free_calls_remaining": 100}}</span></pre>
</div>

<div class="step">
<span class="num">2</span>
<strong>Scan a token (free - 100 calls included)</strong>
<p>Use your API key to scan any token for rug pull indicators. No payment needed for your first 100 calls.</p>
<pre>curl https://api.rugmunch.io/v1/token/scan \\
  -H "X-API-Key: {DEMO_API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "chain": "ethereum"}}'
<span class="comment"># Response: {{"risk_score": 12, "risk_level": "low", "flags": [], "liquidity_usd": 50000000}}</span></pre>
</div>

<div class="step">
<span class="num">3</span>
    <strong>Buy more calls ($0.01 each)</strong>
<p>When you run out of free calls, buy credits with a credit card or USDC on-chain.
Starts at $10 for 1,000 calls. The x402 protocol settles payments automatically on
Base, Solana, Ethereum, BSC, Arbitrum, Polygon, and Optimism.</p>
<pre>curl https://api.rugmunch.io/v1/credits/buy \\
  -H "X-API-Key: {DEMO_API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{{"amount_usd": 10, "payment_method": "card"}}'
<span class="comment"># Response: {{"credits_added": 1000, "total_balance": 1000, "price_per_call": "$0.01"}}</span></pre>
</div>

<div class="note">
<strong>No crypto knowledge required to get started.</strong> Pay with any credit card.
Advanced users can pay with USDC via the x402 protocol for on-chain settlement.
Your API key tracks your balance across both payment methods. No wallet, no gas fees,
no seed phrases to lose.
</div>
""",
        )
    )

@router.get("/docs/v2/api-reference", summary="Api Reference", description="Full API reference documentation for all x402 endpoints, including request/response schemas and authentication.")
async def api_reference() -> Any:
    return HTMLResponse(
        content=_page(
            "API Reference",
            f"""
<h2>API Reference</h2>
<p>All endpoints accept JSON and return JSON. Authenticate with <code>X-API-Key</code> header.</p>

<h3>Token Scanning</h3>
<table>
<tr><th>Endpoint</th><th>Method</th><th>Cost</th><th>Description</th></tr>
<tr><td>/v1/token/scan</td><td>POST</td><td>$0.02</td><td>Full token risk analysis (honeypot, liquidity, holders)</td></tr>
<tr><td>/v1/token/price</td><td>GET</td><td>$0.01</td><td>Current price + 24h change</td></tr>
<tr><td>/v1/token/holders</td><td>GET</td><td>$0.01</td><td>Top holders and concentration</td></tr>
</table>

<h3>Wallet Analysis</h3>
<table>
<tr><th>Endpoint</th><th>Method</th><th>Cost</th><th>Description</th></tr>
<tr><td>/v1/wallet/analyze</td><td>POST</td><td>$0.03</td><td>Full wallet risk assessment</td></tr>
<tr><td>/v1/wallet/balance</td><td>GET</td><td>$0.01</td><td>Wallet balance across chains</td></tr>
<tr><td>/v1/wallet/history</td><td>GET</td><td>$0.05</td><td>Transaction history + patterns</td></tr>
</table>

<h3>Examples</h3>
<pre><span class="comment"># Python - pip install requests</span>
import requests
from typing import Any

API_KEY = "{DEMO_API_KEY}"
api = "https://api.rugmunch.io/v1"

<span class="comment"># Scan a token</span>
resp = requests.post(f"{{api}}/token/scan",
    headers={{"X-API-Key": API_KEY}},
    json={{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "chain": "ethereum"}}
)
print(resp.json())

<span class="comment"># Check remaining credits</span>
resp = requests.get(f"{{api}}/credits/balance",
    headers={{"X-API-Key": API_KEY}}
)
print(f"Credits remaining: {{resp.json()['balance']}}")</pre>

<pre><span class="comment"># JavaScript - fetch</span>
const API_KEY = "{DEMO_API_KEY}";

const resp = await fetch("https://api.rugmunch.io/v1/token/scan", {{
  method: "POST",
  headers: {{ "X-API-Key": API_KEY, "Content-Type": "application/json" }},
  body: JSON.stringify({{ address: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", chain: "ethereum" }})
}});
const data = await resp.json();
console.log(data);</pre>
""",
        )
    )

@router.get("/old-docs")
async def redirect_old_docs() -> Any:
    """Redirect old x402 docs to the new developer-friendly docs."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/docs")
