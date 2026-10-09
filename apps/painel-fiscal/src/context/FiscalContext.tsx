import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import {
  Company,
  User,
  DashboardCounters,
  SyncStatus,
  IntegrationStatus,
  CertificateStatus,
  Workspace,
  getApiClients,
  ApiClients,
} from '@ctrl-fiscal/api-client';

/** Log sem dados sensíveis: só o rótulo e o código/estado HTTP do erro (nunca corpo, token, CNPJ ou XML). */
export function logSafe(label: string, err: unknown): void {
  const e = err as { status?: number; problem?: { code?: string; status?: number }; name?: string } | null;
  const code = e?.problem?.code || e?.name || 'erro';
  const status = e?.status ?? e?.problem?.status;
  console.warn(`[ctrl-fiscal] ${label}: ${code}${status ? ` (HTTP ${status})` : ''}`);
}

interface FiscalContextType {
  user: User | null;
  currentCompany: Company | null;
  companies: Company[];
  /** Workspace ativo (um por empresa/CNPJ): identificação, situação e permissões DESTE vínculo. */
  workspace: Workspace | null;
  /** Certificado do workspace ativo — só situação e validade (sem segredos). */
  certificateStatus: CertificateStatus | null;
  refreshCertificateStatus: () => Promise<void>;
  dashboardCounters: DashboardCounters | null;
  syncStatus: SyncStatus | null;
  integrationStatus: IntegrationStatus | null;
  isLoading: boolean;
  isMock: boolean;
  switchCompany: (companyId: string) => void;
  refreshCounters: () => Promise<void>;
  refreshSyncStatus: () => Promise<void>;
  refreshIntegrationStatus: () => Promise<void>;
  clients: ApiClients;
  showToast: (message: string, type?: 'success' | 'error' | 'info' | 'warning') => void;
  toast: { message: string; type: 'success' | 'error' | 'info' | 'warning' } | null;
  /** Tela com alterações não salvas registra aqui; a navegação interna e a troca de empresa pedem confirmação. */
  setLeaveGuard: (guard: (() => boolean) | null) => void;
  confirmLeave: () => boolean;
}

const FiscalContext = createContext<FiscalContextType | null>(null);

