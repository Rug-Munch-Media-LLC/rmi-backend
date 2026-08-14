# TrueBlocks local address-index deployment
# ==========================================
# Fast, self-hosted **wallet-history forensics** for EVM chains (mainnet
# by default). TrueBlocks maintains a local "Unchained Index" of address
# appearances, so investigators answer "what has this address ever done?"
# with `chifra list` / `chifra export` against a local index instead of
# hammering an archive node with per-address `eth_getLogs` /
# `eth_getTransactionByHash` scans.

## Why this exists

Archive-node RPCs are expensive and rate-limited. A single investigation
that pulls the full transaction history of a wallet can issue thousands
of RPC calls. TrueBlocks inverts the model: it builds a local index of
*address appearances* (which blocks/transactions mention address X), then
extracts full details **only for those appearances** on demand. First
query for a fresh address takes ~1 minute while it warms; subsequent
queries are near-instant from the local cache.

## License / GPL-3.0 note (IMPORTANT)

TrueBlocks is **GPL-3.0**. We run it as a **standalone binary via the
official `trueblocks/core` Docker image** — it is **not vendored into our
codebase, not linked into our products, and not redistributed**. It runs
self-hosted as an internal forensics tool. Do not copy any TrueBlocks
source into `rmi-backend` (or any public repo); treat it like any other
external binary (cf. how we consume other GPL tools as separate
processes). If a future requirement needs to embed TrueBlocks logic, stop
and get legal/Lead sign-off first.

## Requirements

- Docker with Compose v2 (this repo's standard toolchain).
- An **archive + tracing** EVM node RPC for the initial index build
  (e.g. Erigon `--gcmode archive`; the node must answer trace RPCs).
  Shared/rate-limited providers (Infura, public endpoints) will get
  rate-limited during `chifra scrape` — use an internal/self-hosted node.
- Disk: ~1 GB (bloom filters only) up to ~60–80 GB (full index for
  mainnet). The index lives in `./data/unchained`.
- The `chifra` daemon **refuses to start without a reachable RPC** — a
  restart loop in `docker compose ps` means the RPC in `.env` is wrong or
  down.

## Setup

```bash
cd infra/trueblocks
cp .env.example .env

# Edit .env — the one thing you MUST set:
#   TB_CHAINS_MAINNET_RPCPROVIDER=<your archive/tracing node RPC>
# (For another chain, set TB_SETTINGS_DEFAULTCHAIN and rename the RPC
#  variable to TB_CHAINS_<chain>_RPCPROVIDER accordingly.)

docker compose up -d
docker compose ps            # expect: Up (healthy)
```

The container runs `chifra daemon` — a single-user API server on
`http://127.0.0.1:8080` (loopback-bound only; use SSH tunnel /
`tailscale serve` for remote access). It is **not** intended for
multi-user/cloud serving.

## Build the Unchained Index (one of three ways)

All index commands run inside the container:

```bash
# A) Quick start — download bloom filters only (~1 GB, ~30 min).
#    Index chunks are fetched on demand (~25 MB each). Slower first-time
#    queries on new addresses; index trails the chain head.
docker compose exec trueblocks chifra init

# B) Full snapshot — download bloom filters + ALL index chunks
#    (~60–80 GB, 6–10 h). Fastest queries afterwards.
docker compose exec trueblocks chifra init --all

# C) Build from scratch — requires the archive/tracing node in .env.
#    ~60 GB, days of scraping. Best if you don't trust the published
#    snapshot. Run in tmux; ~3 new chunks/day afterwards to stay current.
docker compose exec trueblocks chifra scrape
```

Recommended: `chifra init` for a fast start, then run `chifra scrape`
in the background so the index is built/maintained from our own node.

## Usage — wallet-history lookups

```bash
# How many appearances / first & last block for an address
docker compose exec trueblocks chifra list --count 0x1234...
docker compose exec trueblocks chifra list --bounds 0x1234...

# Raw appearance list (block.transactionIndex pairs), csv for spreadsheets
docker compose exec trueblocks chifra list 0x1234...
docker compose exec trueblocks chifra list --fmt csv 0x1234...

# Full transaction history (JSON) — the forensics workhorse
docker compose exec trueblocks chifra export 0x1234...

# Ether/ERC20/ERC721 transfers involving the address
docker compose exec trueblocks chifra export --transfers 0x1234...

# Logs only (optionally filtered by emitter/topic)
docker compose exec trueblocks chifra export --logs 0x1234...

# Traces only (needs tracing-enabled RPC)
docker compose exec trueblocks chifra export --traces 0x1234...

# Neighbors — every address that appears with this one in any tx
docker compose exec trueblocks chifra export --neighbors 0x1234...

# Human-readable articulation (requires Etherscan key in .env)
docker compose exec trueblocks chifra export --articulate 0x1234...

# Constrain a huge history to a block range
docker compose exec trueblocks chifra export --first_block 15000000 --last_block 17000000 0x1234...
```

Tips:
- `--fmt csv` / `--fmt json` control output; default is JSON.
- The first `chifra list` for an address takes ~1 minute (it builds the
  per-address monitor); later queries hit the cache.
- `chifra export` is the extraction step — it only fetches details for
  the appearances found in the index, which is what keeps RPC load low.
- `chifra chunks` inspects/manages the index itself (e.g.
  `chifra chunks manifest` shows the published index manifest).

## Backup

```bash
# THE index is ./data/unchained — back it up (it takes days to rebuild).
# Add to the existing backup rotation:
#   rsync -a infra/trueblocks/data/unchained/ <backup-target>/trueblocks-unchained/
# ./data/cache is disposable (RPC cache). ./data/config holds ABI cache.
```

Restore: restore `./data/unchained` (and `./data/config`), recreate the
container — no re-scrape needed.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Container `Restarting` | RPC unreachable at boot — daemon exits. Fix `TB_CHAINS_*_RPCPROVIDER` in `.env`, `docker compose up -d` again. |
| `chifra list` slow first time | Normal — building the per-address monitor (~1 min). |
| `chifra scrape` exits | Node missing tracing/archive endpoints, or shared RPC rate-limiting. |
| API on 8080 not reachable | Check `TB_SERVE_PORT` and that you're on the same host (loopback-bound). |

## References

- Core: https://github.com/TrueBlocks/trueblocks-core (GPL-3.0)
- Docker: https://github.com/TrueBlocks/trueblocks-docker
- Docs: https://trueblocks.io/docs/
