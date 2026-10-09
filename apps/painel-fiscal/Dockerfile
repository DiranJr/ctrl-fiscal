# Multi-stage Dockerfile para apps/painel-fiscal (React/Vite)
FROM node:20-alpine AS builder

WORKDIR /app

# Copia manifests do monorepo
COPY package.json package-lock.json* ./
COPY packages/api-client/package.json ./packages/api-client/
COPY packages/ui/package.json ./packages/ui/
COPY apps/web/package.json ./apps/web/
COPY apps/painel-fiscal/package.json ./apps/painel-fiscal/

RUN npm install

# Copia código-fonte
COPY packages ./packages
COPY apps/painel-fiscal ./apps/painel-fiscal

# Build da aplicação
RUN npm run build --workspace=@ctrl-fiscal/api-client
RUN npm run build --workspace=@ctrl-fiscal/ui
RUN npm run build --workspace=@ctrl-fiscal/painel-fiscal

# Stage de Produção: nginx SEM privilégios (usuário 101, porta 8080), mesma imagem fixada do gateway
FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8 AS runner

COPY apps/painel-fiscal/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/apps/painel-fiscal/dist /usr/share/nginx/html/fiscal

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
