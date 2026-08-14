# OSINT Framework — Architecture, Modules, and Extension Guide

This document describes the open-source-intelligence (OSINT) framework in
`app/osint/`, the REST API that exposes it, the Telegram command surface, and
the self-hosted infrastructure it relies on. It is written against the code as
it exists in this repository — every module, endpoint, and file path referenced
here was verified against the tree.

---

## 1. Overview

The OSINT section answers one question: **given a username, email, domain,
wallet address, phone number, or face photo, what can we learn about the person
or entity behind it from open sources?**

The design goal is to attribute scam/rug operators. Every lookup returns the
same unified shape — an `OsintReport` — regardless of which module produced it,
so callers (the REST API, the Telegram bot, or other services) can render any
result with one formatter.

### 1.1 Layered architecture

```
                 ┌─────────────────────────────────────────────────────┐
                 │                 Callers                              │
                 │   REST API (app/routers/osint.py)                    │
                 │   Telegram bot (handlers/osint.py, deepscan.py)      │
                 └───────────────┬─────────────────────────────────────┘
                                 │
                 ┌───────────────▼─────────────────────────────────────┐
                 │   Engine (app/osint/engine.py)                       │
                 │   run_osint / run_aggregate / detect_target_type     │
                 │   lazy registry: (module_path, lookup_fn) tuples     │
                 └───────────────┬─────────────────────────────────────┘
                                 │ dispatches per target type
     ┌───────────┬───────────┬───┴────────┬───────────┬──────────┬──────┴───┐
     │           │           │            │           │          │          │
┌────▼────┐ ┌────▼────┐ ┌────▼─────┐ ┌────▼────┐ ┌────▼────┐ ┌───▼────┐ ┌───▼──────┐
│ username│ │ email   │ │ domain   │ │ wallet  │ │ phone   │ │ face   │ │ spider-  │
│ .py     │ │ .py     │ │ .py      │ │ .py     │ │ .py     │ │ .py    │ │ foot_    │
│ Sherlock│ │ HIBP    │ │ WHOIS    │ │ entity  │ │ lib-    │ │ insight│ │ client   │
│ +maigret│ │ emailrep│ │ DNS      │ │ registry│ │ phone-  │ │ face   │ │ .py      │
│         │ │ dispos- │ │ crt.sh   │ │ Chain-  │ │ numbers │ │ deep-  │ │ (self-   │
│         │ │ able    │ │ SSL      │ │ abuse   │ │ +ignor- │ │ face   │ │ hosted   │
│         │ │         │ │ Wayback  │ │ OFAC    │ │ ant     │ │ face-  │ │ Spider-  │
│         │ │         │ │ theHar-  │ │         │ │         │ │ recog- │ │ Foot)    │
│         │ │         │ │ vester   │ │         │ │         │ │ nition │ │          │
│         │ │         │ │ Amass    │ │         │ │         │ │ compre-│ │          │
│         │ │         │ │          │ │         │ │         │ │ face   │ │          │
│         │ │         │ │          │ │         │ │         │ │ opencv │ │          │
└─────────┘ └─────────┘ └──────────┘ └─────────┘ └─────────┘ └────────┘ └──────────┘
```

Each module implements the same contract, the engine orchestrates them, and
the API/bot are thin renderers over the engine output.

### 1.2 Files in the section

| Path | Role |
|------|------|
| `app/osint/__init__.py` | The `OsintReport` / `OsintFinding` contract and exceptions |
| `app/osint/engine.py` | Orchestrator: target-type detection, module registry, `run_osint`, `run_aggregate` |
| `app/osint/username.py` | Social/profile enumeration (Sherlock CLI, maigret fallback) |
| `app/osint/email.py` | HIBP breaches/pastes, emailrep.io reputation, disposable-domain check |
| `app/osint/domain.py` | WHOIS, DNS, crt.sh subdomains, SSL cert, Wayback, fingerprint, theHarvester, Amass |
| `app/osint/wallet.py` | Chain detection, entity labels, exchange attribution, Chainabuse, OFAC sanctions |
| `app/osint/phone.py` | libphonenumber validation/region/carrier, Ignorant phone-to-social check |
| `app/osint/face.py` | Face detection/embeddings (insightface → deepface → face_recognition → compreface → opencv) |
| `app/osint/spiderfoot_client.py` | Client for the self-hosted SpiderFoot scan engine |
| `app/osint/compreface_client.py` | Client for the self-hosted CompreFace face-recognition server |
| `app/osint/models/` | Committed OpenCV face models (Haar cascade XML + YuNet ONNX) |
| `app/routers/osint.py` | FastAPI router, mounted at `/api/osint` |
| `app/routers/osint_schemas.py` | Pydantic request/response models for the OSINT API |
| `app/domains/telegram/rugmunchbot/handlers/osint.py` | Telegram `/osint /track /investigate /email /phone /face` |
| `app/domains/telegram/rugmunchbot/handlers/deepscan.py` | Telegram `/deepscan` (ML + contract analysis) |
| `infra/spiderfoot/`, `infra/compreface/` | Self-hosted engines consumed by the section |
| `infra/archivebox/`, `infra/trueblocks/`, `infra/kroki/` | Supporting self-hosted tooling |

---

## 2. The unified report contract

`app/osint/__init__.py` defines the contract every module returns.

### 2.1 Exceptions

| Exception | Meaning |
|-----------|---------|
| `OsintError` | Base error for OSINT lookups (subclasses `RuntimeError`). |
| `OsintNotAvailable` | The underlying tool/service for a module is not installed or unreachable. Callers typically map this to a 503. |

### 2.2 `OsintFinding`

