---
title: RMI Audit & Roadmap (2026-Q3)
status: canonical
owner: Rug Munch Media LLC Engineering
last_updated: 2026-07-06
audited_by: opencode (4 deep parallel audits)
supersedes: AUDIT.md (2026-06-30), 2026-Q3 status reports
audience:
  - engineers
  - agents
  - operators
goal: ship RMI (rmi-backend + rmi-frontend) to production-grade as 2026-architecturally-correct modular product, open-source-friendly without leaking IP
---

# RMI Audit & Roadmap

> **Honest one-line:** RMI is a working, profitable product whose codebase shape blocks hiring, reliability, and IP hygiene. The fastest path to a clean ship is **6 weeks of focused refactoring**, not a rewrite. A subset of the codebase is safe to publish open-source; the rest is IP we keep behind the binary.

---

## 1. Brutal Honest State (master table)

| Domain | Claim | Measured Reality |
|---|---|---|
| **Surface area** | "1,300+ endpoints across 46 tag groups" | **1,311 routes defined; 164 routers exist; only 21 are mounted by `app/factory.py` → `app/mount.py`. 148 routers exist in code but never served.** |
| **LOC** | "300k lines of code" | **649 .py files, 241,111 LOC in `app/`** |
| **Modules** | "Clean modular FastAPI" | **220 legacy `.py` files at `app/` root** (no domain structure); the new `app/domain/` package is only 7 modules / 7,433 LOC; everything else is a flat namespace |
| **God-files** | "Well-factored" | **21 files over 1,000 LOC** — including `app/routers/x402_tools.py` (5,781 LOC, 77 endpoints), `app/auth.py` (1,223 LOC, 18 importers), `app/databus/providers.py` (2,991 LOC), `app/wallet_manager_v2.py` (2,321 LOC), `app/telegram_bot/bot.py` (2,097 LOC, 58 command handlers) |
| **Anti-bot layered fallback** | "5 tiers, all real" | **2 tiers real (FlareSolverr + Playwright); 3 dead (cloudscraper, undetected-chrome, Tor — libs not in requirements; Google cache deprecated; Textise fictional).** |
| **Tests** | "886 tests passing" | **886 collected with `PYTHONPATH=.` workaround; 33/35 unit files fail collection by default. 717 unit pass in 9s with marker exclusion.** |
| **Coverage gate** | "60% enforced in CI" | **`fail_under=60` configured but NEVER ENFORCED.** Actual measured coverage: **8.17%** (74,333 stmts, 68,258 missed). 25 files completely uncovered. |
| **CI trustworthy** | "GitHub Actions + Forgejo pipelines gate releases" | **Forgejo `build` job: trustworthy** (ruff + format + mypy + pytest, all real gates). **GitHub `ci.yml`: decorative** — every job has `continue-on-error: true` or `|| true`, including test job. Failure = success. |
| **Pre-commit** | "Configured" | **Configured but never installed.** `.git/hooks/pre-commit` does not exist. |
| **Code style** | "Consistent" | **Two ruff configs (`ruff.toml` + `pyproject.toml`) override each other silently.** `ruff format` would reformat 248 files. |
| **API doc claims (FEATURES.md, AUDIT.md)** | "46 tag groups, 190 endpoints" | **Actual: 38 tag groups, 1,311 routes**, most endpoints are documented but not mounted. Several FEATURES.md rows cite files that don't exist. |
| **DB migrations** | "Alembic-managed" | **`alembic/` directory does not exist. `alembic/versions/` does not exist. Zero migrations.** Models scattered across 9 files. Schema changes today require manual SQL. |
| **Error handling** | "Production-grade" | **2,532 `except Exception:` blocks + 1 bare `except:` + 0 `AppError` class anywhere.** Only 1 custom exception class in 241k LOC (`RAGUnavailableError`). |
| **Secret hygiene** | "Gopass-managed" | **28 gitleaks findings.** `main.py.bak` (374KB) committed, contains 4 mainnet token addresses. `JWT_SECRET` defaults to `"dev-secret-CHANGE-ME"` in `app/config.py:39` — boot-in-prod-with-dev-secret footgun. |
| **Open-source boundary** | n/a | **Not decided.** Codebase has no LICENSE file. `LICENSING_PRICING_STRATEGY.md` proposes "MIT core + Proprietary pro" but it isn't implemented anywhere. |

---

## 2. Per-Area Findings (file:line precision)

### 2.1 Architecture & God-File Inventory

