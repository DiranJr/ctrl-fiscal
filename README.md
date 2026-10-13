# CTRL Fiscal

Plataforma web para gestão de **NFS-e no padrão nacional** (Sistema Nacional da NFS-e / ADN): consulta e
sincronização das notas emitidas e recebidas, importação de XML, geração do DANFSe, rascunhos de DPS,
agendamento de lotes e, de forma controlada, emissão e cancelamento.

Cada empresa (CNPJ) é um **workspace** isolado, com um único certificado digital A1 ativo e permissões por
usuário. A plataforma foi construída para uso interno e está em evolução; a seção
[Limitações atuais](#limitações-atuais-e-requisitos-para-emissão-e-cancelamento) descreve com precisão o que está
liberado e o que continua bloqueado.

> **Importante:** a **emissão** em produção está implementada atrás de uma chave própria
> (`NFSE_PRODUCTION_EMISSION_ENABLED`, desligada por padrão) e exige, em cada lote, perfil de serviço aprovado e
> confirmação individual. O **cancelamento** em produção foi habilitado pelo titular com confirmação individual de
> cada pedido. Emissão, consulta e cancelamento foram **validados tecnicamente na Produção Restrita** oficial em
> 09/10/2026 (cenário de teste, sem validade fiscal); **nenhuma DPS nem evento foi transmitido à produção** pela
> plataforma. Os testes automatizados usam um governo simulado e não substituem essa validação oficial.

---

## Sumário

- [Funcionalidades e estado](#funcionalidades-e-estado)
- [Arquitetura](#arquitetura)
- [Organização: workspaces, certificados e permissões](#organização-workspaces-certificados-e-permissões)
- [Controles de segurança](#controles-de-segurança)
- [Instalação local](#instalação-local)
- [Configuração](#configuração)
- [Migrações do banco](#migrações-do-banco)
- [Testes](#testes)
- [Implantação em VPS Linux](#implantação-em-vps-linux)
- [Migração dos dados de uma instalação local para a VPS](#migração-dos-dados-de-uma-instalação-local-para-a-vps)
- [Backup e restauração](#backup-e-restauração)
- [Integração contínua](#integração-contínua)
- [Limitações atuais e requisitos para emissão e cancelamento](#limitações-atuais-e-requisitos-para-emissão-e-cancelamento)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Licença](#licença)

---

## Funcionalidades e estado

| Funcionalidade | Estado |
|---|---|
| **Sincronização ADN** (distribuição de DF-e por NSU, por empresa/CNPJ e ambiente) | Em uso, **somente leitura** em produção. Executada pelo `fiscal-worker` a cada 12 h ou sob demanda; cursor NSU persistido na mesma transação da gravação; XML que falha no parse vai para quarentena reprocessável. |
| **Consulta de notas** emitidas e recebidas, com filtros, detalhe, XML original e eventos | Em uso. |
| **Importação de XML/ZIP** de NFS-e | Em uso (job assíncrono, limites de tamanho e de XML, `defusedxml`). |
| **DANFSe** (PDF conforme NT SE/CGNFS-e 008) individual e **download em lote** (PDF, XML ou ambos em ZIP) | Em uso. Sem as fontes Microsoft licenciadas usa Liberation Sans e declara o desvio no PDF. |
| **Exportação XLSX** da consulta | Em uso. |
| **Clientes (tomadores)**, perfis fiscais e **perfis de serviço** por workspace (inclusive preparados a partir das notas emitidas, com revisão e aprovação) | Em uso. |
| **Propostas de revisão dos perfis** (análise das notas e das tabelas oficiais com alternativas condicionais, fundamento e perguntas) | Em uso. A proposta não altera o perfil e, enquanto aberta, impede a aprovação. Quem administra o workspace aplica uma alternativa confirmada (o perfil continua pendente de revisão) ou a descarta com justificativa; aprovar é um passo separado. Grupos de notas emitidas sem perfil aparecem como pendência, sem configuração criada. Validado no navegador em ambiente descartável com dados fictícios. |
| **Rascunhos de DPS**: "Nova NFS-e do zero" e "usar nota como referência" (lida do XML oficial de nota emitida pela própria empresa, sem copiar a identidade fiscal), validação (XSD oficial + regras do Anexo I), prévia de IBS/CBS 2026, retenções | Em uso (nada é transmitido ao salvar ou validar). Pendências específicas para códigos ausentes, inválidos ou fora de vigência na competência nova; nada é preenchido por maioria de notas. |
| **Excluir rascunho** (na lista e no editor) | Em uso. Confirmação com tomador, serviço e valor; exclusão **lógica** (migração fiscal 0012: `drafts.deleted_at`/`deleted_by`), preservando identidade, versões, origem e auditoria. Só rascunho sem transmissão e sem vínculo com processamento fiscal, com permissão `prepare`: agendado, em fila, em transmissão, com resultado a confirmar, emitido ou rejeitado é recusado (agendamento se cancela antes). Conferência com a linha travada contra editar, emitir agora e agendar; repetir a exclusão devolve o mesmo resultado. Notas, XMLs, eventos e documentos de referência não são tocados. |
| **Configuração tributária por operação** (cTribNac, NBS, tribISSQN, PIS/COFINS, cIndOp, CST, cClassTrib) | Em uso. Operação equivalente reutiliza a configuração aprovada; tratamento diferente vira variante pendente de revisão, sem alterar o perfil aprovado nem os dados do rascunho. Aprovação explícita por quem administra o workspace, com origem dos campos, versões e auditoria. |
| **"Emitir agora" e "Agendar emissão"** | Implementados, como opções separadas. Emitir agora começa o processamento no instante do servidor; agendar transmite na data escolhida. Um rascunho entra na fila uma vez só (clique repetido devolve o mesmo lote; agendar o que já está emitindo é recusado). Em produção exige a chave `NFSE_PRODUCTION_EMISSION_ENABLED`, permissão `emit`, perfil de serviço aprovado (mesma versão aplicada ao rascunho) e a confirmação individual do lote (empresa, ambiente, documentos com versão e valor, quantidade, total e a ação escolhida), conferida no servidor; sem ela o lote é recusado. |
| **Emissão** (montagem e assinatura XMLDSig da DPS, fila com leases e idempotência, reconciliação antes de reenviar, envio à Sefin Nacional) | Implementada para **Produção Restrita** (`NFSE_RESTRICTED_ENABLED`) e **produção** (`NFSE_PRODUCTION_EMISSION_ENABLED`), chaves independentes e desligadas por padrão. Antes de transmitir, o worker reconfere permissão, workspace ativo, ambiente e perfil. Validada na **Produção Restrita** oficial em 09/10/2026 (autorizada, consultada e cancelada); **nenhuma DPS transmitida à produção** pela plataforma. |
| **Cancelamento** (eventos 101101 e 101103, pedido com confirmação individual e acompanhamento da análise fiscal) | Implementado e testado com governo simulado. Em produção depende de `NFSE_PRODUCTION_CANCELLATION_ENABLED`, **habilitado pelo titular** na configuração da VPS; cada pedido exige confirmação explícita de um usuário com a permissão `cancel`. **Não foi homologado** oficialmente. |
| **Administração**: usuários, vínculos por empresa, SUPER_ADMIN, link de ativação de uso único, revogação de sessões | Em uso. |
| **Certificado A1** por workspace: upload do PFX, validação da cadeia ICP-Brasil e LCR, cofre cifrado | Em uso (um certificado ativo por workspace). |

Até o momento a plataforma foi utilizada com **uma única empresa real**.

## Arquitetura

```
                    Internet (HTTPS)
                          │
                 ┌────────▼────────┐   só na VPS: Nginx DO HOST (certificado do Certbot,
                 │ Nginx do host   │   HTTP→HTTPS) → 127.0.0.1:${HTTP_PORT}
                 └────────┬────────┘
                 ┌────────▼────────┐   nginx: CSP por área, limites de corpo e de requisição,
                 │     gateway     │   roteamento /api/core, /api/fiscal, /fiscal/, /
                 └─┬──────┬──────┬─┘
        ┌──────────┘      │      └───────────────┐
 ┌──────▼──────┐   ┌──────▼──────┐   ┌───────────▼──────┐   ┌──────────────┐
 │ web (Next)  │   │ painel-     │   │ core-api         │   │ fiscal-api   │
 │ login/portal│   │ fiscal (SPA)│   │ (FastAPI)        │   │ (FastAPI)    │
 └─────────────┘   └─────────────┘   └────────┬─────────┘   └──────┬───────┘
                                              │   rede interna     │
                         ┌────────────────────┼────────────────────┤
                  ┌──────▼──────┐     ┌───────▼───────┐     ┌──────▼───────┐
                  │  nfse-api   │◄────│ fiscal-worker │     │  MySQL 8.4   │
                  │ (conector)  │     │ (filas/sync)  │     │ 3 bancos     │
                  └──────┬──────┘     └───────────────┘     └──────────────┘
                         │ única saída de rede (egress-oficial)
                  APIs oficiais (Sefin Nacional / ADN) e LCR ICP-Brasil
```

| Componente | Tecnologia | Papel |
|---|---|---|
| `services/core-api` | Python 3.13, FastAPI, SQLAlchemy 2, Alembic | Autenticação (JWT Ed25519 + refresh em cookie), usuários, empresas/workspaces, clientes, permissões, auditoria; banco `ctrl_core`. |
| `services/fiscal-api` | FastAPI | Documentos, importação, exportação, DANFSe (ReportLab), rascunhos, lotes, cancelamentos; banco `ctrl_fiscal`. |
| `services/fiscal-worker` | Python | Filas persistentes (emissão, cancelamento, importação, exportação, lotes) e sincronização ADN, com exclusividade por leases no banco. |
| `services/nfse-api` | FastAPI, `cryptography`, `lxml` | Conector com o Sistema Nacional: cofre do certificado (AES-256-GCM), validação ICP-Brasil/LCR, assinatura XMLDSig, transporte mTLS com lista de hosts permitidos; banco `ctrl_nfse`. |
| `services/shared/ctrl_common` | Python | Configuração por arquivos de segredo, relógio injetável (UTC), problemas RFC 7807, autenticação entre serviços, domínios oficiais. |
| `apps/web` | Next.js 14 (pages router) | Login, ativação de conta e seleção de empresa. |
| `apps/painel-fiscal` | React 18 + Vite | Painel fiscal (`/fiscal/`), servido por nginx sem privilégios. |
| `packages/ui`, `packages/api-client` | TypeScript | Componentes visuais e cliente tipado das APIs (contratos em `contracts/`). |
| `infra/gateway` | nginx | Gateway único; as rotas `/internal` das APIs não são expostas. |
| MySQL 8.4 | — | Um banco por serviço; usuários de runtime só com DML e usuários `*_migrate` com DDL. |

## Organização: workspaces, certificados e permissões

- **Um workspace por empresa (CNPJ).** Todos os dados fiscais carregam `company_id`; cada consulta é filtrada
  pelo workspace e pelo ambiente (produção ou Produção Restrita).
- **Um certificado A1 ativo por workspace.** O PFX é validado (CNPJ do e-CNPJ igual ao da empresa, validade,
  usos, cadeia ICP-Brasil e LCR); o PFX e a sua senha ficam cifrados (AES-256-GCM, chave `nfse_vault_key` fora
  do banco) no banco do conector, e o upload não passa por arquivo temporário no gateway.
- **Permissões por vínculo** usuário ↔ empresa: `view`, `import`, `sync`, `review`, `prepare`, `schedule`, `emit`,
  `cancel`, `certificates`, `admin`. Cada operação exige a sua; `cancel` nunca é concedida automaticamente.
  `SUPER_ADMIN` gerencia usuários, mas não ganha acesso a documentos.
- Série da DPS e prontidão (certificado, cadastro, parâmetros) são exibidas por workspace.

## Controles de segurança

- Somente o gateway publica porta, e só em `127.0.0.1`; na VPS o HTTPS é terminado pelo Nginx do host, que
  sobrescreve `X-Forwarded-For` (o cliente não consegue forjar o IP). O gateway só aceita `X-Forwarded-For` e
  `X-Forwarded-Proto` vindos do endereço confiável `TRUSTED_PROXY_IP`.
- Redes Docker internas: MySQL, worker e conector sem acesso externo; o `nfse-api` é o único com saída
  (`egress-oficial`) e só para hosts oficiais permitidos.
- Containers `read_only`, `cap_drop: ALL`, `no-new-privileges`, usuários não-root, imagens base fixadas por digest.
- Segredos somente como arquivos (`*_FILE` / Docker secrets), nunca em variáveis de ambiente ou no repositório.
- Senhas com Argon2id; tokens de acesso curtos; refresh em cookie `HttpOnly` `__Secure-` com rotação e detecção
  de reuso; CSRF por token; verificação de `Origin`; limites de tentativa de login por e-mail e por IP.
- `APP_ENV=production` recusa iniciar com cookie sem `Secure`, origem não-HTTPS ou documentação interativa ligada.
- CSP por área, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS (na VPS) e limites de corpo por rota.
- Auditoria imutável: gatilhos recusam `UPDATE`/`DELETE` em `audit_events` (`scripts/endurecer_mysql.py`).
- Logs sem CNPJ, chaves, senhas ou XML; chamadas oficiais registradas com caminho mascarado.

## Instalação local

Requisitos: Docker com Compose v2 (Docker Desktop no Windows; Docker Engine no Linux), Git, Python 3.13+
(para scripts e testes) e Node.js 20+ (para desenvolvimento do frontend).

**Windows (PowerShell):**

```powershell
git clone <url-do-repositorio> ctrl-fiscal
cd ctrl-fiscal
powershell -ExecutionPolicy Bypass -File scripts\gerar-segredos.ps1   # cria .env e ./secrets (valores não exibidos)
docker compose up -d --build
# http://127.0.0.1:8088
```

**Linux (desenvolvimento):** copie `.env.example` para `.env`, defina `SECRETS_DIR` com um caminho absoluto
**fora** do repositório (ex.: `/opt/ctrlfiscal/segredos`) e rode `sudo deploy/gerar-segredos.sh --nova-chave-cofre`.
O script exige root porque ajusta dono e grupo dos arquivos para os usuários dos containers (no Linux o Compose
monta cada segredo preservando dono e modo do host). Depois, `docker compose up -d --build`.

Primeiro acesso: não há usuário padrão nem senha embutida. Para explorar com dados **fictícios**, use o ambiente
descartável (abaixo), que cria empresas de demonstração e contas de teste. Numa instalação nova real ainda não
existe um assistente de criação do primeiro administrador (ver [Limitações](#limitações-atuais-e-requisitos-para-emissão-e-cancelamento));
contas existentes podem receber `SUPER_ADMIN` com `python -m core_api.super_admin --email <email>` dentro da
imagem do `core-api`.

## Configuração

Variáveis não secretas ficam em `.env` (modelo: `.env.example`; na VPS: `deploy/env.producao.exemplo`).
As principais:

| Variável | Uso |
|---|---|
| `HTTP_PORT` | Porta do gateway em `127.0.0.1` (padrão 8088). |
| `APP_ENV` | `development`, `test` ou `production`. |
| `ALLOWED_ORIGINS` | Origens aceitas pelo core-api (na VPS é derivada de `DOMINIO`). |
| `ALLOW_INSECURE_DEV_COOKIE` | Só para HTTP local; proibido em produção. |
| `NFSE_RESTRICTED_ENABLED` | Liga a emissão em Produção Restrita (padrão `false`). |
| `NFSE_PRODUCTION_DISTRIBUTION_ENABLED` | Leitura da distribuição ADN de produção. |
| `NFSE_PRODUCTION_CANCELLATION_ENABLED` | Cancelamento em produção (padrão do Compose `false`; `true` nos exemplos, conforme decisão do titular; não homologado). |
| `NFSE_PRODUCTION_EMISSION_ENABLED` | Emissão em produção, independente do cancelamento (padrão `false`, também nos exemplos); ligada explicitamente só na instalação operacional autorizada. |
| `TRUSTED_PROXY_IP` | Único endereço cujo `X-Forwarded-For`/`X-Forwarded-Proto` o gateway aceita (`0.0.0.0` = nenhum; na VPS `10.250.250.1`). |
| `NFSE_MOCK_ENABLED` | Governo simulado; somente testes. |
| `DANFSE_FONT_DIR` | Pasta com fontes licenciadas do DANFSe (opcional). |
| `SECRETS_DIR` | Pasta dos arquivos de segredo. |
| `VERSAO`, `DOMINIO`, `COMPOSE_PROFILES`, `DB_POOL_*` | Somente VPS (ver abaixo). |

Segredos (arquivos em `SECRETS_DIR`): senhas do MySQL (root, runtime e migração de cada banco), par Ed25519 do
JWT, segredo de CSRF, chaves internas entre serviços e `nfse_vault_key` (chave do cofre dos certificados).
Gere-os com `scripts/gerar-segredos.ps1` (Windows) ou `deploy/gerar-segredos.sh` (Linux). Nenhum deles é versionado.

## Migrações do banco

Cada serviço tem suas migrações Alembic (`services/*/migrations`), executadas pelos serviços `core-migrate`,
`fiscal-migrate` e `nfse-migrate` do Compose, com credenciais DDL próprias, antes de as APIs subirem
(`docker compose up` já as executa). Depois das migrações, aplique os gatilhos de auditoria imutável:

```bash
python -I scripts/endurecer_mysql.py ctrlfiscal-mysql-1
```

## Testes

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r tests/requirements.txt
python -m pytest tests -q -p no:cacheprovider -W ignore      # SQLite em arquivo temporário
python -I scripts/pytest_mysql.py                             # mesma suíte num MySQL 8.4 temporário (Docker)
```

- `tests/conftest.py` sobe core-api, fiscal-api, nfse-api e worker em processo, com migrações reais e governo
  simulado. Com `CTRL_TEST_MYSQL_URL` (ou `CTRL_TEST_MYSQL_URL_FILE`) usa MySQL; senão, SQLite.
- `scripts/pytest_mysql.py` cria um container MySQL com senha aleatória, roda a suíte e remove tudo ao final.
- Frontend: `npm ci`, build de `packages/api-client` e `packages/ui`, `tsc --noEmit` nos apps e
  `node --test --import tsx packages/api-client/test/*.test.ts`.
- **Ambiente descartável** (`scripts/ambiente_descartavel.py preparar|subir|semear|destruir`): pilha completa em
  projeto Compose separado (porta 8098), segredos próprios, integrações oficiais desligadas e governo simulado;
  `scripts/validar_descartavel.py`, `scripts/smoke_gateway.py` e `scripts/ui_validacao_descartavel.py` exercitam
  a pilha real com contas fictícias. Resultados vão para `docs/evidencias/` (ignorada pelo Git).

## Implantação em VPS Linux

Tudo fica em `deploy/` (explicações nos comentários de cada arquivo). Nada é implantado automaticamente e nada é
construído na VPS: as imagens são geradas e empacotadas na máquina de build.

**Topologia na VPS** (Debian 13, 4 vCPU, 16 GB, compartilhada com outros sistemas): o **Nginx do host**, que já
ocupa 80/443 e já tem o certificado do Certbot para o domínio, termina o HTTPS e encaminha para o gateway do
CTRL Fiscal em `127.0.0.1:${HTTP_PORT}` (padrão 8088; **nunca 3001**, usada por outro sistema). A pilha não
publica nenhuma outra porta e todos os comandos usam o projeto Compose `ctrlfiscal` explícito: nenhum container,
rede, volume ou imagem de outro sistema é tocado, e os scripts nunca reiniciam o Docker nem o Nginx (só `reload`
manual do Nginx, depois de `nginx -t`).

| Arquivo | Função |
|---|---|
| `compose.prod.yaml` | Sobreposição de produção: imagens `ctrlfiscal/<serviço>:${VERSAO}` com rótulos OCI do commit, `APP_ENV=production`, perfil `ativo` (worker), orçamento de CPU/memória, MySQL dimensionado, pool de conexões, healthchecks, logs com rotação, `TZ=America/Sao_Paulo` (MySQL segue em UTC). |
| `nginx/fiscal.grupoaelengenharia.com.br.conf` | Site do Nginx do host: 80 → 301 HTTPS, 443 com o certificado do Certbot, HSTS só deste host, `client_max_body_size 26m`, `proxy_pass` para o gateway e `X-Forwarded-For` sobrescrito. Instruções de instalação segura no próprio arquivo. |
| `gateway/00-realip.conf.template` | O gateway passa a usar o IP real informado pelo Nginx do host, aceitando-o só de `TRUSTED_PROXY_IP`. |
| `env.producao.exemplo` | Modelo do `.env` da VPS (sem segredos). |
| `empacotar-imagens.sh` | Na máquina de build: constrói as 6 imagens da `VERSAO`, salva com as imagens base fixadas por digest num `.tar.gz` com manifesto e SHA-256. |
| `gerar-segredos.sh` | Gera ou importa os segredos (pasta 700/root; arquivos 440 com dono 10001 e grupo 999, lidos pelos containers). |
| `inventario-vps.sh` | Inventário **somente leitura** do servidor (recursos, Docker, portas, sites do Nginx, redes, firewall). |
| `implantar.sh` | Implantação manual: confere porta/Nginx/segredos, confere o SHA-256 e carrega o pacote, faz backup cifrado, migrações, gatilhos de auditoria, sobe e verifica a saúde. |
| `rollback.sh` | Volta as imagens para a versão anterior (já carregadas); recusa se o banco tiver migrações que a versão antiga não conhece. |
| `backup-criptografado.sh` / `restaurar-verificado.sh` | Backup cifrado com chave pública e restauração verificada em MySQL temporário. |
| `migracao-dados.sh` | Migração dos dados da instalação atual para a VPS, com ensaio, corte, ativação e retorno. |
| `firewall-ufw.sh` | Exemplo de firewall que preserva o SSH e os serviços existentes (simulação por padrão). |

**Orçamento de recursos** (tetos por serviço; detalhes em `compose.prod.yaml`): MySQL 1,0 vCPU/1536 MB
(`innodb_buffer_pool_size=1G`, `max_connections=120`), fiscal-api e fiscal-worker 0,5/640 MB, core-api 0,4/384 MB,
nfse-api 0,2/256 MB, web 0,2/320 MB, gateway 0,2/128 MB, painel 0,1/64 MB — total ≈ 3,1 vCPU e 3,9 GB, deixando
folga para o host e os demais sistemas. Pool de 5+5 conexões por serviço; worker com `EMISSION_MAX_IN_FLIGHT=5`,
`EMISSION_BATCH_SIZE=5` e `SYNC_MAX_PAGES_PER_CYCLE=10`.

### Instalação do zero até o modo espera

Na **máquina de build** (com Docker; a árvore no commit a implantar):

```bash
V=$(git rev-parse --short=12 HEAD)
PLATAFORMA=linux/amd64 deploy/empacotar-imagens.sh "$V"        # use linux/arm64 se a VPS for ARM
git archive --format=tar.gz -o "pacotes/ctrlfiscal-codigo-$V.tar.gz" HEAD
scp pacotes/ctrlfiscal-imagens-$V.* pacotes/ctrlfiscal-codigo-$V.tar.gz usuario@vps:/opt/ctrlfiscal/pacotes/
```

Na **VPS** (como root):

```bash
install -d -m 755 /opt/ctrlfiscal/app && cd /opt/ctrlfiscal/app
tar -xzf /opt/ctrlfiscal/pacotes/ctrlfiscal-codigo-<V>.tar.gz                # código da MESMA versão das imagens
bash deploy/inventario-vps.sh > /root/inventario-$(date +%Y%m%d).txt        # somente leitura; revise antes de seguir
cp deploy/env.producao.exemplo .env && chmod 600 .env                        # ajuste HTTP_PORT (≠ 3001) e BACKUP_AGE_RECIPIENT
deploy/gerar-segredos.sh --nova-chave-cofre        # instalação nova; numa migração use --importar-de (ver abaixo)
deploy/implantar.sh <V> --pacote /opt/ctrlfiscal/pacotes/ctrlfiscal-imagens-<V>.tar.gz --sem-backup
# Site do Nginx do host (siga o cabeçalho do arquivo: backup do site atual, nginx -t, reload):
install -m 644 deploy/nginx/fiscal.grupoaelengenharia.com.br.conf /etc/nginx/sites-available/
ln -sfn /etc/nginx/sites-available/fiscal.grupoaelengenharia.com.br.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

O resultado é o ambiente **em espera** (`COMPOSE_PROFILES` vazio): aplicação acessível em
`https://${DOMINIO}`, sem worker. Atualizações seguem o mesmo caminho
(`empacotar-imagens.sh` → `implantar.sh <V> --pacote ...`, agora com backup automático antes).

**Rollback de imagens:** `deploy/rollback.sh <VERSAO_ANTERIOR>` (sem argumento, usa a anterior do histórico em
`/opt/ctrlfiscal/estado/historico-implantacoes.tsv`). Migrações não são desfeitas; se o banco já estiver em
migração desconhecida pela versão antiga, o rollback é recusado. Restaurar backup por cima de documentos fiscais
novos é proibido — rollback de banco só com análise.

Migração fiscal **0012** (exclusão lógica de rascunhos) só acrescenta colunas que aceitam nulo: a versão anterior (até a 0011) funciona com o banco migrado, mas o `rollback.sh` recusa por comparar a revisão do banco. Para voltar só as imagens afetadas sem desfazer a migração, recrie `fiscal-api` e `painel-fiscal` com a versão anterior (`VERSAO=<anterior>` no `docker compose ... up -d --no-deps --no-build fiscal-api painel-fiscal`); rascunhos excluídos voltam a aparecer enquanto a versão anterior estiver em uso e somem de novo ao reimplantar a atual. Não use `alembic downgrade` (apagaria a marca de exclusão).

**Ambiente ativo:** o perfil Compose `ativo` liga o `fiscal-worker` (sincronização ADN, filas de emissão e
cancelamento, importações, exportações e limpezas). **Só um ambiente pode ter worker ativo** (a VPS ou a
instalação atual): dois workers com o mesmo certificado e bancos diferentes duplicariam consultas ao ADN e
pedidos de cancelamento, e os bancos divergiriam.

**Fontes do DANFSe:** a imagem inclui só Liberation Sans (SIL OFL). Arial e Microsoft Sans Serif não são
distribuídas nem instaladas; quem tiver licença pode montar uma pasta somente leitura e definir `DANFSE_FONT_DIR`
(exemplo comentado em `compose.prod.yaml`).

## Migração dos dados de uma instalação local para a VPS

Roteiro completo, com todos os comandos, no cabeçalho de `deploy/migracao-dados.sh`. Em resumo:

1. **Preparação** (a instalação atual continua ativa): `origem-ensaio` gera um backup cifrado com `age` sem parar
   nada; `origem-segredos` gera, **em arquivo separado e cifrado**, os segredos necessários — senhas do banco,
   JWT, CSRF, chaves internas e, principalmente, `nfse_vault_key` (o PFX do certificado está cifrado no banco com
   ela; **sem a mesma chave o certificado não abre**). Transfira os dois por SSH (`rsync`/`scp`), em comandos
   separados. Na VPS (em espera): `destino-segredos` instala os segredos e `destino-verificar` restaura o ensaio
   num MySQL temporário e confere as contagens.
2. **Corte:** `origem-exportar` para **primeiro o worker local** (`docker compose -p ctrlfiscal stop fiscal-worker`)
   e os serviços de aplicação, e gera o backup final com contagens e checksums por tabela. Na VPS:
   `destino-verificar` (contagens **e** checksums iguais aos da origem), `destino-importar` (só aceita banco vazio e
   VPS em espera; confere de novo), `implantar.sh <V>` e validação pelo domínio (login, workspaces, notas, DANFSe,
   prontidão do certificado).
3. **Ativação:** `destino-ativar` (`COMPOSE_PROFILES=ativo`, sobe o worker da VPS). Só então troque o acesso dos
   usuários.
4. **Retorno:** se a VPS falhar **antes de qualquer operação nova nela**, `destino-desativar` na VPS e
   `origem-reativar` na instalação local (religa serviços e worker). Depois de operações novas na VPS, voltar exige
   migrar os dados de volta pelo mesmo procedimento.

## Backup e restauração

- `deploy/backup-criptografado.sh [rótulo]`: `mysqldump --single-transaction` dos três bancos e do volume de
  exportações, cifrados em fluxo com `age` (ou GPG) usando chave **pública**; checksums, manifesto, contagens e
  retenção configurável. A chave privada fica fora da VPS.
- `deploy/restaurar-verificado.sh <pasta> <identidade>`: restaura num MySQL temporário isolado e compara as
  contagens de todas as tabelas antes de qualquer uso do backup.
- Restaurar um backup por cima do banco em uso **não** é rollback: com documentos fiscais novos isso perde dados
  e diverge do Sistema Nacional. Rollback de banco só com análise.
- Na instalação local (Windows) há `scripts/backup_verificado.py`.

## Integração contínua

`.github/workflows/ci.yml` (sem segredos e sem implantação): checagem de arquivos proibidos e de chaves privadas,
`bash -n`/`shellcheck` dos scripts, pytest com SQLite, pytest com MySQL 8.4 efêmero (opcional: push na `main` ou
execução manual), typecheck e testes TypeScript, build das imagens Docker sem push e validação do Compose de
produção com valores fictícios.

## Limitações atuais e requisitos para emissão e cancelamento

- **Emissão em produção:** implementada e autorizada pelo titular atrás de `NFSE_PRODUCTION_EMISSION_ENABLED`
  (padrão `false`). Funcionalidade habilitada não significa empresa pronta: a tela de prontidão mostra as duas
  coisas separadamente. Cada lote exige permissão `emit`, perfil de serviço **aprovado** e confirmação individual da
  versão revisada; nenhum perfil é aprovado automaticamente. **Não homologada** oficialmente e nunca exercitada contra
  a Sefin de produção: a primeira nota transmitida terá efeito fiscal real.
- **Produção Restrita — validação técnica oficial (09/10/2026):** em ambiente temporário isolado
  (`scripts/ambiente_producao_restrita.py` e `scripts/validacao_producao_restrita.py`), com um **cenário de teste
  declarado** (R$ 1,00, série exclusiva de teste), a Sefin de testes **autorizou** a NFS-e emitida por "Emitir agora",
  a nota foi consultada (valores, IBS/CBS calculado pela Sefin, XML e DANFSe) e **cancelada** (evento 101101
  consultado). Os defeitos encontrados — assinatura (E0714, canonicalização) e relógio (E0008) — foram corrigidos.
  Evidência sem dados sensíveis: `docs/evidencias/producao-restrita-validacao-20261009.json`. O cenário de teste não
  é a classificação fiscal das operações reais: cada operação real continua exigindo revisão e aprovação próprias.
- **Cancelamento em produção:** habilitado pelo titular (`NFSE_PRODUCTION_CANCELLATION_ENABLED=true` na VPS), com
  confirmação individual de cada pedido por usuário com a permissão `cancel`; **não homologado** oficialmente e com
  efeito fiscal real.
- **Testes simulados não equivalem à homologação oficial.** O governo simulado reproduz contratos documentados,
  não o comportamento real do Sistema Nacional.
- Uso real até agora: uma única empresa. Não há assistente para criar o primeiro administrador numa instalação nova.
- Documentos oficiais: `docs/oficial/` traz cópias integrais de manuais, notas técnicas, anexos, páginas e pacotes
  XSD do portal gov.br/nfse (licença CC BY-ND 3.0 declarada pelo portal). As especificações OpenAPI/Swagger das
  APIs da Sefin/ADN, obtidas com certificado digital, **não** são publicadas: `docs/oficial/fontes.json` lista URL,
  versão, data e SHA-256 de cada arquivo, inclusive dos retirados, e os scripts de obtenção. Mudanças publicadas
  depois das datas indicadas exigem revisão.
- O DANFSe sem as fontes Microsoft usa Liberation Sans (equivalência métrica, não conformidade integral).

## Estrutura do repositório

```
apps/            web (Next.js) e painel-fiscal (React/Vite)
packages/        ui e api-client (TypeScript)
services/        core-api, fiscal-api, fiscal-worker, nfse-api e shared (Python)
infra/           gateway nginx e inicialização do MySQL
contracts/       OpenAPI exportados das APIs
deploy/          implantação em VPS Linux (Compose de produção, site do Nginx do host, scripts)
docs/oficial/    documentação e esquemas oficiais da NFS-e (fontes.json: origem e SHA-256 de cada arquivo)
scripts/         utilitários de desenvolvimento, validação e operação local
tests/           testes integrados (pytest)
```

## Licença

O código do CTRL Fiscal é distribuído sob a **licença MIT** — veja o arquivo [`LICENSE`](LICENSE)
(Copyright (c) 2026 Diran Lima Santos Junior).

A licença MIT vale para o código deste projeto. Materiais de terceiros incluídos ou usados por ele **continuam
sujeitos às respectivas licenças e atribuições**, que a MIT do projeto não substitui:

- **Dependências** (pacotes Python e npm, imagens base de contêiner como MySQL e Nginx): cada uma segue a licença
  declarada pelo seu autor, conforme os respectivos repositórios e metadados dos pacotes.
- **Fontes do DANFSe:** Liberation Sans, incluída nas imagens, segue a SIL Open Font License; as fontes Microsoft
  (Arial, Microsoft Sans Serif) não são distribuídas e só podem ser usadas por quem tiver licença própria.
- **Documentos oficiais** em `docs/oficial/` (manuais, notas técnicas, anexos, páginas e esquemas XSD do portal
  gov.br/nfse): cópias integrais, sem modificação, sob a licença declarada pelo portal (CC BY-ND 3.0), com origem,
  versão, data e SHA-256 registrados em `docs/oficial/fontes.json`. As especificações obtidas com certificado
  digital não são publicadas.
- **Tabelas oficiais** (lista de serviços, NBS, municípios, cIndOp, cClassTrib) usadas pelo código mantêm a fonte
  e as condições de uso do órgão que as publica.