A single resolved fact about the target.

| Field | Type | Meaning |
|-------|------|---------|
| `source` | `str` | Which tool/API produced it, e.g. `"whois"`, `"crt.sh"`, `"hibp"`, `"chainabuse"`, `"sherlock"`. |
| `category` | `str` | Finding type, e.g. `"registrant"`, `"subdomain"`, `"account"`, `"breach"`, `"sanction"`. |
| `summary` | `str` | One-line human-readable summary. |
| `confidence` | `str` | One of `"high" | "medium" | "low"` (default `"medium"`). |
| `data` | `dict[str, Any]` | Structured payload specific to the finding (URLs, records, scores). |

`OsintFinding.to_dict()` returns exactly those five keys.

### 2.3 `OsintReport`

The aggregated result for one target.

| Field | Type | Meaning |
|-------|------|---------|
| `target` | `str` | The lookup subject (normalized). |
| `target_type` | `str` | `"username"`, `"email"`, `"domain"`, `"wallet"`, `"phone"`, or `"face"`. |
| `findings` | `list[OsintFinding]` | All resolved facts. |
| `errors` | `list[str]` | Per-source failure notes; the report is still returned. |

Helpers:

- `report.found` — `len(findings)`.
- `report.add(source, category, summary, **data)` — appends a finding with the
  default `confidence="medium"`. Note: `add` has no confidence keyword; modules
  that need explicit confidence construct `OsintFinding(...)` directly (see
  `wallet.py:_add_finding`).
- `report.to_dict()` — `{"target", "target_type", "findings": [...], "errors": [...]}`.

### 2.4 The module contract

Every module exposes:

```python
lookup_<target>(<target>, *, timeout=60) -> OsintReport
```

- **Never raise for a source failure.** A failed source appends to
  `report.errors`; the remaining sources still run.
- **Raise only for structural problems:** `ValueError` for invalid input and
  `OsintNotAvailable` when a required tool is missing entirely.
- The engine depends on this contract — see Section 4.

---

## 3. Modules in detail

### 3.1 `username.py` — social/profile enumeration

**Capability:** check a handle across social platforms.

- **Sherlock (primary)** — the `sherlock-project` CLI, invoked via subprocess
  and never imported. Checks a curated list of 40 high-value platforms
  (`DEFAULT_SITES`: GitHub, GitLab, Reddit, Twitter, Instagram, LinkedIn,
  Telegram, Discord, YouTube, Bluesky, threads, Snapchat, Mastodon.social, VK,
  Steam Community, Spotify, SoundCloud, Medium, DEV Community, HackerNews,
  Keybase, BitBucket, Codeberg, Patreon, ProductHunt, Freelancer, Vimeo,
  DailyMotion, tumblr, CashApp, Venmo, Hugging Face, TradingView, VirusTotal,
  Imgur, Pastebin, last.fm, About.me, Docker Hub, LeetCode). Pass
  `sites=[]` for a full sweep of Sherlock's entire site database, or an
  explicit list to check only those platforms.
- **maigret (fallback)** — wraps `app.osint_tools.lookup_username`, which runs
  the `maigret` CLI over its top-N Alexa-ranked sites (default
  `DEFAULT_MAIGRET_TOP_SITES = 30`).

**Sherlock CLI compatibility note:** sherlock >= 0.16 changed `--json` from an
output flag to a site-data *input* flag, so the module parses Sherlock's
machine-readable `--csv` report instead, and passes `--timeout` as the
per-request HTTP timeout (capped at 15 s).

**Confidence semantics:** an account that exists on a platform is
`confidence="high"` (source `"sherlock"` or `"maigret"`, category `"account"`,
data carries `url`, `url_main`, `http_status`, `response_time_s` where known).

**Error handling:** missing/failing Sherlock degrades to a maigret fallback;
both missing raises `OsintNotAvailable` with install instructions for both.
Garbage input (empty, email-looking, whitespace/path separators) raises
`ValueError` before any subprocess is spawned. Install:
`pip install sherlock-project` and/or `pip install maigret`.

### 3.2 `email.py` — breach and reputation

**Capability:** aggregate breach + reputation intelligence for one email.

| Source | What it returns | Category | Confidence |
|--------|-----------------|----------|------------|
| HIBP breaches (`https://haveibeenpwned.com/api/v3/breachedaccount/{email}`) | Breach name, domain, date, account count, data classes | `breach` | `high` |
| HIBP pastes (`https://haveibeenpwned.com/api/v3/pasteaccount/{email}`) | Paste source, title, date, email count | `breach` | `high` |
| emailrep.io (`https://emailrep.io/{email}`) | Reputation, suspicious flag, leak flags, linked profiles | `reputation` / `breach` | `medium` |
| Local disposable-domain list | Flag for ~40 known disposable domains | `risk` | `medium` |

**Env vars:** `HIBP_API_KEY` (sent as `hibp-api-key`; HIBP v3 requires a key
for authenticated lookups), `EMAILREP_API_KEY` (sent as `X-API-Key`; basic
lookups work without one).

**HTTP error semantics:** 404 = no records (silent); 429 = rate limited;
401/403 = bad/missing key — each becomes a `report.errors` line, never a crash.
Per-source timeout is clamped to 5–20 s. Requires `httpx`.

### 3.3 `domain.py` — domain investigation

**Capability:** investigate a domain (e.g. a scam landing page) across
independent sources. `lookup_domain` normalizes input first
(`normalize_domain`: strips scheme/path/port, lowercases, IDNA-encodes;
rejects IPs and garbage), then runs each source with a shared wall-clock
budget.

