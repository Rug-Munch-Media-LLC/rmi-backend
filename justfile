# RMI Development — Justfile
# https://github.com/casey/just
#
# Quick reference:
#   just              # show available commands
#   just lint         # run all linters
#   just test         # run tests
#   just sync         # sync code to VPS
#   just deploy       # sync + restart backend

default:
    @just --list

# === CODE QUALITY ===

lint:
    ruff check app/
    ruff format --check app/

format:
    ruff format app/
    ruff check --fix app/

typecheck:
    mypy app/ --ignore-missing-imports

# === TESTING ===

test:
    pytest -x -v --tb=short

test-fast:
    pytest -x -q -n auto

# === SYNC & DEPLOY ===

sync:
    bash scripts/sync.sh all

sync-backend:
    bash scripts/sync.sh backend

sync-frontend:
    bash scripts/sync.sh frontend

deploy: sync
    ssh -i ~/.ssh/id_ed25519 root@167.86.116.51 "docker restart rmi_backend"
    @echo "Deployed + restarted"

# === DOCKER (local) ===

docker-build:
    docker build -t rmi-backend:dev .

docker-run:
    docker run -p 8000:8000 --env-file .env rmi-backend:dev

# === WATCH ===

watch-backend:
    watchexec -e py -w app/ -w main.py -- "bash scripts/sync.sh backend"

watch-frontend:
    cd ../rmi-frontend && npm run dev

# === UTILS ===

clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete

stats:
    @echo "Python files: $$(find app -name '*.py' | wc -l)"
    @echo "Lines: $$(find app -name '*.py' -exec cat {} + | wc -l)"
    @echo "Tests: $$(find . -name 'test_*.py' | wc -l)"

check-vps:
    @ssh -i ~/.ssh/id_ed25519 root@167.86.116.51 "docker ps --filter name=rmi_backend --format '{{.Status}}' && curl -s localhost:8000/live"

# === FRONTEND ===

sync-frontend-src:
    scp -i ~/.ssh/id_ed25519 ~/rmi/rmi-frontend/src/services/rmi/api.ts root@167.86.116.51:/root/frontend/src/services/rmi/api.ts
    @echo "Frontend api.ts synced"

build-deploy-frontend:
    ssh -i ~/.ssh/id_ed25519 root@167.86.116.51 'cd /root/frontend && npm run build 2>&1 | tail -3 && cp -r dist/* /var/www/rmi/ && echo "Frontend deployed"'

deploy-all: sync sync-frontend-src build-deploy-frontend
    @echo "Full deploy complete"

# === CI/CD ===

ship:
    bash ~/rmi/scripts/cicd.sh

ship-fast:
    bash ~/rmi/scripts/cicd.sh --skip-tests

ship-code:
    bash ~/rmi/scripts/cicd.sh --skip-deploy
