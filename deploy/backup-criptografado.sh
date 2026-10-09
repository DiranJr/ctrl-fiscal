#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — backup CIFRADO e consistente (VPS Linux).
#
# Conteúdo de cada backup (pasta BACKUP_DIR/<AAAAmmdd-HHMMSS>-<rótulo>/):
#   mysql.sql.gz.age (ou .gpg)  mysqldump --single-transaction dos bancos ctrl_core, ctrl_fiscal e ctrl_nfse
#                               (rotinas, gatilhos e eventos; binários em hexadecimal). Cifrado em fluxo: o texto
#                               claro nunca é gravado em disco.
#   exports.tar.gz.age          volume de exportações/pacotes (arquivos temporários com TTL)
#   contagens.tsv               linhas por tabela tomadas logo após o dump (usadas pelo restaurar-verificado.sh)
#   SHA256SUMS                  checksums dos arquivos cifrados
#   MANIFESTO.txt               data, versão implantada, método de cifra e tamanhos
#
# Cifra: "age" com a chave PÚBLICA em BACKUP_AGE_RECIPIENT (preferido) ou GPG com BACKUP_GPG_RECIPIENT.
# A chave PRIVADA não fica na VPS: um invasor que leia os backups não consegue abri-los.
# O PFX dos certificados já está cifrado no banco com nfse_vault_key; a chave do cofre NÃO entra no backup
# (guarde-a separadamente, no cofre do titular).
#
# Retenção: remove pastas de backup com mais de BACKUP_RETENCAO_DIAS dias (só as criadas por este script).
# Copie os backups para fora da VPS (ex.: rsync sobre SSH para outro servidor/armazenamento).
#
# Uso:  sudo deploy/backup-criptografado.sh [rótulo]        (ex.: antes-implantacao-abc1234)
# Agendamento sugerido (root): 15 2 * * * /opt/ctrlfiscal/app/deploy/backup-criptografado.sh diario >> /var/log/ctrlfiscal-backup.log 2>&1
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

ROTULO="${1:-manual}"
[[ "$ROTULO" =~ ^[A-Za-z0-9._-]+$ ]] || morrer "rótulo inválido (use letras, números, ponto, hífen ou sublinhado)"

exigir_comando docker gzip sha256sum
BACKUP_DIR="$(env_valor BACKUP_DIR /opt/ctrlfiscal/backups)"
AGE_RCPT="$(env_valor BACKUP_AGE_RECIPIENT)"
GPG_RCPT="$(env_valor BACKUP_GPG_RECIPIENT)"
RETENCAO="$(env_valor BACKUP_RETENCAO_DIAS 30)"
VERSAO="$(env_valor VERSAO desconhecida)"

if [[ -n "$AGE_RCPT" && "$AGE_RCPT" != *TROQUE-ME* ]]; then
  exigir_comando age
  METODO="age"; EXT="age"
  cifrar() { age -r "$AGE_RCPT"; }
elif [[ -n "$GPG_RCPT" ]]; then
  exigir_comando gpg
  METODO="gpg"; EXT="gpg"
  cifrar() { gpg --batch --yes --trust-model always --encrypt --recipient "$GPG_RCPT" --output -; }
else
  morrer "defina BACKUP_AGE_RECIPIENT (chave pública age) ou BACKUP_GPG_RECIPIENT no .env"
fi

container_rodando "$MYSQL_CONTAINER" || morrer "MySQL ($MYSQL_CONTAINER) não está em execução"

umask 077
mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
DEST="$BACKUP_DIR/$(date +%Y%m%d-%H%M%S)-$ROTULO"
mkdir -m 700 "$DEST"
trap 'erro "backup interrompido; pasta incompleta: $DEST"' ERR

msg "dump dos bancos ${BANCOS[*]} (transação consistente) -> $DEST/mysql.sql.gz.$EXT"
docker exec "$MYSQL_CONTAINER" sh -c "MYSQL_PWD=$PW_SEGREDO exec mysqldump -uroot --single-transaction --quick \
  --routines --triggers --events --hex-blob --set-gtid-purged=OFF --no-tablespaces --databases ${BANCOS[*]}" \
  | gzip -6 | cifrar > "$DEST/mysql.sql.gz.$EXT"

msg "contagem de linhas por tabela (referência para a restauração verificada)"
contagens "$MYSQL_CONTAINER" "$PW_SEGREDO" > "$DEST/contagens.tsv"

msg "volume de exportações (${PROJETO}_exports)"
if docker volume inspect "${PROJETO}_exports" >/dev/null 2>&1; then
  IMG_TAR="ctrlfiscal/fiscal-api:${VERSAO}"
  docker image inspect "$IMG_TAR" >/dev/null 2>&1 || IMG_TAR="$IMAGEM_MYSQL"
  docker run --rm --network none --user 10001 -v "${PROJETO}_exports:/dados:ro" --entrypoint tar "$IMG_TAR" \
    -C /dados -cf - . | gzip -6 | cifrar > "$DEST/exports.tar.gz.$EXT"
else
  aviso "volume ${PROJETO}_exports inexistente; exportações não incluídas"
fi

( cd "$DEST" && sha256sum -- *."$EXT" > SHA256SUMS )
{
  echo "backup=$(basename "$DEST")"
  echo "criado_em=$(date -Is)"
  echo "versao_implantada=$VERSAO"
  echo "cifra=$METODO"
  echo "bancos=${BANCOS[*]}"
  echo "tabelas=$(wc -l < "$DEST/contagens.tsv")"
  echo "linhas_total=$(awk -F'\t' '{s+=$2} END{print s+0}' "$DEST/contagens.tsv")"
  echo "observacao=contagens tomadas logo após o dump; diferenças pequenas só por escrita concorrente (sessões/auditoria)"
  ls -l "$DEST" | awk 'NR>1{print "arquivo=" $9 " bytes=" $5}'
} > "$DEST/MANIFESTO.txt"
trap - ERR

# Teste mínimo de integridade: o arquivo cifrado não está vazio e o checksum confere.
[[ -s "$DEST/mysql.sql.gz.$EXT" ]] || morrer "dump cifrado vazio"
( cd "$DEST" && sha256sum --quiet -c SHA256SUMS ) || morrer "checksum não confere"
msg "backup concluído: $DEST"
msg "valide periodicamente com: deploy/restaurar-verificado.sh $DEST <arquivo-da-chave-privada>"

# Retenção: só pastas no padrão AAAAmmdd-HHMMSS-<rótulo> dentro de BACKUP_DIR.
if [[ "$RETENCAO" =~ ^[0-9]+$ && "$RETENCAO" -gt 0 ]]; then
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
    -regex '.*/[0-9]{8}-[0-9]{6}-[A-Za-z0-9._-]+' -mtime "+$RETENCAO" -print -exec rm -rf -- {} + \
    | sed 's/^/removido pela retenção: /'
fi
