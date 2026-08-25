# AUTH TRIAGE — Routers Without In-File Authentication

**Date**: 2026-08-25 · **Author**: build lead (direct audit) · **Status**: ACTIVE POLICY DOC

Evidence base: grep of `Depends(require_auth|get_current_user|_auth_dependency|api_key|x402)`
per file in `app/routers/`, live OpenAPI route dump, anonymous curl probes, CSRF-exempt
prefix list in `app/middleware_setup.py`, frontend call-site check (`rmi-frontend/src`).

## Key facts every reviewer must know

1. **CSRF middleware is NOT auth.** It skips any request carrying an
   `X-API-Key` / `X-RMI-Key` header — *presence only, no validation* — and it
   never applies to GETs. A router "protected" only by CSRF is anonymous.
2. **Rate limiting is NOT auth.** It caps abuse volume; it does not gate data.
3. Fleet auth patterns that DO count:
   - Service key: validated `X-API-Key`/`X-RMI-Key` vs `RMI_AUTH_TOKEN`/`RMI_INTERNAL_KEY`
     (see `degenfeed_storage._auth_dependency`, `databus/router/ingest.require_service_key`)
   - User JWT: `get_current_user` / `require_auth`
   - Admin session: `require_admin` (X-Admin-Session)
   - x402 payment middleware

## Classification

### A. PUBLIC-BY-DESIGN — no action (product surface, rate-limited)
| Router | Notes |
|---|---|
| jwks.py | Public keys by definition |
| degenfeed_health.py | Health endpoint |
| rugmaps_router.py | Public product feature (CSRF-exempt prefix documented) |
| honeypot_map.py, death_clock.py, address_profiler.py, chain_comparability.py, contract_analyzer.py, impersonation_detector.py, criminal_clusters.py, laundry_matcher.py, lp_health.py, scanner_data_router.py, scoring_router.py | Scanner/intel product surface; anonymous reads intended; verify rate-limit dep present per endpoint (follow-up sweep) |
| market_intel_router.py (20 ln) | Re-export shim |
| news_helpers.py, osint_schemas.py | Helper modules, not mounted routers |
| chat_db.py | Internal helper routes for chat persistence; session-scoped upstream |

### B. FIXED THIS PASS — fail-closed service-key gates applied
| Router | Change |
|---|---|
| chain_backup.py | `_require_service_key` on `/system/backup-status` + `/backup-config` (infra disclosure). Contract tests already expected X-API-Key. |
| intelligence_router/queries.py | `_require_premium_access` on `/intelligence/premium/smart-money` + `/premium/exchange-flows`. Were labeled "premium feature" yet anonymously readable. Frontend verified: zero call sites. |

### C. ACTION REQUIRED — classified, fix in follow-up PRs
| Router | Risk | Required action |
|---|---|---|
| kol_intel.py (481 ln) | Medium — KOL attribution intel anonymous | Decide tier: public product vs premium. If premium → same service-key/x402 gate as queries.py |
| security_intel.py (702 ln) | Medium — aggregated threat intel | Same decision as kol_intel |
| live_trades.py (408 ln) | Low-Medium — trade firehose cost per query | Rate-limit audit first; gate if upstream API costs are per-call |
| osint.py (279 ln) | Medium — OSINT lookups may touch PII-adjacent sources | Gate writes; review which reads expose paid upstream APIs |
| degenfeed_proxy.py (412 ln) | Low — proxy to public feeds | Verify upstream quota; add per-IP limits if absent |
| intelligence POST generate endpoints (cards_marketing.py) | **Cost abuse** — AI generation behind accidental CSRF-only block | Move under user auth or x402; CSRF-exempt list does NOT cover them so they are currently unusable-by-API-clients AND unauthenticated-for-browsers — broken both ways |
| databus_ingest.py | DEAD — docstring says not mounted; canonical secured router lives at `app/databus/router/ingest.py` (constant-time key check, tested). Delete this file in cleanup pass. |
| rug_recovery.py, mev_advisory.py, mev_sniper.py, intelligence_panel.py GETs | Low — review individually; likely public product |

### D. Verification commands (re-run after changes)
```bash
# Anonymous probe must be 401 for gated endpoints:
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/system/backup-status
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/intelligence/premium/smart-money
# With service key must be 200:
curl -s -H "X-RMI-Key: $RMI_INTERNAL_KEY" http://localhost:8000/api/v1/system/backup-status
```

## Rule going forward
Every new router MUST declare its auth posture explicitly: either a fleet auth
dependency, or a comment `# PUBLIC-BY-DESIGN: <reason>` next to the router
declaration. Routers failing to declare both get flagged by review checklist.
