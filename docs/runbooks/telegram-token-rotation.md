# Runbook — rotating the RMI Telegram bot tokens

**Status:** required. Two independent bot tokens are in scope. They are **different bots**
and must be rotated separately.

| # | Token | Fingerprint (sha256, first 16) | Bot | Why it is exposed |
|---|---|---|---|---|
| 1 | `$TELEGRAM_BOT_TOKEN` (admin) | `015e6e39930f493a` | `@rmi_admin_bot` | read in plaintext during the 2026-09-26 alert incident; value is in a retained, logged AI session transcript |
| 2 | rugmunch bot token | `ab8286a8b8da8a8d` | `@rugmunchbot` | held in plaintext on disk in several files; **not** in the transcript |

**Only a BotFather rotation closes exposure #1.** Nothing in this repository or on this
host can revoke a Telegram token — no file edit, no `gopass` write, no service restart.

**Owner action:** BotFather conversation with the account owner (`@cryptorugmuncher`).

All host paths are written as placeholders. `HOST` = the alert/infra host. Substitute the
real host at execution time; never commit a real hostname, IP, or absolute host path.

---

## 0. Before you start

Both tokens are already in the secret store, so **no step below has a manual copy/paste
step** — gopass is the single source of truth.

| gopass entry | Fingerprint | Role | Status |
|---|---|---|---|
| `rmi/rmiadminbot/bot_token` | `015e6e39930f493a` | **canonical** — admin bot + alert webhook | authoritative, live |
| `rmi/rugmunchbot/bot_token` | `ab8286a8b8da8a8d` | rugmunch bot (a *different* bot) | authoritative, live |
| `rmi/adminbot/bot_token` | — | **does not exist** | see correction below |

> **Correction (this revision).** Earlier revisions of this runbook listed
> `rmi/adminbot/bot_token` as a "stale copy that returns 401". That entry **is not in the
> password store at all** (`gopass show` → `entry is not in the password store`). The 401
> previously observed came from a test against an empty token string, which the Telegram
> API answers with 404. There is nothing to revoke or update, and no consumer references
> it. Do not create it.

### Consumers of the admin token (`015e6e39930f493a`)

| Consumer | Source | Notes |
|---|---|---|
| `rmiadminbot.service` → `app/domains/telegram/secure_launcher.py` | gopass at startup | map key `adminbot`/`rmiadminbot` |
| `app/domains/telegram/adminbot/config.py` | gopass, env fallback | `GOPASS_PATH` constant |
| `app/domains/telegram/headless_client.py` | gopass | |
| `fleet-infra/scripts/rag-alert.py` | gopass | |
| `alert-webhook.service` | **credential, env fallback** | now `LoadCredential=`; see §6 |

`secure_launcher.py` and `adminbot/config.py` both hardcode
`"rmi/rmiadminbot/bot_token"`; grep for the literal before assuming a consumer is gone.

### Consumers of the rugmunch token (`ab8286a8b8da8a8d`)

- `gopass rmi/rugmunchbot/bot_token` — authoritative.
- `/srv/secrets/telegram.env` → `TELEGRAM_RUGMUNCH_BOT_TOKEN` (still a **literal**, not a
  gopass reference — this is the one file in that set that was never migrated).
- `/srv/rmi-infra/.env` → `TELEGRAM_BOT_TOKEN`, injected by Compose interpolation into
  `rmi-backend` and `rmi-alertmanager` (see §7).

---

## 1. Revoke the leaked token (admin — `@rmi_admin_bot`)

