#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — rollback de IMAGENS para uma versão anterior (manual).
#
# Uso:  sudo deploy/rollback.sh [VERSAO_ANTERIOR] [--confirmar]
#       sem argumento, usa a "versão atual" da última linha de $ESTADO_DIR/historico-implantacoes.tsv.
#
# O QUE ESTE SCRIPT FAZ: volta os containers do projeto Compose "ctrlfiscal" (e só dele) para as imagens
# ctrlfiscal/<svc>:<VERSAO_ANTERIOR>, que JÁ ESTÃO carregadas na VPS (o implantar.sh não apaga imagens antigas;
# se faltarem, carregue o pacote daquela versão com: gunzip -c ctrlfiscal-imagens-<V>.tar.gz | docker load),
# e atualiza VERSAO no .env. Não reinicia o Docker nem o Nginx do host.
#
# O QUE ELE NÃO FAZ (e por quê):
#   - NÃO desfaz migrações. As migrações Alembic aplicadas pela versão nova continuam no banco. Antes de trocar as
#     imagens, o script compara a revisão gravada em cada banco (alembic_version) com a última migração conhecida
#     pela imagem antiga; se o banco estiver À FRENTE, o rollback é RECUSADO: código antigo sobre esquema novo pode
#     falhar ou corromper dados. Nesse caso é preciso análise (correção para frente é o caminho normal).
#   - NÃO restaura backup. Restaurar um backup por cima do banco em uso é PROIBIDO quando já existem documentos
#     fiscais novos (notas sincronizadas do ADN, rascunhos, pedidos de cancelamento, eventos, auditoria): eles se
#     perderiam e o sistema ficaria divergente do Sistema Nacional. Rollback de banco só com análise caso a caso,
#     restaurando primeiro num MySQL temporário (deploy/restaurar-verificado.sh) e reconciliando o que for novo.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

ALVO=""; CONFIRMAR=false
for a in "$@"; do
  case "$a" in
    --confirmar) CONFIRMAR=true ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) ALVO="$a" ;;
  esac
done
[[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
exigir_comando docker

ATUAL="$(env_valor VERSAO)"
if [[ -z "$ALVO" ]]; then
  [[ -s "$ESTADO_DIR/historico-implantacoes.tsv" ]] || morrer "sem histórico; informe a versão anterior"
  ALVO="$(tail -n 1 "$ESTADO_DIR/historico-implantacoes.tsv" | cut -f2)"
fi
[[ "$ALVO" =~ ^[0-9a-f]{7,40}$ ]] || morrer "versão alvo inválida: '$ALVO'"
[[ "$ALVO" != "$ATUAL" ]] || morrer "a versão alvo ($ALVO) já é a atual"

for s in "${SERVICOS_IMAGEM[@]}"; do
  docker image inspect "ctrlfiscal/$s:$ALVO" >/dev/null 2>&1 || morrer "imagem ausente: ctrlfiscal/$s:$ALVO (não é possível voltar sem ela)"
done
container_rodando "$MYSQL_CONTAINER" || morrer "MySQL parado: não há como conferir as migrações"

# ---- O esquema do banco precisa ser conhecido pela versão antiga --------------------------------------------
recusar=0
for par in core-api:ctrl_core fiscal-api:ctrl_fiscal nfse-api:ctrl_nfse; do
  img="ctrlfiscal/${par%%:*}:$ALVO"; banco="${par##*:}"
  cabeca_antiga="$(docker run --rm --network none --entrypoint alembic "$img" heads 2>/dev/null | awk '{print $1}' | head -n 1)"
  no_banco="$(printf 'SELECT version_num FROM %s.alembic_version;\n' "$banco" | mysql_exec "$MYSQL_CONTAINER" "$PW_SEGREDO" 2>/dev/null || true)"
  msg "$banco: banco em '${no_banco:-?}', versão $ALVO conhece até '${cabeca_antiga:-?}'"
  if [[ -z "$cabeca_antiga" || "$no_banco" != "$cabeca_antiga" ]]; then recusar=1; fi
done
if [[ $recusar -ne 0 ]]; then
  erro "rollback RECUSADO: o banco tem migrações que a versão $ALVO não conhece (ou não foi possível conferir)."
  erro "Não restaure backup sobre documentos fiscais novos. Corrija para frente ou faça análise de rollback de banco."
  exit 1
fi

if ! $CONFIRMAR; then
  read -r -p "Voltar imagens de ${ATUAL:-?} para $ALVO? Digite ROLLBACK: " resp
  [[ "$resp" == "ROLLBACK" ]] || morrer "cancelado pelo operador"
fi

export VERSAO="$ALVO"
dc up -d --no-build
perfil_ativo || dc stop fiscal-worker >/dev/null 2>&1 || true
env_definir VERSAO "$ALVO"

falhou=0
servicos=(core-api fiscal-api nfse-api web painel-fiscal gateway)
perfil_ativo && servicos+=(fiscal-worker)
for s in "${servicos[@]}"; do esperar_saudavel "${PROJETO}-${s}-1" 240 || falhou=1; done
install -d -m 700 "$ESTADO_DIR"
printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "${ATUAL:-?}" "$ALVO" "rollback-$([[ $falhou -eq 0 ]] && echo ok || echo FALHA)" \
  >> "$ESTADO_DIR/historico-implantacoes.tsv"
[[ $falhou -eq 0 ]] || morrer "rollback concluído com falha de saúde; verifique os logs"
msg "rollback para $ALVO concluído"
