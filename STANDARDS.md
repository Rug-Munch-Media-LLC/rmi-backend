# RMI Development Standards — AI-Forward + Web3 Best Practices

## ⚠️ FIRST: Read /root/DEVELOPERS.md for canonical paths.

---

## BACKEND DEVELOPMENT

### Pre-commit checklist (run before every commit):
```bash
bash /root/backend/scripts/pre-commit.sh
```
Checks: Python syntax, hardcoded secrets, env var consistency, stale path references.

### Environment variables:
```bash
# Auto-generate from Hermes config:
python3 /root/backend/generate_env.py --force
# Then fill in missing values:
nano /root/backend/.env
```

### Live development (no rebuild needed):
```bash
# Volume mount means code changes are instant:
docker restart rmi-backend
# Verify:
curl http://localhost:8000/health
```

### Adding new env vars:
1. Add to code: `os.getenv("MY_VAR")`
2. Add to `/root/backend/.env.example` with comment
3. Run `python3 /root/backend/generate_env.py --force`
4. Add to `/srv/rugmuncher-backend/docker-compose.yml` if container needs it

---

## AI AGENT DEVELOPMENT WORKFLOW

This system is designed for AI-assisted development. Here's the stack:

```
hermes-agent (CLI)
    │
    ├── Terminal tool → docker exec, git, curl, python
    ├── Web tool → API testing, research
    ├── File tool → Edit /root/backend/ directly
    ├── Delegate → Spawn sub-agents for parallel work
    └── Cron jobs → Automated tasks
```

### How Hermes develops the backend:
1. **Discover**: Reads AGENTS.md in /root/backend/
2. **Edit**: Patches files directly (volume mount = live)
3. **Test**: `curl localhost:8000/health` after changes
4. **Rebuild**: `docker compose build && docker compose up -d`
5. **Verify**: Checks logs, API responses

### n8n workflow development:
- UI: http://localhost:5678 (admin / RugMuncher2024)
- Direct DB: `sqlite3 /root/n8n-data/database.sqlite`
- Import: Copy workflow JSONs into `/root/n8n-workflows/`
- Test: Check execution history in UI

### Orchestrator swarm:
- API: http://localhost:8081
- Health: `curl http://localhost:8081/health`
- Bots: `curl http://localhost:8081/orchestrator/bots`
- Create task: `POST /orchestrator/task`

---

## WEB3 SECURITY BEST PRACTICES

### Secrets management:
- **NO hardcoded secrets** in any `.py` file
- All secrets in `/root/.secrets/` or `/root/.hermes/.env`
- App passwords preferred over account passwords
- Rotate API keys quarterly

### Key scanning:
```bash
# Run before any commit:
grep -rn '0x[0-9a-fA-F]\{64\}\|sk-[a-zA-Z0-9]\{20,\}' /root/backend/app/ --include='*.py'
```

### RPC security:
- Use dedicated RPC URLs, never public endpoints in production
- Rate limit all on-chain queries
- Cache blockchain data aggressively (Redis)

---

## CODE QUALITY

### Python:
- Type hints on all public functions
- Docstrings for modules and classes
- Async/await for all I/O operations
- Use Pydantic for data models

### TypeScript (Frontend):
- Components in `/srv/rugmuncher-backend/rmi-frontend/src/components/`
- Services in `/srv/rugmuncher-backend/rmi-frontend/src/services/`
- Types shared via `/srv/rugmuncher-backend/rmi-frontend/src/types.ts`

---

## MONITORING

### Health checks:
```bash
# All services:
curl http://localhost:8000/health      # Backend
curl http://localhost:8081/health      # Orchestrator
curl http://localhost:5678/healthz     # n8n
curl http://localhost:9001/api/health  # Listmonk
```

### Logs:
```bash
docker logs rmi-backend --tail 50
docker logs rmi-n8n --tail 50
journalctl -u hermes -n 50
```

### Cron jobs:
```bash
# List all:
cronjob action='list'
# Check status of specific job:
cronjob action='list'  # look for last_status
```

---

## DEPLOYMENT

### Full stack restart:
```bash
cd /srv/rugmuncher-backend
docker compose down
docker compose up -d
```

### Rebuild with cache clear:
```bash
docker compose build --no-cache backend worker orchestrator
docker compose up -d
```

### Rollback (if something breaks):
```bash
# Restore backup:
cp /root/backups/n8n/$(date +%Y-%m)/database.sqlite /root/n8n-data/
docker restart rmi-n8n

# Rebuild from known-good commit:
cd /root/backend && git checkout <commit-hash>
docker restart rmi-backend
```
