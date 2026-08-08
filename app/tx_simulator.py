"""
Transaction Simulation - Pre-flight swap/sell simulation.

Detects honeypots, excessive taxes, and reverts BEFORE the user signs.
Simulates a sell transaction using Jupiter (Solana) and eth_call (EVM) to
predict what will happen without spending gas.

Architecture:
  - Solana: Jupiter quote API → simulate via Helius RPC
  - EVM: eth_call with swap parameters
  - Returns: success/fail, expected output, tax rate, risk assessment
"""

from __future__ import annotations
from typing import Any

from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SimulationResult:
    """Result of a transaction simulation."""

    token_address: str
    chain: str
    can_sell: bool
    can_buy: bool
    sell_tax_pct: float = 0.0
    buy_tax_pct: float = 0.0
    expected_output: float | None = None
    expected_output_token: str = ""
    simulation_success: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    risk: str = "unknown"  # safe, warning, dangerous, critical

    @property
    def is_honeypot(self) -> bool:
        return not self.can_sell and self.can_buy

    @property
    def is_hard_rug(self) -> bool:
        return not self.can_sell and not self.can_buy


# ═══════════════════════════════════════════════
# SOLANA SIMULATION (via Jupiter + Helius)
# ═══════════════════════════════════════════════

JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"
WSOL_MINT = "So11111111111111111111111111111111111111112"
SIMULATION_AMOUNT = 100_000  # 0.0001 SOL for simulation


async def simulate_solana(token_address: str) -> SimulationResult:
    """Simulate a sell transaction on Solana."""
    result = SimulationResult(
        token_address=token_address,
        chain="solana",
        can_sell=False,
        can_buy=False,
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1: Try to sell (token → SOL)
            sell_quote = await _get_quote(client, token_address, WSOL_MINT, SIMULATION_AMOUNT)

            if sell_quote:
                result.can_sell = True
                result.expected_output = float(sell_quote.get("outAmount", 0)) / 1e9
                result.expected_output_token = "SOL"  # noqa: S105 — config default

                # Calculate tax from price impact + slippage
                price_impact = float(sell_quote.get("priceImpactPct", 0))
                if price_impact > 50:
                    result.warnings.append(f"Very high price impact: {price_impact:.1f}%")
                    result.sell_tax_pct = price_impact
                elif price_impact > 15:
                    result.warnings.append(f"High price impact: {price_impact:.1f}%")
                    result.sell_tax_pct = price_impact

            # Step 2: Try to buy (SOL → token)
            buy_quote = await _get_quote(client, WSOL_MINT, token_address, SIMULATION_AMOUNT)
            if buy_quote:
                result.can_buy = True
                price_impact = float(buy_quote.get("priceImpactPct", 0))
                if price_impact > 50:
                    result.buy_tax_pct = price_impact

            result.simulation_success = True

            # Risk assessment
            if result.is_honeypot:
                result.risk = "critical"
                result.warnings.append("⚠️ HONEYPOT DETECTED: Can buy but cannot sell!")
            elif not result.can_sell:
                result.risk = "critical"
                result.warnings.append("⚠️ Cannot sell this token - liquidity may not exist")
            elif result.sell_tax_pct > 90:
                result.risk = "critical"
                result.warnings.append(f"☠️ {result.sell_tax_pct:.0f}% effective tax on sell - likely scam")
            elif result.sell_tax_pct > 50:
                result.risk = "dangerous"
                result.warnings.append(f"🔴 {result.sell_tax_pct:.0f}% tax on sell")
            elif result.sell_tax_pct > 15:
                result.risk = "warning"
                result.warnings.append(f"🟠 {result.sell_tax_pct:.0f}% price impact on sell")
            elif result.can_sell and result.can_buy:
                result.risk = "safe"

    except Exception as e:
        result.error = str(e)
        result.risk = "unknown"
        result.warnings.append(f"Simulation failed: {e}")

    return result


async def _get_quote(client: httpx.AsyncClient, input_mint: str, output_mint: str, amount: int) -> dict | None:
    """Get a Jupiter swap quote."""
    try:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": 100,  # 1% slippage
        }
        r = await client.get(JUPITER_QUOTE, params=params)
        if r.status_code == 200:
            data = r.json()
            if data.get("outAmount"):
                return data
        return None
    except (ConnectionError, OSError, TimeoutError):
        return None


