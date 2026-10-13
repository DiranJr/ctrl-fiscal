import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Input, Modal, Select } from '@ctrl-fiscal/ui';
import { AdminUser, ProblemDetailsException, Workspace, WorkspaceCreated } from '@ctrl-fiscal/api-client';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { ScreenSection, TableEmpty } from '../components/screen/ScreenSection.js';
import { Notice } from '../components/screen/Notice.js';
import { CredentialLinkModal } from '../components/admin/CredentialLinkModal.js';

/** Administração → Workspaces. Criar: SUPER_ADMIN ou permissão create_workspace (conferida no servidor a cada
 * criação). Ativar/suspender: só SUPER_ADMIN. O workspace nasce EM PREPARAÇÃO, sem certificado nem transmissão. */
const SITUACAO: Record<string, { label: string; badge: string }> = {
  PREPARING: { label: 'Em preparação', badge: 'PROCESSING' },
  ACTIVE: { label: 'Ativo', badge: 'VALID' },
  SUSPENDED: { label: 'Suspenso', badge: 'WITHDRAWN' },
};
const AMBIENTE: Record<string, string> = { production: 'Produção', restricted: 'Produção Restrita', mock: 'Simulação' };

const soDigitos = (v: string) => v.replace(/\D/g, '').slice(0, 14);
const fmtCnpj = (v: string) => {
  const d = soDigitos(v);
  return d.length === 14 ? `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}` : v;
};
/** Mesmo cálculo do servidor (módulo 11); o servidor confere de novo. */
export function cnpjValido(v: string): boolean {
  const d = soDigitos(v);
  if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false;
  const dv = (base: string, pesos: number[]) => {
    const r = base.split('').reduce((s, c, i) => s + Number(c) * pesos[i], 0) % 11;
    return r < 2 ? 0 : 11 - r;
  };
  const d1 = dv(d.slice(0, 12), [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
  const d2 = dv(d.slice(0, 12) + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
  return d.endsWith(`${d1}${d2}`);
}

type Adm = 'nenhum' | 'existente' | 'novo';

export const AdminWorkspacesPage: React.FC = () => {
  const { user, clients, showToast } = useFiscal();
  const isSuper = !!user?.is_super_admin;
  const podeCriar = isSuper || !!user?.can_create_workspace;
  const [itens, setItens] = useState<Workspace[]>([]);
  const [carregando, setCarregando] = useState(false);
  const [erroLista, setErroLista] = useState<string | null>(null);
  const [aberto, setAberto] = useState(false);
  const [nome, setNome] = useState('');
  const [razao, setRazao] = useState('');
  const [cnpj, setCnpj] = useState('');
  const [adm, setAdm] = useState<Adm>('nenhum');
  const [admUser, setAdmUser] = useState('');
  const [novoNome, setNovoNome] = useState('');
  const [novoEmail, setNovoEmail] = useState('');
  const [usuarios, setUsuarios] = useState<AdminUser[]>([]);
  const [erros, setErros] = useState<Record<string, string>>({});
  const [erroGeral, setErroGeral] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);
  const [criado, setCriado] = useState<WorkspaceCreated | null>(null);
  const [linkAberto, setLinkAberto] = useState(false);
  const [mudando, setMudando] = useState<string | null>(null);

  const carregar = useCallback(async () => {
    setCarregando(true);
    setErroLista(null);
    try {
      setItens(await clients.core.listAdminWorkspaces());
    } catch (err: any) {
      logSafe('workspaces', err);
      setErroLista(err?.message || 'Falha ao listar workspaces.');
    } finally {
      setCarregando(false);
    }
  }, [clients.core]);

  useEffect(() => { carregar(); }, [carregar]);

  useEffect(() => {
    if (!aberto || !isSuper || usuarios.length) return;
    clients.core.listAdminUsers({ status: 'ACTIVE', page_size: 100 }).then((r) => setUsuarios(r.items))
      .catch((err) => logSafe('usuários', err));
  }, [aberto, isSuper, usuarios.length, clients.core]);

  const abrir = () => {
    setNome(''); setRazao(''); setCnpj(''); setAdm('nenhum'); setAdmUser(''); setNovoNome(''); setNovoEmail('');
    setErros({}); setErroGeral(null); setCriado(null); setAberto(true);
  };

  const validar = (): Record<string, string> => {
    const e: Record<string, string> = {};
    if (nome.trim().length < 2) e['name'] = 'Informe o nome do workspace (mínimo de 2 caracteres).';
    if (razao.trim().length < 2) e['company.name'] = 'Informe a razão social (mínimo de 2 caracteres).';
    if (!cnpjValido(cnpj)) e['company.cnpj'] = 'CNPJ inválido (confira os 14 dígitos).';
    if (adm === 'existente' && !admUser) e['admin.user_id'] = 'Escolha o usuário administrador.';
    if (adm === 'novo') {
      if (novoNome.trim().length < 2) e['admin.new_user.name'] = 'Informe o nome do administrador.';
      if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(novoEmail.trim())) e['admin.new_user.email'] = 'Informe um e-mail válido.';
    }
    return e;
  };

  const criar = async () => {
    if (salvando) return; // clique repetido não cria duas vezes (e o servidor recusa CNPJ repetido)
    const e = validar();
    setErros(e);
    setErroGeral(null);
    if (Object.keys(e).length) return;
    setSalvando(true);
    try {
      const r = await clients.core.createWorkspace({
        name: nome.trim(),
        company: { cnpj: soDigitos(cnpj), name: razao.trim() },
        admin: !isSuper || adm === 'nenhum' ? undefined
          : adm === 'existente' ? { user_id: admUser } : { new_user: { name: novoNome.trim(), email: novoEmail.trim() } },
      });
      setCriado(r);
      if (r.credential_link) setLinkAberto(true);
      showToast(`Workspace “${r.name}” criado em preparação.`, 'success');
      await carregar();
    } catch (err: any) {
      logSafe('criar workspace', err);
      if (err instanceof ProblemDetailsException) {
        const porCampo = Object.fromEntries(Object.entries(err.formatFieldErrors()).map(([k, v]) => [k, v.join(' ')]));
        setErros(porCampo);
        setErroGeral(Object.keys(porCampo).length ? null : err.message);
      } else {
        setErroGeral(err?.message || 'Falha ao criar o workspace.');
      }
    } finally {
      setSalvando(false);
    }
  };

  const mudarSituacao = async (w: Workspace, status: string) => {
    setMudando(w.id);
    try {
      await clients.core.updateWorkspace(w.id, { status });
      showToast(`Workspace “${w.name}”: ${SITUACAO[status]?.label || status}.`, 'success');
      await carregar();
    } catch (err: any) {
      logSafe('situação do workspace', err);
      showToast(err?.message || 'Não foi possível alterar a situação.', 'error');
    } finally {
      setMudando(null);
    }
  };

  const souAdmin = criado && criado.admin_user_id === user?.id;
  const irParaConfiguracao = (id: string) => {
    // Recarrega o contexto (permissões e workspaces do usuário) já no novo workspace.
    window.location.href = `/fiscal/?view=configuracoes&company_id=${encodeURIComponent(id)}`;
  };

  return (
    <ScreenSection
      title="Workspaces"
      description="Cada workspace é uma empresa (um CNPJ) com seus próprios usuários, certificado, perfis e notas. Nada é copiado entre workspaces."
      actions={podeCriar ? <Button variant="primary" size="sm" onClick={abrir}>Criar workspace</Button> : undefined}
    >
      {!isSuper && (
        <p className="pf-help">Você vê os workspaces que administra. Ativar ou suspender é feito pelo SUPER_ADMIN.</p>
      )}
      {erroLista && (
        <Notice variant="error" title="Não foi possível carregar os workspaces">
          {erroLista} <Button size="sm" variant="secondary" onClick={carregar}>Tentar novamente</Button>
        </Notice>
      )}
      {carregando && !itens.length ? (
        <p className="pf-help" role="status" aria-busy="true">Carregando workspaces…</p>
      ) : !erroLista && itens.length === 0 ? (
        <TableEmpty title="Nenhum workspace" description={podeCriar ? 'Use “Criar workspace” para cadastrar uma empresa.' : 'Nenhum workspace sob sua administração.'} />
      ) : (
        <ul className="pf-ws-lista" aria-busy={carregando || undefined}>
          {itens.map((w) => {
            const st = SITUACAO[w.status] || { label: w.status, badge: 'UNKNOWN' };
            return (
              <li key={w.id} className="pf-ws-item">
                <div className="pf-ws-cab">
                  <div>
                    <strong>{w.name}</strong>
                    <div className="pf-help">{w.company.name} · CNPJ {fmtCnpj(w.company.cnpj)} · {AMBIENTE[w.environment] || w.environment}</div>
                  </div>
                  <Badge status={st.badge} customLabel={st.label} size="sm" />
                </div>
                {isSuper && (
                  <div className="pf-actions-end">
                    {w.status !== 'ACTIVE' && (
                      <Button size="sm" variant="outline" isLoading={mudando === w.id} onClick={() => mudarSituacao(w, 'ACTIVE')}>Ativar</Button>
                    )}
                    {w.status !== 'SUSPENDED' && (
                      <Button size="sm" variant="ghost" isLoading={mudando === w.id} onClick={() => mudarSituacao(w, 'SUSPENDED')}>Suspender</Button>
                    )}
                    {w.status !== 'PREPARING' && (
                      <Button size="sm" variant="ghost" isLoading={mudando === w.id} onClick={() => mudarSituacao(w, 'PREPARING')}>Voltar à preparação</Button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <Modal isOpen={aberto} onClose={() => !salvando && setAberto(false)} maxWidth="640px"
        title={criado ? 'Workspace criado' : 'Criar workspace'}
        description={criado ? undefined : 'A empresa começa em preparação: sem certificado, sem perfis aprovados e sem transmissão. Ativar é feito pelo SUPER_ADMIN.'}
        footer={criado ? (
          <div className="pf-actions-end">
            <Button variant="outline" onClick={() => setAberto(false)}>Fechar</Button>
            {souAdmin && <Button variant="primary" onClick={() => irParaConfiguracao(criado.id)}>Configurar a empresa</Button>}
          </div>
        ) : (
          <div className="pf-actions-end">
            <Button variant="outline" onClick={() => setAberto(false)} disabled={salvando}>Cancelar</Button>
            <Button variant="primary" onClick={criar} isLoading={salvando} disabled={salvando}>Criar workspace</Button>
          </div>
        )}>
        {criado ? (
          <div className="pf-stack">
            <Notice variant="success" title={`“${criado.name}” criado em preparação`}>
              {criado.company.name} · CNPJ {fmtCnpj(criado.company.cnpj)}.
            </Notice>
            <p>Próximos passos (pendências de prontidão em Configurações): cadastro do prestador (município, regime e
              série da DPS), certificado A1 da própria empresa, perfis de serviço aprovados e, por fim, a ativação pelo
              SUPER_ADMIN. A chave de emissão da instalação não torna a empresa pronta para emitir.</p>
            {souAdmin ? (
              <p className="pf-help">Você é o administrador deste workspace.</p>
            ) : criado.admin_user_id ? (
              <p className="pf-help">Administrador vinculado. {criado.credential_link ? 'Repasse o link de ativação a ele.' : ''}</p>
            ) : (
              <Notice variant="warning" title="Sem administrador">
                Vincule um administrador em Administração → Usuários para configurar a empresa (o SUPER_ADMIN não tem
                acesso fiscal aos workspaces).
              </Notice>
            )}
            {criado.credential_link && (
              <Button variant="secondary" size="sm" onClick={() => setLinkAberto(true)}>Ver link de ativação</Button>
            )}
          </div>
        ) : (
          <form className="pf-form-grid" onSubmit={(e) => { e.preventDefault(); criar(); }} noValidate>
            <Input label="Nome do workspace *" value={nome} maxLength={200} onChange={(e) => setNome(e.target.value)} error={erros['name']} />
            <Input label="Razão social *" value={razao} maxLength={200} onChange={(e) => setRazao(e.target.value)} error={erros['company.name']} />
            <Input label="CNPJ *" value={cnpj} inputMode="numeric" maxLength={18} placeholder="00.000.000/0000-00"
              onChange={(e) => setCnpj(e.target.value)} onBlur={() => setCnpj(fmtCnpj(cnpj))} error={erros['company.cnpj']} />
            {isSuper ? (
              <Select label="Administrador do workspace" value={adm} onChange={(e) => setAdm(e.target.value as Adm)}
                options={[{ value: 'nenhum', label: 'Definir depois' }, { value: 'existente', label: 'Usuário existente' },
                  { value: 'novo', label: 'Novo usuário (link de ativação)' }]} />
            ) : (
              <p className="pf-help">Você será o administrador deste workspace (somente dele).</p>
            )}
            {isSuper && adm === 'existente' && (
              <Select label="Usuário *" value={admUser} onChange={(e) => setAdmUser(e.target.value)} error={erros['admin.user_id']}
                options={[{ value: '', label: 'Selecione…' }, ...usuarios.map((u) => ({ value: u.id, label: `${u.name} (${u.email})` }))]} />
            )}
            {isSuper && adm === 'novo' && (
              <>
                <Input label="Nome do administrador *" value={novoNome} maxLength={160} onChange={(e) => setNovoNome(e.target.value)} error={erros['admin.new_user.name']} />
                <Input label="E-mail do administrador *" type="email" value={novoEmail} maxLength={254} onChange={(e) => setNovoEmail(e.target.value)}
                  error={erros['admin.new_user.email']} helperText="Sem senha automática: o administrador define a própria senha pelo link de ativação." />
              </>
            )}
            {erroGeral && <Notice variant="error" title="Workspace não criado">{erroGeral}</Notice>}
            <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
          </form>
        )}
      </Modal>
      <CredentialLinkModal link={linkAberto ? criado?.credential_link || null : null} personName={novoNome || 'administrador'}
        onClose={() => setLinkAberto(false)} />
    </ScreenSection>
  );
};
