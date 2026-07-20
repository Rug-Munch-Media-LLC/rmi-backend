.PHONY: help install dev build lint format check test typecheck security quality ci clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	pip install -e ".[dev]"
	pip install -r requirements.txt
	@command -v pre-commit >/dev/null 2>&1 && pre-commit install || echo "  (pre-commit not installed; skipping hook install)"

dev: ## Start dev server
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

build: ## Build Docker image
	docker build -t rmi-backend .

lint: ## Lint with ruff (error/flynt/bug/sim/async rules)
	ruff check app/ --select=E,F,B,SIM,ASYNC --ignore=S104,S311

format: ## Format with ruff (writes)
	ruff format app/ tests/
	ruff check --fix app/ tests/

quality: lint typecheck security test
	@echo "✅ All quality gates passed"

check: ## Full check: lint + format + typecheck + test
	ruff check app/ tests/
	ruff format --check app/ tests/
	mypy app/ --ignore-missing-imports
	python3 -m pytest tests/ -x -q -m "not integration"

typecheck: ## MyPy type check
	mypy app/ --ignore-missing-imports --check-untyped-defs

test: ## Run tests with coverage
	python3 -m pytest tests/ -v --tb=short --cov=app --cov-report=term

test-all: ## Run ALL tests including integration
	python3 -m pytest tests/ -q

test-ci: ## Tests with coverage (CI gate)
	python3 -m pytest tests/ -v --tb=short --cov=app.domains.scanner --cov=app --cov-report=term --cov-fail-under=15

security: ## Security audit with bandit
	bandit -r app/ -ll

ci: ## Full CI pipeline
	ruff check app/ tests/
	ruff format --check app/ tests/
	mypy app/ --ignore-missing-imports
	python3 -m pytest tests/ -x -q -m "not integration"

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache
