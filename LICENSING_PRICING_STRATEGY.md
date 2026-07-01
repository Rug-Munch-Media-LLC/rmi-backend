# RUG MUNCH MEDIA LLC — LICENSING & PRICING STRATEGY

> **Complete decisions for every system.**
> Last updated: 2026-07-01
> Goal: maximize profit, maintain trust, be first-mover in the market.

---

## 1. LICENSE DECISIONS (Per System)

| System | License | Why |
|--------|---------|-----|
| **WalletConnect integration** (in WalletPress) | **MIT** | Trust requires open source. Community audits wallet security code. No competitive advantage in the protocol itself. |
| **WalletPress core** (self-hosted backend) | **BSL 1.1** (Business Source) | Readable, auditable, but can't commercially clone. Converts to MIT on 2029-01-01. |
| **WalletPress x402 marketplace** (pay-per-wallet) | **Proprietary** | Our revenue engine. Don't show competitors how we price/route payments. |
| **PryScraper** | **Proprietary** | Our competitive moat. Stealth browser, anti-detection — we don't want anyone copying. |
| **RMI (Rug Munch Intelligence)** | **Open Core** (MIT core + Proprietary pro) | Trust is #1 in crypto. MIT detector framework builds community. Proprietary platform = revenue. |
| **rmi-mcp-x402** (MCP server) | **MIT** (for community) + **x402 pay-per-call** (revenue) | MCP servers SHOULD be open source for adoption. Revenue comes from x402 usage, not licensing. |

---

## 2. WALLETPRESS — DETAILED PROFIT MODEL

WalletPress is **THREE products** that need different strategies:

### 2.1 WalletConnect Integration Layer (MIT)

**What it is:** The wallet connection protocol/dApp bridge inside WalletPress.

**License:** MIT — fully open source.

**Why:** Trust. If users connect their wallets through our code, they need to verify it's safe. Closed source = no trust = no users.

**Revenue from this:** NONE directly. This is a **loss leader** that makes the rest of WalletPress trustworthy.

**What goes in this layer:**
- dApp connector (WalletConnect v2 protocol)
- Multi-chain address derivation (BIP-44/49/84)
- Address validation
- ENS/Unstoppable Domains resolution
- Hardware wallet support (Ledger, Trezor)

### 2.2 WalletPress Self-Hosted Core (BSL 1.1)

**What it is:** The main `main.py` FastAPI backend. Users self-host on their own infrastructure.

**License:** BSL 1.1 (Business Source License). Convert to MIT on 2029-01-01.

**Why BSL not MIT:**
- If MIT, anyone can rebrand and sell "WalletPress Pro" competing with us
- BSL allows: read the code, modify for personal use, contribute back
- BSL forbids: selling the software as a competing product

**Pricing — Dual System:**

| Tier | Price | What You Get |
|------|-------|--------------|
| **Community (BSL)** | Free | Full self-hosted backend, all 14 chains, CLI, API, WP plugin |
| **Self-Hosted Pro** | $99/mo | Priority support, auto-updates, advanced features, multi-user |
| **Self-Hosted Enterprise** | $2,400/yr | SSO, audit logs, SLA, dedicated support engineer |

**Revenue projection (Year 1):**
- 200 Community users (free, builds network)
- 50 Pro users × $99 = $4,950/mo = $59,400/yr
- 10 Enterprise × $2,400 = $24,000/yr
- **Self-hosted total: $83,400/yr**

### 2.3 WalletPress x402 Marketplace (Proprietary)

**What it is:** Standalone pay-per-wallet service at `walletpress.cc`. No account, no subscription. Bots/developers pay USDC per wallet generation.

**License:** Proprietary. Don't show competitors our pricing algorithms.

**Pricing — Pay-per-wallet:**

