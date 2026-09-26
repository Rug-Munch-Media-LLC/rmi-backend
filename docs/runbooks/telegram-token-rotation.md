# Runbook — rotating the RMI admin Telegram bot token

**Status:** required. The current token (`$TELEGRAM_BOT_TOKEN`, fingerprint `sha256:015e6e39930f493a…`)
was read in plaintext during the 2026-09-26 alert incident and is therefore present in a
logged, retained AI session transcript. Plaintext copies on disk have been removed, but
**only a BotFather rotation closes the transcript exposure.** Nothing in this repository
or on this host can revoke a Telegram token.

**Owner action:** BotFather conversation with the account owner (`@cryptorugmuncher`).

All host paths below are written as `[INTERNAL_HOST]`-relative placeholders; substitute the
real host at execution time. `HOST` below = the alert/infra host.

---

## 0. Before you start

The token is already in the secret store, so **step 3 has no manual copy/paste step** —
gopass is the single source of truth:

| gopass entry | Role | Status |
|---|---|---|
| `rmi/rmiadminbot/bot_token` | **canonical** — admin bot + alert webhook | authoritative |
| `rmi/adminbot/bot_token` | stale copy | returns 401, do not use |
| `rmi/rugmunchbot/bot_token` | different bot (rugmuncher) | unrelated, do not touch |

Consumers on `HOST` currently resolving `rmi/rmiadminbot/bot_token`:

- `rmiadminbot.service` → `app/domains/telegram/secure_launcher.py` (gopass-only, at startup)
- `app/domains/telegram/adminbot/config.py` (gopass, env fallback)
- `app/domains/telegram/headless_client.py` (gopass)
- `fleet-infra/scripts/rag-alert.py` (gopass)
- `alert-webhook.service` → **env only** (`/etc/default/alert-webhook`) — see §5

---

## 1. Revoke the leaked token

1. Open Telegram → `@BotFather`.
2. `/mybots` → select the RMI admin bot.
3. **API Token** → **Revoke current token**. Confirm.
4. **API Token** → **Generate a new token**. BotFather replies with the new value.
5. Record the new value **directly into gopass** — do not paste it into a file, a terminal
   echo, an AI session, or a commit message:

   ```bash
   # you will be prompted for the value; it is never echoed back
   gopass insert -f rmi/rmiadminbot/bot_token
   ```

   Verify the write without printing the value:

   ```bash
   gopass show -o rmi/rmiadminbot/bot_token | tr -d '\r\n' | sha256sum | cut -c1-16
   # record this hash as NEW_FP; it must differ from OLD_FP (015e6e39930f493a)
   ```

Do **not** update `rmi/adminbot/bot_token` or `rmi/rugmunchbot/bot_token` — they are
different bots' tokens; writing the admin token there would break those bots.

---

## 2. Point the alert webhook at the new token

The alert webhook still reads its token from the environment file rather than gopass, so
it must be updated by hand at rotation time:

```bash
install -m 600 -o root -g root /dev/null /etc/default/alert-webhook.new
$EDITOR /etc/default/alert-webhook.new      # set TELEGRAM_BOT_TOKEN=<new value>
install -m 600 -o root -g root /etc/default/alert-webhook.new /etc/default/alert-webhook
```

`TELEGRAM_CHAT_ID` and `ALERT_SUPPRESSION_SECONDS` must be carried over unchanged.

**Mode note:** `0600 root:root` is correct and required. `alert-webhook.service` runs as
`User=alert-webhook`, but systemd (PID 1) reads `EnvironmentFile=` **before** dropping
privileges, so a root-only file is readable by the unit. Do **not** relax this file to
`0644`, and do not chown it to `alert-webhook` — that would make the secret readable by
any process running as that user.

---

## 3. Restart the consumers (owner-run — not run during an incident)

Both restarts are required; neither is automatic.

```bash
systemctl daemon-reload
systemctl restart rmiadminbot.service
systemctl restart alert-webhook.service
```

Expected downtime: sub-second for each unit. `alert-webhook` has `Restart=always`, so it
comes back on its own if the token is momentarily unreadable — but it will keep retrying
against the revoked token until §2 is complete, so do §2 first.

---

## 4. Verify the new token end to end

```bash
# a) unit is up and the secret resolved
systemctl is-active rmiadminbot.service alert-webhook.service
journalctl -u alert-webhook -n 20 --no-pager | grep -E 'FATAL|no token|disabled'

# b) direct Telegram API check with the new token (value never echoed)
curl -sS "https://api.telegram.org/bot$(gopass show -o rmi/rmiadminbot/bot_token)/getMe" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("ok:",d["ok"],"@",d["result"]["username"])'
# expect: ok: True @ <bot username>

# c) REAL webhook delivery — push one synthetic alert through the live pipeline
curl -sS -X POST http://127.0.0.1:9099/alert -H 'Content-Type: application/json' -d '{
  "status":"firing",
  "alerts":[{"status":"firing",
    "labels":{"alertname":"TokenRotationProbe","severity":"info"},
    "annotations":{"summary":"token rotation probe"},
    "startsAt":"2026-01-01T00:00:00Z"}]}'
# expect: HTTP 200

# d) confirm Telegram accepted it (this is the real end-to-end proof)
journalctl -u alert-webhook --since '-2min' --no-pager | grep -E 'Telegram sent|HTTP Error'
# expect: "Telegram sent for alert: TokenRotationProbe"  and NO "HTTP Error"
# a Telegram HTTP 400 here means the token/chat id is still wrong — stop and re-check §1/§2
```

Delete the probe alert from the chat afterwards, and remove
`TokenRotationProbe` from the Alertmanager silence list if you added one.

---

## 5. Confirm the old token is dead

```bash
OLD=8633…   # do NOT paste the literal; read it from the pre-rotation snapshot if kept
curl -sS -o /dev/null -w 'old token getMe -> HTTP %{http_code}\n' \
  "https://api.telegram.org/bot${OLD}/getMe"
# expect: HTTP 401

curl -sS -o /dev/null -w 'old token sendMessage -> HTTP %{http_code}\n' \
  "https://api.telegram.org/bot${OLD}/sendMessage"
# expect: HTTP 401
```

If either returns 200, the token was **not** revoked — repeat §1 step 3 before considering
the rotation complete.

---

## 6. Close the remaining gap (recommended, separate change)

`alert-webhook` is the only consumer still sourcing its token from a plaintext
`EnvironmentFile`. Migrating it to gopass is **not** a drop-in change: the unit runs as
`User=alert-webhook`, and the store under `[HOME]` is root/`ops`-owned, so
`alert-webhook` **cannot** reach gopass (verified: `mkdir /root/.local: permission
denied`). A gopass call in the script would silently disable Telegram forwarding.

Two workable options, in order of preference:

1. **Credentials dir + `LoadCredential=`.** systemd materialises the credential as
   `$CREDENTIALS_DIRECTORY/<name>` owned by the unit user, so `User=` does not matter.
   Ship a `LoadCredential=telegram-token:` unit pointing at a root-only
   `/etc/credstore/alert-webhook/telegram-token`, and have the script read
   `$CREDENTIALS_DIRECTORY` before falling back to the environment.
2. **Run the unit as root** and resolve the token from gopass at startup. Smallest code
   change, but it widens the unit's privilege — only with a compensating hardening pass.

Either way, keep `TELEGRAM_BOT_TOKEN` out of git; gitleaks runs pre-commit and any real
value committed will fail the commit — treat that failure as a bug, never as an obstacle
to allowlist around.
