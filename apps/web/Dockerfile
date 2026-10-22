# Build the kiosk + staff web app and serve it as static files.
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
# The browser calls the API through this container's nginx (/api -> api:8000), so the
# kiosk, staff screens and patients' phones all use one address.
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL
RUN npx vite build

# Runs as an unprivileged user (uid 101) and listens on 8080; production mounts
# infra/web/nginx.tls.conf as default.conf and serves HTTPS on 8443.
FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0
COPY infra/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/web/security-headers.conf /etc/nginx/conf.d/security-headers.inc
COPY infra/web/api-proxy.inc /etc/nginx/conf.d/api-proxy.inc
COPY infra/web/tls-headers.inc /etc/nginx/conf.d/tls-headers.inc
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080 8443
