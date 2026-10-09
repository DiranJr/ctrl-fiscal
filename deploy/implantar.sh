#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — implantação MANUAL de uma versão na VPS. Nada aqui roda sozinho (o CI não implanta).
#
# Uso:  sudo deploy/implantar.sh <VERSAO> [--pacote /opt/ctrlfiscal/pacotes/ctrlfiscal-imagens-<VERSAO>.tar.gz]
#                                         [--sem-backup] [--confirmar]
#   VERSAO     SHA curto do commit. As imagens são ctrlfiscal/<svc>:<VERSAO>, geradas FORA da VPS por
#              deploy/empacotar-imagens.sh. Não há build na VPS.
#   --pacote   arquivo do docker save; o .SHA256SUMS ao lado é conferido antes do "docker load".
#              Dispensável se as imagens da versão já estiverem carregadas.
#   --sem-backup  só na PRIMEIRA instalação (banco vazio). Em qualquer outra, o backup cifrado é obrigatório.
#   --confirmar   não pergunta (use só depois de revisar o plano impresso).
#
# Regras de convivência com os outros sistemas da VPS:
#   - todos os comandos usam o projeto Compose "ctrlfiscal" explícito (-p ctrlfiscal); nenhum container, rede,
#     volume ou imagem de outro projeto é tocado; nunca "docker system prune" nem "systemctl restart docker";
#   - a pilha publica SÓ 127.0.0.1:${HTTP_PORT}; a porta precisa estar livre (ou já ser do gateway do CTRL
#     Fiscal) e nunca ser 3001 (outro sistema); o HTTPS é do Nginx do host (deploy/nginx/*.conf).
#
# Passos: pré-checagens -> pacote de imagens (sha256 + docker load) -> backup cifrado -> MySQL -> migrações
#         (credenciais DDL) -> gatilhos de auditoria imutável -> serviços -> saúde -> registro da versão.
# Se a verificação falhar, NADA é desfeito automaticamente: avalie e use deploy/rollback.sh (leia o cabeçalho).
# Ambiente em espera (COMPOSE_PROFILES sem "ativo"): o fiscal-worker é mantido PARADO.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

VERSAO_NOVA=""; PACOTE=""; SEM_BACKUP=false; CONFIRMAR=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pacote) PACOTE="${2:?informe o arquivo do pacote}"; shift 2 ;;
    --sem-backup) SEM_BACKUP=true; shift ;;
    --confirmar) CONFIRMAR=true; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    -*) morrer "opção desconhecida: $1" ;;
    *) [[ -z "$VERSAO_NOVA" ]] || morrer "versão informada duas vezes"; VERSAO_NOVA="$1"; shift ;;
  esac
done
[[ "$VERSAO_NOVA" =~ ^[0-9a-f]{7,40}$ ]] || morrer "informe VERSAO = SHA curto do commit (7 a 40 caracteres hexadecimais)"

