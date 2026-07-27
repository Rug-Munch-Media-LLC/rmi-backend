# RMI Development Standards

## BACKEND DEVELOPMENT

### Pre-commit checklist
- Run linting and formatting tools before every commit
- Check for hardcoded secrets, env var consistency, stale paths
- Ensure all tests pass

### Environment variables
- All configuration via environment variables (never hardcoded)
- Use `.env.example` as template with documented defaults
- Never commit `.env` files to git

### Live development
- Volume mounts enable instant code reload
- Use `uvicorn --reload` for auto-restart during development

---

## WEB3 SECURITY BEST PRACTICES

### Secrets management
- **NO hardcoded secrets** in any `.py` file
- App passwords preferred over account passwords
- Rotate API keys quarterly

### Key scanning
- Run secret scanning before any commit
- Use gitleaks or equivalent tools in CI

### RPC security
- Use dedicated RPC URLs, never public endpoints in production
- Rate limit all on-chain queries
- Cache blockchain data aggressively

---

## CODE QUALITY

### Python
- Type hints on all public functions
- Docstrings for modules and classes
- Async/await for all I/O operations
- Use Pydantic for data models

### TypeScript (Frontend)
- Components follow the framework conventions
- Services are organized by domain
- Shared types in a central types module
