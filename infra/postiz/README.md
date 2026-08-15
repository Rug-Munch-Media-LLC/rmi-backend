# Postiz — @cryptorugmunch social scheduling deployment
# ======================================================
>
> **Purpose:** schedule and publish @cryptorugmunch posts (our own feed +
> cross-post to the Telegram channel @cryptorugmuncher, plus any other
> connected platform). Self-hosted Buffer/Hootsuite alternative
> (`gitroomhq/postiz-app`): compose content once, publish to 30+ platforms
> from one calendar UI.
>
> **Stack:** `ghcr.io/gitroomhq/postiz-app:latest` · PostgreSQL 17 · Redis 7.2
> · Docker Compose · loopback port 4007.

---

## 1. Image + compose confirmed (2026-08-15)

| Item | Value |
|------|-------|
| Image | `ghcr.io/gitroomhq/postiz-app:latest` (multi-arch; pulls amd64 on this fleet) |
| Source repo | `github.com/gitroomhq/postiz-app` — current version `v1.47.0` |
| Official compose | `github.com/gitroomhq/postiz-docker-compose` (`docker-compose.yaml`) |
| Compose services here | `postiz` + `postiz-postgres` (postgres:17-alpine) + `postiz-redis` (redis:7.2) |
| Container internal port | 5000 (web UI + API) — published **loopback-only** `127.0.0.1:4007:5000` |
| Env vars verified | `MAIN_URL`, `FRONTEND_URL`, `NEXT_PUBLIC_BACKEND_URL`, `BACKEND_INTERNAL_URL`, `JWT_SECRET`, `DATABASE_URL`, `REDIS_URL`, `IS_GENERAL`, `STORAGE_PROVIDER`, `UPLOAD_DIRECTORY`, `NEXT_PUBLIC_UPLOAD_DIRECTORY`, `API_LIMIT` + per-platform OAuth vars |

Verified against the upstream compose: the app container needs Postgres + Redis
(`depends_on: service_healthy` both) and requires `JWT_SECRET` + the URL vars
to boot. `BACKEND_INTERNAL_URL` stays `http://localhost:3000` (the image's
internal NestJS port — not the published 4007).

> **[REVIEW] Temporal scheduler stack** — the official compose since the
> v2.12.0 migration adds a full Temporal + Elasticsearch stack
> (`temporal`, `temporal-postgresql`, `temporal-elasticsearch`,
> `temporal-admin-tools`, `temporal-ui`, `spotlight`) and sets
> `TEMPORAL_ADDRESS=temporal:7233` + `RUN_CRON=true`. This deployment ships
> the minimal documented stack (app + Postgres + Redis) to keep the fleet
> lean. Scheduled publishing works in the UI for immediate/queued posts; if
> time-based scheduled publishing doesn't fire, add the Temporal stack
> (section 7). Decision needed: keep minimal or deploy Temporal.

## 2. Environment variables (`infra/postiz/.env.example`)

