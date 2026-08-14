FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# Build args
#   INSTALL_FACE=1  Install ML face-recognition engines (insightface, deepface,
#                   dlib/face_recognition) + pre-bake ArcFace buffalo_l models.
#                   Adds roughly +1GB (see [REVIEW] below). DEFAULT (unset/0)
#                   ships the OSINT CLI tools + OpenCV Haar cascade only, so
#                   /osint /track /face still work with a lean image.
ARG INSTALL_FACE=0

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

WORKDIR /app

# System deps + solc + Foundry + Tini (consolidated for smaller layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc=4:14.2.0-1 \
    libpq-dev=17.10-0+deb13u1 \
    curl=8.14.1-2+deb13u4 \
    git=1:2.47.3-0+deb13u1 \
    ca-certificates=20250419 \
    whois=5.6.3 \
    libglib2.0-0t64=2.84.4-3~deb13u3 \
    libxcb1=1.17.0-2+b1 && \
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

# ─── OSINT toolchain (default: CLI tools + Haar cascade) ───────────────
# Deployed instances must run every app/osint/* module at full capability:
#   username → sherlock CLI (sherlock-project) + maigret fallback
#   email    → holehe CLI (+ HIBP/emailrep via HTTP)
#   domain   → whois CLI fallback (python-whois already in requirements)
#   face     → OpenCV Haar cascade via opencv-python-headless
# The CLIs are invoked via subprocess (never imported); opencv ships the
# cascade at cv2.data.haarcascades (the path handlers/osint.py reads) and we
# mirror it to /usr/share/opencv4/haarcascades as a stable ops path.
RUN pip install --no-cache-dir \
        sherlock-project==0.16.0 \
        maigret==0.6.4 \
        holehe==1.61 \
        opencv-python-headless==4.13.0.90 \
    && mkdir -p /usr/share/opencv4/haarcascades \
    && curl -fsSL \
        https://raw.githubusercontent.com/opencv/opencv/4.13.0/data/haarcascades/haarcascade_frontalface_default.xml \
        -o /usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml \
    && rm -rf /root/.cache/pip

# [REVIEW] Heavy face-recognition engines (opt-in via INSTALL_FACE=1):
#   insightface + onnxruntime + opencv ≈ +500MB; dlib (build via cmake/g++,
#   consumed by face_recognition) adds ~+200MB; pre-baked ArcFace buffalo_l
#   models add ~+330MB and require network at build time. Default image
#   intentionally excludes these: /face still works via the Haar cascade and
#   app/osint/face.py degrades gracefully (OsintNotAvailable) when no ML
#   engine is present. Enable only on face-heavy deployments.
ENV INSIGHTFACE_HOME=/opt/insightface
RUN if [ "${INSTALL_FACE:-0}" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            cmake=3.31.6-2 \
            g++=4:14.2.0-1 \
            && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
        && pip install --no-cache-dir \
            insightface==1.0.1 \
            onnxruntime==1.28.0 \
            deepface==0.0.100 \
            dlib==20.0.1 \
            face-recognition==1.3.0 \
        && rm -rf /root/.cache/pip \
        && mkdir -p /opt/insightface; \
        # Pre-bake ArcFace buffalo_l so first inference is offline. Scoped to
        # its own statement (NOT part of the && chain above) so a build-time
        # model-download failure cannot mask install failures; non-fatal here
        # because runtime auto-download works with a writable $INSIGHTFACE_HOME
        # (mount a volume at /opt/insightface).
        python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l', root='/opt/insightface', providers=['CPUExecutionProvider'])" \
            || echo "WARN: buffalo_l pre-bake failed — will auto-download at first use (needs writable \$INSIGHTFACE_HOME)"; \
    fi

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
