# Drake web image.
#
# The bundle contains NO API origin. In production the browser calls
# same-origin `/v1` and the ingress routes it straight to the API, so
# there is nothing here to point at a Kubernetes Service name — and
# nothing that would leak an internal host into client JavaScript.

# node:22-bookworm-slim
FROM node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS build

ENV PNPM_HOME="/pnpm" PATH="/pnpm:$PATH"
RUN corepack enable

WORKDIR /build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages ./packages
COPY apps/web ./apps/web

RUN pnpm install --frozen-lockfile --filter @drake/web...
# Production mode drops the dev-only /v1 rewrite (ADR-0021).
ENV DRAKE_DEPLOYMENT_MODE=production
RUN pnpm --filter @drake/web build

# node:22-bookworm-slim (same digest as the builder)
FROM node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

RUN groupadd --gid 65532 nonroot && \
    useradd --uid 65532 --gid 65532 --no-create-home --shell /usr/sbin/nologin nonroot

WORKDIR /app
# Next's standalone bundle: the server plus only the modules it actually
# needs. Copying the workspace node_modules instead would depend on pnpm's
# symlink layout surviving a cross-stage COPY, which it does not.
COPY --from=build /build/apps/web/.next/standalone ./
COPY --from=build /build/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /build/apps/web/public ./apps/web/public

ENV NODE_ENV=production \
    DRAKE_DEPLOYMENT_MODE=production \
    PORT=3000

USER 65532:65532
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