# ---------------------------------------------------------------------------------------------- pré-checagens
exigir_comando docker curl awk sed ss sha256sum gunzip
[[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
[[ -f "$ENV_FILE" ]] || morrer "$ENV_FILE ausente (copie deploy/env.producao.exemplo e ajuste)"
if grep -q 'TROQUE-ME' "$ENV_FILE"; then morrer "$ENV_FILE ainda contém valores TROQUE-ME"; fi
[[ "$(stat -c %a "$ENV_FILE")" == "600" ]] || aviso "$ENV_FILE deveria ter modo 600"

v="$(docker compose version --short 2>/dev/null || echo 0)"
awk -v v="$v" 'BEGIN{split(v,a,"."); exit !((a[1]>2)||(a[1]==2&&a[2]>=24))}' || morrer "Docker Compose >= 2.24 necessário (atual: $v)"

# Nginx do host: é ele quem termina o HTTPS. Só verifica; nunca é reiniciado por este script.
command -v nginx >/dev/null 2>&1 || morrer "Nginx do host não encontrado: instale/configure deploy/nginx/*.conf antes"
systemctl is-active --quiet nginx 2>/dev/null || aviso "Nginx do host não está ativo (systemctl status nginx)"

# Porta do gateway: só loopback, livre ou já do gateway do CTRL Fiscal, nunca 3001.
PORTA="$(env_valor HTTP_PORT 8088)"
[[ "$PORTA" =~ ^[0-9]+$ ]] || morrer "HTTP_PORT inválida: $PORTA"
[[ "$PORTA" != "3001" ]] || morrer "HTTP_PORT=3001 é usada por outro sistema; escolha outra porta"
if ss -ltnH "( sport = :$PORTA )" | grep . >/dev/null; then
  dono="$(docker port "${PROJETO}-gateway-1" 80/tcp 2>/dev/null || true)"
  [[ "$dono" == "127.0.0.1:$PORTA" ]] || morrer "127.0.0.1:$PORTA já está em uso por outro processo (ss -ltnp); ajuste HTTP_PORT"
fi
[[ "$(env_valor TRUSTED_PROXY_IP)" =~ ^[0-9.]+$ ]] || morrer "defina TRUSTED_PROXY_IP (10.250.250.1) no .env"

SECRETS_DIR="$(env_valor SECRETS_DIR)"
for s in mysql_root_password.txt core_db_password.txt jwt_private_key.pem jwt_public_key.pem csrf_secret.txt nfse_vault_key.txt; do
  [[ -s "$SECRETS_DIR/$s" ]] || morrer "segredo ausente: $SECRETS_DIR/$s (rode deploy/gerar-segredos.sh)"
done
[[ "$(stat -c %a "$SECRETS_DIR")" == "700" ]] || morrer "$SECRETS_DIR precisa de modo 700"

[[ "$(env_valor NFSE_MOCK_ENABLED false)" == "false" ]] || morrer "NFSE_MOCK_ENABLED precisa ser false em produção"
[[ "$(env_valor ALLOW_INSECURE_DEV_COOKIE false)" == "false" ]] || morrer "ALLOW_INSECURE_DEV_COOKIE precisa ser false"

VERSAO_ATUAL="$(env_valor VERSAO)"
PERFIS="$(env_valor COMPOSE_PROFILES)"

cat <<PLANO
-------------------------------------------------------------------------------
Plano de implantação do CTRL Fiscal (projeto Compose: $PROJETO)
  versão atual : ${VERSAO_ATUAL:-nenhuma}
  versão nova  : $VERSAO_NOVA
  pacote       : ${PACOTE:-(imagens já carregadas)}
  perfis       : ${PERFIS:-nenhum} $(perfil_ativo && echo '(AMBIENTE ATIVO: worker ligado)' || echo '(EM ESPERA: worker parado)')
  gateway      : 127.0.0.1:$PORTA  <-  Nginx do host  <-  https://$(env_valor DOMINIO)
  backup antes : $($SEM_BACKUP && echo 'NÃO (primeira instalação)' || echo 'sim, cifrado')
-------------------------------------------------------------------------------
PLANO
if ! $CONFIRMAR; then
  read -r -p "Digite IMPLANTAR para continuar: " resp
  [[ "$resp" == "IMPLANTAR" ]] || morrer "cancelado pelo operador"
fi

# ---------------------------------------------------------------------------------------------- imagens
imagens_presentes() {
  local s ok=0
  for s in "${SERVICOS_IMAGEM[@]}"; do
    docker image inspect "ctrlfiscal/$s:$VERSAO_NOVA" >/dev/null 2>&1 || { erro "imagem ausente: ctrlfiscal/$s:$VERSAO_NOVA"; ok=1; }
  done
  return $ok
}
if [[ -n "$PACOTE" ]]; then
  [[ -f "$PACOTE" ]] || morrer "pacote não encontrado: $PACOTE"
  SOMAS="${PACOTE%.tar.gz}.SHA256SUMS"
  [[ -f "$SOMAS" ]] || morrer "arquivo de checksums ausente: $SOMAS"
  (cd "$(dirname "$PACOTE")" && sha256sum --quiet -c "$(basename "$SOMAS")") || morrer "sha256 do pacote NÃO confere"
  grep -q "^versao=$VERSAO_NOVA\$" "${PACOTE%.tar.gz}.txt" || morrer "o manifesto do pacote não é da versão $VERSAO_NOVA"
  msg "carregando imagens (docker load) de $PACOTE"
  gunzip -c "$PACOTE" | docker load
fi
imagens_presentes || morrer "imagens da versão $VERSAO_NOVA incompletas (use --pacote)"
arq_img="$(docker image inspect -f '{{.Architecture}}' "ctrlfiscal/core-api:$VERSAO_NOVA")"
arq_vps="$(dpkg --print-architecture 2>/dev/null || uname -m)"
[[ "$arq_img" == "$arq_vps" || ( "$arq_img" == amd64 && "$arq_vps" == x86_64 ) || ( "$arq_img" == arm64 && "$arq_vps" == aarch64 ) ]] || \
  morrer "imagem $arq_img não corresponde à arquitetura da VPS ($arq_vps); reempacote com PLATAFORMA"
docker image inspect "$IMAGEM_MYSQL" >/dev/null 2>&1 || aviso "imagem do MySQL por digest não está local: o Compose fará pull do Docker Hub (mesmo digest)"

# Permissões dos segredos dependem destes ids (ver deploy/gerar-segredos.sh).
gid_app="$(docker run --rm --network none --entrypoint id "ctrlfiscal/core-api:$VERSAO_NOVA" -g)"
[[ "$gid_app" == "999" ]] || morrer "gid inesperado na imagem da aplicação ($gid_app); ajuste o grupo dos segredos"

# ---------------------------------------------------------------------------------------------- backup
if container_rodando "$MYSQL_CONTAINER"; then
  if $SEM_BACKUP; then
    n="$(printf "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='ctrl_fiscal';\n" | mysql_exec "$MYSQL_CONTAINER" "$PW_SEGREDO")"
    [[ "$n" -eq 0 ]] || morrer "--sem-backup recusado: o banco já tem tabelas ($n em ctrl_fiscal)"
  else
    msg "backup cifrado antes da implantação"
    "$RAIZ/deploy/backup-criptografado.sh" "antes-${VERSAO_NOVA}" || morrer "backup falhou; implantação abortada"
  fi
elif ! $SEM_BACKUP; then
  aviso "MySQL parado: sem backup possível agora. Se já houver dados, suba o MySQL e repita (ou use --sem-backup na 1ª instalação)."
  morrer "implantação abortada por falta de backup"
fi

# ---------------------------------------------------------------------------------------------- aplicação
install -d -m 700 "$ESTADO_DIR"
# A variável de ambiente tem precedência sobre o .env no Compose: a nova versão só é gravada no .env depois
# que as migrações passarem (se falharem, os serviços em execução e o .env continuam na versão anterior).
export VERSAO="$VERSAO_NOVA"
dc config --quiet || morrer "configuração do Compose inválida"

msg "MySQL"
dc up -d --no-build mysql
esperar_saudavel "$MYSQL_CONTAINER" 300

msg "migrações (Alembic, credenciais *_migrate)"
for m in core-migrate fiscal-migrate nfse-migrate; do
  dc run --rm --no-deps "$m" || morrer "migração $m falhou; serviços e .env NÃO foram atualizados. Avalie antes de qualquer rollback."
done
env_definir VERSAO "$VERSAO_NOVA"

msg "gatilhos de auditoria imutável (idempotente)"
if command -v python3 >/dev/null 2>&1; then
  python3 -I "$RAIZ/scripts/endurecer_mysql.py" "$MYSQL_CONTAINER" >/dev/null || morrer "falha ao aplicar gatilhos de auditoria"
else
  aviso "python3 ausente: rode scripts/endurecer_mysql.py manualmente (auditoria imutável)"
fi

msg "subindo serviços da versão $VERSAO_NOVA (somente projeto $PROJETO)"
dc up -d --no-build
if ! perfil_ativo; then
  # Em espera: garante o worker parado mesmo que tenha sido iniciado antes (o "up" não para serviços de perfil).
  dc stop fiscal-worker >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------------------------- saúde
msg "verificando saúde"
falhou=0
servicos=(core-api fiscal-api nfse-api web painel-fiscal gateway)
perfil_ativo && servicos+=(fiscal-worker)
for s in "${servicos[@]}"; do
  esperar_saudavel "${PROJETO}-${s}-1" 240 || falhou=1
done
if ! perfil_ativo && container_rodando "${PROJETO}-fiscal-worker-1"; then
  erro "worker em execução num ambiente EM ESPERA"; falhou=1
fi
codigo="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORTA}/api/" || true)"
[[ "$codigo" == "404" ]] || { erro "gateway em 127.0.0.1:$PORTA respondeu $codigo (esperado 404 em /api/)"; falhou=1; }
codigo="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORTA}/login" || true)"
[[ "$codigo" =~ ^(200|30[0-9])$ ]] || { erro "portal (/login) respondeu $codigo"; falhou=1; }
codigo="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$(env_valor DOMINIO)/login" || true)"
if [[ "$codigo" == "200" ]]; then
  # Prova empírica do TRUSTED_PROXY_IP: pedido pelo Nginx do host; o gateway deve registrar o IP do CLIENTE
  # (repassado em X-Forwarded-For), e não o endereço da bridge. Se registrar TRUSTED_PROXY_IP, o realip não
  # está aceitando o Nginx do host (confira a sub-rede "publico" e o gateway dela: docker network inspect).
  curl -s -o /dev/null --max-time 15 "https://$(env_valor DOMINIO)/api/?verificacao-proxy" || true
  ip_log="$(docker logs --tail 20 "${PROJETO}-gateway-1" 2>/dev/null | grep 'verificacao-proxy' | tail -n 1 | cut -d' ' -f1 || true)"
  if [[ -z "$ip_log" ]]; then aviso "não foi possível conferir o IP registrado pelo gateway"
  elif [[ "$ip_log" == "$(env_valor TRUSTED_PROXY_IP)" ]]; then aviso "gateway registrou $ip_log: X-Forwarded-For do Nginx do host NÃO está sendo aceito"
  else msg "gateway registrou o IP do cliente ($ip_log) para um pedido vindo do Nginx do host: TRUSTED_PROXY_IP correto"
  fi
else
  aviso "https://$(env_valor DOMINIO)/login respondeu '$codigo' (site do Nginx do host instalado? deploy/nginx/)"
fi

printf '%s\t%s\t%s\t%s\n' "$(date -Is)" "${VERSAO_ATUAL:-nenhuma}" "$VERSAO_NOVA" "$([[ $falhou -eq 0 ]] && echo ok || echo FALHA)" \
  >> "$ESTADO_DIR/historico-implantacoes.tsv"
if [[ $falhou -ne 0 ]]; then
  erro "implantação de $VERSAO_NOVA com falha de saúde. Veja: docker compose -p $PROJETO logs --tail 200 <serviço>"
  erro "rollback de imagens: deploy/rollback.sh ${VERSAO_ATUAL:-<versao-anterior>} (leia as restrições sobre migrações)"
  exit 1
fi
msg "implantação concluída: versão $VERSAO_NOVA saudável ($(perfil_ativo && echo 'ATIVA' || echo 'EM ESPERA'))"
