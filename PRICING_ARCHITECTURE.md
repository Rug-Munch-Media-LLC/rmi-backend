"""
RMI Pricing & Subscription Architecture v2
=============================================

INTELLIGENT SCAN ECONOMY — One Scan, Many Uses
-------------------------------------------------

The key insight: When a user scans a token address, that's ONE data event.
But it can power DOZENS of downstream analyses without re-fetching.

Example: User scans token 0xDEAD...BEEF
  → 1 API call fetches: price, liquidity, holders, contract bytecode
  → Powers: risk scan, holder analysis, RugMap, contract audit,
    funding trace, social sentiment, whale tracking, cross-chain check
  → All from one scan input, shared across the DataBus cache

This means we can offer SCAN PACKS where one scan credit
actually delivers comprehensive intelligence across ALL our tools,
because the DataBus deduplicates and caches the underlying data.

COMPETITIVE ANALYSIS (June 2026)
---------------------------------

| Platform         | Free Tier             | Pro Tier           | Enterprise         |
|-----------------|-----------------------|--------------------|--------------------|
| GoPlus Security | 150K CU/mo           | $199/mo (6M CU)    | $799/mo (37.5M CU)|
| Arkham          | Limited views         | $99/mo             | $999/mo            |
| Nansen          | Basic dashboard      | $150/mo (Vital)    | $1,000/mo (Onchain)|
| DexScreener     | Free basic           | —                  | Custom             |
| RugMaps      | Free V2              | $29/mo pro         | B2B custom         |
| TokenSniffer    | Free basic           | $99/mo (SnifferPro)| Custom             |
| Honeypot.is     | Free basic           | —                  | —                  |
| Chainalysis KYT | None                 | —                  | $50K+/yr           |
| TRM Labs        | None                 | —                  | $30K+/yr           |
| De.Fi           | Free basic           | $19.99/mo          | Custom             |
| RugCheck        | Free token checks    | —                  | —                  |

KEY INSIGHT: We're the ONLY platform that gives ONE scan = ALL intelligence.
GoPlus charges per CU. Nansen charges per month for limited chains.
Arkham gives entity data but no risk scoring. TokenSniffer gives scores only.

RMI covers 38 chains, 67 data providers, real-time caching, AND risk scoring
in a single scan. That's worth a serious premium.

PRICING TIERS (v2 — REVISED June 2026)
---------------------------------------

FREE TIER (Anonymous / Fingerprint)
  - 3 basic scans per day (urlcheck, pulse, token_age)
  - 1 market overview per day
  - Limited data per scan (summary only, no deep analysis)
  - No wallet tracking, no real-time alerts
  - Powered-by branding on all outputs

SCOUT PACK — $4.99 (25 scan credits)
  - 25 scan credits, each = ONE address scanned
  - Each scan unlocks EVERY tool for that address for 24 hours
  - Includes: risk scan, holder analysis, RugMap, funding trace,
    contract audit, whale tracking, social sentiment, cross-chain
  - Smart money queries: 10 per pack
  - Market overview: unlimited
  - Credits never expire
  - PER-SCAN VALUE: $0.20 per scan (competitive with TokenSniffer's $0.01-0.05
    per basic scan, but we deliver 10-20x more data per scan)

HUNTER PACK — $14.99 (150 scan credits)
  - 150 scan credits, same "one scan = full intelligence" model
  - 70% discount vs Scout per scan ($0.10/scan)
  - Includes everything in Scout plus:
  - Arkham entity intelligence (5 queries)
  - Deep SENTINEL forensic scans
  - Nansen smart money labels (10 queries)
  - Prediction market signals (unlimited)
  - Real-time alerts (24h per activation)
  - Portfolio dashboard (3 wallets)

WHALE PACK — $49.99 (750 scan credits)
  - 750 scan credits ($0.067/scan — bulk rate)
  - 85% discount vs Scout per scan
  - Includes everything in Hunter plus:
  - Unlimited Arkham entity lookups
  - Unlimited SENTINEL forensic scans
  - Unlimited smart money queries
  - 30-day real-time alerts
  - Portfolio tracking (25 wallets)
  - Priority queue (cache bypass)
  - x402 API access for automation

MONTHLY SUBSCRIPTIONS
─────────────────────

SCOUT MONTHLY — $19.99/mo
  - 75 scan credits/month (rolls over 1 month)
  - All Scout Pack features
  - Weekly intelligence digest email
  - Community Discord access

HUNTER MONTHLY — $49.99/mo
  - 350 scan credits/month (rolls over 1 month)
  - All Hunter Pack features
  - Daily watchlist alerts
  - Priority support

WHALE MONTHLY — $149.99/mo
  - 1,500 scan credits/month (rolls over 1 month)
  - All Whale Pack features
  - Dedicated Telegram alert channel
  - Custom webhooks
  - API access with higher rate limits
  - Account manager

ENTERPRISE — $499/mo (or custom)
  - Unlimited scans, all tools, all data
  - Full API access (databus.fetch with admin key)
  - WebSocket real-time streams
  - Custom data pipelines
  - White-label options
  - Dedicated support & SLA

COMMUNITY DISCOUNT — 50% OFF for CRM / $cryptorugmunch holders
  - Verify: Check wallet balance > 0 of CRM (Solana) or
    $cryptorugmunch (Base/Zora) at purchase time
  - Applied automatically when wallet connected
  - Works on ALL tiers (packs and subscriptions)
  - CRM Solana: 6pnitzwjumnzsvfyfejf9mijzpc4iuqh1xugfwvdf8wb
  - $cryptorugmunch Base: 0x93c4f6f6f8a14a255e78de0273d6490719d8538e17dfcc9b72907df6a0d72bf204

PRICE JUSTIFICATION
────────────────────

Why $4.99 for 25 scans when GoPlus gives 150K calls/mo free?
  - GoPlus gives RAW API calls. Most are useless without interpretation.
  - Our 1 scan = 15-20 underlying API calls, all aggregated and scored.
  - Real value: risk assessment, not raw data. A rug pull warning saves $1K+.
  - Users don't buy API calls; they buy protection.

Why $14.99 for 150 scans?
  - Cheaper than Nansen ($150/mo) for a serious trader
  - More comprehensive than Arkham ($99/mo) for security
  - Deep analysis that TokenSniffer can't match

Why $49.99 for 750 scans?
  - Active investigators use 20-30 scans/day
  - Cheaper per-scan than any competitor at this volume
  - Priority access means better data freshness

Why subscriptions?
  - Recurring revenue for sustainability
  - Lower monthly cost vs. buying packs repeatedly
  - Roll-over credits reduce purchase anxiety

WHY NOT CHEAPER?
  - $0.99 for 50 scans devalues the intelligence. Our free tier already
    gives 3 scans/day. The paid product must feel like a significant step up.
  - Crypto security is a serious business. Users spending $500-5K on a rug
    pull want serious tools, not dollar-store pricing.
  - The 50% community discount already gives holders $2.50/25 or $7.50/150
    scans — aggressive discount without cheapening the brand.

IMPLEMENTATION
──────────────

Scan credit tracking: Redis key x402:scan_credits:{wallet}
Community discount: Check wallet balance of CRM/$cryptorugmunch tokens
Pack purchase: x402 payment (USDC on Base or SOL)
Credit deduction: On first API call per unique address per 24h window
Address reuse: Same address within 24h = no additional credit deduction

x402 Tool Pricing (per-call, no pack):
  - urlcheck: Free (loss leader)
  - pulse: Free (loss leader)
  - risk_scan: $0.05
  - holder_analysis: $0.08
  - rugmap: $0.10
  - contract_audit: $0.15
  - funding_trace: $0.08
  - whale_watch: $0.12
  - sentiment: $0.05
  - cross_chain: $0.08
  - arkham_entity: $0.20
  - sentinel_deep: $0.25

Pack scanning: 1 credit = all above tools for 1 address for 24h
  → Per-credit value: $1.00+ of individual tool calls
  → Effective per-scan price: $0.067-$0.20 depending on pack size
"""