| Service | Price | Use Case |
|---------|-------|----------|
| **Generate wallet** | $0.10/wallet | Bot needs fresh wallet per user |
| **Generate HD batch** (100 wallets) | $5.00 | Bulk wallet generation |
| **Generate HD batch** (1000 wallets) | $25.00 | Enterprise bulk |
| **Derive address from mnemonic** | $0.02/derive | Read-only address extraction |
| **Check balance** | $0.01/check | Wallet monitoring |
| **Sign message** | $0.05/sign | Bot authentication |
| **Send transaction** | $0.10 + gas | Automated payouts |
| **Full transaction suite** | $0.50/tx | Multi-sig, scheduling, batching |

**Revenue projection (Year 1):**
- Average usage: 50,000 wallet generations/mo × $0.10 = $5,000/mo
- Power users: 5 × $500/mo = $2,500/mo
- Enterprise: 2 × $2,000/mo = $4,000/mo
- **x402 marketplace total: $138,000/yr**

### 2.4 WalletPress Cloud (Hosted SaaS)

**What it is:** We host WalletPress for users who don't want to self-host. Same features, managed by us.

**License:** Proprietary SaaS.

**Pricing — Subscription tiers:**

| Tier | Price | Features |
|------|-------|----------|
| **Starter** | $29/mo | 100 wallets, 14 chains, basic API |
| **Growth** | $99/mo | 1,000 wallets, x402 enabled, priority support |
| **Business** | $299/mo | 10,000 wallets, team features, SSO |
| **Enterprise** | $999/mo | Unlimited wallets, dedicated support, custom chains |

**Revenue projection (Year 1):**
- 100 Starter × $29 = $2,900/mo
- 30 Growth × $99 = $2,970/mo
- 10 Business × $299 = $2,990/mo
- 3 Enterprise × $999 = $2,997/mo
- **Cloud total: $142,000/yr**

### 2.5 WalletPress TOTAL Revenue Projection

| Stream | Year 1 | Year 2 | Year 3 |
|--------|--------|--------|--------|
| Self-hosted Pro | $59K | $180K | $360K |
| Self-hosted Enterprise | $24K | $60K | $120K |
| x402 marketplace | $138K | $400K | $1M |
| Cloud SaaS | $142K | $500K | $1.2M |
| **TOTAL** | **$363K** | **$1.14M** | **$2.68M** |

---

## 3. PRYSCRAPER — DETAILED PROFIT MODEL

### 3.1 License: PROPRIETARY (CONFIRMED)

**Why:**
- Competitive moat is our stealth browser, anti-detection, and bypass techniques
- If competitors see our code, they can replicate in days
- Crypto scrapers are a race — whoever has the best stealth wins

### 3.2 Pricing — Three Models

**Model A: SaaS (Primary)**
- Hosted API at `pry.dev`
- Pay-per-request with x402 micropayments
- Free tier: 1,000 requests/month
- Pro: $49/mo for 100K requests
- Enterprise: Custom pricing for high volume

| Tier | Price | Volume |
|------|-------|--------|
| **Free** | $0 | 1,000 req/mo |
| **Pro** | $49/mo | 100,000 req/mo |
| **Scale** | $199/mo | 500,000 req/mo |
| **Enterprise** | Custom | 5M+ req/mo |

**Model B: x402 Pay-per-call**
- No account needed
- Pay USDC per API call
- AI agents pay automatically

| Endpoint | Price |
|----------|-------|
| `/scrape` | $0.005/call |
| `/crawl` | $0.02/call |
| `/extract` | $0.01/call |
| `/screenshot` | $0.003/call |
| `/stealth_browser` | $0.05/minute |

**Model C: White-label Enterprise**
- Deploy PryScraper on your infrastructure
- Your branding, your data
- Annual license

| Tier | Price |
|------|-------|
| **Startup** | $10K/yr (up to 1M req/mo) |
| **Growth** | $50K/yr (up to 10M req/mo) |
| **Enterprise** | $200K+/yr (unlimited) |

### 3.3 PryScraper Revenue Projection

| Stream | Year 1 | Year 2 | Year 3 |
|--------|--------|--------|--------|
| SaaS subscriptions | $80K | $300K | $600K |
| x402 pay-per-call | $30K | $120K | $400K |
| White-label Enterprise | $200K | $600K | $1.2M |
| **TOTAL** | **$310K** | **$1.02M** | **$2.2M** |

