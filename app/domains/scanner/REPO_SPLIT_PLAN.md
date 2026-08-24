# RugMunch Scanner — Repo Split Execution Plan

> Status: Phase 0 (seam) COMPLETE. This doc maps what remains.
> The scanner engine lives in `rmi-backend/app/domains/scanner/` today;
> the goal is an independent `rugmunch-scanner` service.

## Current Architecture (verified 2026-08-24)

```
Telegram bot (rugmunchbot)
  ├─ vendored scanner copies (_vendor/scanner_engines, _vendor/unified_scanner)
  │    refreshed via scripts/refresh-vendors.sh from rmi-backend
  └─→ rmi-backend :8000
        └─ app/domains/scanner/          ← THE SCANNER (52.7k LOC total)
             ├─ engines/                 ← core engine (27k LOC, 60 files)
             ├─ modules/ shared/ unified/ service/
             └─ rag_scam_check.py gmgn_enrich.py whale_indexer.py ...
        └─ app/databus/                  ← data layer (89 modules)
```

## What's Already Done (Phase 0)

- **IndexerPort seam** (`engines/_indexer_port.py`): all 18 engine
  modules bind indexer access through one protocol. Post-split, swap the
  adapter for an HTTP client — zero engine changes.
- **External dependency surface is tiny**: logging (48×), indexer port,
  one RAG exception class. Verified by import audit.
- **Free-first ordering invariant** tested in both repos.
- **RPC freemium tier + hedging + auto-tune** shipped and contracted.

## Remaining Phases

### Phase 1 — Port boundaries for remaining deps (~1 session)
- [ ] `LoggingPort`: replace 48 direct `app.core.logging` imports with a
      stdlib logger or injected logger (mechanical sed + review).
- [ ] Move `RAGUnavailableError` into scanner package (own exceptions
      module); backend imports FROM scanner after split.
- [ ] Audit `scanner/config.py`, `models.py`, `_rules/_signals/_weights`
      for hidden backend imports (`grep -rn "from app\." --include="*.py"`).

### Phase 2 — Service extraction (~2 sessions)
- [ ] New repo `rugmunch-scanner`: FastAPI service exposing
      `/v2/scanner/token/scan`, `/wallet/{addr}`, health endpoints.
- [ ] Move `app/domains/scanner/**` → new repo `src/rugmunch_scanner/`.
- [ ] Config: env-driven (no `app.config` import) — settings shim like
      the bot's `_vendor/settings_shim.py`.
- [ ] DataBus access post-split: gateway HTTP client (Layer 3) or vendored
      provider subset — decide per-provider by call frequency.
- [ ] ClickHouse access stays via IndexerPort → HTTP adapter to indexer.

### Phase 3 — Backend integration flip (~1 session)
- [ ] rmi-backend keeps thin proxy routes calling the scanner service.
- [ ] Bot vendors switch source: refresh-vendors.sh points at the new
      repo (same file layout = zero bot changes).
- [ ] Docker compose entry + systemd unit; deploy behind same gateway.

### Phase 4 — Cleanup
- [ ] Delete moved code from rmi-backend; keep proxy routes only.
- [ ] CI pipeline for the new repo (ruff/mypy/bandit/gitleaks/pytest).
- [ ] Coverage baseline ≥ current (940+ tests across suite).

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Hidden `app.*` imports missed | grep gate in CI: `from app\.` forbidden in scanner src |
| Vector-space drift during move | collections untouched; only code moves |
| Bot vendor drift | refresh script already proven; pin by commit SHA |
| Downtime on flip | blue-green: run both, gateway switches when parity verified |
