# ADR-0007: restore `scan_results` via a guarded forward migration

- Date: 2026-10-23
- Status: accepted
- Supersedes: nothing (amends the 0001/75c982fccbb8 application state)

## Context

`to_regclass('public.scan_results')` returned NULL on the production `rmi`
database (PostgreSQL 16.15, 98 public tables, data directory from June — no
re-init). The application nevertheless queries it:

- writer: `app/domains/scanner/unified/persistence.py::persist_result` inserts
  `app.models.scan.ScanResult` (fire-and-forget; failures are only logged);
- reader: `app/ai/rug_learning_sweep.py::run_rug_learning_sweep`, invoked by the
  lifespan scheduler, which raised
  `relation "scan_results" does not exist` every cycle (`app.ai.schema_guard`
  preflight logged `schema_preflight_failed` continuously).

`alembic_version` reported `b7c8d9e0f1a2`, i.e. the full chain
`0001 -> 75c982fccbb8 -> ... -> b7c8d9e0f1a2` was "applied". But **none** of the
five tables created by `75c982fccbb8` exist:

```
alert_subscriptions = NULL   labels = NULL   payments = NULL
scan_results        = NULL   users  = NULL
```

## Root cause

The revision table was stamped from another environment (or restored
separately): the version rows claim the work happened while the tables were
never created. Because `upgrade head` is already at head, the guarded
`75c982fccbb8` upgrade never re-runs and the drift is invisible until a query
touches a missing relation.

Not a rename: `scan_reports` exists but is a different table
(`report_id/subject_type/sections/...`, AI-generated report artifacts). There is
no `scans`, `scan_memory` or `token_scans`.

## Decision

Add forward-only revision `c8f9e0a1b2c3` (`down_revision = b7c8d9e0f1a2`) that
re-creates `scan_results` **only if absent**, with the exact columns, defaults
and indexes of `0001_initial` / `app/models/scan.py`. No existing table is
altered or dropped. The same revision is a no-op on a correctly provisioned
database, so it is safe for fresh installs and for CI.

Rejected: `alembic stamp 0001 && upgrade head` (would replay 16 revisions,
including unrelated news/x402 DDL, against a populated schema) and a manual
`CREATE TABLE` on production (not reproducible, not in version control).

## Consequences

- Scanner persistence and the rug-learning sweep work again; the table starts
  empty and fills as scans land.
- The remaining missing 0001-era tables (`users`, `payments`, `labels`,
  `alert_subscriptions`) are **still absent** and are tracked as a separate
  follow-up — each needs its own scope decision because code paths and data
  volume differ.
- Operational note: `alembic` cannot currently read `ALEMBIC_DATABASE_URL`
  produced by the entrypoint when the password contains URL-escaped `%`
  (`configparser` interpolation error). Workaround used to apply this revision:
  duplicate the `%` as `%%`. Fix the entrypoint/`alembic.ini` separately.
- Pre-change schema snapshot of the relevant tables:
  `/root/backups/db/pre-scan_results-20261023T162039Z.sql`.