- **`app/routers/x402_tools.py:5781`** — 77 endpoints, 19 imports, 0 classes. Mixes payment-gate + 10 tools + 7 fallback chains.
- **`app/routers/x402_enforcement.py:2720`** — 14 importers, 100% procedural. Should be class-based middleware.
- **`app/auth.py:1223`** — 18 importers. JWT + bcrypt + TOTP + Google OAuth + Telegram login + wallet signature all in one file.
- **`app/databus/providers.py:2991`** — 30+ provider classes. **19 importers** of `app.databus.providers`.
- **`app/wallet_manager_v2.py:2321`** — wallet lifecycle + signing + sweeping + vault.
- **`app/telegram_bot/bot.py:2097`** — 58 command handlers. Mounted separately (correct), but internally a god-file.
- **`app/token_deployer.py:1418`** — 119 public functions, 8 classes.
- **`app/unified_scanner.py:1482`** — "unified" name is anti-pattern. Aggregates 8 scanners; should be composition root.
- **`app/databus/provider_chains.py:1723`** — 1 function in a 1,723-line file. Auto-generated; never hand-edit.
- **`app/routers/admin_backend.py:1691`** — 10 admin sections declared in docstring, 0 importers = currently dead (admin endpoints not mounted).
- **`app/routers/scam_school.py:1650`** — 0 importers. Dead educational content router.
- **`app/market_intel_router.py` + `intelligence_router.py` + `intelligence_panel.py`** — three overlapping "intelligence" routers, none fully mounted.

**11 circular-import cycles detected.** Worst:
- `app/auth.py` ↔ `app/auth_wallet.py` (auth-wallet is fundamental, blocked by cycle)
- `app/rag_service.py` ↔ `app/entity_extraction.py` + `app/scam_sources.py` (RAG pipeline split blocked)

**Mounted vs. defined routers:** `app/factory.py` → `app/mount.py` lists **21 routers**. **164 routers defined.** 148 unmounted.

---

### 2.2 Dev Workflow (rmi-backend)

- **`tests/__init__.py` missing** — 33/35 unit-test files fail collection without `PYTHONPATH=.`. New dev hits this in 30 seconds.
- **`app/main.py` referenced in Makefile, AGENTS.md, CONTRIBUTING.md but does not exist** — only `app/factory.py` exists.
- **`.github/workflows/ci.yml`** — `lint`, `typecheck`, `test`, `security`, `openapi`, `qdrant-cleanup` all have `continue-on-error: true` or `|| true`. Failure of any CI step is reported as success.
- **`python3 tests/run_tests.py`** — separate custom runner. `tests/test_rag.py` has its own `@test()` decorator + banner saying "Do NOT run with pytest — it will produce false failures."
- **`.pre-commit-config.yaml`** — configured; not installed.
- **`ruff.toml`** + **`pyproject.toml [tool.ruff]`** — two configs, one wins silently.
- **`tests/conftest.py`** — does not exist. `tests/unit/__init__.py` does not exist. `tests/integration/conftest.py` does not exist.
- **3 empty integration test files** — `tests/integration/test_databus.py` is 21 methods of `pass # TODO`.

**Coverage (real):** `pytest --cov=app --cov-report=term` → **8.17%**. `fail_under=60` exists in pyproject but is never asserted.

**Type discipline:** `mypy.ini` exists. Pyproject has `tool.mypy` section. Neither is enforceable because `mypy` binary not installed via standard `pip install -e .[dev]` (PEP 668 conflict).

**Files most-impactful to fix:**
- `pyproject.toml:1` — tool config, packages missing → `tool.setuptools.packages = ["app"]` fixes the import error.
- `tests/__init__.py` (missing) → `sys.path` setup.
- `.pre-commit-config.yaml:1` → `pre-commit install`.
- `.github/workflows/ci.yml` → strip every `|| true` and `continue-on-error: true`.

---

### 2.3 Documentation — What's Real vs Hallucinated

**REAL & BUILT** (the user-flagged features all exist):
- **Bulletin Board** — 43 endpoints, 2 routers, 2 frontend pages. (`app/bulletin_board.py` engine + `app/routers/bulletin.py`, `bulletin_board.py` HTTP)
- **Intelligence** — 51 endpoints across 3 routers + `app/services/deep_intelligence.py` (1,633 LOC engine) + 3 frontend pages
- **Markets** — 28+ market_intel endpoints, 4 frontend pages
- **Telegram bots** — framework built (`app/telegram_bot/`); only 2 of 5 claimed commands actually wired
- **Admin** — ~134 endpoints across 5 routers, mostly unmounted
- **Scanner** — 93 routes across 13+ routers + frontend pages

