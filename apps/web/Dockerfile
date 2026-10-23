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
FROM nginxinc/nginx-unprivileged:1.30.6-alpine@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
# Alpine security updates published since the base image was built.
USER root
RUN apk upgrade --no-cache
COPY infra/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/web/security-headers.conf /etc/nginx/conf.d/security-headers.inc
COPY infra/web/api-proxy.inc /etc/nginx/conf.d/api-proxy.inc
COPY infra/web/tls-headers.inc /etc/nginx/conf.d/tls-headers.inc
COPY --from=build /app/dist /usr/share/nginx/html
# Explicit, so a base-image change can never run the server as root.
USER 101
EXPOSE 8080 8443
