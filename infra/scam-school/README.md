# Scam School — self-hosted crypto-scam education platform

Runs a **Scam School**: a self-hosted LMS where we teach users how to spot
**rug pulls, honeypots, phishing and wallet-drainer scams**. Courses live in
**CourseLit** (open-source creator-economy LMS); each course includes
**interactive H5P lessons** (quizzes, true/false, drag-and-drop, interactive
video) authored offline in **Lumi** and served as standalone HTML by the
bundled static nginx service.

**Scope:** infra config + runbook only. No application code.

| Service | Image (pinned 2026-08-15) | Role |
|---------|---------------------------|------|
| `courselit` | `codelit/courselit-app:0.74.4` | LMS: course/lesson authoring, student accounts, curriculum pages, magic-link login |
| `mongo` | `mongo:7.0` | Backing store (internal only) |
| `h5p` | `nginx:1.30.4-alpine` | Static host for Lumi-exported H5P lessons (standalone HTML) |

Host ports (all loopback-only): **8090** CourseLit UI · **8091** H5P lessons.
`8000` is the rmi-backend API and `8010` is kroki on this fleet.

---

## 1. What it does

CourseLit is the course container: it handles accounts, course structure,
lessons and (optionally) payments/analytics. H5P is the interaction layer:
instead of reading a static page about honeypots, students click through a
drag-and-drop "spot the red flag" exercise or answer a true/false quiz about
a fake airdrop. H5P content is authored **offline in Lumi** (desktop app) and
exported as a **single self-contained HTML file** per lesson — no H5P server
component is required. CourseLit lessons embed or link those files.

```
┌─────────────────────────────┐     ┌──────────────────────────────┐
│  CourseLit (courselit:80)   │     │  H5P lessons (h5p:80)        │
│  http://127.0.0.1:8090      │     │  http://127.0.0.1:8091       │
│  course/lesson structure    │────▶│  Lumi-exported standalone    │
│  student accounts, login    │link │  HTML (iframe / <a href>)    │
└─────────────┬───────────────┘     └──────────────────────────────┘
              │
              ▼
   ┌────────────────────────┐
   │  MongoDB (mongo:27017) │  internal only
   └────────────────────────┘
```

---

## 2. Prerequisites

- Docker Engine with Compose v2 (`docker compose`).
- The platform `rmi_network` bridge network (created by the root
  `docker-compose.yml`). On a fresh host without the platform stack:

  ```bash
  docker network create rmi_network
  ```

---

## 3. Setup & run

```bash
cd infra/scam-school

# 1. Create .env and fill in the REQUIRED values:
#    AUTH_SECRET  ->  openssl rand -base64 48
#    SUPER_ADMIN_EMAIL
#    DB_CONNECTION_STRING password == MONGO_INITDB_ROOT_PASSWORD
cp .env.example .env
$EDITOR .env

# 2. Start (--wait blocks until all healthchecks pass)
docker compose up -d --wait
docker compose ps

# 3. Verify
curl -s -o /dev/null -w "courselit: %{http_code}\n" http://127.0.0.1:8090/   # 200
curl -s -o /dev/null -w "h5p:       %{http_code}\n" http://127.0.0.1:8091/   # 200
```

First boot runs migrations and creates the super admin from
`SUPER_ADMIN_EMAIL`. Because login is **magic-link based**, the one-time link
is either emailed (if `EMAIL_*` is configured) or printed to the container
log when no SMTP is set:

```bash
docker compose logs -f courselit     # look for the magic-link URL
```

Stop / rotate:

```bash
docker compose -f infra/scam-school/docker-compose.yml down      # keeps mongo_data volume
docker compose -f infra/scam-school/docker-compose.yml logs -f
```

> **Public exposure:** by default everything binds to `127.0.0.1`. To make the
> school reachable for real students, put it behind an authenticated
> Cloudflare Tunnel / Tailscale (see `fleet-infra`) rather than opening the
> ports.

---

## 4. Authoring a course (CourseLit)

1. Open `http://127.0.0.1:8090` and complete the magic-link login as the
   super admin (`SUPER_ADMIN_EMAIL`).
2. From the **creator dashboard**, create a **course** (title, tagline,
   featured image, category).
3. Add **sections** and **lessons** to the course. Each lesson is rich text —
   write the teaching material there.
4. Publish the course. Students log in with their own email via magic link
   and enroll.

CourseLit also supports free/paid gating, custom sales pages and a simple
analytics panel (see upstream docs).

---

## 5. Authoring H5P interactive lessons with Lumi

H5P content is **authored offline** with [Lumi](https://lumi.education)
(free, open-source desktop app — MIT). No H5P server is self-hosted.

1. Install Lumi on a workstation: https://lumi.education (or
   `snap install lumi` / AppImage).
2. **Create new H5P** and pick a content type (Multiple Choice,
   True/False, Drag and Drop, Interactive Video, Accordion, ...).
3. Build the activity — e.g. "Which of these tokenomics is a rug-pull red
   flag?" with correct/incorrect feedback.
4. **File → Export → All-in-one HTML**. This writes ONE self-contained HTML
   file (JS/CSS bundled) that runs with no server.
5. Drop the exported file into the host content dir:

   ```bash
   mv ~/Lumi/rug-pull-red-flags.html /srv/work/repos/rmi-backend/infra/scam-school/content/
   # (Docker creates the dir if missing; the compose mounts it read-only)
   ```

