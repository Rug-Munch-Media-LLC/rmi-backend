# RAG seed corpus — NOT DISTRIBUTED

This directory is intentionally empty in the public repository.

## Why

The JSONL files that used to live here (`crypto_knowledge.jsonl`,
`crypto_knowledge_2.jsonl`, `crypto_knowledge_3.jsonl`, `exploits.jsonl` — ~3.2 MB)
are a **third-party licensed intelligence corpus**. Records are derived from
upstream exploit/hack datasets (e.g. `src:defi_crime_landscape`) and other
provider sources whose licences do not permit public redistribution.

They were tracked here by mistake and have been removed from version control.
The files are **not deleted from any existing deployment working tree** — they
remain on disk for running instances and are now gitignored.

## Where the real corpus lives

Private Forgejo only — never a public mirror:

- `RugMunchMedia/contextcrypto` (private)
- `RugMunchMedia/rmi-codebase-mcp-corpus` (private)

Both require credentials; there is no anonymous fetch.

## How to obtain it (authorised operators only)

```bash
# SSH access to private Forgejo is required (no anonymous HTTPS).
git clone ssh://git@git.rugmunch.io:2222/RugMunchMedia/rmi-codebase-mcp-corpus.git
# then place the required JSONL files into this directory
```

Or restore from the operator-managed object store / deployment backup.

## Behaviour when absent

The seed corpus is loaded **at runtime only** by
`app/rag/ingest/_seed_corpus.py` (`SEED_CORPUS_DEFAULT_PATH`) and is invoked
through the ingest orchestrator CLI. Importing `app.rag` does **not** read this
directory, so the application imports and boots normally without these files.
`ingest_seed_corpus` fails with a clear missing-file error only when explicitly
invoked without a path override.

## Rules

- Never `git add` `*.jsonl` from this directory (enforced by `.gitignore`).
- Never mirror these files to a public remote (GitLab/Codeberg/Radicle).
- Adding a new upstream dataset requires a licence review first.
