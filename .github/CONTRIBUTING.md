# Contributing to RMI Backend

Thank you for your interest in contributing to RMI! This is a commercial security product, and we take security seriously. Please read this guide carefully.

## Table of Contents

- [Development Environment](#development-environment)
- [Code Style](#code-style)
- [Security Requirements](#security-requirements)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Release Process](#release-process)

## Development Environment

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) for package management
- Docker for local development
- Tailscale for secure server access

### Setup

```bash
# Clone the repository
git clone git@github.com:crypto-rug-muncher/rugmuncher-backend.git
cd rugmuncher-backend

# Install dependencies
uv sync --frozen

# Set up environment
cp .env.example .env
# Edit .env with your local credentials

# Run linter
ruff check .

# Run type checker
mypy app/

# Run tests
pytest tests/unit/
```

## Code Style

### Python

- **Format**: Black with 88-character lines
- **Lint**: Ruff with `--select=E,F,I,S,W` rules
- **Types**: Full type hints on all public functions
- **Imports**: Isort with `known_first_party=app`

### Commit Messages

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `perf`

Examples:
```
feat(auth): add JWT token validation middleware
fix(scanner): handle empty response from DexScreener
refactor(databus): consolidate provider initialization
```

## Security Requirements

### Zero Tolerance

1. **No secrets in code** - API keys, tokens, passwords never in source
2. **No debug logging** - Remove print statements, use structlog
3. **No bare except clauses** - Always catch specific exceptions
4. **No sync HTTP calls** - Use httpx async for all external calls

### Security Checklist

Before merging, verify:

- [ ] No hardcoded secrets (run `gitleaks check`)
- [ ] All except clauses are explicit (run `grep -r "except:"`)
- [ ] All HTTP calls are async (run `grep -r "import requests"`)
- [ ] Type hints on all public functions
- [ ] Lint passes (`ruff check .`)
- [ ] Type check passes (`mypy app/`)

### Reporting Vulnerabilities

**DO NOT OPEN A PUBLIC ISSUE.** Email security@rugmunch.io with:
- Type of vulnerability
- Affected endpoint/component
- Steps to reproduce
- Proof of concept (if available)
- Impact assessment

Response time: Within 24 hours.

## Testing

### Unit Tests

```bash
pytest tests/unit/ -v
```

### Integration Tests

```bash
pytest tests/integration/ -v
```

### Test Requirements

- All new features need tests
- Bug fixes need regression tests
- Cover edge cases and error conditions
- Use pytest fixtures for test data

## Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature
   ```

2. **Make your changes**
   - Follow code style guidelines
   - Add tests for new functionality
   - Update documentation as needed

3. **Run checks**
   ```bash
   ruff check . --fix
   mypy app/
   pytest tests/unit/
   ```

4. **Commit your changes**
   ```bash
   git commit -m "feat(scope): add your feature"
   ```

5. **Push and create PR**
   ```bash
   git push origin feature/your-feature
   # Create PR on GitHub
   ```

6. **PR Review**
   - At least one maintainer review required
   - All checks must pass
   - No new lint/type errors introduced

## Release Process

1. **Tag release**
   ```bash
   git checkout main
   git pull
   git tag -a v1.2.3 -m "Release v1.2.3"
   git push origin main --tags
   ```

2. **Create release notes**
   - Summarize changes
   - Note breaking changes
   - List contributors

3. **Deploy**
   - CI/CD automatically deploys to production
   - Monitor logs for errors
   - Run smoke tests

## Questions?

- Open an issue for bugs/features
- Email security@rugmunch.io for security questions
- Check docs/ for API documentation