| Source | What it returns | Category | Confidence |
|--------|-----------------|----------|------------|
| DNS (dnspython) | A/AAAA/MX/NS/TXT/CNAME records; NXDOMAIN | `dns` | `high` |
| WHOIS (python-whois, else `whois` CLI) | Registrant, registrar, dates, nameservers | `registrant` / `registration` | `medium` / `high` |
| SSL certificate (stdlib `ssl`+`socket`) | Issuer, validity, subject alternative names; flags self-signed/invalid | `certificate` | `high` |
| crt.sh (certificate transparency) | Subdomain names (bounded at 5000 rows) | `subdomain` | `high` |
| Wayback Machine CDX | First/last snapshot + capture count | `web-archive` | `high` |
| HTTP fingerprint | Status code, final URL, `Server` + security headers | `fingerprint` | `medium` |
| theHarvester (optional CLI) | Emails + hosts from `crtsh,rapiddns,hackertarget` (limit 50) | `email` / `subdomain` / `host` | `medium` |
| Amass (optional CLI) | Passive subdomains, addresses, ASNs (v3 `-json` / v4 `-oA`) | `subdomain` / `address` / `asn` | `medium` |

**Degradation:** missing optional deps (`httpx`, `dnspython`, `python-whois`)
produce a `report.errors` line and the rest still runs. Missing/failing CLI
tools (theHarvester, Amass) are logged and skipped without touching
`report.errors`, so the core sources always produce a complete report.

### 3.4 `wallet.py` — crypto wallet attribution

**Capability:** attribute a crypto address to known entities — the "track down
the scammer's wallet" flow.

| Source | What it returns | Category | Confidence |
|--------|-----------------|----------|------------|
| Chain detection (`_normalize`) | EVM (`0x…`, 40 hex) vs Solana (base58, 32–44 chars); checksums EVM via `eth_utils` when available | `chain` | `high` |
| `app.entity_registry` | Known CEX hot-wallet / exchange / mixer / scam labels | `entity_label` / `exchange` | `high` (internal) / `medium` |
| `app.wallet_label_loader` (Redis) | Solana CEX/DApp/DeFi, Ethereum phishing, OFAC labels | `entity_label` | `medium` |
| Chainabuse API | Public scam reports (category + description) | `scam_report` | `high` (trusted) / `medium` |
| Local OFAC sanctions list | Sanctioned-entity matches (entity + program) | `sanction` | `high` |

**Chainabuse auth:** reads `CHAINABUSE_API_KEY` from the environment. With a
key it tries v0 (`GET /v0/reports?address=`, HTTP Basic with key as user and
password) then v1 (Bearer). Without a key it tries v0 unauthenticated and
surfaces a clear "key required" error on 401/403.

**OFAC list paths (cheapest first):** `/srv/crypto-sanctions-list/data/sanctioned-addresses.json`,
`/app/data/crypto-sanctions-list/data/sanctioned-addresses.json`,
`/app/data/wallet-labels/ofac_sanctions.json`. Supports both the
`crypto-sanctions-list` format and the repo's flat list format; lazily loaded
and cached.

**Semantics:** an unrecognized address format returns a report with the error
recorded (nothing to look up); a chain override that conflicts with the
detected format warns but continues. When no entity label exists, a
`confidence="low"` "no known entity label" finding is added so callers can
distinguish a checked-but-unlabeled address from an unchecked one.

### 3.5 `phone.py` — phone number intelligence

**Capability:** validate and characterize a phone number.

| Source | What it returns | Category | Confidence |
|--------|-----------------|----------|------------|
| libphonenumber (`phonenumbers`) | Validation (valid/possible/invalid), E.164 + international formats, country code, line type | `validation` | `high` / `medium` / `low` |
| libphonenumber | Region code, region description, timezones | `region` | `high` / `low` |
| libphonenumber | Carrier name | `carrier` | `high` |
| libphonenumber | Scam-risk flag for VOIP / toll-free line types | `risk` | `low` |
| Ignorant (optional) | Amazon / Instagram / Snapchat registration probes | `account` | `medium` |

**Ignorant** (`pip install ignorant==1.2`) is GPL-3.0, HTTP-only since v1.2
(no Telegram API session required). A missing package records an error line and
the rest of the lookup succeeds. Unparseable input does not raise: it returns a
report with a single low-confidence "invalid" finding plus an error entry.

### 3.6 `face.py` — facial recognition and reverse search

**Capability:** detect faces in an image, produce embeddings, and optionally
identify subjects against a collection.

Five engines run in strict fallback order; the first that detects at least one
face wins:

| Order | Engine | Notes |
|-------|--------|-------|
| 1 | `insightface` | ArcFace `buffalo_l` ONNX models (auto-downloaded on first use; needs `onnxruntime`), 512-d embeddings |
| 2 | `deepface` | Facenet512 embeddings (VGG-Face selectable), 512-d |
| 3 | `face_recognition` | dlib HOG detector, 128-d encodings |
| 4 | `compreface` | Remote REST (self-hosted CompreFace); the only engine that **identifies** subjects (matched faces carry `subject` + `similarity`); needs `COMPREFACE_URL` + `COMPREFACE_API_KEY` |
| 5 | `opencv` | Cheap fallback using committed models (`app/osint/models/`): YuNet ONNX (OpenCV 5.x) or Haar cascade (OpenCV 4.x); bounding boxes only, no embeddings |

**Payload hygiene:** embeddings are never dumped in full — each finding stores
a 32-dim preview plus a sha256 fingerprint of the full float32 vector (for
dedup/comparison), plus bounding box and landmarks.