1. Telegram → `@BotFather`.
2. `/mybots` → select the **RMI admin** bot (`@rmi_admin_bot`).
3. **API Token** → **Revoke current token**. Confirm.
4. **API Token** → **Generate a new token**. BotFather replies with the new value.
5. Record the new value **directly into gopass** — never into a file, an `echo`, a terminal
   scrollback, an AI session, or a commit message:

   ```bash
   # prompted for the value; it is never echoed back
   gopass insert -f rmi/rmiadminbot/bot_token
   ```

   Verify the write **without printing the value**:

   ```bash
   gopass show -o rmi/rmiadminbot/bot_token | tr -d '\r\n' | sha256sum | cut -c1-16
   # record as NEW_FP; it MUST differ from 015e6e39930f493a
   ```

Do **not** write the admin token into `rmi/rugmunchbot/bot_token` — that is a different bot
and doing so breaks it.

## 2. Point the alert webhook at the new token

`alert-webhook` reads the token from `$CREDENTIALS_DIRECTORY/telegram-token` first and falls
back to `TELEGRAM_BOT_TOKEN` from `/etc/default/alert-webhook`. Update **both**; the
credential is authoritative, the env file is the fallback for a service that has not been
restarted since the change.

```bash
# a) the systemd credential (authoritative after restart)
umask 077
gopass show -o rmi/rmiadminbot/bot_token | tr -d '\r\n' \
  > /etc/credstore/alert-webhook/telegram-token
chown root:root /etc/credstore/alert-webhook/telegram-token
chmod 600 /etc/credstore/alert-webhook/telegram-token

# b) the env fallback — carry TELEGRAM_CHAT_ID and ALERT_SUPPRESSION_SECONDS over unchanged
install -m 600 -o root -g root /dev/null /etc/default/alert-webhook.new
$EDITOR /etc/default/alert-webhook.new      # set TELEGRAM_BOT_TOKEN=<new value>
install -m 600 -o root -g root /etc/default/alert-webhook.new /etc/default/alert-webhook
rm -f /etc/default/alert-webhook.new
```

**Mode note:** `0600 root:root` is correct and required. systemd (PID 1) reads
`EnvironmentFile=` before dropping privileges, so a root-only file is readable by the unit.
Do **not** relax it to `0644`, and do **not** chown it to `alert-webhook` — that would make
the secret readable by every process running as that user.

## 3. Restart the consumers (owner-run — not run during an incident)

```bash
systemctl daemon-reload
systemctl restart rmiadminbot.service
systemctl restart alert-webhook.service
```

Downtime is sub-second per unit. `alert-webhook` has `Restart=always`, so it returns on its
own if the token is momentarily unreadable — but it will keep retrying the revoked token
until §2 is complete, so do §2 first.

## 4. Verify the new token end to end

```bash
# a) units up and the secret resolved
systemctl is-active rmiadminbot.service alert-webhook.service
journalctl -u alert-webhook -n 20 --no-pager | grep -E 'FATAL|no token|disabled'

# b) direct Telegram API check with the new token (value never echoed)
curl -sS "https://api.telegram.org/bot$(gopass show -o rmi/rmiadminbot/bot_token)/getMe" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("ok:",d["ok"],"@",d["result"]["username"])'
# expect: ok: True @ rmi_admin_bot

# c) REAL delivery — push one synthetic alert through the live pipeline
curl -sS -X POST http://127.0.0.1:9099/alert -H 'Content-Type: application/json' -d '{
  "status":"firing",
  "alerts":[{"status":"firing",
    "labels":{"alertname":"TokenRotationProbe","severity":"info"},
    "annotations":{"summary":"token rotation probe"},
    "startsAt":"2026-01-01T00:00:00Z"}]}'
# expect: HTTP 200

# d) confirm Telegram accepted it (the real end-to-end proof)
journalctl -u alert-webhook --since '-2min' --no-pager | grep -E 'Telegram sent|HTTP Error'
# expect: "Telegram sent for alert: TokenRotationProbe" and NO "HTTP Error"
```

A Telegram HTTP 400 here means the token or chat id is still wrong — stop, re-check §1/§2.
Delete the probe message from the chat afterwards.

## 5. Confirm the old token is dead

