# Operations Runbook — RugMunchBot + RMI Admin Bot

> Production ops runbook for the Telegram bots that ship from this repo.
> Covers system architecture, the deploy path, daily operation, known
> failure modes, and the gopass secrets layout.
>
> **No secret values appear anywhere in this file** — only gopass *paths*
> (names) and env var *names*. Resolve values via gopass on the host.
>
> Related: `docs/DEPLOYMENT.md` (backend container deploy), `docs/ENV_INVENTORY.md`
> (full env surface), `.env.example` / `.env.production.example` (templates),
> `docs/CHANNELS.md` (channel registry), `app/domains/telegram/secure_launcher.py`
> (token bootstrap).

---

## 1. Architecture

### The three runtimes

| Runtime | What it is | Where it runs |
|---|---|---|
| `rugmunchbot` (systemd) | Main Telegram bot @rugmunchbot — user-facing scans, AI chat, alerts, channel content | Host (NOT Docker), systemd unit `rugmunchbot.service` |
| `rmiadminbot` (systemd) | Ops/admin Telegram bot @rmiadminbot — docker/service/backup/git control panel | Host (NOT Docker), systemd unit `rmiadminbot.service` |
| `rmi-backend` (Docker) | FastAPI API (`main.py` → `app.factory.create_app()`), port 8000 | Docker container `rmi-backend` (compose `[INTERNAL]/docker-compose.backend.yml`) |

Both bots boot via `app/domains/telegram/secure_launcher.py`:

```text
python -m app.domains.telegram.secure_launcher <rugmunchbot|rmiadminbot>
```

The launcher fetches the bot token from gopass at process start, exports it
as transient env aliases, imports the bot module, then **unsets the token
before the poll loop** — it never appears in `journalctl`, `systemctl status`,
`ps aux`, or `/proc/*/environ`.

### Systemd unit facts (source of truth in-repo)

| Unit | ExecStart | EnvironmentFile | WorkingDirectory |
|---|---|---|---|
| `rugmunchbot.service` | `.venv/bin/python -m app.domains.telegram.secure_launcher rugmunchbot` | `/srv/secrets/rugmunchbot.env` (non-token config only) | `/srv/work/repos/rmi-backend` |
| `rmiadminbot.service` | `.venv/bin/python -m app.domains.telegram.secure_launcher rmiadminbot` | none (`Environment=ADMIN_CHAT_ID=...`; fill real id at deploy) | `/srv/work/repos/rmi-backend` |

- Both units: `Restart=always`, `RestartSec=10`, hardened (no capabilities,
  restricted namespaces, `ProtectSystem=strict`, `UMask=0077`), run as root
  (gopass/gnupg are root-owned — `[REVIEW]` in the units: non-root `User=`
  requires migrating gopass+gnupg first).
- `rugmunchbot` resource caps: `MemoryMax=768M`, `MemoryHigh=640M`,
  `CPUQuota=100%` (one core). `MemoryHigh` is a soft throttle; `MemoryMax`
  hard-kills.
- The bots run **off the working tree** at `/srv/work/repos/rmi-backend` —
  deploy = sync code, restart unit.

### Data services the system depends on

All infra containers run on the host via Docker Compose. Names shown are
the compose service names.

| Service | Container | Used by | If it's down… |
|---|---|---|---|
| Redis | `rmi-redis` (redis:7-alpine, password-auth) | Backend cache, bot flood control, adminbot Redis listener (`rag:telegram_queue`), RAG queue depth | Bot rate-limits/flood control degrade to SQLite fallback; adminbot alert queue stalls; backend `/ready` fails |
| PostgreSQL | `rmi-postgres` (postgres:16-alpine) | Backend primary DB | Backend is CRITICAL-down: `/health` = unhealthy, `/ready` = not_ready; scans/alerts that persist fail |
| ClickHouse | `rmi-clickhouse` (clickhouse:24-alpine) | Backend analytics, scanner history, cross-chain tracking | `/health` reports clickhouse down; analytics/history endpoints degrade |
| Qdrant | `rmi-qdrant` (qdrant:v1.12) | RAG `threat_intel` vector store, security-pattern KB | RAG retrieval fails; bot chat grounding degrades (fails open) |
| Neo4j | `rmi-neo4j` (neo4j:5-community) | Wallet clustering, entity resolution, rugmaps | Wallet forensics / cluster commands degrade |
| MinIO | `rmi-minio` | Object storage (reports, exports) | Export/file features degrade |
| Infinity (RAG embed/rerank) | `infinity-rag` (port 8001, `INFINITY_URL`) | bge-m3 embeddings + bge-reranker for RAG ingestion | `/health` `rag_pipeline` shows `embedder_unavailable`; RAG ingestion stalls; chat falls back to remote LLM |
| Backend API | `rmi-backend` (port 8000) | Both bots call it for scans, chat RAG, sentinel, pricing | Bot still runs (Telegram polling) but every backend-backed command errors; `/healthz` on the bot stays 200 |