---

## 4. RMI (RUG MUNCH INTELLIGENCE) — DETAILED PROFIT MODEL

### 4.1 License: OPEN CORE (CONFIRMED)

| Layer | License |
|-------|---------|
| RUI Core (8 basic detectors, public API) | MIT |
| RUI Pro (32 detectors, x402, MCP, RAG) | Commercial |
| RUI Enterprise (on-premise) | BSL 1.1 |
| RUI Cloud (managed) | SaaS |
| Detector framework (community-built) | MIT |
| Rug Munch Verified badge | Proprietary Terms |

### 4.2 Pricing — Already Designed in LICENSING_STRATEGY.md

**Pro tier: $99/mo**
**Team tier: $499/mo**
**Enterprise: $10K+/yr**
**Cloud: Pay-per-use**

### 4.3 RMI Revenue Projection

| Stream | Year 1 | Year 2 | Year 3 |
|--------|--------|--------|--------|
| Pro subscriptions | $120K | $600K | $1.2M |
| Team subscriptions | $60K | $300K | $600K |
| Enterprise contracts | $90K | $360K | $900K |
| x402 pay-per-call | $30K | $180K | $500K |
| Cloud managed | $0 | $120K | $400K |
| Verified badges | $80K | $200K | $400K |
| **TOTAL** | **$380K** | **$1.76M** | **$4.0M** |

---

## 5. RMI-MCP-X402 — DETAILED PROFIT MODEL

### 5.1 License: MIT + x402 Pay-per-call

**Why MIT:** MCP servers are ecosystem infrastructure. The more people use them, the more the ecosystem grows. Revenue comes from x402 usage, not licensing.

### 5.2 Pricing — x402 Pay-per-call (No subscriptions)

| Tool | Price | Description |
|------|-------|-------------|
| `rugmunch_scan_token` | $0.001 | Full token scan (32 detectors) |
| `rugmunch_wallet_forensics` | $0.01 | Wallet behavior analysis |
| `rugmunch_rug_probability` | $0.005 | AI rug prediction |
| `rugmunch_contract_audit` | $0.05 | Smart contract security |
| `rugmunch_threat_intel` | $0.002 | Threat intelligence lookup |
| `rugmunch_real_time_alert` | $0.001/min | Real-time monitoring |
| `rugmunch_address_labels` | $0.001 | Wallet label lookup |
| `rugmunch_chain_info` | Free | Multi-chain info |

### 5.3 rmi-mcp-x402 Revenue Projection

| Stream | Year 1 | Year 2 | Year 3 |
|--------|--------|--------|--------|
| x402 pay-per-call | $20K | $150K | $500K |
| Enterprise MCP hosting | $0 | $50K | $200K |
| **TOTAL** | **$20K** | **$200K** | **$700K** |

---

## 6. SPECIFIC IMPROVEMENTS PER SYSTEM

### 6.1 WalletPress Improvements (Self-Hosted BSL)

**Priority 1 (This Week):**
1. **Add WalletConnect v2 integration** — dApp connector for 300+ wallets
2. **Hardware wallet support** — Ledger, Trezor, GridPlus
3. **Multi-sig wallets** — Gnosis Safe integration for 2-of-3, 3-of-5
4. **Address book encryption** — Encrypted contact storage
5. **ENS/Unstoppable Domains** — Human-readable address resolution

**Priority 2 (This Month):**
6. **x402 marketplace UI** — pricing page at walletpress.cc/x402
7. **Stripe billing** — for self-hosted Pro subscriptions
8. **Auto-update mechanism** — Pro users get automatic updates
9. **License key system** — for Pro/Enterprise features
10. **Audit log API** — for Enterprise compliance

**Priority 3 (This Quarter):**
11. **WalletConnect v2 certified** — official WC integration
12. **Multi-user teams** — Organizations, permissions, roles
13. **Transaction scheduling** — Recurring payments, vesting
14. **Gas optimization** — EIP-1559, batch transactions
15. **Mobile SDK** — React Native, Flutter

