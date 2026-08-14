# CompreFace — self-hosted face recognition deployment
# ======================================================
>
> **Purpose:** 4th, dedicated face-recognition engine for the OSINT face module
> (`app/osint/face.py`). Image in → detected faces + matched *subjects*
> (similarity scores) against an enrolled-face collection, consumed by
> `app/osint/compreface_client.py`. Unlike the local engines
> (insightface/deepface/face_recognition) it can **identify** faces, not just
> detect them — the one remote engine in the fallback chain.
>
> **Stack:** official Exadel images pinned to `1.2.0` · Docker Compose ·
> core (FaceNet ML) + admin (management API) + api (REST) + Postgres backing
> store · loopback ports 8000 (API) / 8001 (admin).
>
> **Image note:** official Docker Hub images confirmed for `1.2.0`
> (`exadel/compreface-core`, `exadel/compreface-admin`, `exadel/compreface-api`,
> `exadel/compreface-postgres-db`, `exadel/compreface-fe`) — verified against
> the upstream repo `.env` + release `v1.2.0` (2023-08-22).

---

## 1. Version pinned

| Item | Value |
|------|-------|
| Release | `v1.2.0` (latest stable, published 2023-08-22) |
| Images | `exadel/compreface-{core,admin,api,postgres-db}:1.2.0` |
| REST API | `/api/v1/recognition/*`, `/api/v1/detection/*` (docs/Rest-API-description.md) |
| Admin API | `/admin/*` (users, apps, face services / API keys) |
| API key header | `x-api-key` (per face-recognition service) |
| Ports | `127.0.0.1:8000` → api:8080 · `127.0.0.1:8001` → admin:8080 |
| Data | `postgres-data` named volume (subject metadata + saved subject images) |

**Architecture (internal ports, from upstream `ui/nginx/templates/nginx.conf.template`):**
`compreface-api` and `compreface-admin` both listen on **8080** inside their
containers; `compreface-core` listens on **3000** (`ML_PORT`). This compose
publishes api → 8000 and admin → 8001 directly on loopback, skipping the nginx
`compreface-fe` gateway (we call the REST API, not the web UI).

**Host requirement:** CompreFace's ML core requires an x86-64 CPU with
**AVX** (`lscpu | grep avx`). Talos / Cinnabox qualify.

## 2. Configuration

`cp .env.example .env` — defaults work out of the box (loopback, dev profile).

| Compose var | Default | Notes |
|-------------|---------|-------|
| `COMPREFACE_VERSION` | `1.2.0` | Image tag for all four services. |
| `COMPREFACE_API_PORT` | `8000` | Loopback host port → recognition API. Also the default `COMPREFACE_URL` in the Python client. |
| `COMPREFACE_ADMIN_PORT` | `8001` | Loopback host port → admin management API. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `postgres` / `postgres` / `frs` | Backing Postgres (internal to the compose network). |
| `COMPREFACE_API_JAVA_OPTS` | `-Xmx4g` | Heap for the API JVM. |
| `COMPREFACE_ADMIN_JAVA_OPTS` | `-Xmx1g` | Heap for the admin JVM. |
| `COMPREFACE_UWSGI_PROCESSES` / `_THREADS` | `2` / `1` | ML core workers. |
| `COMPREFACE_MAX_FILE_SIZE` / `_REQUEST_SIZE` | `5MB` / `10M` | Upload caps (Java size suffixes). |

The Java services resolve `compreface-postgres-db` and `compreface-core` by
hostname — **do not rename those services**.

## 3. Runbook

### 3.1 First-time setup

```bash
cd infra/compreface
cp .env.example .env          # defaults are fine; no secrets required
docker compose up -d --wait
docker compose ps             # wait until compreface-core is healthy (~1-3 min: model load)
```

Verify the API answers:

```bash
curl -s http://127.0.0.1:8000/api/v1/recognition/subjects/ | head   # -> {"subjects":[]} (or 401 without key)
```

### 3.2 Create a face collection + API key (admin API)

CompreFace scopes API keys per *face service* inside an *application*
("face collection"). The first registered user becomes the global owner and
claims the built-in demo user. Admin endpoints live on the admin service
(`127.0.0.1:8001`); the OAuth client used by the official UI is
`CommonClientId:password` (Basic `Q29tbW9uQ2xpZW50SWQ6cGFzc3dvcmQ=`).

```bash
A=http://127.0.0.1:8001
B='Authorization: Basic Q29tbW9uQ2xpZW50SWQ6cGFzc3dvcmQ='

# 1) register the first user (owner) — only needed once
curl -s -X POST "$A/admin/user/register" -H "$B" -H 'Content-Type: application/json' \
  -d '{"email":"ops@rugmunch.io","password":"<STRONG_PASSWORD>","firstName":"Ops","lastName":"Admin"}'

# 2) login -> access_token
TOKEN=$(curl -s -X POST "$A/admin/oauth/token" -H "$B" \
  -d 'grant_type=password' -d 'username=ops@rugmunch.io' -d 'password=<STRONG_PASSWORD>' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# 3) create an application (the face collection)
APP=$(curl -s -X POST "$A/admin/app" -H "$B" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"rugmunch-osint"}')
APP_GUID=$(echo "$APP" | python3 -c 'import json,sys; print(json.load(sys.stdin)["guid"])')

# 4) create a face-recognition service inside it -> the x-api-key
curl -s -X POST "$A/admin/app/$APP_GUID/model" -H "$B" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"face-recognition","type":"recognition"}'
# -> {"id": "...", "name": "face-recognition", "apiKey": "<API_KEY>", ...}
```

Store the `apiKey` in the environment for the Python client (never in git):

