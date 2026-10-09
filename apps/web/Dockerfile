# Multi-stage Dockerfile para apps/web (Next.js)
FROM node:20-alpine AS builder

WORKDIR /app

# Copia manifests do monorepo para cache de dependências
COPY package.json package-lock.json* ./
COPY packages/api-client/package.json ./packages/api-client/
COPY packages/ui/package.json ./packages/ui/
COPY apps/web/package.json ./apps/web/
COPY apps/painel-fiscal/package.json ./apps/painel-fiscal/

RUN npm install

# Copia código-fonte
COPY packages ./packages
COPY apps/web ./apps/web

# Build dos packages e da aplicação
RUN npm run build --workspace=@ctrl-fiscal/api-client
RUN npm run build --workspace=@ctrl-fiscal/ui
RUN npm run build --workspace=@ctrl-fiscal/web

# Stage de Produção
FROM node:20-alpine AS runner

WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/packages ./packages
COPY --from=builder /app/apps/web ./apps/web

EXPOSE 3000
# Sem root em execução
USER node

CMD ["npm", "run", "start", "--workspace=@ctrl-fiscal/web"]
