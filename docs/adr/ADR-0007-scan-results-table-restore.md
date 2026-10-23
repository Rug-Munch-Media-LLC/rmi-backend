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
  `alert_subscriptions`) were **still absent** at the time of this decision and
  were restored the same day by the follow-up below.

## Consequences — follow-up: the remaining four tables (2026-10-23)

`to_regclass` still returned NULL for `users`, `payments`, `labels` and
`alert_subscriptions` after `c8f9e0a1b2c3`. The same stamp-past fault applies
(one revision, `75c982fccbb8`, never ran), and the affected code paths are:

| table | readers/writers |
| --- | --- |
| `users` | `app/services/live/xp_service.py`, `app/db_client/repositories.py` |
| `payments` | `app/models/payment.py` consumers (x402 / billing) |
| `labels` | `app/domains/labels/sources/*`, `app/domains/labels/federated.py` |
| `alert_subscriptions` | `app/domains/alerts/service.py` |

Decision: a second guarded forward revision **`d1e2f3a4b5c6`**
(`down_revision = c8f9e0a1b2c3`) re-creates all four **only if absent**, with the
columns, defaults, FKs and indexes of `0001_initial` / `75c982fccbb8` and
`app/models/*`. `users` is created before `alert_subscriptions` because
`alert_subscriptions.owner_id` references `users.id`. One revision (not four)
because the tables share a single cause, no ordering conflict beyond that FK and
no data dependency; a partial apply could leave the FK target absent.

Verified on production (`rmi`, PostgreSQL 16):

- `to_regclass` non-NULL for all four; public table count 91 -> 95;
  `alembic current` = `d1e2f3a4b5c6`.
- ORM parity against `Base.metadata`: 9/9, 9/9, 13/13, 9/9 columns, index names
  and nullability identical, `uq_label` + `alert_subscriptions.owner_id -> users.id`
  present.
- ORM insert/select round-trip for one row per table inside a rolled-back
  transaction succeeds; all four tables remain empty (no probe data).
- 10-minute log watch over `rmi-backend` + `rmi-worker`: zero
  `does not exist` / `schema_preflight_failed` / `UndefinedTable` lines.

Operational notes:

- Snapshot before the change: `/root/backups/db/pre-0001-missing-tables-*.sql`
  (`pg_dump --schema-only`, 91 tables). Forward-only: `downgrade()` exists for
  local use but is never run in production, and drops only these four tables.
- The `backend-rmi-backend` image has **no synchronous Postgres driver**
  (`psycopg`, `psycopg2`, `pg8000` all absent; only `asyncpg`), so `alembic` cannot
  run inside it. The revision was applied with the repo virtualenv:
  `ALEMBIC_DATABASE_URL=postgresql://rmi:<pw with % -> %%>@127.0.0.1:5432/rmi .venv/bin/alembic upgrade head`.
  Adding `psycopg[binary]` to the image (or shipping a migration job) is the
  durable fix.
- `alembic upgrade` is still not part of the deploy path. Until it is, any
  revision added after a stamp will be silently skipped — the guard-only
  pattern in `c8f9e0a1b2c3` / `d1e2f3a4b5c6` keeps re-runs safe but does not
  schedule them.
- Operational note: `alembic` cannot currently read `ALEMBIC_DATABASE_URL`
  produced by the entrypoint when the password contains URL-escaped `%`
  (`configparser` interpolation error). Workaround used to apply this revision:
  duplicate the `%` as `%%`. Fix the entrypoint/`alembic.ini` separately.
- Pre-change schema snapshot of the relevant tables:
  `/root/backups/db/pre-scan_results-20261023T162039Z.sql`.
