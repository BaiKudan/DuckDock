FROM node:22-bookworm

# Optional local image for the OpenClaw official Docker sandbox backend.
# DuckDock runs openclaw/clawhub on the host (or runner host) and asks
# OpenClaw to use this image when sandbox isolation is enabled.

RUN npm install -g openclaw@latest clawhub@latest \
    && npm cache clean --force

WORKDIR /sandbox
