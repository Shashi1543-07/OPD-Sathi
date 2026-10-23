# Build the kiosk + staff web app and serve it as static files.
FROM node:26.11.1-bookworm-slim@sha256:86f07bc9c5dce4578cf37e5a418b7bfc7f817cda25cde66e2b66e95ed86c4567 AS build
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
FROM nginxinc/nginx-unprivileged:1.31.7-alpine@sha256:b5047b9b76845eac124358373c6852377c52e4b05834a47647e955dd91cb8f1e
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
