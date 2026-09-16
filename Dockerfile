# Single-image deployment: builds the dashboard, then serves it and the API
# from one process on one port. One URL to share, and no CORS to configure.
#
# Prefer this over a serverless function for real workloads: analyzing a large
# repository routinely takes longer than a serverless request budget allows,
# and this image ships a git binary, which enables the higher-fidelity
# repository backend.

# ---- Stage 1: build the React dashboard -----------------------------------
FROM node:20-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: runtime ------------------------------------------------------
FROM python:3.12-slim

# git      - enables the git-CLI repository backend (higher fidelity than the
#            pure-HTTP fallback)
# nodejs   - enables JavaScript syntax checking via `node --check`
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend /build/dist ./frontend/dist

ENV PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    HEALING_AGENT_WORKSPACE=/tmp/healing-agent-workspaces \
    PORT=8000

# Executing a submitted repository's test suite runs that repository's code.
# It stays off unless an operator opts in on a deployment where every
# submitted repository is trusted.
ENV HEALING_AGENT_ALLOW_TEST_EXECUTION=false

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/api/health\")"

# Hosts inject $PORT; the shell form expands it.
CMD uvicorn healing_agent.app:app --host 0.0.0.0 --port ${PORT:-8000}