**Reverse-image search:** the report always carries a `reverse_search` finding
with ready-to-open URLs for Google Lens, Google search-by-image, Bing Visual
Search, and Yandex. Those engines need the image at a public URL — pass an
`https://` URL to get clickable links; local files get a note that the image
must be hosted first. Detection itself requires a local file path; URL inputs
skip the face engines (documented limitation).

**Error handling:** no engine installed raises `OsintNotAvailable`;
missing/undecodable file raises `OsintError`; engines installed but zero faces
produce a single low-confidence "no face detected" finding plus per-engine
notes in `report.errors`.

### 3.7 `spiderfoot_client.py` — general OSINT engine client

**Capability:** run a scan against the self-hosted SpiderFoot instance
(`infra/spiderfoot/`) and map its per-event results onto the report model.

SpiderFoot is the general backbone: given a domain, email, username, wallet or
IP it fans out across 100+ modules (DNS/WHOIS, certificate-transparency logs,
breach feeds, social/profile enumeration, blockchain). The client targets the
OSS v4.0.0 web-UI JSON API — **not** the paid HX `/rest/v1` API.

**API endpoints used** (all require `Accept: application/json`):

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/startscan` | POST | `["SUCCESS", scanId]` or `["ERROR", msg]` |
| `/scanstatus?id=…` | GET | `[name, target, created, started, ended, status, riskmatrix]` |
| `/scaneventresults?id=…&eventType=ALL` | GET | Event rows: `[lastseen, data, source_data, module, confidence, visibility, risk, hash, fp, parent_fp, event_type]` |
| `/scanerrors?id=…` | GET | Module-level error strings |

**Scan lifecycle:** `INITIALIZING → STARTING → STARTED → RUNNING` then
`FINISHED` (success) or `ERROR-FAILED` / `ABORTED`. The client polls every 5 s
until terminal or the timeout budget runs out (returning partial results).

**Event mapping:** SpiderFoot event types are mapped to report categories via
`_EVENT_CATEGORY` (e.g. `EMAILADDR → email`, `BREACHED_ACCOUNT → breach`,
`SANCTIONED_ENTITY → sanction`), with a snake_case fallback. Confidence %
becomes `high` (>= 70), `medium` (>= 40), `low` (< 40); a false-positive flag
forces `low`. Module names are stripped of the `sfp_` prefix for the `source`
field. Seed/ROOT events and `WEBSERVER_HTTP_HEADERS` noise are skipped.

**Env vars:** `SPIDERFOOT_URL` (default `http://127.0.0.1:5001`),
`SPIDERFOOT_TOKEN` (optional bearer token), `SPIDERFOOT_USECASE` (default
`Passive` — the safe module set; no active scanning).

**Contract difference:** the client's signature is
`lookup(target, target_type="domain", *, timeout=120)` — it takes the target
type explicitly rather than being named `lookup_*`.

### 3.8 `compreface_client.py` — CompreFace REST client

**Capability:** talk to the self-hosted CompreFace face-recognition server
(Exadel, Apache-2.0, `infra/compreface/`).

All requests carry the per-service `x-api-key` header and send images as
`multipart/form-data` field `file`.

| Function | Endpoint | Purpose |
|----------|----------|---------|
| `add_face(image, subject)` | `POST /api/v1/recognition/faces?subject=…` | Enroll a face into a subject collection |
| `recognize(image, …)` | `POST /api/v1/recognition/recognize` | Detect + match faces; returns `{subject, similarity, all_subjects, bbox, probability}` per face |
| `detect(image, …)` | `POST /api/v1/detection/detect` | Detect-only (no collection match) |
| `is_configured()` | — | True when `COMPREFACE_URL` + `COMPREFACE_API_KEY` are set |

**Env vars:** `COMPREFACE_URL` (default `http://127.0.0.1:8000`), the loopback
port the compose stack publishes; `COMPREFACE_API_KEY` (required; created per
face-recognition service in the CompreFace admin UI/API).

**Error handling:** unconfigured/unreachable raises `CompreFaceNotAvailable`
(a subclass of `OsintNotAvailable`, so existing callers keep working); a
malformed response (missing `result`) is logged and treated as an empty result.

---

## 4. The engine (`app/osint/engine.py`)

The engine is the single entry point for the section.

### 4.1 Target-type auto-detection

`detect_target_type(target, target_type="auto")` classifies a raw string.
Order matters:

1. contains `@` → `email`
2. digits only (7–15 chars, `+` optional, after stripping spaces/dots/dashes) → `phone`
3. starts with `0x` or is a 32–44-char base58 string → `wallet`
4. contains `.` and ends in a TLD → `domain`
5. anything else → `username`

An explicit `target_type` is returned unchanged (the caller validates
membership against `TARGET_TYPES`).

### 4.2 Module registry

```python
TARGET_TYPES = {"username", "email", "domain", "wallet", "phone", "face"}

_REGISTRY = {
    "username": ("app.osint.username", "lookup_username"),
    "email":    ("app.osint.email",    "lookup_email"),
    "domain":   ("app.osint.domain",   "lookup_domain"),
    "wallet":   ("app.osint.wallet",   "lookup_wallet"),
    "phone":    ("app.osint.phone",    "lookup_phone"),
    "face":     ("app.osint.face",     "lookup_face"),
}
```

Lookups are imported **lazily** inside `try/except` — a module that has not
landed yet, or whose optional dependency is missing, is skipped and noted in
`errors`; it can never crash the engine.

SpiderFoot is dispatched separately:

```python
_SPIDERFOOT_MODULE = ("app.osint.spiderfoot_client", "lookup")
_SPIDERFOOT_TYPES = frozenset({"domain", "username"})
```