```bash
export COMPREFACE_URL=http://127.0.0.1:8000
export COMPREFACE_API_KEY='<API_KEY>'
```

> **Note:** `type` must be `recognition` for the `/api/v1/recognition/*`
> endpoints (detection-only services only authorize `/api/v1/detection/*`).

### 3.3 Enroll a face

One image per face. The subject is created automatically on first upload;
adding more images of the same person under the same subject improves
accuracy.

```bash
K='x-api-key: <API_KEY>'
curl -s -X POST "http://127.0.0.1:8000/api/v1/recognition/faces?subject=alice" \
  -H "$K" -H 'Content-Type: multipart/form-data' -F file=@/tmp/alice.jpg
# -> {"image_id":"6b135f5b-...","subject":"alice"}
```

List enrolled subjects:

```bash
curl -s http://127.0.0.1:8000/api/v1/recognition/subjects/ -H "$K"   # -> {"subjects":["alice"]}
```

### 3.4 Recognize

```bash
curl -s -X POST "http://127.0.0.1:8000/api/v1/recognition/recognize?prediction_count=1" \
  -H "$K" -H 'Content-Type: multipart/form-data' -F file=@/tmp/unknown.jpg
# -> {"result":[{"box":{"probability":0.99,"x_min":548,...},
#                "subjects":[{"subject":"alice","similarity":0.87}]}], "plugins_versions":{...}}
```

### 3.5 From Python (the OSINT engine path)

```bash
cd /srv/work/repos/rmi-backend
COMPREFACE_URL=http://127.0.0.1:8000 COMPREFACE_API_KEY='<API_KEY>' \
  .venv/bin/python - <<'EOF'
from app.osint.compreface_client import add_face, recognize

add_face("/tmp/alice.jpg", subject="alice")
for hit in recognize("/tmp/unknown.jpg"):
    print(hit["subject"], hit["similarity"], hit["bbox"])
EOF
```

Or through the unified face lookup (engine 4 in the fallback chain — it wins
as soon as it detects a face, and matched faces carry `subject`/`similarity`):

```bash
COMPREFACE_URL=http://127.0.0.1:8000 COMPREFACE_API_KEY='<API_KEY>' \
  .venv/bin/python -c \
  "from app.osint.face import lookup_face; r=lookup_face('/tmp/unknown.jpg'); print([f.to_dict() for f in r.findings])"
```

### 3.6 Similarity threshold

CompreFace scores similarity so correct matches are typically **> 0.5** and
incorrect matches < 0.5 (FaceNet distribution, upstream
`docs/Face-Recognition-Similarity-Threshold.md`); upstream recommends a
threshold **more than 0.5 for high-security systems**. Start at **0.6** and
tune per use case — lower = fewer false rejects, higher = fewer false
accepts. The client returns raw similarity; the threshold decision belongs to
the caller.

### 3.7 Ops

```bash
docker compose logs -f compreface-api compreface-core
docker compose ps                                # core healthy = models loaded
docker compose restart compreface-api            # pick up env changes
docker compose down                              # stop (volume persists)
docker compose down -v                           # DESTROY the face collection
```

### 3.8 Storage / retention

- Everything lives in the `postgres-data` named volume
  (`/var/lib/postgresql/data`): subjects, enrolled-face metadata, and the
  saved subject images (`SAVE_IMAGES_TO_DB=true`).
- **Back up the volume** per the fleet 3-2-1 policy (local → B2 → HF):
  `docker run --rm -v rmi-backend_compreface-postgres-data:/data -v /backups:/backup \
   alpine tar czf /backup/compreface-$(date +%F).tgz -C /data .`
- Enrolled faces are investigation evidence — do not automate deletion.

---

## 4. API — endpoints used by `app/osint/compreface_client.py`

All requests carry `x-api-key: <service_api_key>` (the face-recognition
service key from §3.2). Images are sent as `multipart/form-data` field `file`
(base64 JSON is also supported upstream).

| Endpoint | Method | Params | Returns |
|----------|--------|--------|---------|
| `/api/v1/recognition/faces` | POST | `subject` (required), `det_prob_threshold` | `{"image_id","subject"}` — enroll a face |
| `/api/v1/recognition/recognize` | POST | `prediction_count`, `limit`, `det_prob_threshold` | `{"result":[{"box","subjects":[{"subject","similarity"}]}], ...}` |
| `/api/v1/detection/detect` | POST | `limit`, `det_prob_threshold` | `{"result":[{"box","landmarks",...}]}` |
| `/api/v1/recognition/subjects/` | GET | — | `{"subjects":["alice",...]}` |

Admin endpoints (base `http://127.0.0.1:8001/admin`): `user/register`,
`oauth/token` (password grant, Basic `CommonClientId:password`),
`app` (create), `app/{guid}/model` (create face service → `apiKey`).

## 5. Security notes

- **Loopback bind** on both ports + `no-new-privileges` + resource limits.
  `COMPREFACE_API_KEY` is the only auth on the recognition API — never expose
  port 8000 publicly. Use an SSH tunnel / `tailscale serve` for remote access
  (same pattern as `infra/spiderfoot`).
- **No secrets in git.** `.env` is gitignored; the API key goes in the
  environment (gopass → systemd/env for production, never committed).
- The admin API (port 8001) can create users/keys — keep it loopback-only and
  behind strong credentials. Change the default Postgres password from
  `postgres/postgres` for anything beyond a local dev box (and note the
  admin password `<STRONG_PASSWORD>` is set by you in §3.2).
- **Biometric data handling:** enrolled faces are PII-adjacent. Restrict
  writes to authorized investigation flows, back up the volume, and purge
  subject collections when an investigation closes.
- Container images are the official Exadel builds — pin the tag
  (`1.2.0`), don't float `latest`.
