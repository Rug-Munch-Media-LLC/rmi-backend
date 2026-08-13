# Social Publisher — RugMunchBot multi-platform posting

`app/domains/social/publisher.py` gives RugMunchBot one fail-open interface for
posting short text (optionally with a media URL) to every social platform it
owns — today Telegram only, but with X, Farcaster, Mastodon and Discord baked
in as first-class surfaces (the aixbt-style multi-account play).

## Interface

```python
from app.domains.social import social_publisher

results = await social_publisher.publish_everywhere(
    "GM, RugMunch here 🧵",
    media_url="https://rugmunch.io/post/1",   # optional
    platforms=["telegram", "farcaster"],      # optional subset; omit = all
)
# → {"telegram": {"ok": True, "post_id": None, "error": None},
#    "farcaster": {"ok": True, "post_id": "0xcast-hash", "error": None},
#    "x": {"ok": False, "post_id": None, "error": "not enabled"}}
```

- `SocialPublisher.post(text, *, media_url=None) -> dict` — posts to every
  enabled platform, returns the same `{platform: {...}}` dict.
- `SocialPublisher.publish_everywhere(text, *, media_url=None, platforms=None) -> dict[str, dict]`
  — same, with an optional platform subset.
- Every platform result is `{"ok": bool, "post_id": str | None, "error": str | None}`.
- **Fail-open**: a disabled platform is skipped (adapter never invoked), a
  raising adapter is caught and recorded — nothing blocks the other platforms.

## Adapters — real vs stub

| Platform | Class | Status | Transport |
|----------|-------|--------|-----------|
| Telegram | `TelegramPublisher` | **REAL** | existing headless Telethon client (`app.domains.telegram.headless`), HTML parse mode |
| Farcaster | `FarcasterPublisher` | **REAL** | Neynar cast API (same path as `app/domains/newsletter/publisher.py`) |
| X | `XPublisher` | **STUB** | none yet — interface + config only |
| Mastodon | `MastodonPublisher` | **STUB** | none yet — interface + config only |
| Discord | `DiscordPublisher` | **STUB** | none yet — interface + config only |

A stub has `implemented = False`, flips `enabled` when its credentials are
present, and `post()` returns `{"ok": False, "error": "… is a stub — …"}` until
a real client is wired. This keeps the account-management surface real (the
`enabled` flags, the registry, the result contract) without shipping fake
posts.

## Per-platform configuration (env vars)

### Telegram (`RMI_MAIN_CHANNEL_ID`)
Numeric chat id or `@handle` for the bot's main broadcast channel (same
convention as the alerts transport's `RMI_MAIN_CHANNEL_ID`). Telethon is
imported lazily inside `post()` — if it is unavailable the adapter fails open
with an error result.

### Farcaster (`FARCASTER_API_KEY`, `FARCASTER_SIGNER_UUID`)
Neynar API key + signer UUID — identical to the newsletter publisher. Without
a signer the adapter is disabled (fail-open). A raw signer key also exists:
`FARCASTER_SIGNER_KEY` env (secrets manager). It is
**not** consumed by this adapter (Neynar needs the signer UUID); it is kept for
a future direct-hub signing client.

### X (`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` / `X_BEARER_TOKEN`)
v1.1 OAuth1 credentials or a v2 bearer token. `enabled` is true when any is
present. **To go live**: wire `tweepy` (v1.1) or the X API v2 `POST
/2/tweets` endpoint, map the returned tweet id to `post_id`, keep 280-char
truncation. Suggested home: `app/domains/social/clients/x_client.py` behind
the existing `XPublisher.post`.

### Mastodon (`MASTODON_ACCESS_TOKEN`, `MASTODON_INSTANCE_URL`)
Already typed in `app/config.py` settings. **To go live**: `pip install
Mastodon.py`, create `Mastodon(api_base_url=…, access_token=…)` once, call
`status_post(text, media_ids=[…])`, map `id` → `post_id`. Suggested home:
`app/domains/social/clients/mastodon_client.py`.

### Discord (`DISCORD_WEBHOOK_URL`)
A channel webhook is the minimal integration (no bot token, no gateway). **To
go live**: `POST {DISCORD_WEBHOOK_URL}` with
`{"content": text, "attachments": [{...}]}` for media; map the returned
message id → `post_id`. A bot-token + gateway client is the heavier
alternative. Suggested home: `app/domains/social/clients/discord_client.py`.

## Where to wire the bot

1. Call `await social_publisher.publish_everywhere(...)` from the alert /
   news send paths (`app/domains/alerts/telegram_transport.py` and
   `app/domains/telegram/rugmunchbot/`) — it is already fail-open, so
   Telegram-only behaviour is unchanged when nothing else is configured.
2. Add new platforms to `ALL_PLATFORMS` / `default_adapters()` when a real
   client lands.
3. Consider Postiz (`app/domains/postiz/`) for scheduled, human-managed posts
   — the social publisher is for *bot-authored* posts.

## Notes

- Config for `X_*` / `DISCORD_*` is read from the environment in the adapter
  (`os.environ`) because those fields are not yet in `app/config.py` Settings;
  when a real client is wired, add them to `Settings` and read from there
  (config-module policy).
- No secrets are shipped in code; all credentials come from env / the secrets manager.
