# RMI Darkroom — Complete Backend Documentation
## RugMunch Intelligence Platform — Admin Backend v2

---

## Overview

The RMI Darkroom is a comprehensive, enterprise-grade admin backend for the RugMunch Intelligence crypto security platform. It provides full control over users, content, wallets, payments, security, analytics, and token deployment across multiple blockchains.

**Status:** Production-ready | **Version:** 2.0 | **Date:** May 31, 2026

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    RMI Darkroom Backend                     │
├─────────────────────────────────────────────────────────────┤
│  Admin SPA (/admin)    │    Darkroom UI (/darkroom)         │
│  ├─ Dashboard          │    ├─ Token Deployer                │
│  ├─ User Management    │    ├─ Airdrop Manager              │
│  ├─ Security Center    │    ├─ Multi-chain Launch            │
│  ├─ Wallet Manager     │    └─ Custom Snapshots              │
│  ├─ Analytics          │                                    │
│  ├─ Bulletin Board     │                                    │
│  ├─ Financial          │                                    │
│  └─ Configuration      │                                    │
├─────────────────────────────────────────────────────────────┤
│  API Layer (757+ endpoints)                                 │
│  ├─ /api/v1/admin/backend/*  — Admin operations             │
│  ├─ /api/v1/wallets/v2/*     — Wallet management            │
│  ├─ /api/v1/analytics/*      — Real-time analytics          │
│  ├─ /api/v1/admin/bulletin/* — Content management            │
│  ├─ /api/v1/admin/tokens/*   — Token deployment             │
│  └─ /api/v1/bulletin/*       — Public content               │
├─────────────────────────────────────────────────────────────┤
│  Core Engines                                               │
│  ├─ Admin Backend (RBAC, Audit, Sessions)                  │
│  ├─ Wallet Manager v2 (25+ chains, HD, Rotation)           │
│  ├─ Security Defense (Bot Detection, WAF, DDoS)            │
│  ├─ Analytics Engine (Real-time, Prometheus, Grafana)        │
│  ├─ Bulletin Board (CMS, Moderation, SEO)                  │
│  ├─ Token Deployer (5 chains, Blacklist, Anti-bot)         │
│  └─ Plugin System (Extensible architecture)                 │
├─────────────────────────────────────────────────────────────┤
│  Data Layer                                                 │
│  ├─ Redis — Sessions, rate limits, caching                  │
│  ├─ Supabase — Users, audit logs, wallet data               │
│  ├─ File System — Vault, configs, backups                  │
│  └─ ClickHouse — Analytics time-series (optional)           │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Features

### 1. Role-Based Access Control (RBAC)
- **5 roles:** superadmin, admin, moderator, viewer, support
- **Permission matrix** with 30+ granular permissions
- **IP allowlists** for admin access
- **Session management** with 8-hour timeout, max 3 concurrent
- **2FA support** (TOTP-ready)

### 2. Wallet Manager v2
- **25+ chains:** Bitcoin, Ethereum, Solana, TRON, Base, BSC, Polygon, Arbitrum, Optimism, Avalanche, Fantom, Gnosis, Dogecoin, Litecoin, and more
- **HD Wallets:** BIP39/BIP44/BIP49/BIP84 mnemonic support
- **Key Rotation:** Scheduled automatic rotation with notifications
- **Payment Integration:** x402 micropayments, subscription tiers
- **Balance Monitoring:** Real-time tracking across all chains
- **AES-256-GCM encryption** with Argon2id key derivation
- **Multi-signature ready** architecture

### 3. Security Defense System
- **Bot Detection:** Behavioral analysis, fingerprinting, heuristics
- **Anomaly Detection:** Statistical analysis on request patterns
- **Honeypot Endpoints:** 10 trap endpoints that auto-ban attackers
- **DDoS Protection:** Circuit breaker pattern, rate limiting
- **IP Reputation:** Integration-ready for AbuseIPDB
- **Geo-blocking:** Country-based access control
- **Request Fingerprinting:** Canvas, WebGL, font analysis

### 4. Analytics Engine
- **Real-time Metrics:** CPU, memory, requests, errors, latency
- **4 Default Dashboards:** System Health, Financial, Security, Users
- **Trend Detection:** Automatic anomaly detection with 3-sigma analysis
- **Prometheus Export:** Compatible with Prometheus/Grafana stack
- **WebSocket-ready:** Real-time streaming data
- **Custom Dashboards:** Configurable widget layouts

### 5. Bulletin Board / CMS
- **Post Management:** CRUD with versioning, scheduling, expiry
- **Categories:** news, alert, update, promo, system, community, announcement, tutorial
- **Targeting:** Audience segmentation (free, premium, pro, admins)
- **Moderation:** Draft/review/published/archived workflow
- **SEO:** Meta tags, OpenGraph, slug generation
- **Comments:** Threaded discussions with moderation

### 6. Token Deployer (Darkroom)
- **5 Chains:** Ethereum, Base, BSC, Solana, TRON
- **Features:** Blacklist, anti-bot, anti-sniper, team allocation, vesting
- **Airdrop System:** 1:1 exact-match airdrop, multi-chain snapshots
- **Custom Snapshots:** JSON, CSV, manual upload
- **Anti-gaming:** Sybil detection, multi-account filtering

---

## API Endpoints Summary

| Category | Endpoints | Auth |
|----------|-----------|------|
| Admin Auth | 5 | Public/Session |
| Dashboard | 2 | dashboard.read |
| Users | 5 | users.read/write |
| Security | 8 | security.read/write |
| System | 5 | system.read/write |
| Content | 3 | content.read/write |
| Financial | 3 | financial.read |
| API Keys | 3 | api_keys.read/write |
| Admin Mgmt | 4 | superadmin only |
| Backups | 2 | superadmin only |
| Webhooks | 2 | webhooks.read/write |
| **Wallet Manager v2** | **19** | token_deploy.read/write |
| **Analytics** | **11** | analytics.read |
| **Bulletin Board** | **18** | content.read/write |
| **Token Deployer** | **24** | X-Admin-Key |
| **Public Bulletin** | **5** | None |
| **Total** | **757+** | Mixed |

---

## File Structure

```
/root/backend/
├── app/
│   ├── admin_backend.py          # Core admin engine (RBAC, audit, sessions)
│   ├── wallet_manager_v2.py      # Wallet management engine
│   ├── security_defense.py        # Bot detection, WAF, DDoS protection
│   ├── analytics_engine.py        # Real-time metrics and dashboards
│   ├── bulletin_board.py          # CMS engine
│   ├── plugin_system.py          # Plugin architecture
│   ├── token_deployer.py         # Multi-chain token deployer
│   ├── multichain_airdrop.py     # Airdrop engine
│   └── routers/
│       ├── admin_backend.py      # Admin API (36 endpoints)
│       ├── wallet_manager_v2.py  # Wallet API (19 endpoints)
│       ├── analytics.py          # Analytics API (11 endpoints)
│       ├── bulletin_board.py     # Bulletin API (18 endpoints)
│       ├── darkroom_tokens.py    # Token deployer API
│       ├── darkroom_airdrop.py   # Airdrop API
│       └── darkroom_multichain.py # Multi-chain API
├── static/
│   ├── admin.html                # Admin SPA (52KB)
│   └── darkroom.html             # Token deployer UI (43KB)
└── main.py                       # FastAPI app (757+ routes)
```

---

## Security Features

### Authentication
- bcrypt password hashing
- JWT session tokens with expiry
- Rate limiting on all endpoints
- IP blocking with auto-ban
- Failed login tracking (auto-ban after 5 attempts)
- Session invalidation on logout
- Concurrent session limits

### Authorization
- Role-based access control
- Permission matrix per endpoint
- Admin-only endpoints for sensitive operations
- Audit logging of all actions
- Before/after state tracking

### Data Protection
- AES-256-GCM encryption for wallet keys
- Argon2id key derivation
- File permissions (chmod 600) on vault files
- No private keys in memory longer than necessary
- Secure session storage in Redis

### Network Security
- Bot detection with behavioral analysis
- Honeypot endpoints (auto-ban on trigger)
- DDoS circuit breaker
- Request fingerprinting
- Anomaly detection on traffic patterns
- Geo-blocking capability

---

## Wallet Manager v2

### Supported Chains

| Chain | Family | Address Pattern | HD Path |
|-------|--------|----------------|---------|
| Bitcoin | Bitcoin | 1/3/bc1... | m/44'/0'/0'/0/0 |
| Bitcoin SegWit | Bitcoin | 3/bc1... | m/49'/0'/0'/0/0 |
| Bitcoin Native SegWit | Bitcoin | bc1... | m/84'/0'/0'/0/0 |
| Ethereum | EVM | 0x... | m/44'/60'/0'/0/0 |
| Base | EVM | 0x... | m/44'/60'/0'/0/0 |
| Polygon | EVM | 0x... | m/44'/60'/0'/0/0 |
| Arbitrum | EVM | 0x... | m/44'/60'/0'/0/0 |
| Optimism | EVM | 0x... | m/44'/60'/0'/0/0 |
| Avalanche | EVM | 0x... | m/44'/60'/0'/0/0 |
| BSC | EVM | 0x... | m/44'/60'/0'/0/0 |
| Fantom | EVM | 0x... | m/44'/60'/0'/0/0 |
| Gnosis | EVM | 0x... | m/44'/60'/0'/0/0 |
| Solana | Solana | Base58 | m/44'/501'/0'/0' |
| TRON | TRON | T... | m/44'/195'/0'/0/0 |
| Dogecoin | Secp256k1 | D... | m/44'/3'/0'/0/0 |
| Litecoin | Secp256k1 | L/M/ltc1... | m/44'/2'/0'/0/0 |

### Wallet Tiers

| Tier | Use Case | Security |
|------|----------|----------|
| hot | Active trading | Standard |
| warm | Regular operations | Enhanced |
| cold | Long-term storage | High |
| vault | Maximum security | Multi-sig ready |

### Payment Integration

- **x402:** Enable per-wallet with price in USD
- **Subscriptions:** Tier-based (free, basic, pro, enterprise)
- **Payment Types:** x402, subscription, one-time, marketplace, refund, withdrawal, deposit, fee, reward

---

## Analytics Dashboards

### System Health Dashboard
- CPU Usage (gauge + line chart)
- Memory Usage (gauge + line chart)
- Disk Usage (gauge)
- Requests/minute (counter)
- Response Latency (line chart)
- Error Rate (line chart)

### Financial Dashboard
- Total Revenue (counter)
- MRR (counter)
- ARPU (counter)
- Churn Rate (gauge)
- Revenue Trend (line chart)
- Payment Count (line chart)

### Security Dashboard
- Threats Blocked (counter)
- Bot Requests (counter)
- Attacks Detected (counter)
- Blocked IPs (counter)
- Threat Types (pie chart)
- Attack Timeline (line chart)

### User Analytics Dashboard
- DAU (counter)
- MAU (counter)
- New Users (counter)
- Retention Rate (gauge)
- User Growth (line chart)
- User Tiers (pie chart)

---

## Plugin System

### Plugin Types
- **connector** — Data sources (exchanges, APIs, oracles)
- **scanner** — Security scanners (contract, wallet, token)
- **analyzer** — Analysis engines (risk, sentiment, on-chain)
- **notifier** — Alert channels (email, telegram, webhook)
- **exporter** — Data export (CSV, PDF, API, webhook)
- **wallet** — Wallet integrations (hardware, custodial)
- **payment** — Payment processors (x402, stripe, crypto)
- **ml** — ML models (fraud detection, prediction)
- **security** — Security tools (WAF, firewall)
- **analytics** — Analytics integrations (Grafana, Prometheus)

### Built-in Plugins
- PrometheusExporter — Export metrics to Prometheus format
- WebhookNotifier — Send notifications to webhooks
- RedisCache — Redis caching and pub/sub connector

### Plugin Directory
```
/root/backend/plugins/
├── connector/
├── scanner/
├── analyzer/
├── notifier/
├── exporter/
├── wallet/
├── payment/
├── ml/
├── security/
└── analytics/
```

---

## Deployment

### Requirements
- Python 3.10+
- Redis 6.0+
- FastAPI + Uvicorn
- Optional: Supabase, ClickHouse, Prometheus, Grafana

### Environment Variables
```bash
# Core
JWT_SECRET=your-jwt-secret
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your-redis-password
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-key

# Wallet Vault
WALLET_VAULT_PASSWORD=your-vault-password

# Admin
ADMIN_API_KEY=your-admin-key

# x402
X402_EVM_PAY_TO=your-wallet-address

# Security
ABUSEIPDB_API_KEY=your-abuseipdb-key  # optional
```

### Startup
```bash
cd /root/backend
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Health Check
```bash
curl http://localhost:8000/health
```

---

## Admin Access

### Default Admin
- **Email:** admin@rugmunch.io
- **Password:** Darkroom2025!
- **Role:** superadmin

### Admin UI
- **URL:** https://your-domain.com/admin
- **Login:** Email + Password + optional 2FA
- **Session:** 8-hour expiry, max 3 concurrent

### Darkroom (Token Deployer)
- **URL:** https://your-domain.com/darkroom
- **Auth:** X-Admin-Key header

---

## API Usage Examples

### Generate Wallet
```bash
curl -X POST https://api.rugmunch.io/api/v1/wallets/v2/generate \
  -H "X-Admin-Session: sess_xxx" \
  -H "Content-Type: application/json" \
  -d '{"chain": "eth", "purpose": "payments", "tier": "hot"}'
```

### Record Payment
```bash
curl -X POST https://api.rugmunch.io/api/v1/wallets/v2/payments \
  -H "X-Admin-Session: sess_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "wallet_id": "wal_eth_123",
    "wallet_address": "0x...",
    "chain": "eth",
    "payment_type": "x402",
    "amount": 0.01,
    "amount_usd": 25.00,
    "user_id": "user_123"
  }'
```

### Get Analytics
```bash
curl https://api.rugmunch.io/api/v1/analytics/dashboards/system \
  -H "X-Admin-Session: sess_xxx"
```

### Prometheus Metrics
```bash
curl https://api.rugmunch.io/api/v1/analytics/prometheus
```

---

## Monitoring & Alerting

### Prometheus Metrics
All system metrics are exportable in Prometheus format at `/api/v1/analytics/prometheus`.

### Key Metrics
- `rmi_cpu_percent` — CPU usage
- `rmi_memory_percent` — Memory usage
- `rmi_requests_per_minute` — Request rate
- `rmi_response_time_ms` — Response latency
- `rmi_error_rate` — Error percentage
- `rmi_revenue_usd` — Total revenue
- `rmi_threats_blocked` — Threats blocked
- `rmi_active_users` — Active users

### Grafana Integration
Import the Prometheus endpoint into Grafana for visualization.

---

## Backup & Recovery

### Wallet Vault
- Encrypted JSON file at `/root/.rmi/wallets/vault_v2.json`
- Keystore at `/root/.rmi/wallets/keystore.enc`
- Payment log at `/root/.rmi/wallets/payments.jsonl`

### Backup Strategy
1. Daily encrypted backups to secure storage
2. Seed phrase recovery for HD wallets
3. Multi-signature backup for vault wallets
4. Audit log retention: 90 days

---

## Development

### Adding a New Plugin
```python
from app.plugin_system import Plugin, PluginType

class MyPlugin(Plugin):
    @property
    def name(self): return "my_plugin"
    @property
    def version(self): return "1.0.0"
    @property
    def plugin_type(self): return PluginType.ANALYZER
    @property
    def description(self): return "My custom analyzer"

    def _setup(self):
        # Initialize your plugin
        pass
```

### Adding a Dashboard Widget
```python
from app.analytics_engine import DashboardWidget

widget = DashboardWidget(
    widget_id="my_widget",
    widget_type="line",
    title="My Metric",
    metric_name="my_metric",
    width=6,
    height=4,
)
engine.add_widget("system", widget)
```

---

## Security Checklist

- [ ] Change default admin password
- [ ] Set strong WALLET_VAULT_PASSWORD
- [ ] Enable Redis AUTH
- [ ] Configure IP allowlists for admin access
- [ ] Set up AbuseIPDB API key
- [ ] Enable 2FA for superadmin accounts
- [ ] Configure backup schedule
- [ ] Set up Prometheus/Grafana monitoring
- [ ] Enable HTTPS only
- [ ] Review audit logs weekly
- [ ] Rotate wallet keys quarterly
- [ ] Test disaster recovery plan

---

## Support

- **Email:** admin@rugmunch.io
- **Docs:** https://docs.rugmunch.io
- **API:** https://api.rugmunch.io/docs
- **Status:** https://status.rugmunch.io

---

## License

Proprietary and confidential. Unauthorized use, distribution, or reproduction is strictly prohibited.

Copyright (c) 2026 RugMunch Intelligence. All rights reserved.

---

**Built with:** FastAPI, Redis, Supabase, Python 3.12, love for crypto security.

**The Bloomberg Terminal of Shitcoins.**