**HALLUCINATED in docs:**
- **`RMI_SYSTEM_MAP.md` "LOCAL DATA FILES" table** lists **7 files that don't exist** (`solana_cex_labels.csv`, `SOSANA-CRM-2024.json`, `scamsniffer_blacklist.json`, etc.)
- "39.4M wallet labels" claim has no source file — the 2,530 Scam Sniffer entries are fetched at runtime from GitHub
- "115 endpoints across 46 tag groups" — actual is **1,311 routes / 38 tag groups**
- "32 scanner modules" — actual is **32** (lucky match)
- "13 chains" — actual is 13 (lucky match)
- **"3 Telegram bot tokens leaked" — misleading.** 3 occurrences in git history are scammer adversary bot tokens embedded in `data/cryptoscamdb_urls.yaml` as detection evidence, not Rugmunch's leaked secrets.
- **`LICENSING_PRICING_STRATEGY.md` is misplaced** — entirely about WalletPress + PryScraper, duplicated verbatim in both rmi-backend and rmi-frontend
- **No LICENSE file** in either repo. Docs claim both "MIT" (README_HF.md) and "Open Core" (LICENSING_PRICING_STRATEGY.md). Neither is implemented in code.
- **`app/main.py` doesn't exist** — refactored to `app/factory.py` + `app/mount.py`. CONTRIBUTING.md, AGENTS.md, DESIGN.md all still reference it.

---

### 2.4 IP Leak Risk

The user said: "some of this will be secret so we cant be pumping all this out we need to decide what to keep backend while still being open source in our development methods with the public keep what we want for ourselves on the backside while explaining to people how to do this and be opensource".

Categorization of the codebase:

| Category | Examples | Open-Source Safe? |
|---|---|---|
| FastAPI scaffolding (factory/mount/middleware) | `app/factory.py`, `app/mount.py`, `app/middleware/*`, `app/core/*` (logging, config, redis, http) | ✅ YES (boilerplate) |
| 3rd-party API clients (Coingecko, Defillama, Birdeye, Jupiter, Alchemy) | `app/integrations/*`, `app/databus/providers/*` | ✅ YES (3rd-party code) |
| AI/LLM wrappers | `app/llm_*`, `app/rag/*` (engine) | ✅ YES (everyone writes the same) |
| Pydantic schemas | `app/models/*` (Pydantic) | ✅ YES |
| **Detection algorithms (risk scoring, honeypot detection, rug prediction, MEV, flash loan, sandwich)** | `app/scanners/*` (32 modules), `app/unified_scanner.py`, `app/flash_loan_attack_detector.py`, `app/pump_dump_manipulation_detector.py`, `app/liquidation_cascade_analyzer.py`, `app/oracle_manipulation_detector.py`, `app/mev_sandwich_detector.py`, `app/smart_contract_honeypot_detector.py`, `app/cross_chain_trace.py`, `app/rug_imminence_predictor.py` | ❌ **NEVER** (this is the IP) |
| **Scoring heuristics + weights** | `app/scanners/*`, `app/wallet_memory/*` (29,000 LOC) | ❌ **NEVER** (these are the calibration) |
| **Chain registry (CEX hot-wallet addresses)** | `app/chain_registry.py` (27 importers) | ❌ **NEVER** (security risk) |
| **Wallet-management flows (keys, signing, routing)** | `app/wallet_manager_v2.py`, `app/auth_wallet.py`, `app/wallet_factory.py`, `app/wallet_monitor.py`, `app/wallet_persona.py` | ❌ **NEVER** (custody) |
| **Customer integration code (webhooks, partner data feeds)** | `app/admin_backend.py`, `app/admin_extensions.py`, `app/admin_control.py` | ❌ **NEVER** (customer confidential) |
| **Curated threat data** | `app/wallet_memory/label_importer.py`, `data/cryptoscamdb_urls.yaml`, `app/scam_sources.py` | ❌ **NEVER** (proprietary intel) |
| **Intelligence generation (the n8n-fed AI analysis output)** | `app/intelligence/`, `app/services/deep_intelligence.py` | ❌ **NO** (output is the product) |
| **x402 payment strategies** | `app/routers/x402_tools.py` (5,781 LOC), `app/billing/*` | ⚠️ mostly IP, but payment-protocol primitives can be open |

**Recommended LICENSE strategy:**
- **Public repo (RMI Core):** MIT — FastAPI scaffolding, 3rd-party API clients, AI/LLM wrappers, Pydantic schemas, tooling, docs
- **Private repo (RMI IP):** BSL 1.1 — scanners/, wallet_memory/, chain_registry, billing IP, intelligence generation, threat data
- **Internal repo (RMI Pro):** No public license — admin/, customer integration, customer configs

---

## 3. The Plan — 5 Phases, 6 Weeks, with Hard Guardrails

