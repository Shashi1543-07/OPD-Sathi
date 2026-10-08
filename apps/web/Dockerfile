# Build the kiosk + staff web app and serve it as static files.
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
# The browser calls the API directly; override when the API is not on localhost:8000.
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npx vite build

FROM nginx:1.27-alpine
COPY infra/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
