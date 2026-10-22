# Telegram security/scam intake — re-probe and persistence (2026-10-23)

## What changed

| File | Change |
|---|---|
| `app/databus/tg_news/verified_channels.py` | measured source table: 5 live security desks, 3 dormant, 13-handle `IMPOSTOR`, `WEIGHTS`, `SWEPT` |
| `app/databus/tg_news/preview_off.py` | closed-handle list + `SECURITY_CLOSED`, `FALLBACK` carrier map, `GAPS` |
| `app/databus/tg_news/channels.py` | `SeedChannel.weight`; registry merge skips impostors and 302 handles |
| `app/databus/tg_news/scraper.py` / `pipeline.py` | curated weight overrides the rubric (`weight_source=curated`) |
| `app/databus/tg_news/databus.py` | impostor JSONL never served as news |
| `app/databus/tg_news/bridge.py` | post → `news_items` row / `news_v2_8b` document (uuid5 doc ids, `tg:` news ids) |
| `app/databus/tg_news/persist.py` | run: insert, adopt pre-existing rows, embed only rows lacking `rag_embedding_id` |
| `scripts/tg_news_probe.py` | repeatable probe; `--check` fails when a swept desk closes |
| `tests/test_tg_news_sources.py` | invariants: no silent drop, no impostor swept, weights monotone, row shape |
| `alembic/versions/b7c8d9e0f1a2_*.py` | `news_items.source_weight` (nullable) with UP + DOWN |

## Why

The sweep reported 22 security-ish channels, but the probe showed three were
squats holding the name and one had been repurposed:

| handle | page title at probe | subs | verdict |
|---|---|---|---|
| `certikalert` | Certik Alert (Meme) | 48 | impostor (was 0.9 security) |
| `peckshieldalert` | Cool | 46 | impostor, 1 post |
| `hypernativelabs` | For sale | 3 | parked domain |
| `rektnews` | fka-rekt | 6.91K | repurposed into a personal dispute (last post 2024-06) |
| `rekt_news` | Rekt News | 15 | impostor |
| `SEALCommunity` | 🏅SEAL COMMUNITY🏅 | 246 | Indonesian internship spam |
| `PumpFunAlerts` | pump.fun alerts | 129 | token spam, not an alert desk |
| `zachxbt_official` | Investigations by ZachXBT | 2 | active clone (posts as of 2026-10-22) |

`@zachxbt` was recorded as preview-closed while its preview answers **200**
(109K, newest 2026-10-21). And nothing wrote the JSONL into `news_items`, so a
swept desk was invisible to SQL consumers and to Rugbrain retrieval.

## Evidence

Probe (`t.me/s/<handle>`, redirects NOT followed), 94 handles: 76 × `302`,
9 unique open previews.

Swept (`200` + posts):

| handle | subs | newest | role | weight |
|---|---|---|---|---|
| zachxbt | 109K | 2026-10-21 | security | 0.95 |
| misttrack_alert | 2.8K | 2026-10-22 | security | 0.90 |
| scamsnifferalert | 706 | 2026-09-24 | security | 0.90 |
| lookonchainchannel | 18.9K | 2026-10-22 | security | 0.85 |
| arkhamintelligence | 16.5K | 2026-10-20 | security | 0.85 |
| slowmist_official | 244 | 2022-09-27 | security (dormant) | 0.50 |
| slowmistteam | 493 | 2022-04-29 | security (superseded) | 0.40 |
| misttrack_official | 246 | 2025-01-17 | security (dormant) | 0.50 |

Fallback-carried (302): BlockSecTeam → `blocksec.com/sitemap.xml` + X,
SlowMist/PeckShield/CertiK → Medium RSS + X, ScamSniffer → X + `scamsnifferalert`,
lookonchain → X + `lookonchainchannel`, MistTrack → `misttrack_alert`.
Gaps: `pump_sol_alert` (no real source), `gmgnai`, `SEAL_911` (X rows only,
`verified=false`).

Run: `30/30` channels ok, `194` appended. `news_items`: 1992 telegram rows typed
and weighted, all embedded; security/scam `601 → 613` (+60 misttrack_alert,
−48 impostor rows purged). Re-run: `rows_new=0, rows_adopted=0, rag_candidates=0`.

Samples: `t.me/zachxbt/369`, `t.me/misttrack_alert/555080`,
`t.me/lookonchainchannel/10732`, `t.me/arkhamintelligence/210`,
`t.me/ScamSnifferAlert/64`.

Retrieval: `POST /api/v1/search/multi` (news_v2_8b) → 0.85 hit, source
`zachxbt`, title "An institutional entity is suspected to have lost ~$2.84M to
DPRK-linked SquidSquad…".

## Risk / follow-up

- 48 impostor rows were deleted from `news_items`; their Qdrant points in
  `news_v2_8b` remain (no delete endpoint — needs a payload-filter purge).
- `data/telegram_news/{certikalert,peckshieldalert,hypernativelabs,rektnews,rekt_news}.jsonl`
  still exist on disk but are skipped by both the feed adapter and the bridge.
- Dormant desks are still swept (one page, state short-circuits); the bridge
  skips their posts at the 180-day age gate.
- `SEALCommunity` (246 subs, Indonesian spam) is rejected on content, not on
  title — re-check before ever adding it.
