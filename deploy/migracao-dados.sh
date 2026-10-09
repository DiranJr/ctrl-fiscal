#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — MIGRAÇÃO dos dados reais da instalação atual (Windows/Docker Desktop) para a VPS.
#
# REGRA CENTRAL: só UM ambiente pode ter o fiscal-worker ativo (sincronização ADN, filas de emissão e de
# cancelamento, importações, exportações). Dois workers com o mesmo certificado e bancos diferentes duplicariam
# consultas ao ADN e pedidos de cancelamento, e os dois bancos divergiriam sem volta. A VPS fica EM ESPERA
# (COMPOSE_PROFILES vazio, sem worker) até o corte; a instalação local só é desligada de vez depois que a VPS
# estiver validada em uso (ela é o caminho de volta).
#
# Requisitos: age (preferido) ou gpg nas duas pontas; a chave PRIVADA do age fica com o titular (nunca na VPS
# além do tempo da restauração); SSH por chave para a VPS. Nenhum subcomando imprime valores de segredo.
#
# ---- PREPARAÇÃO (dias antes; a instalação local continua ATIVA) ----------------------------------------------
#  Windows (Git Bash, na raiz do checkout que roda a produção local):
#   1. deploy/migracao-dados.sh origem-ensaio  <pasta-fora-do-repo> <age1...>   # backup cifrado SEM parar nada
#   2. deploy/migracao-dados.sh origem-segredos <pasta-fora-do-repo> <age1...>  # segredos num arquivo cifrado
#        SEPARADO (senhas do banco, JWT, CSRF, chaves internas e, principalmente, nfse_vault_key: sem ela o
#        certificado A1, cifrado no banco, NÃO abre na VPS)
#   3. Transferência por SSH, em canais SEPARADOS (comandos distintos; o arquivo de segredos nunca junto do dump):
#        rsync -av --chmod=F600,D700 <pasta-ensaio>/ usuario@vps:/opt/ctrlfiscal/migracao/ensaio/
#        scp <pasta-segredos>/segredos-*.tar.age usuario@vps:/root/
#  VPS (root, repositório no commit VERSAO, imagens carregadas, .env com COMPOSE_PROFILES vazio):
#   4. deploy/migracao-dados.sh destino-segredos /root/segredos-<data>.tar.age <identidade-age>
#   5. deploy/migracao-dados.sh destino-verificar /opt/ctrlfiscal/migracao/ensaio <identidade-age>
#        (restauração num MySQL TEMPORÁRIO + contagens; ensaio sem parar a origem: diferenças pequenas em
#        sessões/auditoria são esperadas aqui, mas não no corte)
#
# ---- CORTE (janela combinada; usuários avisados) -------------------------------------------------------------
#  Windows:
#   6. deploy/migracao-dados.sh origem-exportar <pasta-corte> <age1...>
#        - para o worker local (docker compose -p ctrlfiscal stop fiscal-worker) e os serviços de aplicação
#          (o MySQL continua), congelando os dados; confere que o código local é a MESMA versão da VPS;
#        - backup FINAL cifrado + contagens.tsv + checksums.tsv (CHECKSUM TABLE) + SHA256SUMS.
#   7. rsync -av --chmod=F600,D700 <pasta-corte>/ usuario@vps:/opt/ctrlfiscal/migracao/corte/
#  VPS:
#   8. deploy/migracao-dados.sh destino-verificar /opt/ctrlfiscal/migracao/corte <identidade-age>
#        (MySQL temporário: contagens E checksums têm de ser IGUAIS aos da origem)
#   9. deploy/migracao-dados.sh destino-importar  /opt/ctrlfiscal/migracao/corte <identidade-age>
#        (só aceita banco da VPS VAZIO e a VPS em espera; confere contagens e checksums de novo)
#  10. deploy/implantar.sh <VERSAO> --confirmar      (ainda EM ESPERA; migrações devem ser no-op)
#      Valide por https://<DOMINIO>: login, workspaces, notas, DANFSe, prontidão do certificado.
#  11. deploy/migracao-dados.sh destino-ativar       (COMPOSE_PROFILES=ativo e sobe o worker da VPS)
#  12. Só então troque o acesso dos usuários para o domínio da VPS.
#
# ---- RETORNO (somente se a VPS falhar ANTES de qualquer operação nova nela) ---------------------------------
#   VPS:     deploy/migracao-dados.sh destino-desativar   (para o worker; COMPOSE_PROFILES vazio)
#   Windows: deploy/migracao-dados.sh origem-reativar     (religa serviços e o worker local)
#   Depois que a VPS gerar dados novos (sincronizações, rascunhos, cancelamentos), voltar exige migrar os dados
#   de volta pelo mesmo procedimento. Nunca restaure backup antigo por cima de documentos fiscais novos.
# =============================================================================================================
set -euo pipefail
# shellcheck source=deploy/comum.sh
source "$(dirname "${BASH_SOURCE[0]}")/comum.sh"