### 6.2 PryScraper Improvements (Proprietary)

**Priority 1 (This Week):**
1. **camoufox integration** — Firefox-based anti-detection
2. **TLS fingerprint randomization** — Per-request unique fingerprints
3. **Cookie warming** — Pre-aged cookies for trust signals
4. **Residential proxy pool** — 100+ rotating IPs
5. **CAPTCHA solver integration** — 2captcha, anti-captcha

**Priority 2 (This Month):**
6. **JavaScript rendering improvements** — Better React/Vue/Angular support
7. **PDF extraction upgrade** — OCR for scanned documents
8. **Structured data extraction** — Schema.org, JSON-LD, microdata
9. **Screenshot comparison** — Visual diffing for change detection
10. **Rate limiting intelligence** — Per-domain adaptive limits

**Priority 3 (This Quarter):**
11. **AI-powered extraction v2** — Better LLM prompts, structured outputs
12. **Browser extension** — Chrome/Firefox scraping tool
13. **Shopify/WooCommerce integration** — E-commerce scraping
14. **Real-time monitoring** — Webhook + Slack/Discord alerts
15. **Multi-region deployment** — US, EU, APAC for speed

### 6.3 RMI Improvements (Open Core)

**Priority 1 (This Week):**
1. **Split codebase** — core/ (MIT) + pro/ (commercial)
2. **Add LICENSE headers** — Every file has SPDX identifier
3. **MCP tool naming** — rugmunch_scan_token (clear + discoverable)
4. **Verified badge system** — Already built! ✅
5. **Live demo at rugmunch.io** — Paste address → see 32 detector scores

**Priority 2 (This Month):**
6. **Add 8 more detectors** — Currently have 32, add 8 more
7. **RAG investigation reports** — AI-powered forensic analysis
8. **Real-time webhook alerts** — Token launches, deployer activity
9. **Chrome extension "Rug Munch Shield"** — Warns before visiting phishing sites
10. **YouTube demo series** — "How to detect a rug in 30 seconds"

**Priority 3 (This Quarter):**
11. **Threat intel feeds to exchanges** — $10K/mo per exchange
12. **DAO treasury protection** — $5K/mo per DAO
13. **Verified badge at scale** — $500/token, 100 tokens = $50K/mo
14. **Bug bounty program** — $50K for finding wrong safe verdict
15. **AI agent marketplace** — Agents built on top of RMI

### 6.4 rmi-mcp-x402 Improvements (MIT + x402)

**Priority 1 (This Week):**
1. **PyPI package** — `pip install rugmunch-mcp`
2. **Register on pulsemcp.com** — MCP server directory
3. **Register on glama.ai** — Codeberg's MCP registry
4. **Register on mcp.so** — Smithery registry
5. **MCP tool names** — Clear, discoverable, consistent

**Priority 2 (This Month):**
6. **8+ MCP tools** — Already have the framework
7. **x402 payment integration** — USDC on Base, Solana
8. **Streaming responses** — For long-running scans
9. **Batch operations** — Scan multiple tokens in one call
10. **Webhook subscriptions** — Real-time alerts via MCP

**Priority 3 (This Quarter):**
11. **MCP server hosting** — Managed MCP at mcp.rugmunch.io
12. **Custom tool builder** — Let users add their own tools
13. **Tool analytics** — Usage stats, popular tools
14. **Multi-MCP routing** — One request, multiple MCPs
15. **MCP marketplace** — Third-party tools on our platform

---

## 7. UNIFIED REVENUE PROJECTION (All Systems)

| System | Year 1 | Year 2 | Year 3 |
|--------|--------|--------|--------|
| **RMI (Rug Munch Intelligence)** | $380K | $1.76M | $4.0M |
| **WalletPress** (self-hosted + x402 + cloud) | $363K | $1.14M | $2.68M |
| **PryScraper** (SaaS + x402 + white-label) | $310K | $1.02M | $2.2M |
| **rmi-mcp-x402** (x402 pay-per-call) | $20K | $200K | $700K |
| **TOTAL** | **$1.07M** | **$4.12M** | **$9.58M** |

