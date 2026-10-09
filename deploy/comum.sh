#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2016  # variáveis usadas pelos scripts que carregam este arquivo; $(...) avaliado no container
# CTRL Fiscal — funções comuns dos scripts de deploy/ (carregado com "source"; não executar diretamente).
#
# Convenções:
#   - RAIZ: raiz do repositório (pai de deploy/). ENV_FILE: .env da raiz (copiado de deploy/env.producao.exemplo).
#   - Projeto Compose "ctrlfiscal": containers ctrlfiscal-<serviço>-1, volumes ctrlfiscal_<volume>.
#   - Nenhuma função imprime valor de segredo. Senhas do MySQL são lidas DENTRO do container (/run/secrets).

if [[ -n "${CTRL_COMUM_CARREGADO:-}" ]]; then return 0; fi
CTRL_COMUM_CARREGADO=1
# Git Bash (Windows) converte argumentos que parecem caminhos POSIX (ex.: /run/secrets); desliga isso.
# Sem efeito no Linux.
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

# "pwd -W" só existe no Git Bash (Windows): devolve C:/... para que git.exe/docker.exe entendam o caminho, já
# que a conversão automática de caminhos está desligada abaixo. No Linux cai no "pwd" comum.
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && { pwd -W 2>/dev/null || pwd; })"
ENV_FILE="${CTRL_ENV_FILE:-$RAIZ/.env}"
PROJETO="${CTRL_PROJETO:-ctrlfiscal}"   # outro nome só para validação isolada dos scripts
MYSQL_CONTAINER="${CTRL_MYSQL_CONTAINER:-${PROJETO}-mysql-1}"
ESTADO_DIR="${CTRL_ESTADO_DIR:-/opt/ctrlfiscal/estado}"
BANCOS=(ctrl_core ctrl_fiscal ctrl_nfse)
# Mesma imagem fixada do compose.yaml (restauração de teste usa a mesma versão do servidor).
IMAGEM_MYSQL="mysql:8.4@sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242"
IMAGEM_GATEWAY="nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8"
SERVICOS_IMAGEM=(core-api fiscal-api fiscal-worker nfse-api web painel-fiscal)

# Expressões de senha avaliadas DENTRO do container MySQL (nunca no host).
PW_SEGREDO='"$(cat /run/secrets/mysql_root_password)"'
PW_AMBIENTE='"$MYSQL_ROOT_PASSWORD"'

msg()   { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
aviso() { printf '[%s] AVISO: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
erro()  { printf '[%s] ERRO: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
morrer() { erro "$*"; exit 1; }

exigir_comando() {
  local c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || morrer "comando obrigatório ausente: $c"; done
}

# Lê KEY do .env sem executar o arquivo (última ocorrência vence; aspas simples/duplas externas removidas).
env_valor() {
  local chave="$1" padrao="${2:-}" linha
  [[ -f "$ENV_FILE" ]] || { printf '%s' "$padrao"; return 0; }
  linha="$(grep -E "^${chave}=" "$ENV_FILE" | tail -n 1 || true)"
  if [[ -z "$linha" ]]; then printf '%s' "$padrao"; return 0; fi
  linha="${linha#*=}"; linha="${linha%$'\r'}"
  linha="${linha#\"}"; linha="${linha%\"}"; linha="${linha#\'}"; linha="${linha%\'}"
  printf '%s' "${linha:-$padrao}"
}

# Grava/atualiza KEY=valor no .env (arquivo temporário + mv, preservando modo 600).
env_definir() {
  local chave="$1" valor="$2" tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  if grep -qE "^${chave}=" "$ENV_FILE"; then
    awk -v k="$chave" -v v="$valor" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"; printf '%s=%s\n' "$chave" "$valor" >> "$tmp"
  fi
  chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

# docker compose com os dois arquivos (base + produção) e o .env da VPS.
dc() {
  docker compose --project-name "$PROJETO" --project-directory "$RAIZ" --env-file "$ENV_FILE" \
    -f "$RAIZ/compose.yaml" -f "$RAIZ/deploy/compose.prod.yaml" "$@"
}

perfil_ativo() { [[ ",$(env_valor COMPOSE_PROFILES)," == *",ativo,"* ]]; }

container_rodando() { [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]; }

# SQL pela entrada padrão; a senha de root é resolvida dentro do container.
#   mysql_exec <container> <expressão-de-senha> [opções extras do cliente mysql]
mysql_exec() {
  local c="$1" pw="$2"; shift 2
  docker exec -i "$c" sh -c "MYSQL_PWD=$pw exec mysql -uroot -N -B --default-character-set=utf8mb4 $*"
}

# Contagem de linhas de TODAS as tabelas dos 3 bancos: "banco.tabela<TAB>linhas", ordenado.
contagens() {
  local c="$1" pw="$2" tabelas t sql=""
  tabelas="$(printf '%s\n' "SELECT CONCAT(table_schema,'.',table_name) FROM information_schema.tables
    WHERE table_schema IN ('ctrl_core','ctrl_fiscal','ctrl_nfse') AND table_type='BASE TABLE' ORDER BY 1;" \
    | mysql_exec "$c" "$pw")" || return 1
  [[ -n "$tabelas" ]] || { erro "nenhuma tabela encontrada em $c"; return 1; }
  for t in $tabelas; do sql+="SELECT '${t}', COUNT(*) FROM ${t};"$'\n'; done
  printf '%s' "$sql" | mysql_exec "$c" "$pw" | LC_ALL=C sort
}

# CHECKSUM TABLE de todas as tabelas (mesma versão do MySQL dos dois lados): "banco.tabela<TAB>checksum".
checksums() {
  local c="$1" pw="$2" tabelas lista
  tabelas="$(printf '%s\n' "SELECT CONCAT(table_schema,'.',table_name) FROM information_schema.tables
    WHERE table_schema IN ('ctrl_core','ctrl_fiscal','ctrl_nfse') AND table_type='BASE TABLE' ORDER BY 1;" \
    | mysql_exec "$c" "$pw")" || return 1
  lista="$(printf '%s' "$tabelas" | paste -sd, -)"
  printf 'CHECKSUM TABLE %s EXTENDED;\n' "$lista" | mysql_exec "$c" "$pw" | LC_ALL=C sort
}

# Espera um container ficar "healthy" (ou só "running", se não tiver healthcheck).
esperar_saudavel() {
  local c="$1" limite="${2:-180}" i estado
  for ((i = 0; i < limite; i += 3)); do
    estado="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || true)"
    case "$estado" in
      healthy|running) return 0 ;;
      unhealthy|exited|dead) erro "$c: estado $estado"; return 1 ;;
    esac
    sleep 3
  done
  erro "$c: não ficou saudável em ${limite}s (estado: ${estado:-desconhecido})"
  return 1
}
