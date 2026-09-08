# syntax=docker/dockerfile:1
#
# All three base images are pinned to a version tag rather than to a digest, so
# that they stay readable; Dependabot (.github/dependabot.yml) keeps them
# current. `uv` in particular is pinned exactly: it resolves the lockfile, and a
# floating `:latest` could change the build without the lockfile changing.
#
# Three stages, and the runtime starts from a clean base: `uv` is 53 MB of build
# tool the server never calls, so it stays in the `deps` stage and only the
# virtual environment it produces is copied forward.

# ---------------------------------------------------------------------------
# Stage 1 — build the single page app.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS web

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run i18n && npm run build

# ---------------------------------------------------------------------------
# Stage 2 — resolve the Python environment. PyMuPDF has no musl wheels, so this
# is glibc based; it is the same base as the runtime so that the venv it builds
# is valid there, at the same path.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS deps

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so that a source-only change does not re-resolve them.
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --no-editable

# `--no-editable` installs the package into site-packages rather than pointing a
# `.pth` file at the source tree, so the runtime stage needs the venv and
# nothing else.
COPY backend/paperstand ./paperstand
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Stage 3 — runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 paperstand \
    && useradd --uid 1000 --gid 1000 --home-dir /app --no-create-home --shell /usr/sbin/nologin paperstand

WORKDIR /app

COPY --from=deps /app/.venv /app/.venv
COPY --from=web /build/build /app/static
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /library /data && chown paperstand:paperstand /data

ENV PAPERSTAND_STATIC=/app/static \
    PAPERSTAND_LIBRARY=/library \
    PAPERSTAND_DATA=/data \
    PORT=8080

VOLUME ["/data"]
EXPOSE 8080

# `docker stop` sends SIGTERM to PID 1. The entrypoint `exec`s gosu, which
# `exec`s the server, so Uvicorn is PID 1 and handles the signal itself.
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

ENTRYPOINT ["/entrypoint.sh"]
