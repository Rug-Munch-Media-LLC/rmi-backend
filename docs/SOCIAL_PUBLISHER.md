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
| Postiz | `PostizPublisher` | **REAL** | Postiz domain service (`app/domains/postiz`) — 30+ platforms |

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

### Postiz (`app/domains/postiz/` — no env config)
`PostizPublisher` is the "post everywhere" adapter: one call schedules the post
through the Postiz domain service to **every Postiz platform (30+)** — X/Twitter,
Facebook, Instagram, LinkedIn, Bluesky, Mastodon, Reddit, Nostr, Lens, YouTube,
WordPress and more — as a single content item. `enabled` is True whenever the
Postiz service/store is importable (the in-memory `PostizStore` always exists).

- Target ids are resolved from `app.domains.postiz.platforms.PLATFORMS` (all
  30+ ids), **minus `telegram` / `farcaster`** — those have real direct
  adapters and would otherwise be double-posted. Pass `platform_ids=` to the
  constructor to override.
- `post()` calls `schedule_post` from
  `app.domains.postiz.services.scheduling` with `scheduled_at` unset (post
  immediately); the returned Postiz post `id` becomes `post_id`.
- Fail-open: the Postiz service is imported lazily and any import/runtime
  failure returns `{"ok": False, ...}` without touching the other adapters.

## Admin flow (backend site → Postiz router)

The admin site manages Postiz through the existing router at
`/api/v1/postiz/*` (`app/domains/postiz/router.py`):

1. **Connect platforms** — `POST /api/v1/postiz/platforms/connect`
   (`{"platform_id": "twitter", ...}`) per account; check status via
   `GET /api/v1/postiz/platforms`.
2. **Trigger a post** — `POST /api/v1/postiz/schedule` with
   `{"text": "...", "platforms": ["bluesky", "nostr", ...], "media_urls": [...]}`
   and **no `scheduled_at`** → the post is created immediately and appears in
   the calendar/queue with a UUID (`id`). Or send many at once via
   `POST /api/v1/postiz/schedule/bulk`.
3. **Track** — `GET /api/v1/postiz/calendar`, `GET /api/v1/postiz/schedule/queue`,
   `GET /api/v1/postiz/analytics`.

> **Gap:** the Postiz domain has no true "publish now" endpoint — posts created
> without `scheduled_at` are recorded with status `draft` (never transitioned to
> `published`). A thin `POST /api/v1/postiz/publish` that marks a post
> `published` would complete the loop; today the schedule-without-time call is
> sufficient to *trigger* posting via the admin site.

## Where to wire the bot

1. Call `await social_publisher.publish_everywhere(...)` from the alert /
   news send paths (`app/domains/alerts/telegram_transport.py` and
   `app/domains/telegram/rugmunchbot/`) — it is already fail-open, so
   Telegram-only behaviour is unchanged when nothing else is configured.
2. Add new platforms to `ALL_PLATFORMS` / `default_adapters()` when a real
   client lands.
3. Postiz is already wired: `default_adapters()` includes `PostizPublisher`,
   so `publish_everywhere()` posts to Telegram/Farcaster directly **and** the
   30+ Postiz platforms in one call. Use `platforms=["telegram"]` if you want
   to opt a specific call out of Postiz.

## Notes

- Config for `X_*` / `DISCORD_*` is read from the environment in the adapter
  (`os.environ`) because those fields are not yet in `app/config.py` Settings;
  when a real client is wired, add them to `Settings` and read from there
  (config-module policy).
- No secrets are shipped in code; all credentials come from env / the secrets manager.
