FROM python:3.11-slim

WORKDIR /app

# System deps + solc + Foundry + Tini (consolidated for smaller layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl git ca-certificates && \
    curl -sL https://github.com/krallin/tini/releases/download/v0.19.0/tini -o /tini && \
    chmod +x /tini && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Solidity compiler (kept — used by contract scanners)
RUN curl -sL https://github.com/ethereum/solidity/releases/download/v0.8.26/solc-static-linux -o /usr/local/bin/solc && \
    chmod +x /usr/local/bin/solc

# Foundry (cast, forge) — EVM contract analysis
RUN curl -sL https://foundry.paradigm.xyz | bash && \
    export PATH="$HOME/.foundry/bin:$PATH" && \
    foundryup

# Python deps (ordered for layer caching: requirements first, then app)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir slither-analyzer && \
    rm -rf /root/.cache/pip

# Copy app (HF models excluded via .dockerignore — they download at runtime)
COPY . .

# Health check (uses /live for liveness, not /ready)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Security: run as non-root user
RUN groupadd -r rmi && useradd -r -g rmi -s /bin/false rmi && chown -R rmi:rmi /app
USER rmi

EXPOSE 8000

# Use Tini as PID 1 to properly reap zombie processes and forward signals
ENTRYPOINT ["/tini", "--"]
CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "--bind", "0.0.0.0:8000", "--timeout", "120", "--keep-alive", "5", "--worker-tmp-dir", "/dev/shm"]

# Security hardening (applied at runtime via docker run --read-only --cap-drop=ALL):
#   --read-only                    Makes rootfs read-only (writes go to /tmp, /dev/shm)
#   --cap-drop=ALL                 Drops all Linux capabilities
#   --security-opt=no-new-privileges  Prevents privilege escalation
#   --tmpfs /tmp                   Writable tmpfs for runtime temp files
#   --tmpfs /dev/shm               Writable tmpfs for gunicorn worker temp dir
# These are documented in the startup-order guide at docs/production/startup-order.md
