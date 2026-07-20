# ═══════════════════════════════════════════════════════════════
# ADR-005: x402 Payment-Gated API Access
# ═══════════════════════════════════════════════════════════════
# Date: 2026-07-20
# Status: Accepted

## Context

RMI provides premium data (AI-generated reports, batch wallet forensics,
real-time alerts) that incur real costs — LLM inference, RPC queries,
cache storage. A sustainable monetization model is needed to offset
these costs and fund further development.

Traditional API key models (Stripe subscription, monthly billing) have
friction: they require account creation, KYC, and recurring payment
setup. For a crypto-native platform serving developers and traders, a
per-request micropayment model is more natural.

## Decision

Premium data endpoints are gated behind x402 HTTP 402 payments using
on-chain USDC/USDT verification. The architecture is:

```
Client → HTTP GET /v1/premium/endpoint
  → 402 Payment Required (with on-chain payment URL)
  → Client submits USDC payment
  → Client retries with payment proof
  → 200 OK with data (cached for 5 min for same payer)
```

### Implementation Layers

1. **`app/domains/x402/middleware.py`** — FastAPI middleware that intercepts
   402 responses, validates payment proofs, and injects payment-required
   headers on unauthenticated premium requests

2. **`app/domains/billing/`** — Payment verification, invoice generation,
   credit tracking, and refund logic. Integrates with USDC on Base/Ethereum
   via on-chain transaction verification

3. **Pricing tiers:**
   - Free tier: 10 requests/day, basic risk scores, delayed data
   - Premium: $0.01/request (x402), full reports, real-time alerts
   - Enterprise: Custom pricing, dedicated webhooks, SLA (via contract)

### Payment Flow

1. Anonymous user hits a premium endpoint
2. Middleware returns 402 with `X-Payment-Required: true` and
   `X-Payment-URL: https://pay.rugmunch.io/invoice/{id}`
3. User pays via injected browser wallet (Web3) or API-based payment
4. Payment proof (transaction hash) is submitted as `X-Payment-Proof` header
5. Server verifies the on-chain transaction and caches the entitlement
   for 5 minutes
6. Original request is fulfilled

## Alternatives Considered

1. **Stripe subscriptions** — rejected: non-crypto, requires KYC, monthly
   commitment friction for developers who want to try before subscribing
2. **API keys with monthly billing** — rejected: same friction, requires
   account system, admin overhead for key rotation
3. **Free + ads** — rejected: not feasible for API product; ads don't work
   in developer tooling
4. **Token-gated (NFT pass)** — rejected: illiquid, hard to set granular
   pricing, gas costs for transfers

## Consequences

- All new premium endpoints must register in the billing domain
- Free tier limits must be enforced at the middleware layer before any
  business logic executes (fail fast)
- Payment proofs expire after 5 minutes to prevent replay attacks
- On-chain verification adds latency (~2-5s for finality) — use
  optimistic verification with post-hoc validation for UX
- 402 responses must be machine-readable for automated API clients
- Revenue tracked in Postgres with ClickHouse for analytics