uso() { sed -n '2,52p' "$0"; exit "${1:-0}"; }
CMD="${1:-}"; shift || true

# Serviços de aplicação da instalação local (projeto Compose "ctrlfiscal"); o MySQL não entra.
APP_LOCAL=(fiscal-worker gateway web painel-fiscal fiscal-api core-api nfse-api)
# Segredos copiados para a VPS (somente estes nomes; outros arquivos da pasta são ignorados).
NOMES_SEGREDOS=(mysql_root_password.txt core_db_password.txt core_migrate_db_password.txt fiscal_db_password.txt
  fiscal_migrate_db_password.txt nfse_db_password.txt nfse_migrate_db_password.txt jwt_private_key.pem
  jwt_public_key.pem csrf_secret.txt internal_fiscal_api_to_core.txt internal_fiscal_worker_to_core.txt
  internal_fiscal_worker_to_nfse.txt internal_core_to_nfse.txt nfse_vault_key.txt)

dc_local() { docker compose -p "$PROJETO" --project-directory "$RAIZ" "$@"; }

cifrador() { # cifrador <destinatario> -> define a função "cifrar" e EXT
  local d="$1"
  if [[ "$d" == age1* ]]; then exigir_comando age; EXT=age; cifrar() { age -r "$d"; }
  else exigir_comando gpg; EXT=gpg; cifrar() { gpg --batch --yes --trust-model always --encrypt --recipient "$d" --output -; }
  fi
}

decifrar_arquivo() { # decifrar_arquivo <arquivo> <identidade>
  case "$1" in
    *.age) exigir_comando age; [[ -f "${2:-}" ]] || morrer "informe a identidade (chave privada) do age"; age -d -i "$2" "$1" ;;
    *.gpg) exigir_comando gpg; gpg --batch --decrypt "$1" ;;
    *) morrer "formato desconhecido: $1" ;;
  esac
}

