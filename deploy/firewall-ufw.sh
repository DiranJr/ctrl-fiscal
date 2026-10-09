#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — EXEMPLO de firewall com ufw. NÃO EXECUTE SEM REVISAR com o inventário da VPS em mãos
# (deploy/inventario-vps.sh): outros sistemas podem depender de portas que este exemplo não conhece.
#
# Princípios:
#   - PRESERVAR o acesso SSH (porta detectada do sshd; nunca bloqueia antes de liberar o SSH);
#   - PRESERVAR as portas de outros serviços já existentes: liste-as em PORTAS_EXTRAS antes de rodar;
#   - liberar 80/tcp e 443/tcp para o Nginx do host (normalmente já liberadas, pois ele atende outros sites);
#   - MySQL e APIs não têm porta publicada (rede interna do Docker), então não há regra para elas;
#   - o gateway publica só em 127.0.0.1 (loopback), inalcançável de fora.
#
# ATENÇÃO (Docker + ufw): portas publicadas por containers com "0.0.0.0:porta" passam pela cadeia DOCKER do
# iptables e NÃO são filtradas pelas regras comuns do ufw. Por isso a pilha do CTRL Fiscal só publica 80/443 (que
# devem ser públicas de qualquer forma) e o resto fica em loopback/rede interna. Para restringir portas de
# containers de OUTROS sistemas use a cadeia DOCKER-USER (fora do escopo deste exemplo).
#
# Uso (após revisão):  sudo PORTAS_EXTRAS="8443/tcp 51820/udp" bash deploy/firewall-ufw.sh --aplicar
#   sem --aplicar, apenas mostra os comandos que seriam executados.
#
# Endurecimento do SSH (recomendação; aplicar manualmente, com uma segunda sessão SSH aberta para não se trancar):
#   /etc/ssh/sshd_config.d/10-ctrlfiscal.conf
#     PasswordAuthentication no
#     KbdInteractiveAuthentication no
#     PermitRootLogin prohibit-password      # ou "no", se houver usuário administrativo com sudo
#     PubkeyAuthentication yes
#     MaxAuthTries 3
#   Chaves ed25519 por pessoa (ssh-keygen -t ed25519), nunca compartilhadas; depois: sshd -t && systemctl reload ssh
#   Considere fail2ban e atualizações automáticas de segurança (unattended-upgrades).
# =============================================================================================================
set -euo pipefail

APLICAR=false
[[ "${1:-}" == "--aplicar" ]] && APLICAR=true
command -v ufw >/dev/null 2>&1 || { echo "ufw não instalado (apt install ufw). Nada feito."; exit 1; }

executar() {
  printf '+ %s\n' "$*"
  if $APLICAR; then "$@"; fi
}

# Porta(s) do SSH em uso: lê o sshd efetivo; se não conseguir, assume 22.
PORTAS_SSH="$(sshd -T 2>/dev/null | awk '$1=="port"{print $2}' | sort -u | tr '\n' ' ' || true)"
PORTAS_SSH="${PORTAS_SSH:-22}"

echo "Situação atual do ufw:"
ufw status verbose || true
echo
$APLICAR || echo "(simulação: nenhum comando será aplicado; use --aplicar depois de revisar)"

# 1) SSH primeiro, com limite de tentativas (ufw limit: 6 conexões/30 s por IP).
for p in $PORTAS_SSH; do executar ufw limit "${p}/tcp" comment 'SSH (preservado)'; done

# 2) Serviços já existentes na VPS (informados pelo operador a partir do inventário).
for p in ${PORTAS_EXTRAS:-}; do executar ufw allow "$p" comment 'servico existente (preservado)'; done

# 3) Nginx do host (HTTPS de todos os sites, inclusive o CTRL Fiscal); o gateway do CTRL Fiscal fica em loopback.
executar ufw allow 80/tcp comment 'Nginx do host (redirecionamento e ACME)'
executar ufw allow 443/tcp comment 'Nginx do host HTTPS'

# 4) Política padrão e ativação (só depois das liberações acima).
executar ufw default deny incoming
executar ufw default allow outgoing
if ! ufw status | grep 'Status: active' >/dev/null; then
  executar ufw --force enable
fi
echo
echo "Confira: ufw status numbered  — e teste um NOVO login SSH antes de encerrar a sessão atual."