The general engine is only auto-dispatched for `domain` and `username`
targets.

### 4.3 `run_osint(target, target_type="auto", *, timeout=120)`

Single-type dispatch. Resolves the target type, runs the matching module (plus
SpiderFoot for `domain`/`username`), returns:

```json
{
  "target": "scam-example.com",
  "target_type": "domain",
  "results": {
    "domain": { "target": "...", "target_type": "domain", "findings": [...], "errors": [...] },
    "spiderfoot": { ... }
  },
  "errors": []
}
```

### 4.4 `run_aggregate(target, target_type="auto", *, timeout=120)`

Runs all applicable modules in parallel via `ThreadPoolExecutor`
(`_MAX_WORKERS = 8`) and merges every report. Returns the same shape as
`run_osint` plus a flat, deduped `findings` list (dedupe key:
`(source, category, summary, confidence)`):

```json
{
  "target": "...", "target_type": "...",
  "results": { "<module>": { ... } },
  "findings": [ { "source": "...", "category": "...", "summary": "...", "confidence": "...", "data": { ... } } ],
  "errors": []
}
```

Every module failure is recorded in `errors` — the engine never raises.

---

## 5. How to add a NEW OSINT module

The framework is registry-driven; adding a module is a two-part change and
needs no changes to the API or bot.

### Step 1 — create the module

Create `app/osint/<name>.py` with:

1. A `lookup_<name>(<target>, *, timeout=60) -> OsintReport` function (the
   `timeout` keyword is required — the engine always passes it).
2. The module must **never raise for a source failure**: append a
   human-readable string to `report.errors` and continue.
3. Raise `ValueError` for invalid input, `OsintNotAvailable` when a required
   tool is missing. These are the only two exception types callers rely on.
4. Return an `OsintReport` with `target_type="<name>"` and findings whose
   `source`, `category`, `summary`, `confidence`, and `data` follow the
   conventions of the sibling modules (see Section 2 and 3).
5. Guard optional dependencies with `importlib.util.find_spec` (as
   `domain.py` does) or `try/except ImportError` (as `phone.py` does) so the
   module imports cleanly even when its heavy dependency is absent.

A minimal example:

```python
# app/osint/example.py
from __future__ import annotations

from app.osint import OsintFinding, OsintNotAvailable, OsintReport

def lookup_example(target: str, *, timeout: int = 60) -> OsintReport:
    report = OsintReport(target=target, target_type="example")
    # ... perform the lookup; on failure record errors, never raise ...
    report.findings.append(
        OsintFinding(
            source="example_api",
            category="profile",
            summary=f"found profile for {target}",
            confidence="medium",
            data={"url": "https://example.com/..."},
        )
    )
    return report
```

### Step 2 — register in the engine

In `app/osint/engine.py`:

1. Add the type to `TARGET_TYPES` (e.g. `{"username", ..., "example"}`).
2. Add a registry entry:

```python
_REGISTRY["example"] = ("app.osint.example", "lookup_example")
```

3. If the module should also fan out through SpiderFoot, add the type to
   `_SPIDERFOOT_TYPES` — otherwise leave it out.

That is the whole integration. `run_osint` / `run_aggregate` will dispatch to
it automatically, `detect_target_type` will classify for it only if you add
detection rules (otherwise callers must pass `target_type` explicitly), and
the REST API will advertise it via `GET /api/osint/types`.

### Step 3 — optional surface

- **API:** nothing to do; `GET /api/osint/types` reads `TARGET_TYPES` from the
  engine. If the module is slow, the existing `timeout` query parameter (5–600 s)
  applies.
- **Telegram:** add a handler in
  `app/domains/telegram/rugmunchbot/handlers/osint.py` following the existing
  `cmd_*` pattern (validate, reply status, run via `asyncio.to_thread`, render
  with `_format_report`), then register it with `_reg(app, ...)` in
  `app/domains/telegram/rugmunchbot/bot.py`.
- **Tests:** add `tests/test_osint_<name>.py` next to the existing
  `tests/test_osint_*.py` suite.

---

## 6. REST API

Router: `app/routers/osint.py`, mounted in `app/main.py`:

```python
app.include_router(osint_router, prefix="/api/osint", tags=["osint"])
```

The engine is imported lazily; if it is unavailable the endpoints return 503
("OSINT engine not available") instead of breaking app boot.

All OSINT endpoints are rate-limited via
`get_rate_limit_dependency("osint", rate=0.2, burst=3)` (0.2 requests/second,
burst 3).

### 6.1 `GET /api/osint/types`

Lists the supported target types.

```json
{ "target_types": ["username", "email", "domain", "wallet", "phone", "face"] }
```

(If the engine module is missing, the router falls back to
`FALLBACK_TARGET_TYPES = ["username", "email", "domain", "wallet", "phone", "spiderfoot"]`.)

### 6.2 `GET /api/osint/{target_type}/{target}`

Runs `run_osint(target, target_type)` for a single target of the given type.

Query parameters:

| Param | Type | Default | Constraint |
|-------|------|---------|------------|
| `timeout` | int | `60` | 5–600 seconds ("Per-module timeout in seconds — OSINT lookups are slow") |

Errors: `422` for an unknown target type (with the valid list in `detail`),
`503` when the engine or a required tool is unavailable, `422`/`500` mapped
from `ValueError`/`OsintError`.

Response model: `AggregateReportModel` (see Section 6.4).

### 6.3 `POST /api/osint/aggregate`

Runs `run_aggregate(target, target_type?)` across all applicable modules.

Request body (`AggregateRequest`):

```json
{ "target": "scam-example.com", "target_type": "domain" }
```

