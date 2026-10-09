#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — gera os arquivos de segredo da VPS (equivalente Linux de scripts/gerar-segredos.ps1).
#
# Uso (como root, na raiz do repositório):
#   sudo deploy/gerar-segredos.sh --importar-de /root/segredos-transferidos   # MIGRAÇÃO (recomendado): instala os
#        segredos trazidos da instalação local por canal SEPARADO do backup (senhas do banco, JWT, CSRF, chaves
#        internas e, principalmente, nfse_vault_key). Gera só o que faltar (exceto a chave do cofre).
#   sudo deploy/gerar-segredos.sh --chave-cofre /caminho/nfse_vault_key.txt   # MIGRAÇÃO mínima: só a chave do
#        cofre vem da origem; os demais segredos são novos (as sessões antigas expiram)
#   sudo deploy/gerar-segredos.sh --nova-chave-cofre                          # instalação NOVA, sem certificados
#
# - Pasta: SECRETS_DIR do .env (padrão /opt/ctrlfiscal/segredos), dono root, modo 700. Fora do repositório.
# - NUNCA sobrescreve um arquivo existente e NUNCA imprime valores.
# - Permissões dos arquivos: dono 10001 (usuário "ctrl" das imagens Python), grupo 999 (grupo "mysql" da imagem
#   MySQL e grupo primário do usuário "ctrl"), modo 440. Motivo: no Linux o Compose monta cada segredo como
#   arquivo (bind mount) preservando dono e modo do host; com 600/root os serviços não-root não conseguiriam lê-lo.
#   Quem protege no host é a pasta 700/root (no Windows/Docker Desktop isso não aparece).
# - nfse_vault_key cifra os PFX dos certificados no banco ctrl_nfse. Sem a MESMA chave da origem, os
#   certificados migrados NÃO abrem. Por isso este script não inventa essa chave sem ordem explícita.
# - Senhas do MySQL só valem na PRIMEIRA subida do volume (infra/mysql/init). Trocar depois exige ALTER USER.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

UID_APP=10001
GID_APP=999
CHAVE_COFRE=""
NOVA_CHAVE_COFRE=false
IMPORTAR_DE=""
NOMES=(mysql_root_password.txt core_db_password.txt core_migrate_db_password.txt fiscal_db_password.txt
       fiscal_migrate_db_password.txt nfse_db_password.txt nfse_migrate_db_password.txt jwt_private_key.pem
       jwt_public_key.pem csrf_secret.txt internal_fiscal_api_to_core.txt internal_fiscal_worker_to_core.txt
       internal_fiscal_worker_to_nfse.txt internal_core_to_nfse.txt nfse_vault_key.txt)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chave-cofre) CHAVE_COFRE="${2:?informe o arquivo da chave do cofre}"; shift 2 ;;
    --nova-chave-cofre) NOVA_CHAVE_COFRE=true; shift ;;
    --importar-de) IMPORTAR_DE="${2:?informe a pasta com os segredos transferidos}"; shift 2 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) morrer "opção desconhecida: $1" ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo): os arquivos precisam de dono 10001 e grupo 999"
exigir_comando openssl install
umask 077