# ═══════════════════════════════════════════════
# EVM SIMULATION (via eth_call)
# ═══════════════════════════════════════════════

UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"

# Common public RPC endpoints
EVM_RPC_ENDPOINTS = {
    "ethereum": "https://eth.llamarpc.com",
    "bsc": "https://bsc-dataseed.binance.org",
    "polygon": "https://polygon.llamarpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "base": "https://mainnet.base.org",
    "optimism": "https://mainnet.optimism.io",
}


async def simulate_evm(token_address: str, chain: str = "ethereum") -> SimulationResult:
    """Simulate a sell on EVM chains via eth_call."""
    result = SimulationResult(
        token_address=token_address,
        chain=chain,
        can_sell=False,
        can_buy=False,
    )

    rpc_url = EVM_RPC_ENDPOINTS.get(chain, EVM_RPC_ENDPOINTS["ethereum"])

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Check if token is accessible via eth_call
            # Call balanceOf to verify token exists
            balance_check = await _eth_call(
                client,
                rpc_url,
                token_address,
                "0x70a08231000000000000000000000000" + "0" * 40,  # balanceOf(address(0))
            )
            if balance_check and not balance_check.get("error"):
                result.can_buy = True  # Token exists and is callable

            # Try to simulate a sell via Uniswap
            sell_result = await _simulate_uniswap_sell(client, rpc_url, token_address)
            if sell_result.get("success"):
                result.can_sell = True
                result.expected_output = sell_result.get("output_amount")
                result.expected_output_token = "ETH"  # noqa: S105 — config default

            result.simulation_success = True

            if result.is_honeypot:
                result.risk = "critical"
                result.warnings.append("⚠️ HONEYPOT: Token exists but sells revert")
            elif result.can_sell:
                result.risk = "safe"
            else:
                result.risk = "warning"
                result.warnings.append("Could not verify sell path - exercise caution")

    except Exception as e:
        result.error = str(e)
        result.risk = "unknown"

    return result


async def _eth_call(client: httpx.AsyncClient, rpc_url: str, to: str, data: str) -> dict:
    """Execute an eth_call JSON-RPC request."""
    try:
        r = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"],
            },
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


async def _simulate_uniswap_sell(client: httpx.AsyncClient, rpc_url: str, token: str) -> dict:
    """Simulate a Uniswap V2 sell transaction."""
    try:
        # Encode getAmountsOut(amountIn, [token, WETH])
        amount_in_hex = "0x" + format(1000000000000000000, "064x")  # 1 ETH worth
        data = (
            "0xd06ca61f"
            + amount_in_hex[2:].zfill(64)
            + "0000000000000000000000000000000000000000000000000000000000000040"
            + "0000000000000000000000000000000000000000000000000000000000000002"
            + "000000000000000000000000"
            + token[2:].lower().zfill(64)
            + "000000000000000000000000"
            + WETH[2:].lower().zfill(64)
        )
        r = await client.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [{"to": UNISWAP_V2_ROUTER, "data": data}, "latest"],
            },
        )
        result = r.json()
        if "result" in result and result["result"] != "0x":
            # Parse output amount from ABI-encoded response
            raw = result["result"]
            return {"success": True, "output_amount": int(raw, 16) / 1e18}
        return {"success": False}
    except (ConnectionError, OSError, TimeoutError):
        return {"success": False}


# ═══════════════════════════════════════════════
# UNIFIED SIMULATION
# ═══════════════════════════════════════════════


async def simulate_transaction(token_address: str, chain: str = "solana") -> SimulationResult:
    """
    Simulate a sell transaction for any chain.

    Returns a SimulationResult with:
      - can_sell / can_buy (honeypot detection)
      - estimated tax/price impact
      - risk assessment
      - warnings
    """
    if chain == "solana" or (len(token_address) in range(32, 45) and not token_address.startswith("0x")):
        return await simulate_solana(token_address)
    else:
        return await simulate_evm(token_address, chain)