| Var | Default | Notes |
|-----|---------|-------|
| `MAIN_URL` / `FRONTEND_URL` | `http://127.0.0.1:4007` | The URL you access Postiz at. **Set to the final public URL before first login** — changing it later breaks saved OAuth callbacks. |
| `NEXT_PUBLIC_BACKEND_URL` | `http://127.0.0.1:4007/api` | Browser-facing API URL (same host, `/api` suffix). |
| `BACKEND_INTERNAL_URL` | `http://localhost:3000` | Container-internal NestJS port — do not change. |
| `JWT_SECRET` | — | **REQUIRED** — `openssl rand -hex 32`. Unique per install. |
| `DATABASE_URL` / `REDIS_URL` | compose-network URLs | Overridden in compose; kept in example for standalone runs. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `postiz-user` / … / `postiz-db` | Consumed by the postgres service. Set a real password. |
| `DISABLE_REGISTRATION` | `false` | Single-operator install: register the first user, then set `true`. |
| `STORAGE_PROVIDER` + `UPLOAD_DIRECTORY` | `local` + `/uploads` | Local disk storage; no Cloudflare R2 needed. |
| `X_API_KEY` / `X_API_SECRET` | empty | X/Twitter OAuth 1.0a app (developer.twitter.com) for @cryptorugmunch. |
| `TELEGRAM_BOT_NAME` / `TELEGRAM_TOKEN` | empty | BotFather bot for the @cryptorugmuncher channel (see §4.2). |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` | empty | Optional. |
| `MASTODON_URL` / `MASTODON_CLIENT_ID` / `MASTODON_CLIENT_SECRET` | empty | Optional. |
| `API_LIMIT` | `30` | Public API hourly rate limit. |
| `TEMPORAL_ADDRESS` / `RUN_CRON` | commented | Only with the Temporal stack (section 7). |

No secrets are committed: `.env` is git-ignored (root `.gitignore`), the
example ships empty/`change-me` placeholders. Real values live in the secrets
manager (gopass / host env vars).

## 3. Runbook

### 3.1 First-time setup

```bash
cd infra/postiz
cp .env.example .env

# generate real secrets (do NOT commit .env):
openssl rand -hex 32        # -> JWT_SECRET
openssl rand -base64 24     # -> POSTGRES_PASSWORD (or your secrets manager)

docker compose config --quiet   # validates; exits 0 if OK
docker compose up -d --wait
docker compose ps
open http://127.0.0.1:4007
```

### 3.2 First login

1. Browse to `http://127.0.0.1:4007` — the first visitor is asked to register.
2. Register the admin account (only allowed while `DISABLE_REGISTRATION=false`).
3. After the account exists: `sed -i 's/DISABLE_REGISTRATION=false/DISABLE_REGISTRATION=true/' .env && docker compose up -d`.

### 3.3 Common ops

```bash
docker compose up -d            # start
docker compose ps               # status
docker compose logs -f postiz   # app logs
docker compose restart postiz   # restart app only
docker compose down             # stop (volumes persist)
docker compose down -v          # WIPE volumes — do NOT run casually
```

## 4. Connecting a channel

### 4.1 X / Twitter (@cryptorugmunch)

1. Create an app at <https://developer.twitter.com> (OAuth 1.0a) for the
   @cryptorugmunch account. Callback URL must match the Postiz base URL, e.g.
   `http://127.0.0.1:4007/api/v1/socials/x/callback`.
2. Put the app key/secret in `.env` (`X_API_KEY`, `X_API_SECRET`), `docker compose up -d`.
3. In the Postiz UI: **Settings → Connected accounts → X** → authorize with
   the @cryptorugmunch account.
4. The account appears in the composer's platform picker.

### 4.2 Telegram (@cryptorugmuncher) — the cross-post target

Postiz natively supports Telegram channels/groups via a **BotFather bot**
(not user OAuth):

1. `@BotFather` → `/newbot` → create a **dedicated** bot, e.g.
   `cryptorugmunch_poster_bot`. Copy the bot token.
   ⚠️ One token can run one connected process at a time — do **not** reuse the
   @rugmunchbot token (RugMunchBot already polls with it).
2. Add the bot as **administrator** to the @cryptorugmuncher channel (send
   messages permission).
3. Set in `.env`:
   ```
   TELEGRAM_BOT_NAME=cryptorugmunch_poster_bot
   TELEGRAM_TOKEN=<bot token from BotFather>
   ```
   then `docker compose up -d`.
4. In the Postiz UI: **Settings → Connected accounts → Telegram** → the
   @cryptorugmuncher channel appears → connect it.

Now one composer item can publish to X (our feed) **and** Telegram
(@cryptorugmuncher) simultaneously. Ref:
<https://docs.postiz.com/providers/telegram>.

### 4.3 Other platforms

