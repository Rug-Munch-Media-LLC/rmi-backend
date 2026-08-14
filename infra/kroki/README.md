# Kroki — self-hosted diagram server

Deploys [yuzutech/kroki](https://github.com/yuzutech/kroki) (unified diagram
rendering: **Mermaid, D2, PlantUML, GraphViz, Structurizr, Excalidraw**, ...)
so architecture and exploit-flow diagrams in docs/issues can be rendered from
plain-text diagram source via a **local URL** — no image files to maintain, no
dependency on the public kroki.io service.

**Scope:** infra config + runbook only. No application code.

---

## 1. What it does

Kroki converts text-based diagram definitions to SVG/PNG over HTTP. You keep
the diagram source next to the doc (or in the issue), and render it on demand:

```
GET  /{diagram-type}/{output-format}/{deflate+base64url-encoded-diagram}
POST /{diagram-type}/{output-format}     # body = plain-text diagram source
```

### Diagram types supported (v0.32.1)

| Where it lives | Diagram types |
|----------------|---------------|
| **Bundled in `yuzutech/kroki`** (no companion) | D2, PlantUML (incl. C4 model), GraphViz/dot, Structurizr, Vega(-Lite), WaveDrom, Nomnoml, Erd, Pikchr, Svgbob, UMlet, GoAT, BlockDiag family (ActDiag/SeqDiag/NwDiag/RackDiag/PacketDiag), Bytefield, Ditaa, Symbolator, WireViz, TikZ |
| **Companion `yuzutech/kroki-mermaid`** (started by this compose) | Mermaid (flowchart, sequence, Gantt, class) |
| Optional companions (comment back into compose) | Excalidraw (`kroki-excalidraw`), BPMN (`kroki-bpmn`), diagrams.net (`kroki-diagramsnet`, experimental) |

> The most useful for our docs: **Mermaid** (quick flowcharts/sequences),
> **D2** (architecture diagrams), **PlantUML** (incl. `c4plantuml` for C4
> model), **GraphViz** (dependency/state graphs), **Structurizr** (C4 via DSL).

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
cp infra/kroki/.env.example infra/kroki/.env   # optional — defaults are fine
docker compose -f infra/kroki/docker-compose.yml up -d
docker compose -f infra/kroki/docker-compose.yml ps
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8010/   # 200
```

Stop / rotate:

```bash
docker compose -f infra/kroki/docker-compose.yml down      # keeps nothing (stateless)
docker compose -f infra/kroki/docker-compose.yml logs -f
```

---

## 4. Rendering diagrams

### 4.1 POST (plain text — easiest)

```bash
# Mermaid flowchart -> SVG
curl -s -X POST http://127.0.0.1:8010/mermaid/svg \
  -H "Content-Type: text/plain" \
  --data-binary 'graph TD
    A[scanner] --> B[risk score]
    B --> C{> 70?}
    C -->|yes| D[alert]'

# D2 architecture diagram -> SVG
curl -s -X POST http://127.0.0.1:8010/d2/svg \
  -H "Content-Type: text/plain" \
  --data-binary 'scanner -> api -> postgres'

# PlantUML -> PNG
curl -s -X POST http://127.0.0.1:8010/plantuml/png \
  -H "Content-Type: text/plain" \
  --data-binary '@startuml
Alice -> Bob: exploit flow
@enduml'

# GraphViz -> SVG
curl -s -X POST http://127.0.0.1:8010/graphviz/svg \
  -H "Content-Type: text/plain" \
  --data-binary 'digraph G { A -> B -> C }'
```

JSON POST is equivalent (`diagram_source`, `diagram_type`, `output_format`).
Output formats: `svg` (default), `png`; some types also `pdf`/`jpeg`.

### 4.2 GET (URL-encoded — for embedding)

The diagram source is deflate-compressed then base64url-encoded:

```bash
ENCODED=$(printf 'graph TD\nA-->B' | python3 -c \
  "import sys,base64,zlib; print(base64.urlsafe_b64encode(zlib.compress(sys.stdin.read().encode(),9)).decode())")

curl -s "http://127.0.0.1:8010/mermaid/svg/$ENCODED"
```

### 4.3 Embed in Markdown / docs / issues

```markdown
![scanner flow](http://127.0.0.1:8010/mermaid/svg/eNpLyUwvSizIUHBXqPZIzcnJ17ULzy_KSanlAgB1EAjQ)
```

Because the port is loopback-bound, embedded diagrams render only in clients
that can reach `127.0.0.1:8010` (e.g. a docs site or browser on the same host /
behind a tunnel). For public docs, use the same URL through a Cloudflare
Tunnel or render once and commit the SVG.

---

## 5. Config reference (env vars)

| Var | Default | Description |
|-----|---------|-------------|
| `KROKI_IMAGE` | `yuzutech/kroki:0.32.1` | Gateway image pin |
| `KROKI_MERMAID_IMAGE` | `yuzutech/kroki-mermaid:0.32.1` | Mermaid companion image pin |
| `KROKI_LISTEN_PORT` | `8010` | Host port (loopback-only bind; 8000 is the rmi-backend API port) |
| `KROKI_SAFE_MODE` | `SECURE` | `SECURE` (strictest) / `SAFE` / `UNSAFE` |
| `KROKI_MAX_URI_LENGTH` | `8192` | Max encoded-URL length (gateway default 4096) |
| `KROKI_MAX_BODY_SIZE` | `1mb` | Max POST body size |
| `KROKI_COMMAND_TIMEOUT` | `5s` | Diagram CLI timeout |
| `KROKI_CONVERT_TIMEOUT` | `20s` | Render convert timeout |
| `KROKI_DELEGATE_TIMEOUT_MS` | `30000` | Companion HTTP delegate timeout |
| `KROKI_MERMAID_MAX_TEXT_SIZE` | `50000` | Max Mermaid source chars |
| `KROKI_MERMAID_MAX_CONCURRENCY` | `6` | Concurrent Mermaid renders |

Companion addressing is fixed in compose (`kroki-mermaid:8002` over
`rmi_network`); the gateway env `KROKI_MERMAID_HOST/PORT` map to that service.

---

## 6. Operations

- **Health:** gateway `wget --spider http://localhost:8010/`; mermaid
  `/health` (renders a sample diagram — verifies the Chromium worker).
- **Restart policy:** `restart: unless-stopped`.
- **Stateless:** no volumes — nothing to back up; recreated from compose.
- **Resources:** gateway 1 CPU / 1 GiB, mermaid 1 CPU / 768 MiB (deploy limits).

### Security notes

- Port binds to `127.0.0.1` only — never exposed publicly. `SECURE` safe mode
  sandboxes PlantUML (no external `!include` fetch), the default.
- Containers drop all Linux capabilities, run `no-new-privileges`, no
  `host` networking.
- No secrets in `.env` — Kroki needs none. `.env` is gitignored;
  `.env.example` is the committed template.
- Image pins audited against upstream at commit time
  (`yuzutech/kroki:0.32.1`, `yuzutech/kroki-mermaid:0.32.1`, 2026-08-12).

---

## 7. References

- Upstream: https://github.com/yuzutech/kroki (MIT)
- Docs — install: https://docs.kroki.io/kroki/setup/install/
- Docs — usage/API: https://docs.kroki.io/kroki/setup/usage/
- Docs — configuration: https://docs.kroki.io/kroki/setup/configuration/
- Docker Hub: https://hub.docker.com/r/yuzutech/kroki
