# ClickHouse Versioned Schema Migrations

## Directory

Each file is a numbered migration: `NNN_description.sql`. Apply in order.

## How to add a migration

1. Create `app/db/clickhouse_migrations/NNN_description.sql`
2. Write pure ClickHouse SQL — `CREATE`, `ALTER TABLE`, `MODIFY TTL`, etc.
3. Every schema change = new number. Never edit an existing migration.
4. Apply with:

```bash
docker exec rmi-clickhouse clickhouse-client --multiquery \
  < app/db/clickhouse_migrations/NNN_description.sql
```

## Migration history

| # | File | Description | Date |
|---|------|-------------|------|
| 001 | `001_initial_schema_snapshot.sql` | All 7 wallet_memory tables (CREATE IF NOT EXISTS) | 2026-07-21 |
| 002 | `002_add_ttls.sql` | 6-month TTL DELETE on all tables | 2026-07-21 |

## Rules

- **Every schema change = new numbered file.** Never edit an existing migration.
- Each migration must be idempotent (`IF NOT EXISTS` / `MODIFY` not `SET`).
- Test on a copy before applying to production.
- Rollback: re-run the previous migration state (or keep a reverse migration file).