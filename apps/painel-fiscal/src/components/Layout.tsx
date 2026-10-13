import React, { useEffect, useState } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { MockBanner, Badge, Drawer, Icon } from '@ctrl-fiscal/ui';
import { getEnvironmentBadgeConfig } from '@ctrl-fiscal/api-client';

export interface LayoutProps {
  currentRoute: string;
  onRouteChange: (route: string) => void;
  children: React.ReactNode;
}

const MENU = [
  { id: 'dashboard', label: 'Painel', icon: 'home' },
  { id: 'nova-nfse', label: 'Nova NFS-e', icon: 'plus' },
  { id: 'rascunhos', label: 'Rascunhos', icon: 'draft' },
  { id: 'agendamentos', label: 'Emissões e agendamentos', icon: 'clock' },
  { id: 'emitidas', label: 'Notas emitidas', icon: 'upload' },
  { id: 'recebidas', label: 'Notas recebidas', icon: 'download' },
  { id: 'importar-exportar', label: 'Importar / exportar', icon: 'swap' },
  { id: 'sincronizacao', label: 'Sincronização ADN', icon: 'sync' },
  { id: 'clientes', label: 'Clientes', icon: 'users' },
  { id: 'configuracoes', label: 'Configurações', icon: 'settings' },
];

/** Seção "Administração" (ocultar é cosmético; o backend valida cada operação):
 * Usuários — SUPER_ADMIN ou admin de alguma empresa; Workspaces — esses e quem tem a permissão create_workspace. */
const ADMIN_MENU = [
  { id: 'admin-usuarios', label: 'Usuários', icon: 'users' },
  { id: 'admin-workspaces', label: 'Workspaces', icon: 'file' },
];
type AdminUserLike = { is_super_admin?: boolean; admin_company_ids?: string[]; can_create_workspace?: boolean } | null;
const adminItems = (u: AdminUserLike) => ADMIN_MENU.filter((i) => {
  const adm = !!u && (!!u.is_super_admin || (u.admin_company_ids?.length ?? 0) > 0);
  return i.id === 'admin-usuarios' ? adm : adm || !!u?.can_create_workspace;
});

function formatCnpj(v?: string | null): string {
  const d = (v || '').replace(/[^0-9A-Za-z]/g, '').toUpperCase();
  return d.length === 14 ? `${d.slice(0, 2)}.${d.slice(2, 5)}.${d.slice(5, 8)}/${d.slice(8, 12)}-${d.slice(12)}` : (v || '');
}

const CERT_LABEL: Record<string, { text: string; status: 'AUTHORIZED' | 'PROCESSING' | 'REJECTED' | 'DRAFT' }> = {
  VALID: { text: 'Certificado válido', status: 'AUTHORIZED' },
  EXPIRING: { text: 'Certificado vence em breve', status: 'PROCESSING' },
  EXPIRED: { text: 'Certificado vencido', status: 'REJECTED' },
  CNPJ_MISMATCH: { text: 'Certificado de outro CNPJ', status: 'REJECTED' },
  ABSENT: { text: 'Sem certificado', status: 'DRAFT' },
};

const WS_STATUS: Record<string, string> = { PREPARING: 'Em preparação', ACTIVE: 'Ativo', SUSPENDED: 'Suspenso' };

/** Identificação do workspace ativo: empresa, CNPJ, ambiente e situação do certificado (sem segredos).
 *  O seletor só aparece quando o usuário tem mais de um workspace. */
