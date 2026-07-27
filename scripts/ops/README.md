# ops/ — Operations Scripts (Example Pattern)

> This directory contains operational scripts that run on the server (outside the application container).
> The contents here are **example patterns** — actual production scripts live in a private repository.

## Typical Operations Scripts

| Script | Purpose |
|--------|---------|
| `backup.sh` | Database and configuration backup |
| `restore_test.sh` | Periodic restore verification |
| `health_check.sh` | Service health monitoring |

## Backup Pattern (Example)

A typical backup strategy includes:

1. **Database dumps** — PostgreSQL, Neo4j, vector stores
2. **Configuration files** — System and application configs
3. **Application data** — Persistent volumes
4. **Rotation** — Old backups pruned after retention period
5. **Notifications** — Success/failure alerts

### Backup Schedule (Example)

- **Daily:** Full backup to local storage
- **Weekly:** Replicated to off-site/cloud storage
- **Monthly:** Archive (long-term retention)

### Restore Verification

Periodically verify that backups can actually be restored:
- Spin up a test database instance
- Restore from latest backup
- Validate data integrity (row counts, schema checks)
- Notify on success or failure

## Security Notes

- Scripts should never contain hardcoded credentials
- Use environment variables or a secrets manager for all secrets
- Backup destinations should be encrypted at rest
- Access to backup scripts and data should be restricted

---

*This is an example pattern. Your production operations configuration belongs in a private repository.*
