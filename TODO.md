# RMI Backend — TODO

## Active
- [ ] Fix `rmi-redis:6379` name resolution so `/health` returns healthy
- [ ] Fix Qdrant stale IP bind (`100.100.18.18:6333` → `127.0.0.1:6333`)
- [ ] Rotate 5 Telegram tokens leaked in `data/cryptoscamdb_urls.yaml` history
- [ ] Rotate Solana Tracker, Arkham WS, Stripe, Helius webhook secrets
- [ ] Remove `continue-on-error: true` and `|| true` from `.github/workflows/ci.yml`

## Next
- [ ] Replace 17 `time.sleep()` calls with `asyncio.sleep()`
- [ ] Convert top 10 f-string log files to structured logging
- [ ] Delete or migrate 5 `sqlite3` usages
- [ ] Split `app/routers/x402_tools.py` (>4,000 lines) into modules

## Blocked
- GitHub external mirror pushes until org account flag is lifted

## Done
- HuggingFace backups consolidated to single bucket
- External mirrors active on GitLab + Codeberg
