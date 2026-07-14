# Changelog

All notable changes to the rmi-backend project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Temporal workers for workflow orchestration

### Changed
- Archived legacy dead-code files to `/archive/`

## [2026-07-14]

### Added
- Temporal worker scaffolding for async job processing
- Archive directory for 136 dead-code files removed during Wave 3 audit

## [0.5.0] - 2026-07-10

### Added
- AppError exception hierarchy in `app/core/errors.py` with Pydantic v2-compatible rendering
- `app/main.py` entrypoint delegating to factory pattern
- Installable Python package declaration for `app/`
- `.gitleaksignore` + `.gitleaks.toml` for secret scanning
- Pre-commit hooks wired into `make install`
- CI gating jobs (lint, typecheck, test, security) split from informational jobs
- Test `__init__.py` for package discovery
- YAML issue forms (bug report, feature request)

### Changed
- **Major refactor (Phase 4):** Moved all domain code into `app/domains/`:
  - `app/billing/` + `app/facilitators/` → `app/domains/billing/`
  - `app/wallet/` → `app/domains/wallet/`
  - `app/auth/` → `app/domains/auth/`
  - `app/tokens/` → `app/domains/tokens/`
  - `app/providers/` + `_generated/` → `app/domains/databus/`
  - `app/telegram_bot/` → `app/domains/telegram/rugmunchbot/`
  - `app/scanners/` → `app/domains/scanners/`
  - `app/domain/` renamed to `app/domains/`
- **God-file splits (Phase 3):** Broke 5 monolithic modules into packages:
  - `x402_tools.py` (5,780 LOC) → class-based middleware in `app/billing/x402/`
  - `wallet.py` (2,321 LOC) → `app.wallet` package
  - `telegram_bot.py` (2,097 LOC) → `rugmunchbot` package
  - `auth.py` (1,223 LOC) → `auth` package
  - `databus` (2,991 LOC) → `providers` package
  - `tokens` (1,418 LOC) → `tokens` package
  - `provider_chains.py` → `_generated` package
- 1,175 lint errors eliminated via ruff auto-fix + manual cleanup
- Health checks use env vars instead of hardcoded localhost
- Wire routers for Waves 1-3 of audit mounting

### Fixed
- `jwt_secret` is now required and fails fast in production
- `CryptContext` import for bcrypt backup codes (dropped passlib)
- Fresh httpx client per qdrant health check (stale connection pool)
- `HEALTH_CHECK_DURATION` constant and `test_factory_has_minimum_routes`

### Security
- Removed hardcoded API keys — env-reference instead
- Removed hardcoded ClickHouse password fallback from status page
- Ignored scammer Telegram tokens in CryptoScamDB data via gitleaks

### Removed
- `passlib` dependency (replaced by direct bcrypt)
- `ruff.toml` — `pyproject.toml` [tool.ruff] is canonical

## [0.4.0] - 2026-06-20

### Added
- First-class Makefile with lint/test/check/commit targets
- `.env.example` for local development
- Forgejo CI workflow (`.forgejo/workflows/ci.yml`)

### Changed
- Applied fleet-template standards: `.editorconfig`, `.gitattributes`
- Rewrote README with product, architecture, roadmap, TODO sections
- Tightened `qdrant-audit` CI gate

### Security
- Ignored stale IP references across codebase
- Updated issue templates with security-focused prompts

## [0.3.0] - 2026-06-01

### Added
- Initial production monitoring stack
- Celery task queue integration with Redis broker
- Ponder indexer data integration

### Changed
- Migrated SQLAlchemy to 2.0 async pattern
- Restructured project layout

### Fixed
- Redis connection pooling under load
- Celery task retry backoff timing
