# app/_archive/legacy_2026_07

**Archived 2026-07-06** as part of AUDIT-2026-Q3.md Phase 2.3 + P2.4.

This directory contains **136 `.py` files** moved out of `app/` and `app/routers/`
because they had **zero reach** into the running application at the time
of the audit. Reach was verified across 5 surfaces via a forward-import
closure from `app/mount.py`, `app/main.py`, `app/factory.py`, and
`app/core/lifespan.py`:

1. NOT in `app/mount.py` mount expressions
2. NOT statically imported by any `.py` in `app/`, `tests/`, `scripts/`
3. NOT dynamically loaded by `app/core/lifespan.py`
4. NOT referenced as a string anywhere in `*.yaml`/`*.yml`/`*.toml`/`*.json`
5. NOT in the lifecycle always-loaded set

## Composition (137 files)

| Bucket | Count | Notes |
|--------|------:|-------|
| `app/*.py` (flat) | 63 | Domain-specific modules never imported |
| `app/routers/*.py` | 73 | Routers never mounted and not imported (1 restored, see below) |
| **Total** | **137** | — |

Total LOC moved: **44,932 lines** (reduces active tree by ~25%).

## Reach-graph re-verification (over 4 surfaces)

```
imports      = 348 app.* modules statically imported
ref_files    = 51 files with string references
closure      = forward BFS from mount.py + main.py + factory.py + lifespan.py
reached      = 194 files reachable from roots
archive set  = audit_dead (186) - reached - forced_live (4 stale items) - test_live (1) = 136
```

## Restored post-archive (test dependency surfaced by smoke test)

- `app/routers/x402_bridge_health.py` — restored 2026-07-07.
  The reach graph showed it as DEAD, but `tests/unit/test_bridge_health.py`
  has 7 direct imports of it. The audit's reach graph considered `tests/`
  only as a transitive reach, not as a source of LIVE. The smoke test
  caught the regression and we restored. Reach graph will be patched to
  include `tests/*.py` as roots in the next audit cycle.

## Forced-LIVE (NOT archived per user directive)

The reach graph would have archived these, but the user listed them as LIVE
and asked us to DEFER with a note:

- `app/ai_pipeline_v3.py` — imported by `app/unified_wallet_scanner.py` and
  `app/news_intelligence.py`. The reach graph shows those importers are
  themselves DEAD, so `ai_pipeline_v3` is also technically DEAD. But kept
  per user instruction (3 importers in the audit reach-graph window).
- `app/splade_bm25.py` — imported by `app/rag_service.py` (LIVE).
- `app/wallet_manager_v2.py` — imported by `app/routers/wallet_manager_v2.py`,
  `app/routers/x402_enforcement.py`, `app/routers/x402_tools.py`,
  `app/sweep_all.py`, `app/sweep_now.py`, `scripts/setup_x402_rotation.py`.
  Has a top-level `NameError` (`Bip44Coins`) but the import works because
  the failing line is in a function body only executed at runtime.
- `app/crypto_embeddings.py` — NOT in the audit ARCHIVE list at all (the
  audit had a stale dead-list entry that was already KEEP). Heavy import
  graph: `rag_service`, `bundle_cluster_rag`, `rag/embedder`, `supabase_vector`,
  `rugmaps_ai`, `scam_sources`, `intel_feed_pipeline`, `rag_agentic`,
  `ragas_eval`, `tests/manual/test_rag.py`, `scripts/ingest_sigmod_fast.py`.

## Wave 3 (newly mounted, NOT archived)

- `app/routers/unified_scanner_router.py` — mounted 2026-07-07 at
  `/api/v2/scanner/{token,wallet}/scan` (2 routes). Reach graph would have
  archived it; the new mount entry in `app/mount.py` makes it LIVE.
- `app/routers/unified_wallet_scanner.py` — DEFERRED (no `router` APIRouter
  attribute; library consumed by `unified_scanner_router`).
- `app/routers/admin_extensions.py` — DORMANT per audit (darkroom-specific,
  25 routes at `/api/v1/admin/*`); would shadow `/api/v1/admin/alerts_webhook`.

## How to restore

These files are kept in git history under the path `app/_archive/legacy_2026_07/`.
To restore any of them:

```bash
cd /srv/work/repos/rmi-backend
git mv app/_archive/legacy_2026_07/<name>.py app/routers/   # or app/
# Then add the import to app/mount.py ROUTER_MODULES
git commit -m "restore <name>.py from legacy archive"
git push origin main
```

`git log --all --oneline -- app/_archive/` shows every move. We did NOT
rewrite history (would break external mirrors at github.com, gitlab.com,
huggingface.co).

## Why archive, not delete

Git retains the full history. `git log --all --oneline -- app/_archive/`
shows every move. The archive path itself is excluded from:

- **pytest discovery** — `pyproject.toml tool.pytest.ini_options testpaths = ["tests"]` does not traverse `app/`.
- **ruff** — `[tool.ruff] extend-exclude = [..., "app/_archive/"]` (added 2026-07-07).
- **mypy** — `[tool.mypy] exclude = [..., "app/_archive/"]` (added 2026-07-07).
- **setuptools** — `[tool.setuptools.packages.find] exclude = ["app._archive*"]` (added 2026-07-07).

The audit doc at `/home/dev/pry/rmi-final-deadcode-2026-07-06.md` lists
every file with the original path and recommendation.
