# RMI Backend — Roadmap

**Next 90 days only.** See `TODO.md` for current session tasks.

## Week 1 — Stabilize
- Fix Redis host resolution for `rmi-backend` health
- Fix Qdrant stale Tailscale IP bind
- Rotate leaked secrets (Telegram tokens, API keys)
- Harden public service binds

## Week 2 — Gates
- Make CI authoritative (remove `|| true` and `continue-on-error`)
- Add `web3-safety` and `hallucination-check` pre-commit hooks
- Get `ruff check` and `ruff format --check` green

## Week 3-4 — Standards Autofix
- Replace all `time.sleep()` with `asyncio.sleep()`
- Convert f-string logs to structured logs
- Remove/replace `print()` statements
- Migrate `sqlite3` to async Postgres
- Add `# noqa: ANY` or replace `Any` annotations

## Week 5-6 — Modularize
- Split `x402_tools.py` into domain modules
- Split `x402_enforcement.py`
- Split `databus/providers.py`
- Begin extracting x402/MCP into standalone product boundaries

## Acceptance
- `/health` green
- CI fails on lint/secrets
- Coverage >60%
- No file >500 lines