### Guardrails (apply forever, no exceptions)

1. **No new file at `app/` root.** All new modules go under `app/<domain>/` or `app/core/` or `app/integrations/`. Enforce with CI: `comm -23 <(find app -maxdepth 1 -name "*.py" -not -name "__init__.py" | sort) <(cat app/_allowlist.txt | sort) | wc -l` must be 0.
2. **No router file >500 LOC or >20 endpoints.** CI check: `awk '/^@router\./{c++} END{print c}' app/routers/*.py | sort -rn | head` + LOC grep.
3. **All errors logged with context (URL, params, request_id).** No bare `except:` or `except Exception:` outside `app/core/`.
4. **No `|| true` in CI.** Ever. If a step is informational, move it to a separate `informational` job.
5. **Coverage gate enforced at 80%** for the public RMI Core repo; private repos measured separately.
6. **All secrets in gopass.** `JWT_SECRET` is `Field(...)` with no default — fail-fast on missing.
7. **All commits conventional (`feat:` / `fix:` / `chore:` / `refactor:`).** `make commit` enforced via pre-commit.
8. **Every router mounted or deleted.** CI: every `router = APIRouter()` must appear in `app/mount.py` OR in `app/_archived_routers.txt` with a justification comment.
9. **All public functions have docstrings.** Public API per-domain includes a typed `__all__`.

---

### Phase 1 — Stop the bleeding (1 week)

**Why now:** Every day we ship with the current shape, we accumulate debt. This phase pays for itself in week 1 by unblocking every new engineer.

**Tasks (in order):**
1. **Add `app/main.py`** that delegates to `app/factory.create_app`. Fixes `make dev`. [10 min]
2. **Add `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`.** [5 min]
3. **`pyproject.toml:tool.setuptools.packages = ["app"]`** — fixes the "33/35 unit files fail to import" bug. [5 min]
4. **`pre-commit install`** on Talos + Cinnabox. Verify pre-commit hooks fire. [10 min]
5. **Strip `|| true` from `.github/workflows/ci.yml`.** Move gap-aware mypy to a separate `informational` job. [2 hr]
6. **Remove `ruff.toml` (or align with `pyproject.toml`).** `ruff format --check` as real gate. [1 hr]
7. **Add `app/main.py.bak` to .gitignore. Remove from git history with `git filter-repo` (or just `git rm` for current tree if we accept history loss).** [2 hr] — *unblocks gitleaks going from 28 findings to 24.*
8. **Mark `JWT_SECRET = Field(...)` (no default)** so production fails-fast. [15 min]
9. **Add error base classes** in `app/core/errors.py`:
   - `AppError(Exception)`, `AuthError(AppError)`, `RateLimitError(AppError)`, `UpstreamError(AppError)`, `ValidationError(AppError)`, `NotFoundError(AppError)` [1 hr]
10. **Move `tests/run_tests.py` to `tests/manual/run_tests.py`** (out of `pytest` collection). [5 min]
11. **Add CI check: every `router = APIRouter()` is either in `app/mount.py` or in `app/_archived_routers.txt`.** [3 hr]
12. **Add CI check: no `except:` or `except Exception:` in `app/` outside `app/core/`.** [3 hr]
13. **Run `pre-commit run --all-files`** to seed the new hooks. [5 min]
14. **Commit + push**: `chore(rmi-backend): unblock dev loop + CI trustworthiness (Phase 1 of AUDIT-2026-Q3)`

**Done = ✅** all tests pass in fresh checkout, no `|| true`, pre-commit fires on commit, `make dev` works, `make test` works without `PYTHONPATH=.`.

---

### Phase 2 — Delete dead code (1 week)

**Why now:** Less code = fewer god-files. Every dead router is a security liability.

