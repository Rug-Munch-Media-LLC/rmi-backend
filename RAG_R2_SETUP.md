# RAG R2 Storage — Setup Required

## One-time Cloudflare setup:

1. Create R2 bucket "rmi-rag-storage" in Cloudflare dashboard
2. Generate R2 API token with Object Read & Write permissions
3. Set environment variables:
   - R2_ACCESS_KEY (the Access Key ID from R2 token)
   - R2_SECRET_KEY (the Secret Access Key from R2 token)

## Architecture (already deployed):

Hot  → Redis (in-memory, fast queries, always available)
Warm → Local /data/rag-storage (7-day cache, auto-cleaned)
Cold → Cloudflare R2 (permanent, 10GB free, zero egress)

## Endpoints (all working, all bypass write middleware):

POST /api/v1/rag/permanence/snapshot  → Save all collections to R2
POST /api/v1/rag/permanence/restore   → Pull latest from R2 into Redis
POST /api/v1/rag/permanence/nightly   → Full cycle: snapshot→R2, clean local, rebuild ANN
GET  /api/v1/rag/permanence/stats     → R2 usage + local cache stats

## Cron (active):

cd0f23b963f2 — runs nightly at 3 AM UTC — full RAG persistence cycle
