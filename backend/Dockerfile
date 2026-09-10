# Cylist's images. Built from the repository root, not from backend/:
#
#   docker build -f backend/Dockerfile -t cylist .              # production
#   docker build -f backend/Dockerfile --target dev -t cylist-dev .
#
# The context is the root because production is a single container holding both
# halves — FastAPI serves /api/v1 and hands every other path the built SPA. One
# process, one port, and a first-party session cookie.
#
# Development does not use the production stage: docker-compose.yml runs this
# file's `dev` stage and frontend/Dockerfile, each reloading its own half.

# --- Build the web app -----------------------------------------------------
FROM node:22-alpine AS web

WORKDIR /web

# Dependencies first: this layer survives every change that is not to the
# lockfile. `npm ci` rather than `install`, so the build is the lockfile.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Type-checks as well as bundles: a production image should not be buildable
# from a frontend that does not compile.
RUN npm run build

# --- Development API -------------------------------------------------------
# The venv lives outside /app because compose bind-mounts the source tree over
# it, which would otherwise shadow the installed dependencies. Dev dependencies
# are included so pytest, ruff and mypy can be run inside the container.
FROM python:3.12-slim AS dev

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project

# The source arrives as a bind mount, and /data as a volume.
ENV CYLIST_DATA_DIR=/data
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- Production ------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first: this layer is cached until the lockfile changes.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
RUN uv sync --frozen --no-dev

# The SPA, served by app.spa at every path the API does not claim.
COPY --from=web /web/dist ./web

RUN useradd --system --uid 10001 cylist && mkdir -p /data && chown cylist /data
USER cylist

ENV CYLIST_DATA_DIR=/data \
    CYLIST_WEB_DIR=/app/web
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/v1/health').status==200 else 1)"

# The WebSocket parameters are spelled out rather than inherited, because two
# of them are load-bearing and neither is obvious from the app's own code.
#
# The ping pair is the drop-detection budget: an agent's connection that dies
# without saying so surfaces as a disconnect within about forty seconds, and
# that number is the reason a card can be trusted. It also has to stay well
# under any idle timeout a proxy in front of us might have.
#
# --ws-max-size is a security cap, not a tuning knob. It defaults to 16 MiB,
# which is how much an unauthenticated peer on a public funnel could make this
# process buffer before anyone had checked who they were. Nothing in the
# protocol is within three orders of magnitude of 64 KiB.
#
# No --workers. See DEPLOY.md: the connection registry is in-process, and a
# second worker would split it silently.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--ws-ping-interval", "20", "--ws-ping-timeout", "20", \
     "--ws-max-size", "65536"]
