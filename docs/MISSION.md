# Mission & Product Documentation — Rug Munch Intelligence (RMI)

> Landing page for the RugMunch mission, what we build, who it serves, and how
> our documentation is organized and published.

---

## (a) Mission Statement

> **Protect retail investors from common and everyday crypto scams, and provide
> them with all the information and analysis they need to make informed and
> educated decisions in the crypto marketplace.**

Rug Munch Intelligence exists for one reason: the people who lose money to
crypto scams are overwhelmingly retail investors — individuals investing their
own savings, often without the tools, data, or time that institutions take for
granted. We build those tools for them, openly and affordably.

---

## (b) What We Do

RMI is a multi-chain crypto intelligence platform. Our product surface maps to
five capability areas, all built to serve the mission:

### 1. Scan — Token Security Scanning
- Rug pull prediction and risk scoring across tokens
- Honeypot detection, deployer history, and deployer reputation
- Wash trading, pump-and-dump, and bundle/cluster detection
- Liquidity watchdog, launch fairness, and pool manipulation analysis
- Smart contract deep-scan and upgrade monitoring
- Coverage across **38 data chains** (Solana, Ethereum, Base, BSC, Polygon,
  Arbitrum, Optimism, Avalanche, Fantom, Gnosis, TRON, Bitcoin, and more)

### 2. Intelligence — Analysis & Investigation
- AI-powered risk explainers that translate findings into plain language
- Investigation narratives for wallets, tokens, and events
- RAG search (vector + BM25 hybrid) over security knowledge
- DeFi attack detection: MEV sandwich attacks, flash loans, oracle
  manipulation, governance attacks, liquidation cascades
- Cross-chain entity tracking and entity clustering
- Deployer reputation and insider-network analysis

### 3. Alerts — Real-Time Protection
- WebSocket alert streams and configurable webhooks
- Token watch, whale accumulation, and liquidity-removal alerts
- Alert pipeline with multi-source verification
- Telegram bot alerts for on-the-go monitoring
- Mempool sentinel and intelligent webhooks for emerging threats

### 4. Wallet — Wallet Forensics
- Wallet drain and risk scanning
- Address labeling from 15+ sources (CEX labels, phishing databases, OFAC
  sanctions, scam databases, exchange clusters, cross-chain propagation)
- Wallet clustering, behavior profiling, and cross-chain fund tracing
- Portfolio risk aggregation and entity attribution

### 5. Content — Education & Information
- News intelligence and curated market/news feeds
- Educational content explaining common scams and how to spot them
- Bulletins, campaign radar, and community forensics
- All content written to help retail investors make **informed, educated
  decisions** — never to hype

These five areas are exposed through a **unified product surface**:

| Interface | Protocol | Description |
|-----------|----------|-------------|
| HTTP API | REST + WebSocket | Public and authenticated endpoints |
| MCP Server | JSON-RPC / SSE | AI-agent tool interfaces |
| Telegram Bot | Polling / Webhook | Bot commands and alerts |
| Web / Frontend | HTTPS | Customer dashboard (rugmunch.io) |
| DataBus | Unified data layer | 270+ tools across 67+ providers with automatic failover |

---

## (c) Who It's For

**Retail investors.** Concretely:

- People buying tokens on DEXs who want to check a project before committing money
- Holders who want to monitor their wallets for drains, unusual activity, or risk
- Investors evaluating new launches who need to see deployer history, liquidity
  locks, and holder distribution
- Anyone who has ever wondered *"is this a scam?"* and deserves a straight,
  evidence-based answer

We do **not** optimize for professional traders chasing alpha, nor for
institutional desks — though those users are welcome. Our product decisions,
pricing, and documentation are shaped by the retail investor's need for
**clarity, plain language, and affordable access**.

---

## (d) Free + Open Philosophy

1. **Free first.** Core protection is free: token scans, risk alerts, wallet
   checks, and educational content. No paywall on safety basics.
2. **Pay-per-use, not subscriptions.** Premium deep-dive tools are priced per
   call via the x402 protocol ($0.01–$0.25). No monthly fees, no minimums, no
   lock-in. Empty results are auto-refunded — you are never charged for no data.
3. **No accounts, no KYC.** A crypto wallet *is* the authentication. Connect any
   supported wallet and use the platform immediately.