**Dependency rule of thumb:** Redis + Postgres are the critical pair — the
backend refuses `ready` without them. Everything else degrades gracefully and
the bots keep polling.

### Health & metrics endpoints

| Endpoint | Owner | Purpose |
|---|---|---|
| `GET http://127.0.0.1:9191/healthz` | rugmunchbot (`observability.py`, port via `RUGMUNCHBOT_METRICS_PORT`, default 9191) | Bot liveness: status, uptime, scheduler_running, last_error |
| `GET http://127.0.0.1:9191/metrics` | rugmunchbot | Prometheus text format (`rmi_bot_*` counters/gauges); JSON fallback if `prometheus_client` missing |
| `GET http://localhost:8000/live` | backend | Liveness (process alive, no deps) |
| `GET http://localhost:8000/ready` | backend | Readiness (Redis + Postgres reachable) |
| `GET http://localhost:8000/health` | backend | Full health: all stores + config validation + RAG pipeline |
| `GET http://localhost:8000/metrics` | backend | Prometheus scrape target |
| `bash scripts/rmi-health-check.sh` | repo script | Aggregated backend + DegenFeed check (exit 0/1/2) |

> The adminbot has **no** `/healthz` — its liveness signal is the systemd
> unit status + its own `/health` Telegram command (docker dashboard).

---

## 2. Deploy

### Flow: commit → gates → push → restart

```text
edit code
  → make ci / make test (local verification)
  → git add + git commit          # lefthook pre-commit gates run automatically
  → git push (PR, or hotfix)      # lefthook pre-push gates run automatically
  → push mirrors (codeberg, gitlab, radicle)
  → sync + restart on the host
```

### Lefthook gates (canonical config: `standards/lefthook.yml`, symlinked)

- **pre-commit** (parallel): `methodology-leak`, `no-env-files` (blocks `.env`),
  `gitleaks`, `envsitter`, `ruff` (check+format), `vulture`, `radon`,
  `interrogate`, `pip-audit`, `semgrep`, `pyright`, plus `shellcheck` /
  `hadolint` / `squawk` for infra/SQL files.
- **pre-push** (parallel): `methodology-leak`, `gitleaks-full` (full-repo
  `--no-git` scan with `.gitleaks.toml`), `mypy`, `pyright`, `bandit`,
  `deptry`, `pip-audit`.
- Server-side CI (Forgejo) is the real gate — do not rely on `--no-verify`.

### Push remotes

`push-all-repos.sh` pushes every fleet repo to all 4 remotes:
**forgejo** (primary) → **codeberg** → **gitlab** → **radicle**.
A per-repo lighter alternative exists at `scripts/push-all.sh [branch]`.

### Forgejo branch protection — unlock → push → relock

`main` is **protected** on forgejo (PR + ≥1 approval + status checks
required — see `/root/scripts/enable-branch-protection.sh` for the
documented settings and API verification). Direct pushes to main are blocked.

- **Normal path (recommended):** push a feature branch, open a PR, let
  PR-Agent + human review run, merge on forgejo.
- **Hotfix path (direct to main):**
  1. Unlock main via the Forgejo API with the token from
     `gopass show [secrets]/infra/forgejo_token`:
     `DELETE [INTERNAL]/api/v1/repos/RugMunchMedia/rmi-backend/branch_protections/<id>`
  2. `git push forgejo main`
  3. Re-apply the protection rules (web UI: `[INTERNAL]/settings/branches`,
     or re-run the documented settings from `enable-branch-protection.sh`).
- **Automated fallback:** if `push-all-repos.sh` hits a rejected main push on
  forgejo, it retries `main:wip/auto-sync` so work is never lost — but the
  branch-protection unlock/relock is the correct flow for production changes.

### Restart after deploy

Bots run off the working tree — sync code, then restart:

```bash
cd /srv/work/repos/rmi-backend && git pull          # on the host
# if the unit files changed:
sudo install -m 0644 -o root -g root \
  /srv/work/repos/rmi-backend/app/domains/telegram/rugmunchbot.service \
  /etc/systemd/system/rugmunchbot.service
sudo install -m 0644 -o root -g root \
  /srv/work/repos/rmi-backend/app/domains/telegram/rmiadminbot.service \
  /etc/systemd/system/rmiadminbot.service
sudo systemctl daemon-reload
sudo systemctl restart rugmunchbot rmiadminbot
```

