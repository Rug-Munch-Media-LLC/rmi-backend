# RMI — Rug Munch Intelligence

> **The first open-source crypto intelligence platform.**
> Bloomberg-grade security and market intelligence for the on-chain world.

---

## About Rug Munch Media LLC

We are **Rug Munch Media LLC**, a Wyoming-based software company building open-source tools that make the crypto economy safer — for consumers, builders, and institutions.

Crypto moves fast, and bad actors move faster. Phishing sites clone trusted brands within hours of a certificate being issued. Token launches rug in seconds. Wallets drain before a user knows what happened. The existing security stack is fragmented, expensive, and gated behind paywalls. **We are building the open alternative.**

RMI (Rug Munch Intelligence) is the **first open-source crypto intelligence platform** — a unified, AI-powered command center for token risk analysis, wallet forensics, multi-chain market data, phishing detection, and regulatory-grade intelligence. Our ambition is to become the **Bloomberg terminal of crypto**: one trusted surface where the entire industry can see what is happening, what is risky, and what is real.

**Who we serve:**
- **Consumers** — real-time protection against rug pulls, phishing, and wallet drainers
- **Industry** — exchanges, custodians, compliance teams, and researchers who need defensible on-chain intelligence
- **AI agents** — first-class Model Context Protocol (MCP) integration and x402 micropayments for autonomous tooling

We are an open-source company. Our code, our data, and our threat intelligence are public. Our business model is delivering the managed product, the API, and the institutional tier — not locking away the safety net.

---

## What RMI does

### Threat detection
- **Token scanning** — real-time rug-pull, honeypot, and scam detection across 18+ EVM chains + Solana
- **Wallet forensics** — track wallet behavior and transaction patterns, flag known bad actors
- **Phishing detection** — Certificate Transparency monitoring catches phishing domains within hours of registration, before they go live
- **Bayesian reputation** — statistically grounded deployer trust scoring, not vibes

### Market intelligence
- **2,500+ assets** — full coverage across major chains
- **News aggregation** — 1,800+ sources with clustering, dedup, and sentiment
- **Whale alerts** — large wallet movements and trading patterns in real time
- **Multi-chain market data** — OHLCV, liquidity, and volume from a single API

### AI-native platform
- **RAG-grounded reports** — every claim cited, no hallucination
- **MCP server** — drop RMI into any AI agent or LLM workflow
- **x402 micropayments** — pay-per-call pricing for autonomous agents
- **Open weights and open data** — no vendor lock-in

---

## Architecture

RMI is a single FastAPI backend with modular domain services:

```
Backend (FastAPI + Python)
├── DataBus       — single data plane, 18 chains, 4-layer defense (labels → scanner → RAG → prices)
├── SENTINEL      — multi-chain token security scanner
├── Wallet Bank   — 39M+ labeled wallets, federated from open sources
├── RAG Engine    — 3-pillar hybrid retrieval (dense + sparse + entity)
├── x402 Gateway  — AI-agent micropayments
├── MCP Server    — 221 tools for AI agents
└── Observability — Prometheus, GlitchTip, OpenTelemetry
```

Storage: PostgreSQL, Redis, Neo4j, Qdrant, ClickHouse, DuckDB.

---

## Repositories

| Platform | URL |
|----------|-----|
| 🐙 GitHub (canonical) | https://github.com/Rug-Munch-Media-LLC/rugmuncher-backend |
| 🦊 GitLab (mirror) | https://gitlab.com/cryptorugmuncher/rugmuncher-backend |
| 🤗 HuggingFace (canonical) | https://huggingface.co/cryptorugmuncher/rugmuncher-backend |

---

## Links

- 🌐 **Website**: https://rugmunch.io
- 📧 **Contact**: info@rugmunch.io
- 💬 **Telegram**: https://t.me/cryptorugmuncher
- 🐦 **X / Twitter**: https://x.com/cryptorugmunch
- 🐙 **GitHub org**: https://github.com/Rug-Munch-Media-LLC
- 🦊 **GitLab**: https://gitlab.com/cryptorugmuncher
- 🤗 **HuggingFace org**: https://huggingface.co/cryptorugmuncher

---

## License

**MIT** — see `LICENSE.md`.

RMI is open source because the safety of the crypto economy cannot depend on a single vendor. We compete on the quality of the product, not on who is allowed to see the threat.

---

**Built by Rug Munch Media LLC · Wyoming, USA**  
*Open-source crypto intelligence. AI-native. Built to last.*