`target_type` is optional (auto-detection when omitted). Same `timeout` query
parameter and error mapping as `GET`.

### 6.4 Response models (`app/routers/osint_schemas.py`)

```python
class OsintFindingModel(BaseModel):
    source: str
    category: str
    summary: str
    confidence: str = "medium"           # high | medium | low
    data: dict[str, Any] = Field(default_factory=dict)

class OsintReportModel(BaseModel):        # per-module report
    target: str
    target_type: str
    findings: list[OsintFindingModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

class AggregateReportModel(BaseModel):    # engine output (both run_osint and run_aggregate)
    target: str
    target_type: str
    results: dict[str, OsintReportModel] = Field(default_factory=dict)
    findings: list[OsintFindingModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

class AggregateRequest(BaseModel):        # POST body
    target: str
    target_type: str | None = None
```

`findings` defaults to `[]`, so the `run_osint` shape (which omits the flat
list) validates against the same model as `run_aggregate`. The router
normalizes engine output with `json.dumps(..., default=str)` before validation,
so non-JSON-native values (datetimes, enums, bytes) never break the response.

---

## 7. Telegram commands

### 7.1 OSINT handlers (`app/domains/telegram/rugmunchbot/handlers/osint.py`)

Registered in `app/domains/telegram/rugmunchbot/bot.py`:

| Command | Handler | What it does | Timeout |
|---------|---------|--------------|---------|
| `/osint <username>` | `cmd_osint` | Social account enumeration (maigret wrapper) | 25 s |
| `/track <wallet>` | `cmd_track` | Wallet entity/exchange/scam/sanction attribution | 45 s |
| `/investigate <domain>` | `cmd_investigate` | WHOIS/DNS/subdomains/SSL/archive summary; non-domain args fall through to the Pro-tier deep investigation | 45 s |
| `/email <email>` | `cmd_email` | HIBP breaches + emailrep.io reputation | 30 s |
| `/phone <phone>` | `cmd_phone` | Validation, carrier, region, line-type risk | 20 s |
| `/face` (reply to a photo) | `cmd_face` | Face count via OpenCV YuNet/Haar committed models + reverse-search hint | — |

Conventions (shared with the rest of the bot): reply with a status message,
then `edit_text` the result; render with the report formatter
(`_format_report`), which shows up to 8 findings inline (category + confidence
emoji + summary) and collapses the rest to "+N more…"; errors become a helpful
"OSINT Unavailable" / "OSINT Lookup Failed" card, never a crash. All lookups
are blocking, so they run via `asyncio.to_thread` to keep the event loop
responsive.

The bot's `/face` command uses the same committed OpenCV models as
`app/osint/face.py` (`app/osint/models/`), detected with `cv2.FaceDetectorYN`
(YuNet, OpenCV 5.x) falling back to `cv2.CascadeClassifier` (Haar, OpenCV 4.x).

### 7.2 Deep scan (`app/domains/telegram/rugmunchbot/handlers/deepscan.py`)

| Command | Handler | What it does |
|---------|---------|--------------|
| `/deepscan <address> [chain]` | `cmd_deepscan` | ML + contract-analysis deep scan: fraud-GNN / pyod / pygod anomaly scores, decompiled ABI (heimdall), sell simulation (pyrevm) honeypot/tax/blacklist flags, top findings. Can take 30–90 s. |

`cmd_deepscan` wraps `app.deep_scan.deep_scan_address` (the deep-scan
orchestrator, `app/deep_scan.py`), runs via `asyncio.to_thread`, and degrades
gracefully when the ML/contract modules are not provisioned. The older
scanner-based scan is preserved as `/scandeep` (alias in `bot.py`).

---

## 8. Self-hosted infrastructure

All services are loopback-bound by design — OSINT data is never exposed
publicly. Remote access is via SSH tunnel or `tailscale serve` (see each
`infra/*/README.md`).

### 8.1 SpiderFoot (`infra/spiderfoot/`) — general OSINT engine

- **Pinned:** built from upstream source at tag `v4.0.0` (there is no official
  `spiderfoot/spiderfoot` image on Docker Hub; the image is built from the
  pinned `Dockerfile`). Port `5001` (loopback). Named volume
  `spiderfoot-data:/var/lib/spiderfoot`.
- **Plug-in:** consumed by `app/osint/spiderfoot_client.py` via the OSS web-UI
  JSON API (`POST /startscan`, `GET /scanstatus`, `GET /scaneventresults`,
  `GET /scanerrors`). Auto-dispatched by the engine for `domain` and
  `username` targets.
- **Module set:** the default `Passive` use case (199 modules that only consult
  third-party data sources; no active scanning). Modules are enabled/disabled
  in the web UI; for investigation work the "stalker-ish" social/geolocation
  modules (`sfp_socialprofiles`, `sfp_sociallinks`, `sfp_gravatar`,
  `sfp_twitter`, `sfp_fullcontact`, `sfp_googlemaps`) should stay off unless
  the investigation justifies them.
- **Ops:** `cp .env.example .env && docker compose up -d --build --wait`;
  back up the data volume (it is the investigation state). The OSS web UI has
  no API token; the client sends an optional `Authorization: Bearer` header if
  `SPIDERFOOT_TOKEN` is set (forward-compat/proxy use) — the primary security
  boundary is the loopback bind.

### 8.2 CompreFace (`infra/compreface/`) — self-hosted face recognition

- **Pinned:** official Exadel images at `1.2.0`
  (`compreface-core`, `compreface-admin`, `compreface-api`,
  `compreface-postgres-db`). Loopback ports `8000` (recognition API) / `8001`
  (admin API). Named volume `postgres-data` (subject metadata + saved images).
  Requires an x86-64 CPU with AVX for the ML core.