const WorkspaceIdentity: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const { currentCompany, companies, switchCompany, workspace, certificateStatus } = useFiscal();
  if (!currentCompany) return null;
  const cert = CERT_LABEL[certificateStatus?.status || ''] || null;
  const validade = certificateStatus?.active?.not_after
    ? new Date(certificateStatus.active.not_after).toLocaleDateString('pt-BR', { timeZone: 'America/Sao_Paulo' })
    : null;
  return (
    <div className="pf-workspace" data-compact={compact ? 'true' : undefined} aria-label="Workspace ativo">
      {companies.length > 1 ? (
        <select id={compact ? 'pf-company-select-faixa' : 'pf-company-select'} value={currentCompany.id} onChange={(e) => switchCompany(e.target.value)}
          aria-label="Workspace selecionado" className="pf-workspace-select">
          {companies.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
      ) : (
        <span className="pf-workspace-name" title={currentCompany.name}>{currentCompany.name}</span>
      )}
      <span className="pf-workspace-meta">
        <span>CNPJ {formatCnpj(workspace?.company.cnpj || currentCompany.cnpj)}</span>
        {workspace && workspace.status !== 'ACTIVE' && (
          <Badge status="PROCESSING" customLabel={`Workspace ${WS_STATUS[workspace.status] || workspace.status}`} size="sm" />
        )}
        {cert && (
          <span title={certificateStatus?.note}>
            <Badge status={cert.status} customLabel={validade && certificateStatus?.status !== 'ABSENT'
              ? `${cert.text} · até ${validade}` : cert.text} size="sm" />
          </span>
        )}
      </span>
    </div>
  );
};

const COLLAPSE_KEY = 'ctrl_fiscal_sidebar_collapsed'; // preferência visual por navegador (não é dado fiscal)

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

type MenuItem = { id: string; label: string; icon: string };

const NavList: React.FC<{ current: string; collapsed: boolean; onGo: (id: string) => void; showAdmin?: boolean; hasCompany?: boolean; adminMenu?: MenuItem[] }> = ({
  current, collapsed, onGo, showAdmin = false, hasCompany = true, adminMenu = [],
}) => {
  const adminHeadingId = React.useId(); // a lista aparece na lateral e no menu móvel: ids distintos
  return (
  <>
    {hasCompany && <NavItems items={MENU} current={current} collapsed={collapsed} onGo={onGo} />}
    {showAdmin && (
      <div style={{ marginTop: hasCompany ? '0.75rem' : 0 }}>
        <div id={adminHeadingId} className={collapsed ? 'ui-sr-only' : undefined}
          style={{ padding: '0.25rem 0.75rem', fontSize: '0.6875rem', fontWeight: 700, letterSpacing: '0.06em',
            textTransform: 'uppercase', color: 'var(--color-text-subtle)' }}>
          Administração
        </div>
        <NavItems items={adminMenu} current={current} collapsed={collapsed} onGo={onGo} labelledBy={adminHeadingId} />
      </div>
    )}
  </>
  );
};

const NavItems: React.FC<{ items: MenuItem[]; current: string; collapsed: boolean; onGo: (id: string) => void; labelledBy?: string }> = ({
  items, current, collapsed, onGo, labelledBy,
}) => (
  <ul aria-labelledby={labelledBy} style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '0.125rem' }}>
    {items.map((item) => {
      const active = current === item.id;
      return (
        <li key={item.id}>
          <a
            href={`?view=${item.id}`}
            aria-current={active ? 'page' : undefined}
            title={collapsed ? item.label : undefined}
            onClick={(e) => {
              e.preventDefault();
              onGo(item.id);
            }}
            className="pf-nav-link"
            data-active={active ? 'true' : undefined}
            style={{ justifyContent: collapsed ? 'center' : 'flex-start' }}
          >
            <Icon name={item.icon} />
            <span className={collapsed ? 'ui-sr-only' : undefined}>{item.label}</span>
          </a>
        </li>
      );
    })}
  </ul>
);