**Tasks:**
1. **Inventory all 148 unmounted routers.** Tag each: `KEEP` (moved to new domain) / `ARCHIVE` (no runners) / `MERGE` (split into another router).
2. **Move the 148 unmounted routers to `app/_archive/legacy_routers_2026_07/`.** Don't delete from git history yet — we'll squash + rewrite history in Phase 6 (private-repo pivot).
3. **Archive the 220 root-level `app/*.py` files** that don't belong in a domain. Specifically the dead-code list:
   ```
   app/ai_pipeline*.py app/ai_pipeline_v2.py app/ai_pipeline_v3.py
   app/all_connectors.py app/ann_index.py app/agent_system.py
   app/analytics_storage.py app/analytics_engine.py
   app/bigquery_pipeline.py app/brand_marketing_generator.py
   app/bulk_marketing_generator.py app/marketing_templates.py
   app/campaign_radar.py app/card_generator.py
   app/chain_feeder.py app/circuit_breaker.py
   app/cross_chain_trace.py app/crypto_embeddings.py
   app/databus_gateway.py app/db_client.py
   app/deployer_history.py app/deployer_reputation.py
   app/discovery_router.py
   app/email_router.py app/embedding_router.py
   app/entity_graph.py app/entity_labeler.py
   app/entity_registry.py
   app/factor.py app/flash_loan_attack_detector.py
   app/gcloud_multi.py app/ghostunnel_manager.py
   app/github_rag_feeder.py
   app/intel_feed_pipeline.py app/intel_pipeline.py
   app/key_loader.py
   app/liquidation_cascade_analyzer.py
   app/mail_dashboard.py app/mev_sandwich_detector.py
   app/meme_intelligence.py app/middleware_setup.py
   app/multimodal_rag.py
   app/n8n_intelligence_workflows.py
   app/news_service.py app/news_intelligence.py
   app/oracle_manipulation_detector.py
   app/prediction_market_service.py app/protection_router.py
   app/pump_dump_manipulation_detector.py
   app/rag_chunking.py app/rag_endpoints.py
   app/rag_historical.py app/rag_metrics_api.py
   app/ragas_eval.py
   app/rug_imminence_predictor.py
   app/scam_classifier.py app/scam_sources.py
   app/smart_contract_honeypot_detector.py
   app/token_supply_analyzer.py
   app/token_deployer.py app/token_scanner.py
   app/unified_scanner.py app/unified_wallet_scanner.py
   app/velocity_risk.py
   app/wallet_factory.py app/wallet_intel_loader.py
   app/wallet_manager_v2.py
   app/wallet_monitor.py app/wallet_persona.py
   app/defi_protocol_auditor.py app/degen_security_scanner.py
   app/degen_scan_endpoint.py
   app/ai_risk_explainer.py app/ai_router.py
   app/portfolio_risk_aggregator.py
   app/url_scam_detector.py app/auth_wallet.py
   app/community_forensics/__init__.py (14-line docstring-only file)
   app/test_bundler_detect.py app/test_clone_scanner.py
   ```
4. **Commit + push**: `chore(rmi-backend): archive 148 dead routers + 70 dead .py files (Phase 2 of AUDIT-2026-Q3)`

**Done = ✅** `app/_archive/legacy_routers_2026_07/` exists with 148 routers. `app/` is now ~30% smaller.

---

### Phase 3 — Split the god-files (2 weeks)

**Why now:** Phase 2 cleared space; now we can split without merge-conflict hell.

**Tasks (in order of risk):**

#### 3.1 — `app/auth.py` (1,223 LOC, 18 importers)

Split into `app/auth/` package:
```
app/auth/__init__.py        # public API
app/auth/jwt.py            # 177-188 section
app/auth/passwords.py       # 103-115 bcrypt
app/auth/totp.py           # 42-100 2FA
app/auth/oauth.py          # 498-600 Google/Telegram/Supabase
app/auth/wallet.py         # migrate from auth_wallet.py — break circular
app/auth/deps.py
app/auth/schemas.py
app/auth/router.py         # was app/routers/auth_extensions.py
```

**Break the circular:** `app/auth_wallet.py` → `app/auth/wallet.py`. Wallet functions import lazily.

#### 3.2 — `app/routers/x402_tools.py` (5,781 LOC, 77 endpoints)

Split into `app/billing/x402/tools/`:
```
app/billing/x402/tools/scanner_tools.py
app/billing/x402/tools/wallet_tools.py
app/billing/x402/tools/token_tools.py
app/billing/x402/tools/market_tools.py
app/billing/x402/tools/label_tools.py
app/billing/x402/tools/news_tools.py
app/billing/x402/tools/report_tools.py
```

Plus move payment-gate helper from `x402_tools.py` into `app/billing/x402/middleware.py`.

#### 3.3 — `app/routers/x402_enforcement.py` (2,720 LOC, 14 importers)

Convert from procedural to a `X402Enforcer` class. Domain: `app/billing/x402/enforcement.py`.

#### 3.4 — `app/databus/providers.py` (2,991 LOC)

Split by chain family:
```
app/databus/providers/solana.py
app/databus/providers/evm.py
app/databus/providers/btc.py
app/databus/providers/tron.py
app/databus/providers/free_tier.py
app/databus/providers/paid_tier.py
app/databus/providers/mcp_servers.py
```

Keep `app/databus/providers/__init__.py` as the registry.

#### 3.5 — `app/rag_service.py` (1,390 LOC, 21 importers)

