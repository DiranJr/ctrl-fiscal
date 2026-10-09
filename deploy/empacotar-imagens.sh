#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — constrói e EMPACOTA as imagens de uma versão FORA da VPS (máquina de build: Linux ou Git Bash no
# Windows com Docker). A VPS só recebe o pacote e faz "docker load" (deploy/implantar.sh); nada é construído lá.
#
# Uso:  deploy/empacotar-imagens.sh <VERSAO> [pasta-de-saida]
#   VERSAO  SHA curto do commit em HEAD (git rev-parse --short=12 HEAD); a árvore precisa estar limpa.
#   PLATAFORMA=linux/amd64 (padrão) ou linux/arm64 — deve ser a arquitetura da VPS (uname -m lá).
#
# Saída (padrão: ./pacotes/, ignorada pelo Git):
#   ctrlfiscal-imagens-<VERSAO>.tar.gz   docker save de ctrlfiscal/{core-api,fiscal-api,fiscal-worker,nfse-api,
#                                        web,painel-fiscal}:<VERSAO> + imagens base fixadas por digest
#                                        (mysql:8.4 e nginxinc/nginx-unprivileged:1.28-alpine)
#   ctrlfiscal-imagens-<VERSAO>.txt      manifesto: commit, data, plataforma, id/rótulos de cada imagem
#   ctrlfiscal-imagens-<VERSAO>.SHA256SUMS  sha256 dos dois arquivos acima (conferido pelo implantar.sh)
#
# As imagens recebem rótulos OCI (org.opencontainers.image.revision = commit completo, version = VERSAO, created).
# Transfira os três arquivos à VPS por SSH (scp/rsync); o pacote não contém segredos nem dados.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

VERSAO_ARG="${1:-}"
SAIDA="${2:-$RAIZ/pacotes}"
PLATAFORMA="${PLATAFORMA:-linux/amd64}"
[[ "$VERSAO_ARG" =~ ^[0-9a-f]{7,40}$ ]] || morrer "uso: $0 <VERSAO = SHA curto do commit> [pasta-de-saida]"
exigir_comando docker git gzip sha256sum

COMMIT="$(git -C "$RAIZ" rev-parse HEAD)"
[[ "$COMMIT" == "$VERSAO_ARG"* ]] || morrer "HEAD ($COMMIT) não corresponde a $VERSAO_ARG; faça checkout da versão"
[[ -z "$(git -C "$RAIZ" status --porcelain --untracked-files=no)" ]] || \
  morrer "árvore com alterações não commitadas: a imagem não corresponderia ao commit"

export VERSAO="$VERSAO_ARG" IMAGE_TAG="$VERSAO_ARG" CTRL_COMMIT="$COMMIT"
CTRL_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; export CTRL_BUILD_DATE
export DOCKER_DEFAULT_PLATFORM="$PLATAFORMA"

msg "construindo ctrlfiscal/*:$VERSAO para $PLATAFORMA (commit $COMMIT)"
# Valores fictícios do exemplo só satisfazem a interpolação do Compose; nenhum segredo é necessário no build.
docker compose -p ctrlfiscal-build --project-directory "$RAIZ" --env-file "$RAIZ/deploy/env.producao.exemplo" \
  -f "$RAIZ/compose.yaml" -f "$RAIZ/deploy/compose.prod.yaml" --profile ativo build --pull \
  core-api fiscal-api fiscal-worker nfse-api web painel-fiscal

IMAGENS=()
for s in "${SERVICOS_IMAGEM[@]}"; do
  img="ctrlfiscal/$s:$VERSAO"
  rev="$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$img")"
  [[ "$rev" == "$COMMIT" ]] || morrer "$img sem o rótulo de revisão esperado ($rev)"
  IMAGENS+=("$img")
done
for base in "$IMAGEM_MYSQL" "$IMAGEM_GATEWAY"; do
  docker image inspect "$base" >/dev/null 2>&1 || docker pull --platform "$PLATAFORMA" "$base"
  IMAGENS+=("$base")
done

mkdir -p "$SAIDA"
PACOTE="$SAIDA/ctrlfiscal-imagens-$VERSAO.tar.gz"
MANIF="$SAIDA/ctrlfiscal-imagens-$VERSAO.txt"
msg "docker save (${#IMAGENS[@]} imagens) -> $PACOTE"
docker save "${IMAGENS[@]}" | gzip -6 > "$PACOTE"
{
  echo "versao=$VERSAO"
  echo "commit=$COMMIT"
  echo "construido_em=$CTRL_BUILD_DATE"
  echo "plataforma=$PLATAFORMA"
  for img in "${IMAGENS[@]}"; do
    case "$img" in
      ctrlfiscal/*) docker image inspect -f "imagem=$img id={{.Id}} arquitetura={{.Architecture}} revisao={{index .Config.Labels \"org.opencontainers.image.revision\"}}" "$img" ;;
      *)            docker image inspect -f "imagem=$img id={{.Id}} arquitetura={{.Architecture}} (imagem base fixada por digest)" "$img" ;;
    esac
  done
} > "$MANIF"
SOMAS="$SAIDA/ctrlfiscal-imagens-$VERSAO.SHA256SUMS"
( cd "$SAIDA" && sha256sum -- "$(basename "$PACOTE")" "$(basename "$MANIF")" > "$(basename "$SOMAS")" )
msg "pacote pronto: $PACOTE ($(du -h "$PACOTE" | cut -f1))"
msg "envie à VPS:  scp $PACOTE $MANIF $SOMAS usuario@vps:/opt/ctrlfiscal/pacotes/"
