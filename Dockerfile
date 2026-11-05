FROM node:22-bookworm-slim AS ui-build

WORKDIR /app/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="vsniper" \
    org.opencontainers.image.description="Vinted listing discovery that learns your taste and alerts on Telegram" \
    org.opencontainers.image.source="https://github.com/janishahn/VSniper"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    API_HOST=127.0.0.1 \
    API_PORT=8000 \
    VNC_PORT=6080 \
    DISPLAY=:99 \
    SQLITE_PATH=/data/sqlite/vsniper.db \
    UPLOAD_DIR=/data/uploads \
    CACHE_DIR=/data/cache \
    FEEDBACK_ASSET_DIR=/data/feedback-assets \
    VINTED_BROWSER_PROFILE_DIR=/data/browser-profile \
    VINTED_BROWSER_EXECUTABLE=/usr/bin/chromium

# Chromium for Add-by-URL imports, on a virtual display that noVNC exposes so a
# browser challenge can be solved by hand.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        novnc \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY --from=ui-build /app/ui/dist ./ui/dist
COPY docker/entrypoint.sh /entrypoint.sh

RUN useradd --uid 1000 --create-home vsniper \
    && mkdir -p /data \
    && chown vsniper:vsniper /data \
    && chmod +x /entrypoint.sh
USER vsniper
VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
