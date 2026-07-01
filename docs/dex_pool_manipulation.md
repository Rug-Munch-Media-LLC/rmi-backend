# DEX Pool Manipulation Analyzer

**Tool:** `dex_pool_manipulation`
**Tier:** Premium
**Price:** $0.10 (100,000 atoms)
**Trial:** 1 free check per 24h
**Endpoint:** `POST /api/v1/x402-tools/dex_pool_manipulation`
**File:** `app/dex_pool_manipulation_analyzer.py`
**Router:** `app/routers/x402_dex_pool_manipulation.py`
**Tests:** `app/test_dex_pool_manipulation.py` (20 tests, all passing)

## Overview

Analyzes DEX liquidity pools for manipulation, fake liquidity, and attack vectors. Works with Uniswap V2/V3, PancakeSwap, Raydium, Orca, and other major DEXes across EVM and Solana chains.

## Analysis Signals

| Category | Weight | Description |
|----------|--------|-------------|
| Liquidity Concentration | 25% | Top positions controlling >70% of liquidity |
| Sandwich Vulnerability | 15% | Pool thinness + detected sandwich patterns |
| Pool Owner Risk | 20% | Owner privileges, fee manipulation, age |
| Fake/Wash Liquidity | 25% | Single-owner, zero-swap liquidity, wash pairs |
| Price Manipulation | 30% | Abnormal price swings, thin pool impacts |
| MEV Exposure | 10% | Rapid same-block trading patterns |
| Fee Tier Abuse | 15% | Unusual fee configurations for pair type |

## Risk Scoring

- **0-9:** Minimal risk
- **10-24:** Low risk
- **25-44:** Medium risk
- **45-69:** High risk
- **70-100:** Critical risk

## Metrics Returned

- `price_impact`: Simulated impact for 1/10/100 ETH trades
- `top_5_concentration_pct`: % of liquidity controlled by top 5 positions
- `liquidity_depth_1pct_change_usd`: Trade volume to cause 1% slippage
- `sandwich_profit_estimate_usd`: Estimated sandwich bot profit

## Input Parameters

```json
{
  "pool_address": "0x...",
  "chain": "ethereum",
  "dex": "uniswap_v3",
  "recent_swaps": [
    {
      "tx_hash": "0x...",
      "block": 12345,
      "timestamp": 1700000000,
      "amount_in": 5.0,
      "amount_out": 4950.0,
      "price_before": 1000.0,
      "price_after": 1005.0
    }
  ],
  "positions": [
    {
      "owner": "0x...",
      "tick_lower": -100,
      "tick_upper": 100,
      "liquidity": 500000,
      "usd_value": 1000000.0
    }
  ],
  "pool_metadata": {
    "chain": "ethereum",
    "dex": "uniswap_v3",
    "version": "v3",
    "token0_symbol": "WETH",
    "token1_symbol": "USDC",
    "fee_tier": 500,
    "total_liquidity_usd": 5000000.0
  }
}
```
