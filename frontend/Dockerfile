# Production frontend image (Day 11C1, docs/architecture.md Section 15
# "illustrative" frontend service made real). Two stages: a Node builder
# that produces a static Vite bundle, and an Nginx runtime that serves it.
# The API stays cross-origin (browser calls its public host URL directly;
# see docker-compose.yml's frontend service and the API's existing
# META_RNE_CORS_ALLOWED_ORIGINS default) — no reverse proxy in this image.

FROM node:24-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Baked into the static bundle at build time (Vite convention) — the
# browser resolves the API from this URL directly, not through this
# container. Override at build time to point a given image at a
# different API host.
ARG VITE_API_BASE_URL=http://localhost:8080
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# No curl/wget installed just for this — nginx:alpine ships wget already.
HEALTHCHECK --interval=2s --timeout=3s --retries=10 --start-period=5s \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
