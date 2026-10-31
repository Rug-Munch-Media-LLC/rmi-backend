# Attic — quarantined, not deleted

Each file below had zero inbound references (import or dotted-string) at HEAD, verified with grep across app/, tests/, scripts/ *including* app/mount.py ROUTER_MODULES and register_external_router() strings. Restore with: git mv app/_attic/<file> app/<file>

- rpc_cache.py — superseded by app/caching_shield/rpc_cache.py (the live cache; 5+ consumers). Two caches with independent TTLs was a split-brain risk.
- confidence.py — zero references
- mmr_dedup.py — zero references
- rag_tool.py — zero references
- sosana_crm.py — zero references
- email_router.py — zero references
