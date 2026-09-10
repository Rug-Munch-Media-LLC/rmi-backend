# SpiderFoot — self-hosted OSINT engine deployment
# =================================================
>
> **Purpose:** general OSINT automation for rug/scam investigations. Target
> (domain, email, username, wallet, IP) in → 100+ modules (DNS/WHOIS/CT logs,
> breach feeds, social/profile enumeration, blockchain, IRC/forum search) →
> unified JSON report consumed by `app/osint/spiderfoot_client.py`.
>
> **Stack:** built from upstream source pinned to `v4.0.0` · Docker Compose ·
> named volume `spiderfoot-data:/var/lib/spiderfoot` · loopback port 5001.
>
> **Image note:** there is **no official `spiderfoot/spiderfoot` image on
> Docker Hub** (verified 2026-08-14 — the repo path returns 404). The upstream
> project ships a `Dockerfile` and expects you to build it. We build from the
> official Dockerfile pinned to the `v4.0.0` git tag (see `Dockerfile`) so the
> image is reproducible and auditable.

---

## 1. Version pinned

| Item | Value |
|------|-------|
| Source tag | `v4.0.0` (last stable release, 2022-04-07) |
| Master version string | still `4.0.0` (`spiderfoot/__version__.py`) |
| REST API | **OSS web UI** (CherryPy), JSON via `Accept: application/json` — *not* the `/rest/v1` HX API |
| Port | `5001` (web UI + JSON API) |
| Data dir | `/var/lib/spiderfoot` (named volume) |