pasta_nova_fora_do_repo() {
  [[ ! -e "$1" ]] || morrer "$1 já existe (use uma pasta nova)"
  mkdir -p "$1"; chmod 700 "$1"
  case "$(cd "$1" && pwd)/" in "$RAIZ"/*) rmdir "$1"; morrer "a pasta de saída não pode ficar dentro do repositório";; esac
}

exportar_banco() { # exportar_banco <saida> <rotulo>
  local saida="$1" versao
  versao="$(git -C "$RAIZ" rev-parse --short=12 HEAD)"
  msg "dump cifrado dos bancos ${BANCOS[*]}"
  docker exec "$MYSQL_CONTAINER" sh -c "MYSQL_PWD=$PW_SEGREDO exec mysqldump -uroot --single-transaction --quick \
    --routines --triggers --events --hex-blob --set-gtid-purged=OFF --no-tablespaces --databases ${BANCOS[*]}" \
    | gzip -6 | cifrar > "$saida/mysql.sql.gz.$EXT"
  msg "contagens e checksums da origem"
  contagens "$MYSQL_CONTAINER" "$PW_SEGREDO" > "$saida/contagens.tsv"
  checksums "$MYSQL_CONTAINER" "$PW_SEGREDO" > "$saida/checksums.tsv"
  if docker volume inspect "${PROJETO}_exports" >/dev/null 2>&1; then
    docker run --rm --network none --user 10001 -v "${PROJETO}_exports:/dados:ro" --entrypoint tar \
      "$(docker inspect -f '{{.Config.Image}}' "${PROJETO}-fiscal-api-1")" \
      -C /dados -cf - . | gzip -6 | cifrar > "$saida/exports.tar.gz.$EXT" || aviso "exportações não copiadas (são temporárias)"
  fi
  ( cd "$saida" && sha256sum -- *."$EXT" > SHA256SUMS )
  {
    echo "origem=instalacao-local"; echo "tipo=$2"; echo "versao_codigo=$versao"; echo "exportado_em=$(date -Is)"
    echo "tabelas=$(wc -l < "$saida/contagens.tsv")"
    echo "linhas_total=$(awk -F'\t' '{s+=$2} END{print s+0}' "$saida/contagens.tsv")"
    echo "segredos=NAO_INCLUIDOS (arquivo separado: origem-segredos)"
  } > "$saida/MANIFESTO.txt"
}

origem_ensaio() {
  local saida="${1:?pasta de saída}" dest="${2:?destinatário age (age1...) ou id GPG}"
  exigir_comando docker gzip sha256sum git
  cifrador "$dest"
  container_rodando "$MYSQL_CONTAINER" || morrer "MySQL local ($MYSQL_CONTAINER) parado"
  pasta_nova_fora_do_repo "$saida"
  aviso "ENSAIO: nada é parado; a instalação local continua ativa."
  exportar_banco "$saida" ensaio
  msg "ensaio pronto em $saida"
}

origem_segredos() {
  local saida="${1:?pasta de saída}" dest="${2:?destinatário age (age1...) ou id GPG}" dir arq n
  exigir_comando tar
  cifrador "$dest"
  dir="$(env_valor SECRETS_DIR ./secrets)"; [[ "$dir" == /* || "$dir" =~ ^[A-Za-z]: ]] || dir="$RAIZ/${dir#./}"
  pasta_nova_fora_do_repo "$saida"
  local presentes=()
  for n in "${NOMES_SEGREDOS[@]}"; do [[ -s "$dir/$n" ]] && presentes+=("$n"); done
  [[ " ${presentes[*]} " == *" nfse_vault_key.txt "* ]] || morrer "nfse_vault_key.txt não encontrada em $dir"
  arq="$saida/segredos-$(date +%Y%m%d-%H%M%S).tar.$EXT"
  tar -C "$dir" -cf - "${presentes[@]}" | cifrar > "$arq"
  ( cd "$saida" && sha256sum -- "$(basename "$arq")" > "$(basename "$arq").sha256" )
  msg "${#presentes[@]} segredos cifrados em $arq (valores não exibidos). Transfira por canal SEPARADO do backup."
}

origem_exportar() {
  local saida="${1:?pasta de saída}" dest="${2:?destinatário age (age1...) ou id GPG}" s
  exigir_comando docker gzip sha256sum git
  cifrador "$dest"
  container_rodando "$MYSQL_CONTAINER" || morrer "MySQL local ($MYSQL_CONTAINER) parado"
  [[ -z "$(git -C "$RAIZ" status --porcelain --untracked-files=no)" ]] || aviso "checkout local com alterações não commitadas"
  msg "versão do código local: $(git -C "$RAIZ" rev-parse --short=12 HEAD) (a VPS deve receber a MESMA)"
  read -r -p "CORTE: parar o worker e os serviços locais agora? Digite CORTE: " r; [[ "$r" == "CORTE" ]] || morrer "cancelado"
  pasta_nova_fora_do_repo "$saida"

  msg "parando o worker local primeiro (único ambiente ativo até aqui)"
  dc_local stop fiscal-worker
  container_rodando "${PROJETO}-fiscal-worker-1" && morrer "worker local ainda em execução"
  msg "congelando os serviços de aplicação (MySQL continua)"
  for s in "${APP_LOCAL[@]}"; do docker stop "${PROJETO}-${s}-1" >/dev/null 2>&1 && msg "  parado: $s" || true; done

  exportar_banco "$saida" corte
  msg "backup FINAL pronto em $saida. Instalação local CONGELADA (não religue o worker)."
}

origem_status() {
  local s
  for s in mysql "${APP_LOCAL[@]}"; do
    printf '%-14s %s\n' "$s" "$(docker inspect -f '{{.State.Status}}' "${PROJETO}-${s}-1" 2>/dev/null || echo ausente)"
  done
}

origem_reativar() {
  aviso "reativando a instalação LOCAL. O worker da VPS PRECISA estar parado (destino-desativar) antes."
  read -r -p "Digite REATIVAR-LOCAL para continuar: " r; [[ "$r" == "REATIVAR-LOCAL" ]] || morrer "cancelado"
  local s
  for s in nfse-api core-api fiscal-api painel-fiscal web gateway; do
    docker start "${PROJETO}-${s}-1" >/dev/null && msg "  iniciado: $s"
  done
  dc_local start fiscal-worker && msg "  iniciado: fiscal-worker (instalação local volta a ser o ambiente ativo)"
}

destino_segredos() {
  local arq="${1:?arquivo segredos-*.tar.age}" ident="${2:-}" tmp
  [[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
  if [[ -f "$arq.sha256" ]]; then (cd "$(dirname "$arq")" && sha256sum --quiet -c "$(basename "$arq").sha256") || morrer "sha256 não confere"; fi
  tmp="$(mktemp -d /root/segredos-transferidos.XXXXXX)"; chmod 700 "$tmp"
  decifrar_arquivo "$arq" "$ident" | tar -C "$tmp" -xf - || { rm -rf "$tmp"; morrer "falha ao decifrar os segredos"; }
  "$RAIZ/deploy/gerar-segredos.sh" --importar-de "$tmp" || { shred -u "$tmp"/* 2>/dev/null; rm -rf "$tmp"; morrer "importação falhou"; }
  shred -u "$tmp"/* 2>/dev/null || rm -f "$tmp"/*; rmdir "$tmp"
  msg "segredos instalados; apague também o arquivo transferido: shred -u '$arq'"
}

destino_verificar() {
  local pasta="${1:?pasta do pacote}" ident="${2:-}"
  [[ -s "$pasta/checksums.tsv" ]] || morrer "checksums.tsv da origem ausente"
  local ensaio=false
  grep -q '^tipo=ensaio$' "$pasta/MANIFESTO.txt" 2>/dev/null && ensaio=true
  if ! "$RAIZ/deploy/restaurar-verificado.sh" "$pasta" ${ident:+"$ident"} --checksums; then
    $ensaio && { aviso "ENSAIO com divergência de contagens (origem não estava congelada): confira se são só sessões/auditoria"; return 0; }
    morrer "restauração verificada falhou"
  fi
  if diff -u "$pasta/checksums.tsv" "$pasta/checksums-restaurados.tsv" >/dev/null; then
    msg "checksums por tabela CONFEREM com a origem"
  elif $ensaio; then
    aviso "ENSAIO: checksums diferentes em tabelas alteradas durante o dump (esperado sem congelamento)"
  else
    morrer "checksums divergem da origem (veja: diff $pasta/checksums.tsv $pasta/checksums-restaurados.tsv)"
  fi
}

destino_importar() {
  local pasta="${1:?pasta do pacote}" ident="${2:-}" dump n
  [[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
  grep -l 'resultado=RESTAURAÇÃO VERIFICADA' "$pasta"/VERIFICACAO-*.txt >/dev/null 2>&1 || \
    morrer "não há verificação aprovada deste pacote: rode antes $0 destino-verificar $pasta <identidade>"
  grep -q '^tipo=corte$' "$pasta/MANIFESTO.txt" || morrer "este pacote não é o backup FINAL do corte (origem-exportar)"
  ( cd "$pasta" && sha256sum --quiet -c SHA256SUMS ) || morrer "checksum não confere"
  if perfil_ativo; then morrer "COMPOSE_PROFILES contém 'ativo': importe com a VPS EM ESPERA (COMPOSE_PROFILES vazio)"; fi

  dc up -d --no-build mysql
  esperar_saudavel "$MYSQL_CONTAINER" 300
  n="$(printf "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema IN ('ctrl_core','ctrl_fiscal','ctrl_nfse');\n" \
       | mysql_exec "$MYSQL_CONTAINER" "$PW_SEGREDO")"
  [[ "$n" -eq 0 ]] || morrer "o banco da VPS já tem $n tabelas: importação recusada (nunca sobrescrever dados existentes)"

  dump="$(find "$pasta" -maxdepth 1 -name 'mysql.sql.gz.*' | head -n 1)"
  msg "importando $dump no MySQL da VPS"
  decifrar_arquivo "$dump" "$ident" | gunzip \
    | docker exec -i "$MYSQL_CONTAINER" sh -c "MYSQL_PWD=$PW_SEGREDO exec mysql -uroot --default-character-set=utf8mb4" \
    || morrer "importação falhou"

  contagens "$MYSQL_CONTAINER" "$PW_SEGREDO" > "$pasta/contagens-vps.tsv"
  checksums "$MYSQL_CONTAINER" "$PW_SEGREDO" > "$pasta/checksums-vps.tsv"
  diff -u "$pasta/contagens.tsv" "$pasta/contagens-vps.tsv" || morrer "contagens divergem após a importação"
  diff -u "$pasta/checksums.tsv" "$pasta/checksums-vps.tsv" || morrer "checksums divergem após a importação"
  msg "dados importados e conferidos (contagens e checksums iguais à origem)."
  msg "próximo: deploy/implantar.sh <VERSAO> (EM ESPERA) e validação pelo domínio; depois destino-ativar."
  msg "apague a identidade age desta máquina quando terminar: shred -u '$ident'"
}

destino_ativar() {
  [[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
  aviso "A VPS passará a ser o ÚNICO ambiente ativo. O worker da instalação local TEM de estar parado."
  read -r -p "Worker local parado e VPS validada? Digite ATIVAR-VPS: " r; [[ "$r" == "ATIVAR-VPS" ]] || morrer "cancelado"
  env_definir COMPOSE_PROFILES ativo
  dc up -d --no-build fiscal-worker
  esperar_saudavel "${PROJETO}-fiscal-worker-1" 240 || morrer "worker da VPS não ficou saudável"
  msg "VPS ATIVA (COMPOSE_PROFILES=ativo). Troque agora o acesso dos usuários."
}

destino_desativar() {
  [[ "$(id -u)" -eq 0 ]] || morrer "execute como root (sudo)"
  env_definir COMPOSE_PROFILES ""
  dc stop fiscal-worker || true
  if container_rodando "${PROJETO}-fiscal-worker-1"; then morrer "worker da VPS ainda em execução"; fi
  msg "VPS EM ESPERA (worker parado, COMPOSE_PROFILES vazio)."
}

case "$CMD" in
  origem-ensaio)     origem_ensaio "$@" ;;
  origem-segredos)   origem_segredos "$@" ;;
  origem-exportar)   origem_exportar "$@" ;;
  origem-status)     origem_status ;;
  origem-reativar)   origem_reativar ;;
  destino-segredos)  destino_segredos "$@" ;;
  destino-verificar) destino_verificar "$@" ;;
  destino-importar)  destino_importar "$@" ;;
  destino-ativar)    destino_ativar ;;
  destino-desativar) destino_desativar ;;
  -h|--help|"")      uso 0 ;;
  *) erro "subcomando desconhecido: $CMD"; uso 1 ;;
esac