Mark deprecated. Codemod: `sed -i 's|from app.rag_service|from app.rag.service|g' $(grep -rl 'app.rag_service' app/)`.

#### 3.6 — `app/telegram_bot/bot.py` (2,097 LOC, 58 handlers)

Per-command split:
```
app/telegram_bot/rugmunchbot/commands/{ai,trending,scan,portfolio,simulate,chart,watch,address,...}.py
```

#### 3.7 — `app/wallet_manager_v2.py` (2,321 LOC)

Split per lifecycle stage (create, sign, sweep, vault, monitor).

#### 3.8 — `app/market_intel_router.py` (1,590 LOC)

Extract httpx calls to `app/integrations/coingecko.py`, `app/integrations/dexscreener.py`, `app/integrations/polymarket.py`.

**Each split: 1 commit, single PR, run tests. Mount new router in `app/mount.py`. Verify no regression.**

**Done = ✅** all 8 god-files replaced with single-purpose modules under their domain. CI enforces: no `app/`-root file >500 LOC.

---

### Phase 4 — Consolidate scattered domains (1.5 weeks)

**Why now:** With god-files split, we can move the pieces into proper domain packages.

**Tasks:**

#### 4.1 — `bulletin` domain (`app/domains/bulletin/`)
- Move `app/bulletin_board.py` → `app/domains/bulletin/service.py`
- Merge `app/routers/bulletin.py` + `bulletin_board.py` → `app/domains/bulletin/router.py`
- Add to `app/mount.py`

#### 4.2 — `intelligence` domain
- Move `app/services/deep_intelligence.py` → `app/domains/intelligence/service.py`
- Merge `intelligence_router.py` + `intelligence_panel.py` → `app/domains/intelligence/router.py`
- Add to `app/mount.py`

#### 4.3 — `markets` domain
- Move `market_intel_router.py` + `mbal_market.py` + `prediction_market_router.py` → `app/domains/markets/`
- Httpx calls extracted to `app/integrations/`
- Add to `app/mount.py`

#### 4.4 — `admin` domain
- Consolidate 5 admin routers (3,056 LOC combined) → `app/domains/admin/router.py`
- Move `app/admin_backend.py` engine → `app/domains/admin/service.py`
- Add to `app/mount.py`

#### 4.5 — `billing` domain (consolidate x402 legacy + new)
- Move `app/routers/x402_*.py` into `app/billing/x402/`
- Already has `app/domain/x402/` — merge into `app/billing/`
- Move `app/facilitators/` into `app/billing/facilitators/`

#### 4.6 — `referral` (new domain)
- Extract referral logic from `app/telegram_bot/bot.py` + `caching_shield/membership_plans.py`
- New `app/domains/referrals/`

#### 4.7 — `app/mcp/` deduplication
- Delete 4 unmounted MCP routers (`mcp_server.py`, `mcp_local_router.py`, `mcp_proxy.py`, `x402_mcp_handler.py`)
- Keep `app/api/v1/mcp/router.py` as the only MCP router

#### 4.8 — Per-domain package contract
Every domain follows:
```
domains/<name>/
├── __init__.py       # exports public API
├── models.py         # Pydantic schemas
├── service.py        # business logic
├── router.py         # thin FastAPI router
├── deps.py           # FastAPI dependencies
├── repository.py     # data access
├── analyzer.py       # pure analysis (no I/O)
└── errors.py         # domain-specific exceptions
```

**Each: 1 commit, 1 PR, mount in `app/mount.py`, document in `app/domains/<name>/README.md`.**

**Done = ✅** All 8 scattered domains now `app/domains/<name>/` packages. Mount count is ~30 (was 21). Every router ≤500 LOC.

---

### Phase 5 — Standardize + harden (1 week)

**Why now:** Locks in the new shape before we add new features.

**Tasks:**

1. **Add `alembic/` + initial migration** (currently MISSING). Generate from all 9 scattered `models.py` files consolidated into `app/db/models/`.
2. **CI gate: `alembic upgrade head` on every PR.**
3. **Replace `app/test_rag.py` custom runner with pytest port.**
4. **Add `app/core/lifespan.py`** with async SQLAlchemy `AsyncSession`, Redis pool lifecycle.
5. **Add testcontainers fixtures** (`postgres`, `redis`, `qdrant`) for integration tests. Wire `tests/integration/conftest.py`.
6. **Wire `PrometheusMiddleware` into `app/factory.py`** so `/metrics` actually serves request histograms.
7. **Fix `app/core/logging.py:18`** — `is_prod = os.getenv("ENVIRONMENT") == "prod"` (was: `level == "INFO"`, broken).
8. **Wire `Sentry.capture_exception()` in `app/error_handlers.py:_register_500`.**
9. **Add schemathesis contract tests** — auto-generate from `openapi.json`.
10. **Coverage gate `--cov-fail-under=80` enforced in CI.**
11. **`pre-commit` hook for `detect-private-keys`** verified working.
12. **Document the new shape** in `ARCHITECTURE.md` (replace 8-year-old version).

