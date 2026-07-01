# CLEANUP.md — rmi-backend

> Audit + cleanup log. Most recent first.

## 2026-07-02 — bloat + secrets removal

**Branch**: `chore/cleanup-remove-bloat-and-secrets`

### 🚨 SECURITY: removed `.secrets/`
- `.secrets/ghost_session_cookies` was committed to main. Content was a Netscape cookie jar.
- **CRITICAL ACTION REQUIRED: rotate any cookies/secrets that were in this file.**
- The file may have been browsable by anyone with repo read access.
- Added `.secrets/` to `.gitignore` (was missing).

### 🗑️ Bloat removed
- `rmi-frontend/` subtree (~2.7MB) — React app lives in its own repo `RugMunchMedia/rmi-frontend`
- `backend/` subtree (273 files, ~1.7MB) — duplicate scaffold, NOT the source of truth

### 🚮 Broken files removed
- `, r.stdout)...` — shell heredoc accident with literal newlines in filename
- `5s}`, `7s}`, `=2.0.0` — fragment files from another shell accident

### 🛡️ .gitignore hardened
Added patterns for:
- Secrets (`.secrets/`, `*.pem`, `*.key`, `*.crt`, `.env`)
- Data blobs (`*.zip`, `*.parquet`, `*.sqlite`, `*.duckdb`)
- Model weights (`*.bin`, `*.safetensors`, `*.pt`, `*.onnx`)

## Files deliberately NOT removed
These look "extra" but are legit:
- `worker.py`, `main.py`, `Dockerfile.worker` — runtime entry points
- `alembic.ini`, `alembic/` — DB migrations
- `ruff.toml`, `mypy.ini`, `pytest.ini` — lint/type/test config
- `safe_deploy.sh`, `databus_warm_cron.py` — ops scripts
- `justfile`, `uv.lock`, `requirements.txt` — modern Python tooling
- `rmi_sdk.py`, `rmi_langchain.py`, `x402_tool_builder.py` — domain modules
- `smithery.yaml`, `huggingface.yaml` — service configs
- `docker-compose.email.yml` — email service config
- `supabase/*.sql` — Supabase migrations
- `PROPRIETARY_REGISTRATION.txt` — legal doc
- `main.py.bak` — backup, consider deleting in future PR
- `backend.log` — log file, should be ignored (will be cleaned by better .gitignore)

## Pre-cleanup size
134MB → ~70MB

## Lessons learned
- Pre-commit hooks must include gitleaks (it WAS configured but this file slipped through)
- `.secretsallow` is an allowlist — anything not on it must be blocked
- Empty/short-lived subtrees should be deleted immediately, not left as legacy
- A `CLEANUP.md` in each repo prevents this pattern from recurring
