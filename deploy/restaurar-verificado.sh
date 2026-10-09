#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — restauração VERIFICADA de um backup cifrado, num MySQL TEMPORÁRIO e isolado.
#
# Nunca toca no banco em uso. Passos:
#   1. confere SHA256SUMS dos arquivos cifrados;
#   2. sobe um MySQL 8.4 descartável (mesma imagem fixada do compose), sem rede, com senha aleatória gerada aqui
#      (passada pelo ambiente do comando docker, fora da linha de comando; nunca exibida);
#   3. decifra em fluxo (age -d -i <chave privada> ou gpg -d) e restaura o dump;
#   4. compara a contagem de linhas de TODAS as tabelas com contagens.tsv do backup;
#   5. remove o container temporário (a menos que --manter seja usado) e informa o resultado.
#
# Só depois de "RESTAURAÇÃO VERIFICADA" o backup pode ser usado (migração para a VPS, recuperação de desastre).
# A chave PRIVADA do age/gpg é trazida só para esta verificação e apagada em seguida (não deixe na VPS).
#
# Uso:  sudo deploy/restaurar-verificado.sh <pasta-do-backup> [arquivo-identidade-age] [--manter] [--checksums]
#   --manter     mantém o MySQL temporário para inspeção manual (nome impresso no fim; remova com docker rm -f)
#   --checksums  grava também checksums.tsv (CHECKSUM TABLE) do banco restaurado, para comparar com a origem
# Saída: código 0 somente se a restauração terminou e as contagens conferem.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

PASTA=""; IDENTIDADE=""; MANTER=false; CHECKSUMS=false
for a in "$@"; do
  case "$a" in
    --manter) MANTER=true ;;
    --checksums) CHECKSUMS=true ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) if [[ -z "$PASTA" ]]; then PASTA="$a"; elif [[ -z "$IDENTIDADE" ]]; then IDENTIDADE="$a"; else morrer "argumento extra: $a"; fi ;;
  esac
done
[[ -n "$PASTA" && -d "$PASTA" ]] || morrer "informe a pasta do backup"
exigir_comando docker gzip sha256sum diff openssl

DUMP="$(ls "$PASTA"/mysql.sql.gz.* 2>/dev/null | head -n 1 || true)"
[[ -n "$DUMP" ]] || morrer "dump cifrado não encontrado em $PASTA"
[[ -s "$PASTA/contagens.tsv" ]] || morrer "contagens.tsv ausente em $PASTA"
case "$DUMP" in
  *.age) exigir_comando age
         [[ -n "$IDENTIDADE" && -f "$IDENTIDADE" ]] || morrer "informe o arquivo de identidade (chave privada) do age"
         decifrar() { age -d -i "$IDENTIDADE" "$DUMP"; } ;;
  *.gpg) exigir_comando gpg
         decifrar() { gpg --batch --decrypt "$DUMP"; } ;;
  *)     morrer "formato de dump desconhecido: $DUMP" ;;
esac

msg "1/5 conferindo checksums"
( cd "$PASTA" && sha256sum --quiet -c SHA256SUMS ) || morrer "checksum não confere: backup corrompido ou alterado"

NOME="ctrlfiscal-restauracao-$(openssl rand -hex 3 2>/dev/null || date +%s)"
TMPD="$(mktemp -d)"; chmod 700 "$TMPD"
limpar() {
  rm -rf "$TMPD"
  if ! $MANTER; then docker rm -f "$NOME" >/dev/null 2>&1 || true; fi
}
trap limpar EXIT

msg "2/5 subindo MySQL temporário $NOME (sem rede, senha aleatória não exibida)"
# "-e NOME" sem valor: o docker lê o valor do ambiente deste comando (não aparece na linha de comando/ps).
MYSQL_ROOT_PASSWORD="$(openssl rand -hex 24)" docker run -d --name "$NOME" --network none -e MYSQL_ROOT_PASSWORD \
  --label ctrl-fiscal=restauracao-verificada "$IMAGEM_MYSQL" --max-allowed-packet=256M >/dev/null
# O servidor de inicialização responde por socket antes do definitivo: espera duas respostas estáveis.
pronto=0
for _ in $(seq 1 120); do
  if docker exec "$NOME" sh -c "MYSQL_PWD=$PW_AMBIENTE mysqladmin -uroot --protocol=socket ping --silent" >/dev/null 2>&1 \
     && docker logs "$NOME" 2>&1 | grep 'ready for connections.*port: 3306' >/dev/null; then pronto=1; break; fi
  sleep 2
done
[[ $pronto -eq 1 ]] || morrer "MySQL temporário não ficou pronto"

msg "3/5 decifrando e restaurando (em fluxo; nada em texto claro no disco)"
decifrar | gunzip | docker exec -i "$NOME" sh -c "MYSQL_PWD=$PW_AMBIENTE exec mysql -uroot --default-character-set=utf8mb4" \
  || morrer "restauração falhou"

msg "4/5 comparando contagens por tabela"
contagens "$NOME" "$PW_AMBIENTE" > "$TMPD/contagens-restauradas.tsv"
if $CHECKSUMS; then
  checksums "$NOME" "$PW_AMBIENTE" > "$PASTA/checksums-restaurados.tsv"
  msg "checksums gravados em $PASTA/checksums-restaurados.tsv"
fi
if diff -u "$PASTA/contagens.tsv" "$TMPD/contagens-restauradas.tsv" > "$TMPD/diferencas.txt"; then
  RESULT="RESTAURAÇÃO VERIFICADA"
  status=0
else
  RESULT="DIVERGÊNCIA NAS CONTAGENS"
  status=1
  cat "$TMPD/diferencas.txt" >&2
  aviso "diferenças pequenas em tabelas de sessão/auditoria podem vir de escrita concorrente durante o dump;"
  aviso "qualquer diferença em documentos, eventos, certificados ou rascunhos impede o uso do backup."
fi
{
  echo "verificado_em=$(date -Is)"
  echo "resultado=$RESULT"
  echo "tabelas=$(wc -l < "$TMPD/contagens-restauradas.tsv")"
  echo "linhas_total=$(awk -F'\t' '{s+=$2} END{print s+0}' "$TMPD/contagens-restauradas.tsv")"
} > "$PASTA/VERIFICACAO-$(date +%Y%m%d-%H%M%S).txt"

msg "5/5 $RESULT"
if $MANTER; then msg "container mantido para inspeção: $NOME (remova com: docker rm -f $NOME)"; fi
exit $status
