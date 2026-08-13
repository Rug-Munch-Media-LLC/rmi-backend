# Documentation Plan — Rug Munch Intelligence (RMI)

> Short plan for building a complete, self-hosted documentation site for the
> RugMunch platform. Toolchain recommendation: **MkDocs + Material for MkDocs**
> (see [MISSION.md](MISSION.md) §f for the framework evaluation).

---

## 1. Goals

1. Give retail investors plain-language docs on how to stay safe and use the platform.
2. Give developers complete API/MCP/x402 reference material.
3. Give operators deployment and architecture documentation.
4. Stay **free, self-hostable, and open** — no SaaS lock-in for docs.

---

## 2. Site Structure (Proposed Outline)

```
docs/  (MkDocs source)
├── index.md                  # Landing: mission + quick links (from MISSION.md)
├── mission/
│   ├── mission.md            # Mission statement (MISSION.md §a–d)
│   ├── philosophy.md         # Free + open philosophy, open-core model
│   └── product-map.md        # What's built, per product area (MISSION.md §e)
├── getting-started/
│   ├── quickstart.md         # 5-minute intro: free scan → wallet → alerts
│   ├── free-vs-paid.md       # Free tools, trials, pay-per-use pricing
│   └── supported-chains.md   # Chains, payment methods, wallets
├── user-guides/              # Retail investor focus — plain language
│   ├── scan-a-token.md
│   ├── check-a-wallet.md
│   ├── set-up-alerts.md
│   ├── spot-common-scams.md  # Educational content: rug pulls, honeypots, etc.
│   └── telegram-bot.md
├── developers/
│   ├── api/                  # REST API guides (from API.md, API_REFERENCE.md)
│   ├── mcp/                  # MCP server setup + tool catalog
│   ├── x402/                 # Payment protocol, refunds, discovery
│   ├── databus/              # Data layer, providers, failover
│   └── sdk/                  # Python SDK
├── architecture/             # ARCHITECTURE.md, DATABASE.md, DEPLOYMENT.md
├── contributing/             # Coding standards, extending, CI gates
├── faq.md                    # From FAQ.md + frontend FAQ
└── reference/                # Auto-generated via mkdocstrings (API reference)
```

## 3. Migration & Authoring

| Source | Target |
|--------|--------|
| `docs/*.md` (existing 50+ files) | Migrate into the structure above; keep internal/AUDIT docs under `docs/internal/` out of the public site |
| `README.md` | Becomes the repo landing + link to the docs site |
| Python docstrings | Auto-rendered API reference via `mkdocstrings` (no duplication) |
| Frontend `FAQPage.tsx` FAQ_DATA | Single source of truth for user-facing FAQ; sync both directions |

## 4. Toolchain & CI

- `mkdocs-material` (BSD, self-hostable) + `mkdocstrings[python]` + `mkdocs-git-revision-date`
- Search: built-in offline search (no external service)
- CI: add a `docs` job to `.forgejo/workflows/ci.yml` — `mkdocs build --strict`, publish to GitHub Pages / Forgejo Pages / static host
- Versioning: `mike` for versioned docs (stable / latest)

## 5. Rollout Phases

| Phase | Scope | Deliverable |
|-------|-------|-------------|
| **1** | Mission + structure | `MISSION.md`, `DOCS_PLAN.md`, MkDocs skeleton, `index.md` |
| **2** | Retail-facing guides | User guides + FAQ + educational scam pages |
| **3** | Developer reference | API/MCP/x402/Databus guides, mkdocstrings auto-reference |
| **4** | Ops + contribute | Architecture, deployment, contributing, CI docs build |

## 6. Success Criteria

- `mkdocs build --strict` passes in CI on every PR that touches `docs/`
- Retail users can answer "is this token safe?" from the docs alone
- API reference stays in sync with code (generated, not hand-maintained)
- Zero external/SaaS dependencies to publish docs