export const Layout: React.FC<LayoutProps> = ({ currentRoute, onRouteChange, children }) => {
  const { currentCompany, companies, user, isMock, toast, clients, integrationStatus, dashboardCounters } = useFiscal();
  const envBadge = getEnvironmentBadgeConfig(
    integrationStatus?.environment || dashboardCounters?.environment || currentCompany?.environment,
    integrationStatus?.simulated ?? dashboardCounters?.simulated ?? true
  );
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed);
  const adminMenu = adminItems(user as AdminUserLike);
  const showAdmin = adminMenu.length > 0;
  const hasCompany = companies.length > 0;
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
    } catch {
      /* armazenamento indisponível: preferência não persiste */
    }
  }, [collapsed]);

  const go = (id: string) => {
    setMobileOpen(false);
    onRouteChange(id);
  };

  const logout = async () => {
    try {
      await clients.core.logout();
    } catch (e) {
      logSafe('logout', e);
    } finally {
      sessionStorage.clear();
      window.location.href = '/';
    }
  };

  return (
    <div className="pf-shell">
      <a href="#conteudo" className="ui-skip-link">Pular para o conteúdo</a>
      {isMock && <MockBanner />}

      {toast && (
        <div role={toast.type === 'error' ? 'alert' : 'status'} aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
          className="pf-toast" data-type={toast.type}>
          <span aria-hidden="true">{toast.type === 'error' ? '✕' : toast.type === 'success' ? '✓' : toast.type === 'warning' ? '!' : 'i'}</span>
          <span>{toast.message}</span>
        </div>
      )}

      <header className="pf-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
          <button type="button" className="pf-icon-btn ui-only-mobile" aria-label="Abrir menu" aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}>
            <Icon name="menu" />
          </button>
          <button type="button" className="pf-icon-btn ui-only-desktop" aria-label={collapsed ? 'Expandir menu lateral' : 'Recolher menu lateral'}
            aria-pressed={collapsed} onClick={() => setCollapsed((c) => !c)}>
            <Icon name={collapsed ? 'chevronRight' : 'chevronLeft'} />
          </button>
          <div className="pf-brand">
            <img className="pf-brand-logo ui-only-desktop" src={`${import.meta.env.BASE_URL}logo-ctrl-fiscal.svg`}
              alt="CTRL Fiscal" width={150} height={29} />
            <img className="pf-brand-mark ui-only-mobile" src={`${import.meta.env.BASE_URL}marca-ctrl-fiscal.svg`}
              alt="CTRL Fiscal" width={30} height={32} />
          </div>
        </div>

        <div className="pf-header-right">
          {hasCompany && <span className="pf-ws-header"><WorkspaceIdentity /></span>}
          <span className="ui-only-desktop">{currentCompany && <Badge status={envBadge.status} customLabel={envBadge.label} size="sm" />}</span>
          <div className="pf-user">
            <div className="ui-only-desktop" style={{ textAlign: 'right', lineHeight: 1.25 }}>
              <div style={{ fontSize: '0.8125rem', fontWeight: 600 }}>{user?.name || '—'}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>{user?.email || ''}</div>
            </div>
            <button id="btnLogoutPainel" type="button" className="pf-icon-btn" onClick={logout} title="Encerrar sessão">
              <Icon name="logout" />
              <span className="ui-only-desktop" style={{ marginLeft: '0.25rem' }}>Sair</span>
              <span className="ui-only-mobile ui-sr-only">Sair</span>
            </button>
          </div>
        </div>
      </header>
      {/* Tablet e celular: identificação do workspace numa faixa abaixo do cabeçalho (sem rolagem horizontal). */}
      <div className="pf-ws-strip" aria-label="Workspace e ambiente">
        {hasCompany && <WorkspaceIdentity compact />}
        {currentCompany && <span className="ui-only-mobile"><Badge status={envBadge.status} customLabel={envBadge.label} size="sm" /></span>}
      </div>

      <div className="pf-body">
        <nav aria-label="Navegação principal" className="pf-sidebar ui-only-desktop" data-collapsed={collapsed ? 'true' : undefined}>
          <NavList current={currentRoute} collapsed={collapsed} onGo={go} showAdmin={showAdmin} hasCompany={hasCompany} adminMenu={adminMenu} />
          {!collapsed && hasCompany && (
            <div className="pf-sidebar-note">
              <div style={{ fontWeight: 600, color: 'var(--color-text-muted)' }}>Fuso horário da empresa</div>
              <div>{currentCompany?.timezone || '—'}</div>
            </div>
          )}
        </nav>

        <Drawer isOpen={mobileOpen} onClose={() => setMobileOpen(false)} title="Menu" side="left" width="290px">
          <nav aria-label="Navegação principal">
            <NavList current={currentRoute} collapsed={false} onGo={go} showAdmin={showAdmin} hasCompany={hasCompany} adminMenu={adminMenu} />
          </nav>
        </Drawer>

        <main id="conteudo" tabIndex={-1} className="pf-main">
          {children}
        </main>
      </div>
    </div>
  );
};
