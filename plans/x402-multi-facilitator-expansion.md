# x402 Multi-Facilitator Expansion Plan

> **For Hermes:** Execute directly — build facilitator modules then integrate.

**Goal:** Expand x402 payment system from 1 facilitator (PayAI) to 8+ facilitators with smart routing, multi-chain settlement, fiat off-ramp (EUR/SEPA), TRON, Bitcoin, and universal EVM support.

**Architecture:** Replace the hardcoded FACILITATOR_CONFIGS dict with a modular facilitator registry. Each facilitator is a pluggable module with verify/settle endpoints. A smart router picks the best facilitator per request based on chain, token, amount, health, and cost.

**Tech Stack:** Python 3.11+, aiohttp, eth_account, FastAPI, Redis

---

## Current State (Before)

```
System: 1 facilitator (PayAI), 7 chains, 2 gateways
Facilitators: FACILITATOR_CONFIGS hardcoded in x402_middleware.py
Verification: Local EIP-712 (primary) + PayAI HTTP (fallback)
Settlement: None (verify only, no on-chain settlement)
Gateway: Base CF Worker + Solana CF Worker
```

## Target State (After)

```
System: 8+ facilitators, 12+ chains, 4+ gateways
Facilitators: Modular registry with smart routing
Verification: Multi-facilitator with automatic fallback
Settlement: Hosted (CDP, PayAI, AsterPay) + Self-hosted (x402-rs)
Gateway: Base, Solana, TRON, Bitcoin + self-hosted
```

---

## Task Inventory

### Phase 1: Facilitator Registry Foundation

**Task 1.1: Create facilitator base class and registry**
- File: `app/facilitators/__init__.py`
- File: `app/facilitators/base.py`
- Abstract base with verify(), settle(), health() methods
- Registry pattern for loading/discovering facilitators

**Task 1.2: Create facilitator config schema**
- File: `app/facilitators/config.py`
- YAML/JSON schema for facilitator configuration
- Environment variable mapping
- Health check intervals, timeouts, retry policies

**Task 1.3: Create smart router**
- File: `app/facilitators/router.py`
- Routes payments to best facilitator
- Factors: chain support, token support, fees, latency, health
- Automatic fallback when primary fails

### Phase 2: Hosted Facilitator Modules

**Task 2.1: Coinbase CDP facilitator**
- File: `app/facilitators/coinbase_cdp.py`
- Base + Base Sepolia with instant settlement
- Uses CDP SDK for on-chain USDC settlement
- Verify via CDP API

**Task 2.2: PayAI facilitator (refactor existing)**
- File: `app/facilitators/payai.py`
- Extract from x402_middleware.py
- Base, Solana support
- Deferred settlement

**Task 2.3: Cloudflare x402 facilitator**
- File: `app/facilitators/cloudflare_x402.py`
- x402.org official facilitator
- Base/Ethereum with deferred settlement
- Used as fallback for Base Sepolia

**Task 2.4: BNB Chain Pieverse facilitator**
- File: `app/facilitators/pieverse.py`
- BNB Chain with instant settlement
- USDT, USDC on BSC
- Verify via Pieverse API

**Task 2.5: AsterPay facilitator (EUR/SEPA)**
- File: `app/facilitators/asterpay.py`
- European x402 with EUR off-ramp via SEPA Instant
- MiCA compliant, ERC-8004 ready
- Fiat conversion for European users

**Task 2.6: MERX x402 for TRON facilitator**
- File: `app/facilitators/merx_tron.py`
- TRON mainnet: USDT, USDC, USDD
- Sub-3-second confirmation for micropayments
- TRON-specific verification

**Task 2.7: Primev FastRPC facilitator**
- File: `app/facilitators/primev.py`
- Ethereum mainnet fee-free
- Sub-200ms settlement via mev-commit preconfirmations
- ERC-8004 registered (Agent #23175)

**Task 2.8: Satoshi Facilitator (Bitcoin)**
- File: `app/facilitators/satoshi.py`
- Bitcoin-focused pay-per-call
- Base, Base Sepolia, Solana Mainnet/Devnet
- Multi-chain settlement from Bitcoin payments

### Phase 3: Self-Hosted Facilitator

**Task 3.1: x402-rs facilitator Docker deployment**
- File: `app/facilitators/x402_rs.py` (client module)
- File: `docker/x402-rs/docker-compose.yml`
- File: `docker/x402-rs/config.yaml`
- Production-grade Rust facilitator
- Multi-chain configuration
- REST API endpoints (/verify, /settle)

### Phase 4: Universal EVM (EIP-7702)

**Task 4.1: EIP-7702 universal EVM facilitator**
- File: `app/facilitators/eip7702.py`
- Support for ALL EVM blockchains (BNB, Polygon, Avalanche, etc.)
- ALL tokens (USDT, DAI, WBTC, etc.)
- ALL native coins (POL, AVAX, etc.)
- Uses EIP-7702 for delegated execution

### Phase 5: Integration

**Task 5.1: Refactor x402_middleware.py to use facilitator registry**
- Replace FACILITATOR_CONFIGS with registry calls
- Backward compatible — existing API unchanged
- Add facilitator health dashboard endpoint

**Task 5.2: Add new chains to x402_enforcement.py**
- TRON (USDT, USDC, USDD)
- Bitcoin (via Satoshi facilitator)
- BNB Chain native
- AVAX, Fantom, Gnosis via EIP-7702

**Task 5.3: Update gateway discovery response**
- Expose all facilitators in /.well-known/x402
- Show facilitator health status
- Multi-facilitator payment requirements

**Task 5.4: Create TRON gateway worker**
- File: `x402-gateway/tron/index.ts`
- TRON-specific x402 gateway
- MERX facilitator integration
- TRC-20 USDT/USDC payment verification

**Task 5.5: Create Bitcoin gateway worker**
- File: `x402-gateway/bitcoin/index.ts`
- Bitcoin x402 gateway (Satoshi facilitator)
- Multi-chain settlement from BTC payments

**Task 5.6: Update dashboard with facilitator metrics**
- Per-facilitator success rate, latency, volume
- Facilitator health status
- Cost comparison (which facilitator is cheapest per chain)

### Phase 6: Environment & Config

**Task 6.1: Update .env.example with new vars**
- CDP API keys
- AsterPay credentials
- MERX TRON config
- Primev endpoint
- Satoshi API keys
- EIP-7702 RPC URLs

**Task 6.2: Update generate_env.py**
- New facilitator environment variables
- Chain RPC URLs for new chains

---

## Implementation Order (Priority)

1. **Phase 1** — Foundation (facilitator registry, base class, router) — CRITICAL PATH
2. **Phase 2** — Hosted facilitators (CDP, PayAI refactor, BNB, AsterPay, TRON, Primev, Bitcoin) — CAN PARALLELIZE
3. **Phase 3** — Self-hosted x402-rs
4. **Phase 4** — Universal EIP-7702
5. **Phase 5** — Integration (refactor middleware, update gateways, dashboard)
6. **Phase 6** — Environment config