Backend container (separate from the bots):

```bash
docker compose -f [INTERNAL]/docker-compose.backend.yml up -d --build rmi-backend
# or: bash [INTERNAL]/deploy-backend.sh   (pull → rebuild → restart → health check)
```

---

## 3. Operate

### Restart a bot

```bash
sudo systemctl restart rugmunchbot
sudo systemctl restart rmiadminbot
systemctl status rugmunchbot --no-pager
```

Both units self-heal (`Restart=always`, `RestartSec=10`).

### Check health

```bash
curl -s http://127.0.0.1:9191/healthz          # bot liveness + last_error
curl -s http://127.0.0.1:9191/metrics | head    # bot Prometheus metrics
curl -s http://localhost:8000/health            # backend full health (all stores)
curl -s http://localhost:8000/ready             # backend readiness (redis+pg)
bash scripts/rmi-health-check.sh --verbose      # aggregated check (exit codes)
```

`/health` semantics: `critical_up` = Redis AND Postgres healthy; any other
store down ⇒ `degraded`; RAG pipeline with no embedder ⇒ `degraded`.

### Tail logs

```bash
journalctl -u rugmunchbot -f          # follow
journalctl -u rugmunchbot -e --no-pager -n 200   # last 200 lines
journalctl -u rmiadminbot -e --no-pager -n 200
journalctl -u rugmunchbot --since "10 min ago"
```

Bot logs are JSON (structlog) in production. The adminbot writes to
`journalctl -u rmiadminbot`.

### Key log greps (logger names to watch)

| Symptom | Command |
|---|---|
| Startup token fetch | `journalctl -u rugmunchbot -e | grep -i 'FATAL\|gopass'` |
| Bot errors/state | `journalctl -u rugmunchbot -e | grep -iE 'rmi_bot|traceback|exception|error'` |
| Backend reachability at boot | `journalctl -u rugmunchbot -e | grep -iE 'Backend (reachable|UNREACHABLE)'` |
| Metrics server up | `journalctl -u rugmunchbot -e | grep -i 'metrics_server_started'` |
| Provider rate limits | `journalctl -u rugmunchbot -e | grep -iE '429|rate.limit|timed.out|timeout'` |
| OOM / memory kill | `journalctl -u rugmunchbot -e | grep -iE 'oom|memory|killed'` |
| Adminbot ops activity | `journalctl -u rmiadminbot -e | grep -iE 'admin_bot|error|exception'` |

Logger names that appear in production logs:

- **rugmunchbot:** `rmi_bot`, `rmi_bot.observability`, `rmi.config`,
  `app.domains.telegram.rugmunchbot.db`, `telegram.access_guard`,
  `alert_hooks` (shared with backend)
- **rmiadminbot:** `admin_bot`, `admin_bot.admin_ops`, `admin_bot.admin_ai`,
  `admin_bot.admin_audit`, `admin_bot.redis_listener`
- **backend:** `app.*` (see `app/core/logging.py`)

---

## 4. Troubleshoot — known failure modes

| Failure | Symptom | One-line fix |
|---|---|---|
| ClickHouse auth | `/health` clickhouse `healthy:false`; 401/403 on CH queries | Verify `CLICKHOUSE_USER`/`CLICKHOUSE_PASSWORD` env match the server (basic auth header), restart backend; CH is non-critical — analytics degrade only |
| Infinity cold-start | `/health` `rag_pipeline.status=degraded`, `embedder_unavailable` | Wait for bge-m3 model load or restart `infinity-rag`; confirm via `GET [INTERNAL]:8001/models` shows `bge-m3`; chat falls back to remote LLM meanwhile |
| Helius 429 | Helius-sourced data gaps, `429` in logs | Rotate/supply `HELIUS_API_KEY` (`.env.production.example` placeholder `__FILL__`), raise backoff; Helius is fail-open |
| OpenRouter rate-limit | `429` from remote LLM; chat returns "AI is temporarily unavailable" | Check `OPENROUTER_API_KEY` + `OPENROUTER_BACKUP_API_KEY` (gopass `[secrets]/backend/*`), credit at `/api/v1/key`; chat degrades to cost-aware fallback, never dies |
| Chat timeout | User gets "⚠️ AI is temporarily unavailable — try again shortly" | Backend `CHAT_TIMEOUT=60.0` exceeded or remote snappy/long timeout (12s/15s); check backend `/health` + RAG pipeline; retry in a few seconds |
| OOM | `systemctl status` shows killed / journalctl OOM lines | `MemoryMax=768M` hard cap (live peak ~507M); if peak grows, raise the cap in the unit, restart |
| Adminbot fails fast at boot | `FATAL: ADMIN_CHAT_ID ...` / `FATAL: TELEGRAM_ALLOWED_USERS ...` | Set real `ADMIN_CHAT_ID` in the unit `Environment=` and owner ids via `TELEGRAM_ALLOWED_USERS` / `hermes/keys/env/telegram_allowed_users` |
| Backend unreachable at bot boot | `Backend UNREACHABLE at ...` warning in logs | Bot still starts and polls; restart the backend container, then `systemctl restart rugmunchbot` to re-ping |
| Bot token fetch failed | `FATAL: Could not fetch token from gopass` | Confirm gopass entry exists (`gopass show [secrets]/rugmunchbot/bot_token`) and `HOME=/root` + gopass/gnupg readable by the unit; systemd will keep retrying |

