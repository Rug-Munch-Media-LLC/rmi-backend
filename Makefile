.PHONY: help install dev build lint format check test typecheck security ci clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies
	pip install -e ".[dev]"
	pip install -r requirements.txt

dev: ## Start dev server
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

build: ## Build Docker image
	docker build -t rmi-backend .

lint: ## Lint with ruff
	ruff check app/ tests/

format: ## Format with ruff (writes)
	ruff format app/ tests/
	ruff check --fix app/ tests/

check: ## Full check: lint + format + typecheck + test
	ruff check app/ tests/
	ruff format --check app/ tests/
	mypy app/ --ignore-missing-imports
	pytest tests/ -x -q -m "not integration"

typecheck: ## MyPy type check
	mypy app/ --ignore-missing-imports

test: ## Run tests (non-integration)
	pytest tests/ -x -q -m "not integration"

test-all: ## Run ALL tests including integration
	pytest tests/ -q

security: ## Security audit
	bandit -r app/ -q
	ruff check --select S app/ tests/

ci: ## Full CI pipeline
	ruff check app/ tests/
	ruff format --check app/ tests/
	mypy app/ --ignore-missing-imports
	pytest tests/ -x -q -m "not integration"

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache .mypy_cache .ruff_cache