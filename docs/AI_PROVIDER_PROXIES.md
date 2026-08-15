# AI Provider Residential Proxies

How the free AI-provider accounts (OpenCode Zen, KiloCode, OpenRouter free)
rotate Webshare residential IPs so they don't get rate-limited/banned from the
datacenter IP.

## Where each provider is called from

| Provider | Called from | Egress |
|----------|-------------|--------|
| OpenCode Zen (`opencode-zen`, `-backup`, `-3`) | **opencode CLI config only** (`~/.config/opencode/opencode.json`) | local egress proxies `127.0.0.1:18081-18083` |
| KiloCode (`kilocode`, `-backup`, `-3`) | **opencode CLI config only** | local egress proxies `127.0.0.1:18091-18093` |
| OpenRouter free (`openrouter`, `openrouter-backup`) | opencode CLI config **and** app code (`app/rag_agentic.py`, `app/hallucination_guard.py`, `app/routers/ai_analysis.py`, `app/routers/bot_api.py`) | `https://openrouter.ai/api/v1` direct |
| Local llama.cpp (`qwen-chat`, `qwen-analysis`) | `app/ai_router.py` | docker bridge `172.22.0.1` (private — never proxied) |

Zen/KiloCode are **not** called from rmi-backend app code in their provider
form: the app only reaches Zen through the fleet egress proxy at
`127.0.0.1:18081` (`app/domains/telegram/rugmunchbot/handlers/chat_ai/_llm.py`
→ `_call_zen_llm`, `ZEN_CHAT_URL`).

## app/ai_router.py (in-repo wiring)

`chat_completion` now builds its httpx client via
`app.databus.proxy_rotator.get_proxy_rotator().get_client()` — proxy-first
when the rotator is enabled **and** the destination is public, direct
otherwise. A 403/429/5xx or transport error on the proxied egress marks the
proxy failed (cooldown) and retries once direct (gmgn_provider pattern).
Private/loopback destinations (local llama.cpp) never go through the tier.
Streaming (`stream_chat_completion`) is unchanged (local-only).

## Zen / KiloCode / OpenRouter-free at the opencode process level

These ride the proxies at **deploy/launch time**, not in app code. Launch
opencode with `HTTP_PROXY`/`HTTPS_PROXY` pointed at the Webshare residential
gateway, and exclude the loopback egress proxies so the local Zen/KiloCode
gateways (same box) are not routed through the residential tier:

```bash
export HTTP_PROXY="http://<user>:<pass>@proxy.webshare.io:1080"
export HTTPS_PROXY="http://<user>:<pass>@proxy.webshare.io:1080"
export NO_PROXY="127.0.0.1,localhost,172.22.0.0/16"
opencode
```

(The Webshare credentials come from gopass — never commit them. `NO_PROXY`
keeps the `127.0.0.1:18081-18083 / 18091-18093` egress proxies local.)

Verification — the opencode process should egress via a residential IP:

```bash
curl -s --proxy "$HTTPS_PROXY" https://api.ipify.org
```

For systemd-managed opencode, set the three vars in the unit's
`Environment=` instead of exporting them.

## OpenRouter in app code

The direct OpenRouter call sites listed above are **not** yet wired through
the rotator — only `app/ai_router.py` is. Route them the same way (module
imports `get_proxy_rotator` + the gmgn proxy→direct pattern) when they get
rate-limited.