SECRETS_DIR="$(env_valor SECRETS_DIR /opt/ctrlfiscal/segredos)"
[[ "$SECRETS_DIR" == /* ]] || morrer "SECRETS_DIR precisa ser caminho absoluto fora do repositório (atual: $SECRETS_DIR)"
case "$SECRETS_DIR/" in "$RAIZ"/*) morrer "SECRETS_DIR não pode ficar dentro do repositório ($RAIZ)";; esac

install -d -m 700 -o root -g root "$SECRETS_DIR"

gravar() { # gravar <nome> <comando que escreve o valor na saída padrão>
  local nome="$1"; shift
  local destino="$SECRETS_DIR/$nome" tmp
  if [[ -e "$destino" ]]; then msg "mantido: $nome"; return 0; fi
  tmp="$(mktemp "$SECRETS_DIR/.novo.XXXXXX")"
  "$@" > "$tmp"
  [[ -s "$tmp" ]] || { rm -f "$tmp"; morrer "valor vazio ao gerar $nome"; }
  chown "$UID_APP:$GID_APP" "$tmp"; chmod 440 "$tmp"
  mv "$tmp" "$destino"
  msg "criado:  $nome"
}

hex32()  { openssl rand -hex 32 | tr -d '\r\n'; }
b64_32() { openssl rand -base64 32 | tr -d '\r\n'; }

# ---- Importação dos segredos da instalação de origem (migração) ----------------------------------------------
# Só nomes conhecidos são copiados (outros arquivos da pasta, ex. senhas pessoais, são ignorados). Valores de uma
# linha perdem CR/LF finais (o entrypoint do MySQL não os remove); PEM é convertido para LF.
if [[ -n "$IMPORTAR_DE" ]]; then
  [[ -d "$IMPORTAR_DE" ]] || morrer "pasta de importação inexistente: $IMPORTAR_DE"
  for nome in "${NOMES[@]}"; do
    [[ -s "$IMPORTAR_DE/$nome" ]] || continue
    case "$nome" in
      *.pem) gravar "$nome" sh -c 'tr -d "\r" < "$1"' _ "$IMPORTAR_DE/$nome" ;;
      *)     gravar "$nome" sh -c 'tr -d "\r\n" < "$1"' _ "$IMPORTAR_DE/$nome" ;;
    esac
  done
  if [[ -e "$SECRETS_DIR/nfse_vault_key.txt" ]]; then
    n="$(base64 -d < "$SECRETS_DIR/nfse_vault_key.txt" 2>/dev/null | wc -c)"
    [[ "$n" -eq 32 ]] || morrer "nfse_vault_key.txt importada é inválida (esperado base64 de 32 bytes)"
  fi
  msg "importação concluída; apague a cópia de transferência: shred -u '$IMPORTAR_DE'/* && rmdir '$IMPORTAR_DE'"
fi

for nome in mysql_root_password core_db_password core_migrate_db_password fiscal_db_password \
            fiscal_migrate_db_password nfse_db_password nfse_migrate_db_password csrf_secret \
            internal_fiscal_api_to_core internal_fiscal_worker_to_core internal_fiscal_worker_to_nfse \
            internal_core_to_nfse; do
  gravar "$nome.txt" hex32
done

# ---- Chave do cofre do conector NFS-e -----------------------------------------------------------------------
if [[ -e "$SECRETS_DIR/nfse_vault_key.txt" ]]; then
  msg "mantido: nfse_vault_key.txt"
elif [[ -n "$CHAVE_COFRE" ]]; then
  [[ -f "$CHAVE_COFRE" ]] || morrer "arquivo da chave do cofre não encontrado: $CHAVE_COFRE"
  # Confere o formato (base64 de 32 bytes) sem exibir o valor.
  n="$(tr -d '\r\n' < "$CHAVE_COFRE" | base64 -d 2>/dev/null | wc -c)"
  [[ "$n" -eq 32 ]] || morrer "chave do cofre inválida (esperado base64 de 32 bytes)"
  gravar nfse_vault_key.txt sh -c 'tr -d "\r\n" < "$1"' _ "$CHAVE_COFRE"
  msg "chave do cofre instalada; apague a cópia de transferência com: shred -u '$CHAVE_COFRE'"
elif $NOVA_CHAVE_COFRE; then
  gravar nfse_vault_key.txt b64_32
  aviso "nova chave do cofre gerada: certificados cifrados com outra chave NÃO abrirão nesta instalação"
else
  morrer "nfse_vault_key.txt ausente: use --importar-de <pasta> ou --chave-cofre <arquivo> (migração) ou --nova-chave-cofre (instalação nova)"
fi

# ---- Par Ed25519 do JWT (core-api) ------------------------------------------------------------------------------
if [[ ! -e "$SECRETS_DIR/jwt_private_key.pem" ]]; then
  gravar jwt_private_key.pem openssl genpkey -algorithm ed25519
  rm -f "$SECRETS_DIR/jwt_public_key.pem"
fi
if [[ ! -e "$SECRETS_DIR/jwt_public_key.pem" ]]; then
  gravar jwt_public_key.pem openssl pkey -in "$SECRETS_DIR/jwt_private_key.pem" -pubout
fi

# ---- Conferência final (só nomes, tamanhos e permissões) ---------------------------------------------------------
faltando=0
for nome in "${NOMES[@]}"; do
  if [[ ! -s "$SECRETS_DIR/$nome" ]]; then erro "faltando: $nome"; faltando=1; fi
done
[[ $faltando -eq 0 ]] || exit 1
msg "segredos prontos em $SECRETS_DIR (pasta 700 root; arquivos 440 ${UID_APP}:${GID_APP}; valores não exibidos)"