Same pattern per platform (LinkedIn, Bluesky, Mastodon, …): create the app
on the provider, put credentials in `.env`, restart, authorize in the UI.
Full list: <https://docs.postiz.com/providers/>.

## 5. Pairing with feed.rugmunch.io + @cryptorugmuncher

This deployment is the **real publishing backend** for the @cryptorugmunch
account. Two complementary paths exist in `rmi-backend`:

- **Existing in-code Postiz domain** (`app/domains/postiz/` + router
  `/api/v1/postiz/*` + `PostizPublisher` in
  `docs/SOCIAL_PUBLISHER.md`) is an **in-memory facade** (`PostizStore` —
  TODO: replace with DB-backed service). It models Postiz's platform list
  (30+, `platforms.py`) and scheduling API but does not talk to a live
  Postiz instance yet.
- **This deployment** is the live self-hosted instance those admin routes and
  the `PostizPublisher` adapter should eventually target: the in-code domain
  gets a real HTTP client pointed at `http://127.0.0.1:4007/api` (or a future
  public URL), replacing the in-memory store.

Direction summary:

| Surface | Owner | Publisher |
|---------|-------|-----------|
| feed.rugmunch.io (our own feed) | Postiz UI / API | X + other connected platforms |
| @cryptorugmuncher (Telegram) | Postiz Telegram bot | dedicated BotFather bot |
| @rugmunchbot (bot alerts) | RugMunchBot (`rmi-backend` Telegram domain) | Telethon — unaffected, separate token |

Postiz covers the *editorial/calendar* workload (compose once → publish
everywhere); RugMunchBot keeps covering the *reactive/alert* workload. They
share no tokens and can run fully independently.

## 6. Backup

The state that matters lives in the compose volumes:

```bash
docker run --rm -v rmi-postiz_postgres-volume:/data -v /backups:/backup alpine \
  tar czf /backup/postiz-postgres-$(date +%F).tar.gz -C /data .
docker run --rm -v rmi-postiz_postiz-uploads:/data -v /backups:/backup alpine \
  tar czf /backup/postiz-uploads-$(date +%F).tar.gz -C /data .
```

- `postgres-volume` — all posts, connections, OAuth tokens, settings.
- `postiz-uploads` — uploaded media.
- Restore = stop stack, replace volume contents, start stack. Verify the
  existing fleet backup jobs (`backup-quick-check.sh`) cover these volumes
  once the stack is live.

## 7. [REVIEW] Temporal scheduler stack (only if scheduled posts don't fire)

Postiz ≥ v2.12.0 routes scheduled publishing through Temporal. The minimal
stack here covers manual/queued publishing; for reliable time-based
scheduling, deploy the upstream stack. Easiest path: overlay the official
compose services onto this file (temporal, temporal-postgresql,
temporal-elasticsearch, temporal-admin-tools, temporal-ui — see
<https://github.com/gitroomhq/postiz-docker-compose/blob/main/docker-compose.yaml>),
add the temporal network, then set in `.env`:

```
TEMPORAL_ADDRESS=temporal:7233
RUN_CRON=true
```

⚠️ This adds ~5 containers incl. Elasticsearch (256m JVM heap) — reassess
fleet resource budget before enabling. Decision to be confirmed by Lead.

## 8. Access

Loopback-only by default (`127.0.0.1:4007`). For remote access: SSH tunnel
(`ssh -L 4007:127.0.0.1:4007 talos`) or a Cloudflare Tunnel for the team —
set `MAIN_URL`/`FRONTEND_URL`/`NEXT_PUBLIC_BACKEND_URL` to the public URL
**before** connecting channels (OAuth callbacks depend on it).

## 9. Docs

- Install/docker: <https://docs.postiz.com/installation/docker>
- Config reference: <https://docs.postiz.com/configuration/reference>
- Telegram provider: <https://docs.postiz.com/providers/telegram>
- Upstream repo: <https://github.com/gitroomhq/postiz-app>
