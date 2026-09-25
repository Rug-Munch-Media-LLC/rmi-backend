# Current task evidence

## Task
Restore the adminbot command picker, native menu routing, and read-only AI bridge without changing the existing owner gate or tooled `/oc` confirmation flow.

## Files in scope
- `app/domains/telegram/adminbot/bot.py`
- `app/domains/telegram/adminbot/admin_ai.py`
- `app/domains/telegram/adminbot/owner_persona.py`
- `app/domains/telegram/adminbot/tg_menu.py`
- `app/domains/telegram/adminbot/tg_menu_handlers.py`
- `app/domains/telegram/adminbot/chat/register.py`
- `app/domains/telegram/adminbot/chat/on_text_handler.py`
- `tests/domains/test_adminbot_owner_persona.py`
- `tests/unit/adminbot_chat/test_menu_ai_contracts.py`

## Commands and results
- `pyright` on the changed adminbot modules: **0 errors**.
- `ruff check` on the changed modules and tests: **passed**.
- `pytest tests/domains/test_adminbot_owner_persona.py tests/unit/domain/test_adminbot_callbacks_routing.py tests/unit/adminbot_chat/test_menu_ai_contracts.py -q`: **124 passed**.
- Direct OpenCode smoke test through `admin_ai.run_opencode`: returned `OK`.
- `rmiadminbot.service`: active after restart; journal reports `Telegram command menu published: 27 commands`.
- Telegram `getMyCommands`: 27 curated commands returned.
- Queue cleanup: 9,426 exploit-alert messages removed after backup; `rag:telegram_queue=0`.
- `npx agent-guardrails check --base-ref HEAD~1 --review --commands-run ...`: **95/100, passed** with one non-blocking state-management complexity warning.
- Task contract was revised to include the two chat type-annotation files and the persona test.

## Security and performance notes
- Existing owner-only decorators and DM gate remain in force.
- Tooled `/oc` still requires explicit confirmation; `/ai` uses the read-only `plan` agent.
- The menu runs commands asynchronously with a bounded timeout and does not grant the generic dispatcher ownership of native menu callbacks.
- No new dependency or public API was added.

## Continuity and rollback
- Reused the existing command handler, callback dispatcher, `admin_ai`, and Telegram `Application` patterns.
- Rollback is the single commit `127e9ce0` (local source); the live service can be rolled back by restarting the prior image/source revision.
- The exploit queue backup is outside the repository and contains only the pre-cleanup alert payloads.

## Residual risk
- The Dockerized backend image was not rebuilt in this task because BuildKit/Buildx is unavailable; the host-based adminbot service is the deployed surface for these changes.
- The separate Qdrant schema mismatch for `newsletters` and `podcast_episodes` remains outside this task.

## GetXAPI follow-up
- Replaced the active ApiPass X reads with cached GetXAPI profile/tweet reads.
- ApiPass is disabled with `RMI_APIPASS_MAX_CALLS_PER_HOUR=0`.
- GetXAPI profile smoke test returned HTTP 200 with valid profile data.
- Single-flight X-ingest lock verified: one Gunicorn worker starts the loop, other workers skip.
- GetXAPI/X-ingest focused suite: **15 passed**.
- `npx agent-guardrails check --base-ref 4a0cb7cc --review ...`: **100/100, safe-to-deploy**.
- GetXAPI migration and single-flight lock are pushed to Forgejo and Codeberg.