Do **not** paste the literal. Read it from a pre-rotation snapshot, or — if none was kept —
skip to the BotFather confirmation: after "Revoke current token" the old value is unusable
by construction.

```bash
OLD=…   # from the pre-rotation snapshot only; never a shell history entry
curl -sS -o /dev/null -w 'old token getMe -> HTTP %{http_code}\n' \
  "https://api.telegram.org/bot${OLD}/getMe"
# expect: HTTP 401
```

If it returns 200, the token was **not** revoked — repeat §1 step 3 before declaring success.

---

## 6. `alert-webhook` credential migration (DONE — needs one restart to activate)

The plaintext `EnvironmentFile` was the last on-disk copy of the admin token. It is now
replaced by a systemd credential:

- **Unit drop-in:** `alert-webhook.service.d/80-loadcredential-telegram.conf`
  → `LoadCredential=telegram-token:/etc/credstore/alert-webhook/telegram-token`
- **Script:** `alert_webhook.py` gained `_read_credential()`; the token resolves as
  credential → environment, so a deployment without the credential still works.

Why not gopass directly: the unit runs as `User=alert-webhook` and the store is under a
root-only home. Verified failure — `setpriv --reuid=alert-webhook … gopass show` →
`mkdir /root/.local: permission denied`. `LoadCredential=` sidesteps this: PID 1 reads the
root-only source and materialises it owned by the unit user, so `User=` does not matter and
the secret never enters the process environment (`/proc/<pid>/environ`, crash dumps).

**To activate — one command, when the alert plane is quiet:**

```bash
systemctl daemon-reload && systemctl restart alert-webhook.service
```

After a successful restart, the env fallback in `/etc/default/alert-webhook` may be reduced
to a non-secret placeholder to complete the removal of the plaintext copy. Do that as a
separate, verified step — not in the same maintenance window.

## 7. The rugmunch token (`ab8286a8b8da8a8d`) — separate rotation

**This is a different bot.** It has its own consumer set and its own BotFather entry. It was
**not** in the session transcript, so there is no emergency; rotate it on the normal
schedule, or immediately if you want the disk copies gone.

Findings that changed the picture:

- The token is **live**: `getMe` on it returns HTTP 200 (`@rugmunchbot`).
- It is **not** held by any running container. Both `rmi-backend` and `rmi-alertmanager`
  currently carry fingerprint `a99f5fa42987` for `TELEGRAM_*TOKEN`, and `a99f5fa42987`
  returns **401** — a third, already-dead token. Compose injects
  `${TELEGRAM_BOT_TOKEN}` by **interpolation** from the directory-local `.env`, not via
  `env_file` (those services list `/srv/secrets/backend.env` and
  `/etc/rmi-backend-getxapi.env`). Changing the `.env` therefore changes the value only on
  the next `docker compose up -d`; it does not affect the running containers.
- That dead `a99f5fa42987` is what alert forwarding is **currently** using. If Telegram
  alerts from Alertmanager are expected to arrive and are not, this is why — it is a
  separate defect from the rotation, and fixing it means pointing the `.env` at a live token.

Ordered steps:

1. BotFather → `/mybots` → **rugmunch** bot (`@rugmunchbot`) → revoke, then generate.
2. `gopass insert -f rmi/rugmunchbot/bot_token`, then verify the fingerprint changed:
   `gopass show -o rmi/rugmunchbot/bot_token | tr -d '\r\n' | sha256sum | cut -c1-16`.
3. Replace the **literal** in `/srv/secrets/telegram.env`
   (`TELEGRAM_RUGMUNCH_BOT_TOKEN`) with the reference
   `TELEGRAM_RUGMUNCH_BOT_TOKEN=${GOPASS:rmi/rugmunchbot/bot_token}` — the other keys in
   that file already use this form; this one was missed.
4. Replace `TELEGRAM_BOT_TOKEN` in the directory-local `.env` used by Compose
   interpolation, or remove it and set it per-service. Do not leave two names for two bots
   in one file.
