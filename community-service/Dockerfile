ARG NODE_IMAGE=node:24-bookworm-slim
FROM ${NODE_IMAGE} AS build
WORKDIR /build
RUN npm install --global pnpm@11.22.0
COPY community-service/package.json community-service/pnpm-lock.yaml community-service/pnpm-workspace.yaml ./community-service/
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./web/
RUN pnpm --dir community-service install --frozen-lockfile && pnpm --dir web install --frozen-lockfile
COPY community-service/src ./community-service/src
COPY community-service/scripts/build-container.mjs ./community-service/scripts/build-container.mjs
COPY web ./web
RUN pnpm --dir community-service build:container

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8787 COMMUNITY_DATABASE=/data/community.sqlite
WORKDIR /app
RUN mkdir /data /backups && chown node:node /data /backups && chmod 700 /data /backups
COPY --from=build --chown=node:node /build/community-service/dist/runtime ./dist/runtime
COPY --chown=node:node community-service/migrations ./migrations
USER node
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["node", "-e", "fetch('http://127.0.0.1:8787/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "dist/runtime/server.mjs"]