6. The lesson is served at `http://127.0.0.1:8091/rug-pull-red-flags.html`.
   Link it from a CourseLit lesson:

   ```markdown
   🔬 Try it yourself: [Spot the red flags](http://127.0.0.1:8091/rug-pull-red-flags.html)
   ```

   or embed inline with an iframe (H5P standalone HTML works inside an
   iframe; add `scrolling="no"` and a fixed height).

> **Versioning/backups:** lesson HTML files in `content/` are gitignored
> (authored content, not infra). Keep the master `.h5p` files in Lumi /
> a backup location and re-export on change; include `content/` in the normal
> backup rotation.

---

## 6. Content roadmap (course outline skeleton)

Suggested "Rug School" curriculum. Each module = one CourseLit course with
lecture lessons + one or more H5P interactive lessons (marked `[H5P]`).

| Module | Lessons |
|--------|---------|
| **M1 — Rug pull fundamentals** | What a rug pull is; classic pattern (LP removed, team dumps); real vs fake project signals; `[H5P]` true/false "is this a rug pull?" |
| **M2 — Tokenomics red flags** | Supply concentration; mint/burn authority (renounced vs not); LP lock duration; buy/sell tax asymmetry; `[H5P]` drag-and-drop "sort safe vs red-flag tokenomics" |
| **M3 — Honeypots & trap contracts** | Buy works / sell blocked; hidden fees; malicious transfer hooks; ownership self-destruct; `[H5P]` multiple-choice honeypot scenarios |
| **M4 — Phishing & social engineering** | Fake airdrops; wallet drainers (approve/token approval phishing); look-alike sites & Telegram impersonators; seed phrase scams; `[H5P]` interactive video "phishing attempt walkthrough" |
| **M5 — Verification workflow** | LP locks; deployer history; contract audit basics; using scanner/risk outputs; on-chain lookup walkthrough; `[H5P]` scenario quiz "would you invest?" |
| **M6 — Final assessment** | Capstone: mixed-question H5P quiz across M1–M5; passing = "Scam School graduate" badge |

Reuse real (already-public) scam examples as case studies; never link
students to live drainer sites.

---

## 7. Operations

- **Health:** `docker compose ps` — `courselit` pings `/` via Node fetch;
  `mongo` runs `db.adminCommand('ping')` via mongosh; `h5p` GETs `/`.
- **Restart policy:** `restart: unless-stopped` on all services.
- **Backup (MongoDB — the course state):**

  ```bash
  docker compose -f infra/scam-school/docker-compose.yml exec mongo \
    mongodump --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" \
    --authenticationDatabase admin --archive > /backups/scam-school-mongo-$(date +%F).archive
  ```

  Restore:

  ```bash
  docker compose -f infra/scam-school/docker-compose.yml exec -T mongo \
    mongorestore --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" \
    --authenticationDatabase admin --archive < scam-school-mongo-YYYY-MM-DD.archive
  ```

- **Resources:** courselit 1 CPU/1 GiB, mongo 1 CPU/1 GiB, h5p 0.25 CPU/64 MiB
  (deploy limits). Mongo data persists in the `mongo_data` volume.

### Security notes

- All ports bind to `127.0.0.1` only — never exposed publicly. MongoDB has no
  host port at all (internal network only).
- Containers drop all Linux capabilities and run `no-new-privileges`.
- Secrets (AUTH_SECRET, DB password) live only in `.env`, which is
  gitignored; `.env.example` is the committed template. Generate
  `AUTH_SECRET` with `openssl rand -base64 48`.
- Image pins audited against Docker Hub at commit time
  (`codelit/courselit-app:0.74.4` == `:latest` digest, `mongo:7.0`,
  `nginx:1.30.4-alpine`; 2026-08-15).
- H5P content is untrusted user-authored media — it is served from an
  isolated static nginx (read-only mount), not executed by CourseLit.

---

## 8. License notes (self-host only)

| Component | License | Notes |
|-----------|---------|-------|
| **CourseLit** (`codelit/courselit-app`) | **AGPL-3.0** | Network copyleft: if we **modify** it and serve it over a network, we must offer the modified source to users. Running the stock upstream image self-hosted is fine. Forge it under the RugMunch AGENTS.md open-core rule: never merge proprietary enhancements into a public fork. |
| **H5P core / content types** | MIT | Permissive; H5P content we author is ours. |
| **Lumi** (authoring tool) | MIT | Desktop authoring only; exports we host are standalone HTML. |

Both CourseLit and H5P are **self-hostable** — no SaaS account required.
CourseLit's optional media backend (MediaLit) is a separate cloud/S3 service;
this scaffold runs without it (media features are disabled or use its
defaults). CourseLit's optional paid extras (Stripe payments) are off by
default.

> ⚠️ **AGPL practical note:** this deployment is an internal/self-hosted
> instance with the stock upstream image, which is fully within the license.
> Do not expose modified CourseLit code publicly without publishing the
> source; do not embed it in proprietary distributions.

---

## 9. References

- CourseLit upstream: https://github.com/codelitdev/courselit (AGPL-3.0)
- CourseLit docker deploy: https://github.com/codelitdev/courselit/tree/main/deployment/docker
- CourseLit docs: https://docs.courselit.com
- Lumi (H5P authoring): https://lumi.education · https://github.com/Lumieducation/Lumi (MIT)
- H5P: https://h5p.org (core MIT) · export-as-HTML: Lumi File → Export → All-in-one HTML
- MongoDB 7.0: https://hub.docker.com/_/mongo
