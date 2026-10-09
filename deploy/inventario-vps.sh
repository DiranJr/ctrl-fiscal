#!/usr/bin/env bash
# =============================================================================================================
# CTRL Fiscal — inventário SOMENTE LEITURA da VPS, antes de instalar qualquer coisa.
#
# Objetivo: conhecer o servidor e PRESERVAR os sistemas que já rodam nele (portas, proxies, redes, firewall).
# Não instala, não altera, não reinicia nada. Não lê arquivos de configuração de outros sistemas nem segredos.
#
# Uso:  bash deploy/inventario-vps.sh            (sem root: parte das informações pode faltar)
#       sudo bash deploy/inventario-vps.sh > inventario-$(hostname)-$(date +%Y%m%d).txt
#
# Saída: texto puro. Revise antes de compartilhar (contém nomes de containers/serviços do servidor).
# =============================================================================================================
set -euo pipefail

secao() { printf '\n==================== %s ====================\n' "$*"; }
tem()   { command -v "$1" >/dev/null 2>&1; }
rodar() { # executa e mostra a saída; falha não interrompe o inventário
  printf '$ %s\n' "$*"
  "$@" 2>&1 || printf '(comando falhou ou indisponível: código %s)\n' "$?"
}

secao "Data e identificação"
date -Is
rodar hostname
rodar uptime

secao "Sistema operacional e kernel"
[[ -r /etc/os-release ]] && grep -E '^(PRETTY_NAME|VERSION_ID|ID)=' /etc/os-release
rodar uname -srmo

secao "Arquitetura (amd64/arm64 define as imagens a construir)"
rodar uname -m
tem dpkg && rodar dpkg --print-architecture

secao "CPU"
if tem lscpu; then lscpu | grep -E '^(Architecture|CPU\(s\)|Model name|Thread|Core|Socket)' || true; else rodar nproc; fi

secao "Memória e swap"
rodar free -h
tem swapon && rodar swapon --show

secao "Disco"
rodar df -hT -x tmpfs -x devtmpfs -x overlay
tem lsblk && rodar lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT

secao "Fuso horário e relógio (NTP)"
tem timedatectl && rodar timedatectl status

secao "Docker"
if tem docker; then
  rodar docker version --format 'cliente {{.Client.Version}} / servidor {{.Server.Version}}'
  rodar docker compose version
  rodar docker info --format 'driver={{.Driver}} cgroup={{.CgroupVersion}} raiz={{.DockerRootDir}} containers={{.Containers}} imagens={{.Images}}'
  secao "Containers (todos os projetos)"
  rodar docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  secao "Projetos Compose"
  rodar docker compose ls -a
  secao "Redes Docker e sub-redes (evitar colisão com PROXY_SUBNET)"
  for n in $(docker network ls -q); do
    docker network inspect -f '{{.Name}}  driver={{.Driver}}  interna={{.Internal}}  sub-redes={{range .IPAM.Config}}{{.Subnet}} {{end}}' "$n" 2>/dev/null || true
  done
  secao "Volumes Docker"
  rodar docker volume ls
  secao "Uso de disco do Docker"
  rodar docker system df
else
  echo "Docker não instalado."
fi

secao "Portas em escuta (TCP/UDP)"
if tem ss; then rodar ss -tulpn; elif tem netstat; then rodar netstat -tulpn; fi

secao "Portas 80/443 (Nginx do host) e porta do gateway do CTRL Fiscal"
if tem ss; then
  ocupadas="$(ss -Htln '( sport = :80 or sport = :443 )' 2>/dev/null || true)"
  if [[ -n "$ocupadas" ]]; then
    echo "OCUPADAS (esperado: Nginx do host). O CTRL Fiscal NÃO usa 80/443: o site deploy/nginx/*.conf do Nginx"
    echo "do host encaminha para 127.0.0.1:<HTTP_PORT> (padrão 8088; nunca 3001)."
    echo "$ocupadas"
  else
    echo "80 e 443 livres (o Nginx do host deveria estar nelas)."
  fi
  # 8088: porta padrão do gateway do CTRL Fiscal; 3001: outro sistema (Carreiras), que não pode ser afetado.
  for p in 8088 3001; do
    if ss -Hltn "( sport = :$p )" 2>/dev/null | grep . >/dev/null; then echo "porta $p: EM USO"; else echo "porta $p: livre"; fi
  done
fi

secao "Nginx do host: sites habilitados e certificados (somente nomes)"
ls -l /etc/nginx/sites-enabled/ 2>/dev/null || echo "(sem /etc/nginx/sites-enabled)"
ls /etc/nginx/conf.d/ 2>/dev/null || true
ls /etc/letsencrypt/live/ 2>/dev/null || echo "(sem permissão ou sem certificados do Certbot)"
if tem nginx; then nginx -v 2>&1 || true; nginx -t 2>&1 | tail -n 2 || true; fi

secao "Servidores web/proxy instalados no host"
for s in nginx apache2 httpd caddy traefik haproxy; do
  if tem systemctl && systemctl list-unit-files "${s}.service" >/dev/null 2>&1 && \
     systemctl list-unit-files "${s}.service" | grep "${s}.service" >/dev/null; then
    printf '%s: %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || true)"
  fi
done

secao "Firewall"
if tem ufw; then rodar ufw status verbose; else echo "ufw não instalado"; fi
if tem nft; then
  echo "nftables: tabelas presentes:"; nft list tables 2>&1 || true
fi
if tem iptables; then
  echo "iptables (resumo das cadeias INPUT/DOCKER-USER):"
  iptables -S INPUT 2>&1 | head -n 40 || true
  iptables -S DOCKER-USER 2>&1 | head -n 20 || true
fi

secao "SSH (somente diretivas relevantes; sem chaves)"
if [[ -r /etc/ssh/sshd_config ]]; then
  grep -hiE '^\s*(Port|PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|KbdInteractiveAuthentication|AllowUsers|AllowGroups)\b' \
    /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null || echo "(sem diretivas explícitas: valem os padrões)"
else
  echo "sem permissão para ler /etc/ssh/sshd_config (rode com sudo)"
fi

secao "Ferramentas usadas pelos scripts de deploy"
for c in git curl openssl age gpg sha256sum gzip python3 rsync; do
  printf '%-10s %s\n' "$c" "$(command -v "$c" || echo AUSENTE)"
done

secao "Atualizações automáticas de segurança"
if tem systemctl; then
  printf 'unattended-upgrades: %s\n' "$(systemctl is-enabled unattended-upgrades 2>/dev/null || echo 'não instalado')"
fi

secao "Fim do inventário (nada foi alterado)"