---

## 5. Secrets (gopass — names only, never values)

Bots NEVER take bot tokens from `Environment=` or `.env`. The token bootstrap
is `app/domains/telegram/secure_launcher.py` (`GOPASS_PATHS` map); config
modules fall back to the same gopass → env chain.

| Secret | gopass path | Consumed by |
|---|---|---|
| Main bot token | `[secrets]/rugmunchbot/bot_token` | rugmunchbot (secure_launcher) |
| Admin bot token | `[secrets]/rmiadminbot/bot_token` | rmiadminbot (secure_launcher) |
| Admin bot token (alias) | `[secrets]/rugmunchbot/admin_bot_token` | adminbot domain config |
| Backend shared X-API-Key | `[secrets]/backend/rmi_auth_token` | bot→backend auth (`RMI_AUTH_TOKEN`; also `/srv/secrets/rmi_auth_token` + `/run/secrets/rmi_auth_token` secret files) |
| Backend admin API key | `[secrets]/backend/ADMIN_API_KEY` | backend admin endpoints |
| Backend JWT secret | `[secrets]/backend/JWT_SECRET` / `[secrets]/backend/jwt-secret` | backend auth |
| Backend Redis password | `[secrets]/backend/REDIS_PASSWORD` | Redis auth (`rmi-redis`) |
| OpenRouter keys | `[secrets]/backend/openrouter_api_key`, `[secrets]/backend/openrouter_backup_api_key` | LLM fallback chains (`OPENROUTER_API_KEY` / `OPENROUTER_BACKUP_API_KEY`) |
| Telegram headless client | `[secrets]/telegram/api_id`, `[secrets]/telegram/api_hash`, `[secrets]/telegram/phone` | TG news sweep (Telethon) |
| Telegram channel ids | `[secrets]/telegram/CHANNEL_*` (6 entries) | channel registry (see `docs/CHANNELS.md`) |
| Owner/operator Telegram ids | `hermes/keys/env/telegram_allowed_users` | rmiadminbot owner gate (fail-closed) |
| Forgejo API token | `[secrets]/infra/forgejo_token` | branch-protection unlock/verify (API) |
| Ghost Admin API key | `[secrets]/local-services/ghost-admin-api-key` | Ghost cross-post |
| Wallet vault password | `[secrets]/infra/wallet_vault_password` | wallet vault |
| Supabase keys | `[secrets]/keys/api/supabase/*` | Supabase |
| X/Twitter credentials | `[secrets]/social/x_*` | X publisher |
| Payment/wallet keys | `[secrets]/payments/*`, `[secrets]/wallets/*` | payments, agent wallets |

Non-token runtime config for the main bot lives in
`/srv/secrets/rugmunchbot.env` (referenced by the unit as `EnvironmentFile`).
Bot tokens are NOT in that file — they stay in gopass.

> Rotation: `gopass generate <path> 32` (or `gopass edit`), then restart the
> unit. Audit: `gopass audit`. Never echo a value to a log; use
> `gopass show <path>` only on the host. CI gate: `gitleaks` + `envsitter`
> block accidental commits.

---

## Operator notes

- **Token security:** tokens live in gopass only; `secure_launcher.py` unsets
  them before the poll loop. Do not add tokens to `.env`, `Environment=`, or
  this repo.
- **Running as root:** both units run as root because gopass/gnupg are
  root-owned (`/root/.local/share/gopass`, `/root/.gnupg`). Migrating to a
  non-root `User=` requires moving gopass+gnupg to the service account and a
  rollout window — flagged `[REVIEW]` in the unit files; do not flip blindly.
- **Memory:** `rugmunchbot.service` is capped at `MemoryMax=768M` (live peak
  ~507M); watch `systemctl status` for OOM.
- **Env inventory drift:** re-scan with
  `grep -rhoE 'os\.getenv\("[A-Z_]+"' app/ --include='*.py' | sort -u` and
  update `docs/ENV_INVENTORY.md` when adding new knobs.