**Done = ✅** CI is authoritative (no `|| true`). Coverage ≥80%. All migrations in `alembic/versions/`. Logs structured to stdout + Loki.

---

### Phase 6 — Open-source pivot (1 week, in parallel with Phase 5)

**Why now:** Once the codebase is shaped right, we can do the IP split cleanly.

**Tasks:**

1. **Create `RugMunchMedia/rmi-core` (PUBLIC, MIT).** Move:
   ```
   - All of app/core/ (logging, config, redis, http, errors, lifespan, observability)
   - All of app/integrations/ (coingecko, defillama, birdeye, jupiter, alchemy, arkham, etc.)
   - All of app/rag/ (RAG engine)
   - All of app/catalog/ (catalog service)
   - All of app/databus/ (provider ABSTRACTIONS only — concrete impls go RMI IP)
   - app/factory.py + app/mount.py (FastAPI scaffolding)
   - app/middleware/ (CORS, request_id, error_handlers)
   - All Pydantic schemas (app/models/)
   - All of tests/integration/ (portable integration tests)
   ```
2. **Create `RugMunchMedia/rmi-ip` (PRIVATE, BSL 1.1).** Move:
   ```
   - All of app/scanners/ (32 detection modules — the actual moat)
   - All of app/wallet_memory/ (scoring + labels)
   - All of app/chain_registry.py (CEX hot-wallet addresses)
   - app/wallet_manager_v2.py (custody)
   - app/billing/x402/strategies/ (payment strategies)
   - app/services/deep_intelligence.py (intelligence generation)
   - data/* (curated threat data)
   ```
3. **Create `RugMunchMedia/rmi-pro` (PRIVATE, no public license).** Move:
   ```
   - app/admin_backend.py + admin routers (customer integration)
   - app/domains/admin/ (when promoted)
   - app/fleet-infra integration with admin
   - Customer-specific configs
   ```
4. **Public `AGENTS.md`** in `rmi-core` — "how to build the public parts of RMI". Documents the development method without exposing IP.
5. **Public `ARCHITECTURE.md`** — shows the domain layout, AS A REFERENCE for developers building on top.
6. **Update `LICENSING_PRICING_STRATEGY.md`** in the PRIVATE repo with the actual implementation: "RMI Core is MIT, RMI IP is BSL 1.1, RMI Pro is private."

**Done = ✅** Three clean repos with crystal-clear boundaries. Public can be linked from rugmunch.io without IP exposure.

---

## 4. Total Time Budget

| Phase | Effort | Calendar |
|---|---|---|
| Phase 1 — Stop the bleeding | 1 engineer-week | Week 1 |
| Phase 2 — Delete dead code | 1 engineer-week | Week 2 |
| Phase 3 — Split god-files | 2 engineer-weeks | Weeks 3-4 |
| Phase 4 — Consolidate domains | 1.5 engineer-weeks | Week 5 + half of 6 |
| Phase 5 — Standardize + harden | 1 engineer-week | Week 6 (parallel with 6) |
| Phase 6 — Open-source pivot | 1 engineer-week | Week 6 (parallel with 5) |

**Total: 5-6 weeks for a single engineer, or 3 weeks with a 2-engineer pair.**

---

## 5. Status Today (where we stand)

| Area | Today | Target |
|---|---|---|
| Total LOC | 241,111 (with 30% dead code) | ~170,000 after Phase 2 |
| Files >1000 LOC | 21 | 0 |
| Mounted routers | 21 of 164 | all useful routers mounted, no dead ones |
| Tests | 886 collected via PYTHONPATH hack | 2000+ via `pip install -e .` + auto-collection |
| Coverage | 8.17% (60% gate unenforced) | ≥80% enforced |
| CI trustworthy | GitHub fake-pass / Forgejo real | both authoritative |
| Pre-commit | configured, not installed | installed, blocking |
| Alembic | missing | initial migration + auto-upgrade CI |
| Errors | 2,532 except Exception swallows, 0 AppError | typed hierarchy, Sentry-wired |
| Open-source boundary | unclear | 3 repos (MIT / BSL / private) |
| Secrets in prod | `JWT_SECRET` defaults to dev secret | fail-fast on missing |
| Menu on Talos | fixed in prior work | done |

---

## 6. Open Followups (separate from this plan, not blocking)

