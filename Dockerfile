# syntax=docker/dockerfile:1
#
# Multi-stage build for Grok2API. Two independent targets:
#   - backend   : FastAPI/uvicorn proxy + Camoufox/Firefox for account login
#   - admin-ui  : Next.js admin dashboard
# docker-compose.yml builds each service by `target`.
#
# ---------------------------------------------------------------------------
# HEAVY-BROWSER WARNING (backend target)
# The account-login feature (login_camoufox.py) drives a real anti-detect
# Firefox downloaded by `camoufox fetch`. That makes this image large (hundreds
# of MB) and requires the X/GTK system libraries below plus Xvfb for headless
# (virtual-display) runs. The OpenAI-compatible proxy endpoints work WITHOUT a
# browser; only the login flow needs it. If you never trigger login inside the
# container you can drop the apt block and the `camoufox fetch` line and run a
# much smaller image. See DEPLOY.md -> "Catatan Docker & Camoufox".
# ---------------------------------------------------------------------------

############################
# Backend target
############################
FROM python:3.12-slim-bookworm AS backend
# 3.12 is used here for broad wheel availability; the dev box runs 3.14 and the
# code only needs 3.10+ syntax, so both run it fine.

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libraries required by Firefox/Camoufox (equivalent to `playwright install-deps firefox`).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxtst6 libxt6 \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-glib-1-2 libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libxkbcommon0 libxshmfence1 \
    fonts-liberation fonts-unifont xvfb xauth ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Download the Camoufox Firefox build into the image (large). Remove this line
# if you do not need in-container account login.
RUN python -m camoufox fetch

# App source. Runtime data (grok2api.db, media/, .admin_secret) is intentionally
# excluded via .dockerignore and provided through volumes at runtime.
COPY . .

EXPOSE 8168
# Camoufox's virtual headless mode uses Xvfb; the proxy itself needs no display.
CMD ["python", "server.py", "--port", "8168"]


############################
# Admin UI target
############################
FROM node:20-bookworm-slim AS admin-ui
WORKDIR /app/admin-ui

# Install deps (incl. devDependencies — tailwind/typescript/@types are needed to build).
COPY admin-ui/package.json admin-ui/package-lock.json ./
RUN npm ci --include=dev

# Build the production bundle.
COPY admin-ui/ ./
RUN npm run build

ENV NODE_ENV=production \
    PORT=3000
EXPOSE 3000
CMD ["npm", "start"]