---

## 8. GO-TO-MARKET SEQUENCE

### Phase 1: Trust Foundation (Month 1-3)
- Launch RUI Core as MIT (open source)
- Launch PryScraper as SaaS (no source)
- Launch WalletPress Community (BSL, free self-hosted)
- Goal: 1,000 GitHub stars, 100 SaaS users

### Phase 2: Revenue (Month 4-6)
- Launch RUI Pro ($99/mo)
- Launch PryScraper Pro ($49/mo)
- Launch WalletPress x402 marketplace ($0.10/wallet)
- Goal: $50K MRR

### Phase 3: Enterprise (Month 7-12)
- Launch RUI Enterprise ($10K+/yr)
- Launch WalletPress Enterprise ($2,400/yr)
- Launch PryScraper White-label ($10K+/yr)
- Goal: $1M ARR

### Phase 4: Scale (Year 2)
- Launch RUI Cloud (managed SaaS)
- Launch WalletPress Cloud (hosted)
- Launch MCP marketplace
- Goal: $4M ARR

### Phase 5: Dominate (Year 3)
- First-mover advantage compounds
- Network effects (more users = better data = better product)
- Goal: $10M ARR

---

## 9. COMPETITIVE POSITIONING

### WalletPress vs Competition

| Competitor | Our Advantage |
|------------|---------------|
| Trust Wallet | Open source, auditable, 14 chains vs 10 |
| MetaMask | Self-hostable, institutional features |
| Exodus | BSL means we can build features they can't copy |
| Coinbase Wallet | We don't have their KYC baggage |

### PryScraper vs Competition

| Competitor | Our Advantage |
|------------|---------------|
| ScrapingBee | Proprietary = we don't show them how |
| Bright Data | x402 pay-per-call, no minimums |
| ScraperAPI | $0.005/call vs $0.10/call, 20x cheaper |
| Apify | We have AI extraction built in |

### RMI vs Competition

| Competitor | Our Advantage |
|------------|---------------|
| GoPlus | Open core = verifiable, x402 = AI agents |
| De.Fi | Open source = trustworthy |
| Token Sniffer | 32 detectors vs their 5, 96 chains |
| Chainalysis | 100x cheaper |

---

## 10. THE FIRST-MOVER ADVANTAGE

Why we win in 2026:

1. **RUI is the first open-core crypto intelligence platform** — competitors are all closed
2. **PryScraper is the first x402-native scraper** — competitors charge $0.10/call, we charge $0.005
3. **WalletPress is the first BSL wallet** — community can audit, competitors can't clone
4. **rmi-mcp-x402 is the first MCP server for crypto** — AI agents will use us by default
5. **The Rug Munch Verified badge is the first honest assessment** — others are paid shills

We are in the right place at the right time. The only thing that can stop us is execution.

---

## 11. NEXT STEPS (Immediate)

### This Week
- [ ] Decide on WalletConnect = MIT (done above)
- [ ] Add WalletConnect v2 to WalletPress
- [ ] Build `pip install rugmunch-mcp` package
- [ ] Register on pulsemcp.com + glama.ai
- [ ] Split RMI code into core/ (MIT) + pro/ (commercial)

### This Month
- [ ] Launch PryScraper Pro tier ($49/mo)
- [ ] Launch WalletPress x402 marketplace UI
- [ ] Launch RUI Pro tier ($99/mo)
- [ ] Create rugmunch.io live demo
- [ ] Start content marketing (YouTube, blog)

### This Quarter
- [ ] Launch Verified Badge program
- [ ] Launch PryScraper White-label
- [ ] Launch RUI Cloud
- [ ] Launch WalletPress Cloud
- [ ] Enterprise sales (DAOs, exchanges)

---

**The decisions are made. The licenses are set. The pricing is designed. The first-mover window is open. Now we ship.**