4. **Open-core code model.** Public scanner modules, API patterns, and database
   schemas are open source; the scoring heuristics and admin tooling remain
   private. See [`OPEN-CORE.md`](../OPEN-CORE.md).
5. **Open documentation.** This documentation site is free, self-hostable, and
   published openly — the same transparency we ask of the projects we analyze.

---

## (e) Product Map — Built Features

| Area | Built Features | Where |
|------|----------------|-------|
| Token Risk | Rug pull predictor, unified token scanner, honeypot & contract deep-scan, wash trading detector, bundle detection, liquidity watchdog, launch fairness analyzer, clone scanner, deployer history/reputation | `app/scanners/`, `app/domains/scanner/`, `app/domains/scanners/` |
| Wallet | Unified wallet scanner, drain scanner, wallet clustering, behavior profiling, cross-chain trace, wallet label loader, portfolio risk aggregator | `app/wallets/`, `app/domains/wallet/` |
| Market Data | DataBus (67+ providers, failover chains), price consensus, multi-chain market feeds, OHLCV and social data | `app/databus/`, `app/price_consensus.py` |
| Alerts | Alert pipeline, WebSocket alerts, intelligent webhooks, token watch, whale accumulation, liquidity watchdog, mempool sentinel | `app/alert_pipeline.py`, `app/websocket_alerts.py`, `app/intelligent_webhooks.py` |
| AI / Intelligence | AI risk explainer, investigation narratives, RAG (vector + BM25), entity graph, insider networks, fraud GNN | `app/rag/`, `app/ai_risk_explainer.py`, `app/investigation_narratives.py` |
| DeFi Detection | MEV sandwich, flash loan, oracle manipulation, governance attack, liquidation cascade, pump & dump detectors | `app/detectors/` |
| Content / News | News intelligence, news service, content platform, newsletter, bulletins, campaign radar | `app/news_intelligence.py`, `app/news_service.py`, `app/domains/news/` |
| Distribution | Telegram bot, MCP server, x402 pay-per-call gateway, REST API, web frontend | `app/mcp/`, `app/domains/x402/`, `app/domains/telegram/` |
| Billing / Access | x402 micropayments, tool tiers, trial gating, auto-refunds, 6-tier access control | `app/billing/`, `app/tool_tiers.py`, `app/domains/x402/` |

**Status:** token scanning, wallet forensics, market data, AI reports, x402
monetization, alert streaming, RAG search, and DeFi detection are all in
production. See [`SYSTEM_STATUS.md`](SYSTEM_STATUS.md) for live status.

---

## (f) Documentation Framework Recommendation

We evaluated four options for publishing self-hostable, free, open-source docs
for this Python/FastAPI monorepo:

| Framework | Language / Toolchain | License | Self-Hostable & Free | Verdict |
|-----------|----------------------|---------|----------------------|---------|
| **MkDocs + Material for MkDocs** | Python (`pip install mkdocs-material`) | BSD | ✅ Yes | ⭐ **Recommended** |
| Docusaurus | Node.js / React | MIT | ✅ Yes | Good, but Node toolchain |
| Astro Starlight | Node.js / Astro | MIT | ✅ Yes | Fast, but Node toolchain |
| Mintlify | Hosted / proprietary | Commercial | ⚠️ Limited | Great DX, not free/OSS |

### Recommendation: **MkDocs + Material for MkDocs**

Rationale: the backend team already lives in the Python toolchain, so docs
build with `pip` and integrate with our existing CI — no Node/React dependency
added to a Python monorepo. Material for MkDocs is BSD-licensed, fully
self-hostable, ships offline search, versioning, blog, and syntax highlighting
out of the box, and `mkdocstrings` can auto-generate API reference pages
directly from our Python docstrings, keeping reference docs in sync with code.

**Runner-up note:** Astro Starlight is an excellent choice if we later decide
to converge docs tooling with the frontend (Nuxt) ecosystem — revisit if that
team adopts it first.

---

## Docs

- [Documentation Plan](DOCS_PLAN.md) — structure and rollout for the full docs site
- [API Guide](API-GUIDE.md) · [API Reference](API_REFERENCE.md) · [FAQ](FAQ.md)
- [ARCHITECTURE.md](ARCHITECTURE.md) · [DEPLOYMENT.md](DEPLOYMENT.md)

---

*Rug Munch Media LLC — protecting retail investors, one scan at a time.*
