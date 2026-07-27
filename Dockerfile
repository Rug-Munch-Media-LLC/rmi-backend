FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# System deps + solc + Foundry + Tini (consolidated for smaller layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc=4:14.2.0-1 \
    libpq-dev=17.10-0+deb13u1 \
    curl=8.14.1-2+deb13u4 \
    git=1:2.47.3-0+deb13u1 \
    ca-certificates=20250419 && \
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
    pip install --no-cache-dir slither-analyzer==0.11.5 && \
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
