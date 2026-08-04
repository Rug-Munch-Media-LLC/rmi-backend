# Wiring ChatGuardrails into the deployed chat bridge (`chat-rag-bridge.py`)

**Status: FOLLOW-UP — requires Lead/ops approval before applying to production.**

The live chat surface (`/srv/scripts/chat-rag-bridge.py`, OpenAI-compatible
proxy on `127.0.0.1:8091`) is a deployed artifact **outside version control**,
so it is intentionally NOT modified by this change. The guardrail module is
deployed alongside it at `/srv/scripts/chat_guardrails.py` (installed by
`scripts/install-chat-guardrails.sh`).

## The hook (10 lines)

In `chat_completions()` after the `messages` validation block:

```python
# ── Guardrails: block injection / hacking / internal probes (fail-closed) ──
from chat_guardrails import ChatGuardrails  # deployed at /srv/scripts

_guardrails = ChatGuardrails()  # logs to /var/log/chat-guardrails.log

last_user = next(
    (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
    "",
)
allowed, guard_message = _guardrails.sanitize_input(last_user)
if not allowed:
    return JSONResponse(
        status_code=403,
        content={"error": {"message": guard_message, "type": "guardrail_block"}},
    )
```

## Notes

- The bridge's existing fail-open policy applies only to *context enrichment*;
  security blocks should be **fail-closed** (403). That is a behavior change
  for the live chat and is why this needs sign-off.
- The backend chat API (`app/routers/chat.py`) is already fully wired via
  `AIGuard` in this change — this hook only covers the standalone bridge path.
- Consider binding `_guardrails` once at module scope (after `import`) rather
  than per-request; per-request is fine for a single-tenant local proxy.
- Rate limiting for the bridge is handled upstream by nginx/cloudflared;
  the backend API limiter (`ChatRateLimiter`) applies to `/api/v1/chat`.
