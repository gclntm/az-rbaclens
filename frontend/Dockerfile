# The build runs on the builder's own platform: its output (static files) is the same for
# every target platform, so a multi-platform release build needs no emulation here.
FROM --platform=$BUILDPLATFORM node:24-alpine AS base
WORKDIR /app

FROM base AS deps
# npm ci installs exactly package-lock.json (make lock regenerates it) and checks each hash.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM deps AS build
COPY . .
RUN npm run build

# The same nginx, built to run as its own user (uid 101), not root: it listens
# on 8080 (compose publishes it on the host's port 80) and keeps its pid in /tmp.
FROM nginxinc/nginx-unprivileged:1.30-alpine AS prod
COPY --from=build /app/dist /usr/share/nginx/html
# Rendered at start into /etc/nginx/conf.d/default.conf (see the template's header).
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# Empty unless .env.<env> sets it; must be defined for the template to render.
ENV EXTRA_SERVER_NAMES=""
EXPOSE 8080