These are NOT in the Phase 1-6 scope but worth tracking:

- rmi-frontend (React 19 + Vite + Tailwind + Radix) needs a separate audit. Prior data: 850 `any`/`@ts-nocheck`, 101 KB MCPDocsPage.tsx, but it builds, has vitest+biome+linter, Forgejo CI is solid. **Open question: does it need the same Phase 1-6 treatment, or is it OK?**
- Status: needs a fresh subagent run since the first attempt timed out.
- Fleet/menu work from earlier sessions is done.
- Pry audit (`AUDIT-2026-Q3.md`) is at Phase 0 done + 4 followups complete.
- rmi-backend has a separate goal: ship the new shape to production-grade. This audit covers that.

---

## 7. Definition of Done for the whole RMI refactor

When ALL of these are true:

- [ ] Every router mounted in `app/mount.py` OR explicitly archived with justification.
- [ ] No `app/`-root `.py` file >500 LOC.
- [ ] No `except Exception:` outside `app/core/`.
- [ ] `alembic upgrade head` runs cleanly on every PR.
- [ ] Coverage ≥80% enforced in CI.
- [ ] `pre-commit` installed and blocking bad commits.
- [ ] CI has zero `|| true` / `continue-on-error: true`.
- [ ] No file from `RMI_SYSTEM_MAP.md` "LOCAL DATA FILES" table exists.
- [ ] Three clean repos: `rmi-core` (MIT), `rmi-ip` (BSL), `rmi-pro` (private).
- [ ] Every domain has a `README.md` documenting public API + invariants.
- [ ] `app/main.py` exists, `make dev` works without `PYTHONPATH=`.
- [ ] `JWT_SECRET = Field(...)` — boot fails on missing.
- [ ] `gitleaks detect` returns 0 findings on the PUBLIC repo.

---

## 8. Commit & Push Conventions (reaffirmed)

- All commits are conventional (`feat:` / `fix:` / `chore:` / `refactor:` / `docs:` / `test:` / `perf:` / `ci:` / `security:`).
- `make commit` (via commitizen) is the only commit path enforced in pre-commit.
- Branch model: `main` + `feat/<scope>` + `fix/<scope>` + `chore/<scope>`. All merges to `main` via PR with required `lint`, `test`, `coverage` checks green.
- One phase = one PR per repo. PRs are merged with `--squash` to keep `main` history clean.
- Push cadence: every commit → Forgejo (`push.origin`), weekly → external mirrors via `external-push.sh` (now fixed for Codeberg + GitLab).

---

## 9. What I will NOT do

For the record, things I am explicitly NOT touching in this plan because the user said:

- **Pry, WalletPress, x402-gateway, Rugmuncher-Bot, fleet-infra** — separate products with their own audits/plans. Pry's is `AUDIT-2026-Q3.md`.
- **Pre-existing customer data, referrals revenue tracking, $ flow** — out of scope. Reaffirm later.
- **Existing token/billing on Talos** — current `PRY_X402_WALLET` is ephemeral, scheduled to be replaced before mainnet launch.

---

## 10. The exact first 10 commands to start Phase 1 tomorrow

```bash
# On Cinnabox
cd /srv/work/repos/rmi-backend
echo '# app/main.py — entrypoint delegating to factory' > app/main.py
echo 'from app.factory import create_app' >> app/main.py
echo 'app = create_app()' >> app/main.py

# Three tests/__init__.py files
touch tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py

# Update pyproject.toml: tool.setuptools.packages = ["app"]
# (manual edit, lines ~30)

# Symlink main.py.bak out of repo
mv main.py.bak /tmp/main.py.bak.todelete-2026-07
echo 'main.py.bak' >> .gitignore

# Mark JWT_SECRET mandatory
# Edit app/config.py line 39: JWT_SECRET: str = "dev-secret-CHANGE-ME"
# to:   JWT_SECRET: str  # Field(...) — no default
# Or use pydantic Settings: JWT_SECRET: str = Field(...)
# Then update .env.example

# Strip || true from .github/workflows/ci.yml — manual edit, all 6 jobs

# Install pre-commit
pre-commit install

# Commit + push
git add app/main.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py pyproject.toml .gitignore app/config.py .github/workflows/ci.yml .pre-commit-config.yaml
git commit -m "chore(rmi-backend): phase 1 unblock — entrypoint, tests init, JWT fail-fast, CI trustworthy (per AUDIT-2026-Q3)"
make commit  # actually use commitizen via Makefile
git push origin main:main
git push legacy-mirror main:main
```

After these 10 commands, every new dev can `git clone && make install && make test` without `PYTHONPATH=.`.

That's the plan.
