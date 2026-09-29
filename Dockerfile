# syntax=docker/dockerfile:1
#
# OpenDot image — multi-stage:
#   1) build the React/Vite web app (node)
#   2) python runtime with Playwright's Chromium deps, running as a non-root user
#
# Not yet built in CI — if `docker build` trips, please open an issue.

# ---------- stage 1: build the web app ----------
FROM node:20-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Playwright's Chromium needs these system libs (see playwright.dev/python/docs/docker —
# this list matches what `playwright install-deps` would otherwise pull in for Debian).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git bash \
      libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
      libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
      fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Non-root user; its $HOME also holds the Playwright browser cache.
RUN useradd --create-home --shell /bin/bash dot
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY opendot/ ./opendot/
COPY skills/ ./skills/
COPY dot.sh ./dot.sh
COPY --from=web-build /web/dist ./web/dist

# Install Chromium as the `dot` user so it lands in /home/dot/.cache (writable at runtime).
RUN mkdir -p /app/data /app/logs && chown -R dot:dot /app
USER dot
RUN python -m playwright install chromium

# Inside the container we bind every interface; docker-compose is what restricts the
# published port to 127.0.0.1 on the host — see docker-compose.yml.
ENV DOT_HOST=0.0.0.0 \
    DOT_PORT=7878 \
    DOT_DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1

VOLUME ["/app/data"]
EXPOSE 7878

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${DOT_PORT}/api/health" || exit 1

ENTRYPOINT ["python", "-m", "opendot"]
CMD ["serve", "--host", "0.0.0.0", "--port", "7878"]
