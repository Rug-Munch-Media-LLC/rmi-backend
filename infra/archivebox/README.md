# ArchiveBox — evidence-capture deployment
# ========================================
>
> **Purpose:** permanent, timestamped web evidence for rug/scam investigations.
> ArchiveBox self-hosted: URL in → WARC + PDF + full-page PNG + singlefile HTML
> + raw DOM + response headers out, browsable in a web UI with CLI/API access.
> Scam sites disappear fast (takedown, 403, domain kill) — archive before they do.
>
> **Stack:** `archivebox/archivebox:0.7.4` (pinned) · Docker Compose · bind mount `./data:/data` · loopback port 8000.

---

## 1. Image + version pinned

| Item | Value |
|------|-------|
| Image | `archivebox/archivebox:0.7.4` |
| Line | v0.7.x — current stable (v0.7.4, published 2026-05-18) |
| `:latest` / `:stable` | Resolve to the **same digest** as `0.7.4` (`sha256:1a5a3733…` as of 2026-08-14) |
| Newer tags | `0.9.35rcNNN` are **prereleases** — do not use for evidence capture |
| Built-in HEALTHCHECK | **None** (commented out in the image Dockerfile) — one is defined in `docker-compose.yml` |

Pinned to the stable line for reproducible evidence tooling. If you must move
off `0.7.4`, re-verify against https://github.com/ArchiveBox/ArchiveBox/releases
and update this README.

## 2. Configuration env vars (v0.7.4 — verified against `archivebox/config.py`)