**API caveat:** the `/rest/v1/*` endpoints (`new-scan`, `scan-status`,
`scan-results`, `scan-data`) are part of **SpiderFoot HX**, the paid cloud
product — they do **not** exist in the self-hosted OSS release. The OSS web UI
exposes equivalent JSON endpoints under the same paths the browser uses. See
[§4 API](#4-api-json-endpoints) for the real contract the client uses.

## 2. Configuration

The image reads no mandatory env vars. Everything is configured through the
web UI at `http://127.0.0.1:5001/opts` (module enable/disable, API keys for
third-party feeds) and persisted in the data volume.

| Compose var | Default | Notes |
|-------------|---------|-------|
| `SPIDERFOOT_VERSION` | `v4.0.0` | Git tag of upstream source to build. |
| `SPIDERFOOT_PORT` | `5001` | Host port (loopback only). |

## 3. Runbook

### 3.1 First-time setup

```bash
cd infra/spiderfoot
cp .env.example .env          # defaults are fine; no secrets required

docker compose up -d --build --wait
docker compose ps             # expect healthy
open http://127.0.0.1:5001    # web UI (scan manually if you want)
```

First boot builds the image (several minutes: pip compiles deps). Subsequent
boots are instant.

### 3.2 Running a scan via the API

The API is the same JSON contract `app/osint/spiderfoot_client.py` uses:

```bash
SF=http://127.0.0.1:5001

# start a passive scan (safe default module set) — returns ["SUCCESS", scanId]
curl -s -X POST "$SF/startscan" \
  -H 'Accept: application/json' \
  -d 'scanname=example.com' \
  -d 'scantarget=example.com' \
  -d 'modulelist=' \
  -d 'typelist=' \
  -d 'usecase=passive'

# poll status until FINISHED / ERROR-FAILED / ABORTED
curl -s "$SF/scanstatus?id=<scanId>"
# -> [name, target, created, started, ended, status, riskmatrix]

# dump results as JSON
curl -s "$SF/scaneventresults?id=<scanId>&eventType=ALL"
# -> [[lastseen, data, source_data, module, confidence, visibility, risk, hash, fp, parent_fp, type], ...]

# scan-level errors (module failures etc.)
curl -s "$SF/scanerrors?id=<scanId>"
```

Or from Python:

```bash
cd /srv/work/repos/rmi-backend
SPIDERFOOT_URL=http://127.0.0.1:5001 \
  .venv/bin/python -c \
  "from app.osint.spiderfoot_client import lookup; r=lookup('example.com'); print(r.found, r.errors)"
```

### 3.3 Enabling modules

Modules are enabled/disabled in the web UI (**Settings → Modules**). The
default module set is the **`passive` use case** — 199 modules that only
consult third-party data sources (no active port scans, no direct interaction
with the target beyond what passive sources require).

To include more active checks, pass `usecase=footprint` / `investigate` /
`all` to `/startscan`, or a comma-separated `modulelist=sfp_x,sfp_y`.

### 3.4 Remote access (loopback-bound by design)

The port binds to `127.0.0.1` only — OSINT data is never exposed publicly.

- **SSH tunnel:** `ssh -L 5001:127.0.0.1:5001 ops@[INTERNAL_HOST]` then open `http://127.0.0.1:5001`
- **Tailscale:** `tailscale serve --bg 5001` (or `tailscale serve https / http://127.0.0.1:5001`)
  then open `https://spiderfoot.<tailnet>.ts.net`
- Do **not** publish port 5001 to `0.0.0.0` — the OSS web UI has no
  built-in API token; its only auth is an optional digest passwd file
  (`sf.py --passwdfile=...`) which we leave disabled because we bind loopback.

### 3.5 Storage / retention

- Everything lives in the `spiderfoot-data` named volume (`/var/lib/spiderfoot`):
  `spiderfoot.db` (SQLite — all scans + results), `log/`, `cache/`.
- **Back up the volume** — follow the fleet 3-2-1 policy (local → B2 → HF).
  `docker run --rm -v rmi-backend_spiderfoot-data:/data -v /backups:/backup \
   alpine tar czf /backup/spiderfoot-$(date +%F).tgz -C /data .`
- Retention: keep scan results for the life of an investigation. Use the web
  UI **Scan History → delete** for manual cleanup; never automate deletion of
  investigation data.

### 3.6 Ops

```bash
docker compose logs -f spiderfoot
docker compose exec spiderfoot python3 sf.py -h      # CLI mode (same image)
docker compose restart spiderfoot
docker compose down                                  # stop (volume persists)
```

---

## 4. API — JSON endpoints (OSS v4.0.0, verified from `sfwebui.py`)

The client (`app/osint/spiderfoot_client.py`) depends on exactly these four
endpoints. All return JSON when the request carries `Accept: application/json`.

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/startscan` | POST | `scanname`, `scantarget`, `modulelist`, `typelist`, `usecase` | `["SUCCESS", scanId]` or `["ERROR", msg]` |
| `/scanstatus` | GET | `id` | `[name, target, created, started, ended, status, riskmatrix]` — status at index 5 |
| `/scaneventresults` | GET | `id`, `eventType=ALL` | `[[lastseen, data, source_data, module, confidence, visibility, risk, hash, fp, parent_fp, event_type], …]` |
| `/scanerrors` | GET | `id` | `[error strings]` (module-level failures) |

**Status values (polling terminal states):** `INITIALIZING` → `STARTING` →
`STARTED` → `RUNNING` → `FINISHED` (success), or `ERROR-FAILED` / `ABORTED`
(failure). Terminal = `FINISHED`, `ERROR-FAILED`, `ABORTED`.

**Auth:** the OSS web UI has **no API token**. Our client sends an optional
`Authorization: Bearer` header if `SPIDERFOOT_TOKEN` is set (forward-compat /
proxy use), but the primary security boundary is the loopback bind. Do not
expose this port publicly.

## 5. Security notes

- **Loopback bind** + `no-new-privileges` + resource limits. No secrets in
  git (`.env` and the data volume are gitignored / never committed).
- **Disable aggressive/stalker modules** for investigation work. The
  `passive` use case is safe by default. For tighter control, disable
  modules in **Settings → Modules** — in particular the profile/social
  enumeration and geolocation modules which are the "stalker-ish" ones:
  `sfp_socialprofiles`, `sfp_sociallinks`, `sfp_gravatar`, `sfp_twitter`,
  `sfp_fullcontact`, `sfp_googlemaps`. Leave them enabled only when the
  investigation justifies it.
- **Do not self-scan targets you don't own** and do not point `investigate`
  / `all` use cases (active scanning) at third parties without authorization —
  SpiderFoot is an OSINT/attack-surface tool and the operator is responsible
  for lawful use.
- **Treat results as intelligence, not proof.** Cross-check every finding
  against the primary source before acting on it (false positives are
  flagged per-event in the DB).
- Container runs as non-root user `spiderfoot` (upstream Dockerfile default).