5. Recreate the affected containers so the change takes effect:
   `docker compose up -d` for the services that interpolate it.
6. Re-run the §4(b) check against `rmi/rugmunchbot/bot_token`; expect `@rugmunchbot`.

## 8. Stale plaintext copies to delete (not rotations)

Every path below holds a **dead** token (401) or a superseded value. Deleting them is safe
and is the correct remediation — do **not** rotate anything because of them.

| Path | Fingerprint | Status |
|---|---|---|
| `/etc/default/alert-webhook.bak-20260919-125035` | `4c6b37687a4d` | dead (401) |
| `/srv/rmi-infra/.env.gatus` | `4c6b37687a4d` | dead (401) |
| `/srv/rmi-infra/.env.bak-tg-20260924-120631` | `ab8286a8b8da` | **live** — the rugmunch token; see §7 |
| `/srv/rmi-infra/.env.secrets` (+ `.bak-mainkey-20260920-115519`) | `1a69ad8a7942`, `89ce88cbe1c2` | both dead (401) |
| `/srv/rmi-infra/compose/netdata/.env` | `a99f5fa42987` | dead (401) — but see §7, it is the *injected* value |
| `/srv/secrets/telegram.env.bak-token-sanitized-20260926-203100` | `015e6e39930f`, `ab8286a8b8da` | **live** — sanitisation was incomplete; contains the exposed admin token |
| `/srv/secrets/telegram.env.bak-20260924-120554`, `.bak-20260924-130929` | `e66a8b3d36bd`, `a99f5fa42987`, `4c6b37687a4d` | dead (401) |
| `/root/.hermes/.env.env.bak-20260704-*` (7 copies) | `1e396cc43382`, `fca942671ccf` | dead (401) |

The `.bak-token-sanitized-20260926-203100` file is the notable one: it is named as if the
token had been removed, and it still contains the exact exposed value
(`015e6e39930f493a`). Sanitisation must be verified by fingerprint, not by filename.

**Verification discipline for all of the above:** never confirm "deleted" by filename.
Re-run the fingerprint sweep and assert the fingerprint is absent.

## 9. Never regress this

Keep `TELEGRAM_BOT_TOKEN` out of git. gitleaks runs pre-commit and a real value committed
will fail the commit — **treat that failure as a bug, never as an obstacle to allowlist
around.** No allowlist entry was added for either token in this remediation.

---

## 10. The two container paths (added after the 2026-09-26 container outage)

A separate incident found both `rmi-backend` and `rmi-alertmanager` running with a
**revoked** token, fingerprint `a99f5fa42987ad66` (HTTP 401 on `getMe`). The alert-webhook
service was never affected. Root cause and the three genuinely distinct paths:

| Path | Delivers via | Bot | Why that bot |
|---|---|---|---|
| Alertmanager → alert-webhook → Telegram | `alert-webhook` (systemd), port 9099 | **admin** (`015e6e39930f493a`) | the owner reads that chat for admin commands; infra alerts belong there |
| Backend alert plane — whale / LP-health / CertStream threat / telegram auth | `rmi-backend` container | **rugmunch** (`ab8286a8b8da8a8d`) | the backend's own error reporting is high-volume operational traffic. Pointing it at the admin bot would bury the owner's interactive chat under routine backend noise |
| Admin bot (interactive commands) | `rmiadminbot.service` | **admin** | it *is* the admin bot |

These are **not** interchangeable. Verified: the admin bot **cannot** post to the alerts
channel at all — `sendMessage` returns `HTTP 400 Bad Request: chat not found`. So the two
paths legitimately need different credentials, and a single shared token could never have
been correct for both.

### The real defect: `environment:` is resolved at container-create time