- **Plug-in:** consumed by `app/osint/compreface_client.py` (engine 4 in the
  `face.py` fallback chain — the only engine that can identify subjects, not
  just detect faces). Configured via `COMPREFACE_URL` + `COMPREFACE_API_KEY`.
- **Setup:** the compose stack deliberately omits the nginx UI gateway — the
  platform calls the REST API directly. Create the face collection + API key
  through the admin API (`/admin/user/register`, `/admin/oauth/token`,
  `/admin/app`, `/admin/app/{guid}/model` with `type=recognition`); the
  OAuth client used by the official UI is `CommonClientId:password`. The
  default similarity threshold is > 0.5; upstream recommends > 0.6 for
  high-security use. Service names (`compreface-postgres-db`,
  `compreface-core`) must stay as-is — the Java services resolve them by
  hostname.

### 8.3 ArchiveBox (`infra/archivebox/`) — evidence capture

- **Pinned:** `archivebox/archivebox:0.7.4` (the stable line; `0.9.x` tags are
  prereleases). Loopback port `8000`. Bind mount `./data:/data`.
- **Plug-in:** not wired into the OSINT report pipeline — it is the
  evidence-capture companion for investigations. Scam sites disappear fast
  (takedown, 403, domain kill), so archive before they do. Each snapshot
  stores WARC + PDF + full-page PNG + singlefile HTML + raw DOM + response
  headers, with `SAVE_HEADERS` recording capture time, server, and TLS
  metadata for provenance.
- **Lock-down:** keep `PUBLIC_INDEX=False`, `PUBLIC_SNAPSHOTS=False`,
  `PUBLIC_ADD_VIEW=False`; `CHECK_SSL_VALIDITY=False` so sites with
  broken/self-signed TLS are still captured. The `./data` directory is hostile
  input (attacker-controlled pages) — treat it as such.
- **Ops:** `archivebox add --depth=1 <url>` (CLI) or the JSON API
  (`/api/v1/core/snapshots/`) for batch/tagged captures; back up `./data` as
  the evidence itself; never automate deletion of evidence.

### 8.4 TrueBlocks (`infra/trueblocks/`) — wallet-history forensics

- **Pinned:** `trueblocks/core` v5.0.0 Docker image; `chifra daemon` serves
  the single-user API on loopback port `8080`.
- **Plug-in:** not wired into the OSINT report pipeline — it is the
  fast, self-hosted wallet-history forensics layer for EVM chains. TrueBlocks
  maintains a local "Unchained Index" of address appearances, so investigators
  answer "what has this address ever done?" with `chifra list` / `chifra
  export` against a local index instead of hammering an archive node.
- **Setup:** requires an **archive + tracing** EVM node RPC for the index
  build (`TB_CHAINS_MAINNET_RPCPROVIDER`; shared/rate-limited providers get
  hammered during `chifra scrape`). Index options: `chifra init` (~1 GB
  blooms, fast start), `chifra init --all` (~60–80 GB snapshot), or
  `chifra scrape` (build from scratch). Backup `./data/unchained` — it takes
  days to rebuild.

### 8.5 Kroki (`infra/kroki/`) — diagram server

- **Pinned:** `yuzutech/kroki:0.32.1` + `yuzutech/kroki-mermaid:0.32.1`
  companion. Loopback port `8010`. Stateless (no volumes).
- **Plug-in:** no OSINT code consumes it; it renders architecture and
  exploit-flow diagrams (Mermaid, D2, PlantUML, GraphViz, Structurizr,
  Excalidraw, ...) from plain-text source over `POST /{type}/{format}` or
  `GET /{type}/{format}/{deflate+base64url}`. Useful for docs/issues in
  investigations. `SECURE` safe mode (default) sandboxes PlantUML.

### 8.6 Related infra (`infra/scam-school/`)

The scam-school stack (CourseLit `codelit/courselit-app` + H5P + MongoDB +
nginx) is a separate self-hosted educational component, not part of the OSINT
lookup pipeline. It is listed here because the same license discipline applies
(see Section 10).

---

## 9. Deployment notes

### 9.1 Docker provisioning (`Dockerfile.multistage`)

The runtime image installs the OSINT CLI toolchain by default:

- `sherlock-project==0.16.0` (username enumeration)
- `maigret==0.6.4` (username fallback)
- `holehe==1.61` (email service enumeration via `app.osint_tools`)
- `opencv-python-headless==4.13.0.90` + the Haar cascade mirrored to
  `/usr/share/opencv4/haarcascades` (face detection for the bot and
  `app/osint/face.py`)

The CLIs are invoked via subprocess (never imported), so installing them into
the runtime image's `/usr/local` is sufficient.

### 9.2 Optional heavy engines (`INSTALL_FACE=1`)

The full face-recognition ML engines are opt-in because they add ~1 GB to the
image:

- `insightface==1.0.1` + `onnxruntime==1.28.0` (+ ~500 MB)
- `deepface==0.0.100`
- `dlib==20.0.1` (build via cmake/g++) + `face-recognition==1.3.0` (+ ~200 MB)
- Pre-baked ArcFace `buffalo_l` models under `/opt/insightface` (+ ~330 MB,
  requires network at build time; the bake is non-fatal — at runtime models
  auto-download to a writable `$INSIGHTFACE_HOME`, so mount a volume at
  `/opt/insightface`)

Without `INSTALL_FACE=1` the default image still serves `/face` via the Haar
cascade, and `app/osint/face.py` degrades gracefully (`OsintNotAvailable`)
when no ML engine is present.

