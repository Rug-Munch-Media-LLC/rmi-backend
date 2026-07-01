# Contributing to RMI (Rug Munch Intelligence)

Thank you for your interest in contributing. RMI is a multi-chain crypto intelligence platform
providing real-time scam detection, token security scanning, wallet analysis, and market intelligence
across 96 blockchains.

## Quick Start

1. **Read AGENTS.md first** — it contains the architecture rules and development constraints.
2. Clone the repo and set up a virtual environment:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```
3. Run the test suite to verify your environment:
   ```bash
   python3 -m pytest tests/unit/ -x --tb=short
   ```

## Code Standards

- **Formatter:** `ruff format app/`
- **Linter:** `ruff check --fix app/`
- **Type checker:** `mypy app/core/ app/domain/ --ignore-missing-imports`
- **No file > 500 lines.** Split god files into domain modules.
- **No bare `except:`** — always use `except Exception:`.
- **No `print()` in app code** — use `from app.core.logging import get_logger; log = get_logger(__name__)`.
- **No sync HTTP calls** — use `httpx.AsyncClient` or `app.core.http.http_client`.
- **Never add to `main.py`** — it is the entry point only. New code goes in `app/domain/*` or `app/core/*`.

## PR Process

1. Create a feature branch: `git checkout -b feat/your-change`
2. Make changes following the code standards above.
3. Add tests for new functionality (target: 80% coverage on new code).
4. Run the full test suite: `python3 -m pytest tests/ -x`
5. Commit with a clear message (conventional commits preferred).
6. Push and open a PR with a description of what changed and why.

## Where to Start

- Look for issues labeled `good-first-issue` or `help-wanted`.
- Improve test coverage on domain modules.
- Add type hints to untyped public functions.
- Fix lint errors (`ruff check app/`).
- Improve documentation.

## Questions

Open a GitHub issue or discussion if you're unsure about something. We're happy to help.