export const FiscalProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Usa cliente HTTP real por padrão; permite mock explícito apenas se VITE_USE_MOCK for 'true'
  const isMockEnv = typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_USE_MOCK === 'true';
  const clients = getApiClients({ useMock: isMockEnv });

  const [user, setUser] = useState<User | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [currentCompany, setCurrentCompany] = useState<Company | null>(null);
  const [dashboardCounters, setDashboardCounters] = useState<DashboardCounters | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [integrationStatus, setIntegrationStatus] = useState<IntegrationStatus | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [certificateStatus, setCertificateStatus] = useState<CertificateStatus | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  /** Geração do contexto: muda a cada troca de workspace; respostas atrasadas da geração anterior são ignoradas. */
  const genRef = React.useRef(0);
  const companyRef = React.useRef<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' | 'warning' } | null>(null);
  const leaveGuardRef = React.useRef<(() => boolean) | null>(null);
  const setLeaveGuard = useCallback((g: (() => boolean) | null) => {
    leaveGuardRef.current = g;
  }, []);
  const confirmLeave = useCallback(() => (leaveGuardRef.current ? leaveGuardRef.current() : true), []);

  const showToast = useCallback((message: string, type: 'success' | 'error' | 'info' | 'warning' = 'info') => {
    setToast({ message, type });
    setTimeout(() => {
      setToast(null);
    }, 4000);
  }, []);

  /** Aplica o resultado só se ainda estivermos no MESMO workspace em que a requisição começou. */
  const sameContext = useCallback((companyId: string, gen: number) => genRef.current === gen && companyRef.current === companyId, []);

  const refreshCertificateStatus = useCallback(async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    const gen = genRef.current;
    try {
      const st = await clients.core.getCertificateStatus(id);
      if (sameContext(id, gen)) setCertificateStatus(st);
    } catch (err) {
      logSafe('certificado', err);
      if (sameContext(id, gen)) setCertificateStatus(null);
    }
  }, [currentCompany, clients.core, sameContext]);

  const refreshCounters = useCallback(async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    const gen = genRef.current;
    try {
      const counters = await clients.fiscal.getDashboard(id);
      if (sameContext(id, gen)) setDashboardCounters(counters);
    } catch (err) {
      logSafe('painel', err);
    }
  }, [currentCompany, clients.fiscal, sameContext]);

  const refreshSyncStatus = useCallback(async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    const gen = genRef.current;
    try {
      const sync = await clients.fiscal.getSyncStatus(id);
      if (sameContext(id, gen)) setSyncStatus(sync);
    } catch (err) {
      logSafe('sincronização', err);
    }
  }, [currentCompany, clients.fiscal, sameContext]);

  const refreshIntegrationStatus = useCallback(async () => {
    if (!currentCompany) return;
    const id = currentCompany.id;
    const gen = genRef.current;
    try {
      const status = await clients.core.getIntegrationStatus(id);
      if (sameContext(id, gen)) setIntegrationStatus(status);
    } catch (err) {
      logSafe('integração', err);
    }
  }, [currentCompany, clients.core, sameContext]);

  const loadInitialData = useCallback(async () => {
    setIsLoading(true);
    try {
      // Prioridade 1: Após reload e na transição Next.js -> Vite, obter /auth/csrf antes de /auth/refresh
      if (!clients.isMock) {
        try {
          await clients.core.getCsrf();
          await clients.core.refresh();
        } catch (authErr) {
          logSafe('sessão', authErr);
        }
      }

      // Carrega usuário e empresas via GET /auth/me
      const me = await clients.core.getMe();
      setUser(me);
      setCompanies(me.companies || []);
      setWorkspaces(me.workspaces || []);

      // Preservação da empresa selecionada (via query param ou sessionStorage)
      const urlParams = new URLSearchParams(window.location.search);
      const urlCompanyId = urlParams.get('company_id') || sessionStorage.getItem('ctrl_fiscal_company_id');
      const targetCompany = (me.companies || []).find((c) => c.id === urlCompanyId) || me.companies?.[0] || null;

      setCurrentCompany(targetCompany);
      companyRef.current = targetCompany?.id || null;
      const gen = genRef.current;

      if (targetCompany) {
        sessionStorage.setItem('ctrl_fiscal_company_id', targetCompany.id);
        const [counters, sync, integration, cert] = await Promise.all([
          clients.fiscal.getDashboard(targetCompany.id),
          clients.fiscal.getSyncStatus(targetCompany.id),
          clients.core.getIntegrationStatus(targetCompany.id).catch(() => null),
          clients.core.getCertificateStatus(targetCompany.id).catch(() => null),
        ]);
        if (sameContext(targetCompany.id, gen)) {
          setDashboardCounters(counters);
          setSyncStatus(sync);
          setIntegrationStatus(integration);
          setCertificateStatus(cert);
        }
      }
    } catch (err) {
      logSafe('carga inicial', err);
      // Se 401 e não for mock, redireciona para login Next.js
      if (!clients.isMock && window.location.pathname.startsWith('/fiscal')) {
        window.location.href = '/';
      }
    } finally {
      setIsLoading(false);
    }
  }, [clients, showToast, sameContext]);

  useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  const switchCompany = useCallback((companyId: string) => {
    const target = companies.find((c) => c.id === companyId);
    if (target && target.id !== currentCompany?.id) {
      if (!confirmLeave()) return;
      leaveGuardRef.current = null;
      // Isolamento estrito entre workspaces: nova geração (respostas atrasadas da anterior são descartadas) e
      // caches anteriores limpos imediatamente.
      genRef.current += 1;
      const gen = genRef.current;
      companyRef.current = target.id;
      setDashboardCounters(null);
      setSyncStatus(null);
      setIntegrationStatus(null);
      setCertificateStatus(null);
      setCurrentCompany(target);
      sessionStorage.setItem('ctrl_fiscal_company_id', target.id);

      // Atualiza a URL sem recarregar. Filtros, página e nota aberta pertencem à empresa anterior: são descartados
      // (a tela é remontada com key = empresa, limpando dados e seleções em memória).
      const url = new URL(window.location.href);
      const view = url.searchParams.get('view');
      url.search = '';
      if (view) url.searchParams.set('view', view);
      url.searchParams.set('company_id', target.id);
      window.history.replaceState({}, '', url.toString());

      Promise.all([
        clients.fiscal.getDashboard(target.id),
        clients.fiscal.getSyncStatus(target.id),
        clients.core.getIntegrationStatus(target.id).catch(() => null),
        clients.core.getCertificateStatus(target.id).catch(() => null),
      ]).then(([c, s, intg, cert]) => {
        if (!sameContext(target.id, gen)) return; // o usuário já trocou de novo: resposta atrasada ignorada
        setDashboardCounters(c);
        setSyncStatus(s);
        setIntegrationStatus(intg);
        setCertificateStatus(cert);
        showToast(`Workspace alterado para: ${target.name}`, 'info');
      }).catch((err) => {
        logSafe('troca de workspace', err);
        if (sameContext(target.id, gen)) showToast('Erro ao carregar dados do workspace selecionado.', 'error');
      });
    }
  }, [companies, currentCompany, clients.fiscal, clients.core, showToast, confirmLeave, sameContext]);

  const workspace = React.useMemo(
    () => workspaces.find((w) => w.id === currentCompany?.id) || null,
    [workspaces, currentCompany],
  );

  return (
    <FiscalContext.Provider
      value={{
        user,
        currentCompany,
        companies,
        workspace,
        certificateStatus,
        refreshCertificateStatus,
        dashboardCounters,
        syncStatus,
        integrationStatus,
        isLoading,
        isMock: clients.isMock,
        switchCompany,
        refreshCounters,
        refreshSyncStatus,
        refreshIntegrationStatus,
        clients,
        showToast,
        toast,
        setLeaveGuard,
        confirmLeave,
      }}
    >
      {children}
    </FiscalContext.Provider>
  );
};

export const useFiscal = () => {
  const context = useContext(FiscalContext);
  if (!context) {
    throw new Error('useFiscal must be used within a FiscalProvider');
  }
  return context;
};