| Var | Default | Notes |
|-----|---------|-------|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | unset | **First-run only** — creates admin superuser at init. Change later via `archivebox manage changepassword`. *(Note: `ARCHIVEBOX_USERNAME`/`ARCHIVEBOX_PASSWORD` are NOT image vars — that name comes from a 2021 feature-request issue #734 and third-party integrations.)* |
| `SECRET_KEY` | auto-gen | Django session/CSRF secret. Set a fixed one so sessions survive restarts. |
| `ALLOWED_HOSTS` | `*` | Hosts allowed to serve the UI. Restrict when exposed. |
| `BASE_URL` | — | Canonical URL; used for absolute links/host validation. |
| `PUBLIC_INDEX` | `True` | Set `False` so anonymous users can't browse the snapshot list. |
| `PUBLIC_SNAPSHOTS` | `True` | Set `False` so anonymous users can't view snapshots. *(Removed as a global toggle in v0.8+; per-snapshot `PERMISSIONS` there.)* |
| `PUBLIC_ADD_VIEW` | `False` | Anonymous URL submission — keep `False`. |
| `TIMEOUT` | `60` | Per-extractor per-snapshot timeout (s). Raised to `120` for slow scam pages. |
| `CHECK_SSL_VALIDITY` | `True` | `False` = still capture sites with broken/self-signed TLS (common on scam pages). |
| `MEDIA_MAX_SIZE` | `750m` | Per-file media download cap. |
| `MEDIA_TIMEOUT` | `3600` | Max media download time (s). |
| `SAVE_*` (WARC, WGET, PDF, SCREENSHOT, SINGLEFILE, DOM, READABILITY, TITLE, FAVICON, HEADERS, MEDIA, GIT) | all `True` | Per-extractor toggles. Set `False` to skip an extractor. |
| `PUID` / `PGID` | container user | UID/GID archive files are written as on the host. |

Full reference: <https://github.com/ArchiveBox/ArchiveBox/wiki/Configuration>.
Note: `SERVER_SECURITY_MODE` (v0.8+) does **not** exist in 0.7.4 — lock-down is
via the `PUBLIC_*` vars above.

## 3. Runbook

### 3.1 First-time setup

```bash
cd infra/archivebox
cp .env.example .env

# generate real secrets (do NOT commit .env):
openssl rand -hex 32                        # -> SECRET_KEY
openssl rand -base64 24                     # -> ADMIN_PASSWORD (or your secrets manager)

# edit .env: set ADMIN_PASSWORD, SECRET_KEY, and BASE_URL/ALLOWED_HOSTS
# to the URL investigators will actually use.

docker compose pull
docker compose up -d --wait
docker compose ps            # expect healthy
open http://127.0.0.1:8000   # log in with ADMIN_USERNAME/ADMIN_PASSWORD
```

### 3.2 Archiving evidence

**Web UI** — log in → **Add URLs** → paste one URL per line (optionally a
comment/tag like `case-2026-014`) → **Add**. Wait for the extractors to finish;
each snapshot shows the WARC/PDF/PNG/DOM/headers captures plus a timestamp.

**CLI (recommended for batch / scripts):**

```bash
# single URL
docker compose exec archivebox archivebox add --depth=1 'https://scam-site.example/promo'

# from a file (one URL per line) — great for a full scam funnel
docker compose exec -T archivebox archivebox add < urls.txt

# tag + depth: archive the page and one hop of links (linked pages/contracts)
docker compose exec archivebox archivebox add --tag=case-2026-014 --depth=1 'https://scam-site.example'

# rescan existing snapshots / update missing captures
docker compose exec archivebox archivebox update --status=done
```

**API** — the server exposes a JSON API (Django REST). With a logged-in
session or an API token:

```bash
# list snapshots
curl -s -u admin:ADMIN_PASSWORD http://127.0.0.1:8000/api/v1/core/snapshots/
# add a URL
curl -s -u admin:ADMIN_PASSWORD \
  -X POST http://127.0.0.1:8000/api/v1/core/snapshots/ \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://scam-site.example", "tag": ["case-2026-014"], "depth": 1}'
```

See <https://github.com/ArchiveBox/ArchiveBox/wiki/API> for the full schema
(auth via session cookie, API token, or basic auth).

### 3.3 Remote access (loopback-bound by design)

The port binds to `127.0.0.1` only — evidence is never exposed publicly.

- **SSH tunnel:** `ssh -L 8000:127.0.0.1:8000 ops@talos` then open `http://127.0.0.1:8000`
- **Tailscale:** `tailscale serve --bg 8000` (or `tailscale serve https / http://127.0.0.1:8000`)
  then open `https://archivebox.<tailnet>.ts.net` and set `BASE_URL`/`ALLOWED_HOSTS` to it.
- Do **not** publish port 8000 to `0.0.0.0` — ArchiveBox's own docs warn the
  UI has no transport-level auth beyond login.

### 3.4 Storage / retention

- Everything lives in `./data/` (bind mount): `archive/` (WARC/PDF/PNG/HTML),
  `index.sqlite3`, `ArchiveBox.conf`, `logs/`.
- **Backup `./data/` as the evidence itself** — follow the fleet 3-2-1 policy
  (local → B2 → HF). There is no separate "export" step; the directory IS the
  archive.
- Each snapshot stores the full HTTP response headers (`SAVE_HEADERS`) which
  record capture time, server, and TLS metadata — useful for court-style
  provenance. The sqlite index timestamps every capture.
- Retention: keep snapshots **forever** by default (evidence must not be
  auto-pruned). Disk is cheap; `MEDIA_MAX_SIZE` caps the heavy files.
  To cap growth, run `archivebox prune --before=<date> --keep=...` manually —
  never automate deletion of evidence.
- Verification: `docker compose exec archivebox archivebox oneshot --check`
  sanity-checks the index and archive integrity.

### 3.5 Ops

```bash
docker compose logs -f archivebox          # watch captures run
docker compose exec archivebox archivebox status   # index + disk summary
docker compose exec archivebox archivebox manage changepassword   # rotate admin pw
docker compose restart archivebox
docker compose down                        # stop (data persists in ./data)
```

## 4. Security notes

- Loopback bind + login required (`PUBLIC_*` all `False`). No secrets in git
  (`.env` and `data/` are gitignored).
- Runs with `no-new-privileges` and a `pids_limit`; the image itself drops no
  capabilities (Chrome needs them) — if you harden further, test captures
  still complete.
- The container fetches **untrusted attacker-controlled content** (scam pages).
  Treat `./data` as hostile input: don't serve it over the public internet,
  don't open archived HTML in a privileged browser context, and scan with the
  fleet antivirus before exfiltrating files.