### 9.3 Environment variables

| Variable | Used by | Default / notes |
|----------|---------|-----------------|
| `HIBP_API_KEY` | `app/osint/email.py` | Optional; sent as `hibp-api-key` header |
| `EMAILREP_API_KEY` | `app/osint/email.py` | Optional; sent as `X-API-Key` header |
| `CHAINABUSE_API_KEY` | `app/osint/wallet.py` | Optional; enables v0 Basic / v1 Bearer auth |
| `SPIDERFOOT_URL` | `app/osint/spiderfoot_client.py` | `http://127.0.0.1:5001` |
| `SPIDERFOOT_TOKEN` | `app/osint/spiderfoot_client.py` | Optional bearer token |
| `SPIDERFOOT_USECASE` | `app/osint/spiderfoot_client.py` | `Passive` (safe default) |
| `COMPREFACE_URL` | `app/osint/compreface_client.py` | `http://127.0.0.1:8000` |
| `COMPREFACE_API_KEY` | `app/osint/compreface_client.py` | Required for CompreFace |
| `INSTALL_FACE` | `Dockerfile.multistage` | `0` — set to `1` for heavy face engines |

`CHAINABUSE_API_KEY` appears in the committed `.env.example`. The SpiderFoot,
CompreFace, ArchiveBox, TrueBlocks, and Kroki compose variables live in their
respective `infra/*/.env.example` templates (see Section 8).

### 9.4 Verification

The OSINT section has a dedicated test suite:

```
tests/test_osint_api.py
tests/test_osint_domain.py
tests/test_osint_email.py
tests/test_osint_engine.py
tests/test_osint_phone.py
tests/test_osint_username.py
tests/test_osint_wallet.py
```

---

## 10. License notes

The OSINT section composes several third-party components with distinct
licenses. Self-hosting the stock upstream images/binaries is fine; the
discipline is to never vendor GPL/AGPL code into proprietary distributions and
never merge proprietary enhancements into a public fork.

| Component | License | Notes |
|-----------|---------|-------|
| **CompreFace** (`exadel/compreface-*`) | Apache-2.0 | Self-hosted via `infra/compreface/`; consumed over REST only, not linked in. |
| **Ignorant** (`megadose/ignorant`) | GPL-3.0 | Optional phone-to-social check in `app/osint/phone.py`; installed via pip (`ignorant==1.2`), invoked at runtime. |
| **TrueBlocks** (`trueblocks/core`) | GPL-3.0 | Standalone binary via the official Docker image (`infra/trueblocks/`); not vendored, not linked, not redistributed. Do not copy TrueBlocks source into any repo. |
| **CourseLit** (`codelit/courselit-app`) | AGPL-3.0 | Network copyleft: running the stock self-hosted image is fine; modifying and serving it over a network obligates publishing the modified source. See `infra/scam-school/README.md` (the platform runs the stock upstream image). |
| **H5P / Lumi** | MIT | Permissive; used by the scam-school stack only. |
| **Sherlock / maigret / holehe / theHarvester / Amass / SpiderFoot / ArchiveBox / Kroki** | See upstream projects | Consumed as external CLIs, binaries, or services — never imported/vendored into the platform's code. |

> [REVIEW] — CompreFace license: the codebase client docstring
> (`app/osint/compreface_client.py`) states Apache-2.0, which matches the
> upstream project. If the fleet treats CompreFace under an AGPL-style policy,
> confirm the applicable license version before quoting this table externally.

---

## 11. Confidence semantics (summary)

Confidence is a per-finding field with three levels; it reflects how
trustworthy the *fact* is, not how severe it is:

| Level | Used when | Examples |
|-------|-----------|----------|
| `high` | The source is authoritative and the signal is unambiguous | HIBP breach record, OFAC sanctions match, DNS record, sherlock "claimed" account, internal entity-registry match, TLS cert verification failure |
| `medium` | The source is a third-party inference or unauthenticated signal | emailrep reputation, Chainabuse reports (untrusted), theHarvester/Amass findings, Ignorant registration probes, WHOIS registrant details |
| `low` | Heuristic / absence-of-evidence signals | "no known entity label", "no face detected", VOIP/toll-free risk flag, no-sanctions-match, SpiderFoot false-positive-flagged events |

Absence findings are emitted deliberately (e.g. "no known entity label", "no
Chainabuse reports", "no archived snapshots") so callers can distinguish a
checked-but-clean target from an unchecked one.

---

## 12. References

- Section contract: `app/osint/__init__.py`
- Engine: `app/osint/engine.py`
- API: `app/routers/osint.py`, `app/routers/osint_schemas.py`
- Telegram: `app/domains/telegram/rugmunchbot/handlers/osint.py`,
  `app/domains/telegram/rugmunchbot/handlers/deepscan.py`
- Infra: `infra/spiderfoot/`, `infra/compreface/`, `infra/archivebox/`,
  `infra/trueblocks/`, `infra/kroki/`, `infra/scam-school/`
- Upstream: sherlock-project (https://github.com/sherlock-project/sherlock),
  maigret (https://github.com/soxoj/maigret), HIBP API v3
  (https://haveibeenpwned.com/API/v3), emailrep.io (https://emailrep.io/),
  SpiderFoot (https://github.com/smicallef/spiderfoot), CompreFace
  (https://github.com/exadel-inc/CompreFace), ArchiveBox
  (https://github.com/ArchiveBox/ArchiveBox), TrueBlocks
  (https://github.com/TrueBlocks/trueblocks-core), Kroki
  (https://github.com/yuzutech/kroki)