`docker compose` interpolates `${TELEGRAM_BOT_TOKEN}` when the container is **created**, so
the value is baked into the container and survives every later restart. That is why the
revoked token stayed live in a container for days even though the correct token had since
been written to the compose `.env` file. Rotating a secret in the env file changed nothing
until the container was recreated — and nobody recreated it because nothing looked wrong.

The fix inverts precedence: the credential is a **Docker secret** mounted at
`/run/secrets/`, and the entrypoint **overrides** any inherited environment value
(`_override_secret` in the backend entrypoint). The mounted secret is now authoritative, so
rotation survives `docker compose up -d` with nobody re-pasting a token.

### Rotation — one command, no re-pasting

```bash
# 1. write the new value into the store (prompted; never echoed, never in argv)
gopass insert -f rmi/rugmunchbot/bot_token

# 2. re-seed the mounted secret from the store
umask 077
gopass show -o rmi/rugmunchbot/bot_token | tr -d '\r\n' > /srv/secrets/telegram_bot_token
chown root:root /srv/secrets/telegram_bot_token && chmod 600 /srv/secrets/telegram_bot_token

# 3. verify without printing the value
printf '%s' "$(tr -d '\r\n' < /srv/secrets/telegram_bot_token)" | sha256sum | cut -c1-16
# record as NEW_FP; it MUST differ from the old fingerprint

# 4. recreate the consumer (REQUIRED — see the create-time interpolation above)
cd /srv/rmi-infra
docker compose -f docker-compose.backend.yml up -d rmi-backend
```

Step 4 is a production restart. Do it in a window, never as part of a rotation you are
racing. The same shape applies to the admin token: update
`/etc/credstore/alert-webhook/telegram-token` and restart `alert-webhook`.

> **The seeding command is deliberately not a committed script.** The commit-msg guard
> blocks `gopass ... rmi/` paths in tracked files, and a committed script that resolves
> gopass paths would also put the secret-store layout in git. It lives here, in the
> runbook, for that reason.

### Verifying after rotation

```bash
# credential accepted? (prints the bot's @username, never the token)
cd /srv/work/repos/rmi-backend
TELEGRAM_BOT_TOKEN="$(tr -d '\r\n' < /srv/secrets/telegram_bot_token)" \
  .venv/bin/python scripts/tg-bot-token-probe.py backend
# expect: [tg-probe] backend: OK (HTTP 200 @<bot>)

# real delivery through the backend's own alert path
.venv/bin/python scripts/tg-bot-token-probe.py --help >/dev/null 2>&1 || true
# then POST one synthetic alert and confirm the chat received it — a 200 from
# Telegram, not merely "the variable is set".
```

## 11. Drift check — the probe (`scripts/tg-bot-token-probe.py`)

A revoked token is invisible until something tries to use it, so the credential itself is
probed on a schedule. The probe is the piece that would have caught this outage on day one:
before it, nothing asked Telegram whether the token was still valid, and a dead alert path
looked exactly like a quiet one.

It classifies outcomes, because a transient network fault must not page anyone:

| State | Meaning | Behaviour |
|---|---|---|
| `ok` | HTTP 200 | exit 0, silent |
| `revoked` | HTTP 401 | exit **2**, loud, terminal, no retry — a 401 will not fix itself |
| `unknown` | timeout / DNS / TLS / 429 / 5xx | bounded backoff retry, **never pages** |

That distinction is the whole point: only an explicit 401 is treated as a real fault.
Deploy with:

```bash
install -m 644 systemd/tg-bot-token-probe.service  /etc/systemd/system/
install -m 644 systemd/tg-bot-token-probe.timer   /etc/systemd/system/
# per-service credentials (one env file per instance, each 0600 root:root):
#   TELEGRAM_BOT_TOKEN for the rugmunch instance
#   TELEGRAM_BOT_TOKEN for the admin instance
systemctl daemon-reload
systemctl enable --now tg-bot-token-probe.timer
```

Check it with `systemctl --failed` and
`journalctl -u tg-bot-token-probe -n 50`.
