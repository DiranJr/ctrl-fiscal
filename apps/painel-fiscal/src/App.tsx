import React, { Suspense, lazy, useEffect, useState } from 'react';
import { FiscalProvider, useFiscal } from './context/FiscalContext.js';
import { Layout } from './components/Layout.js';
import { Button, ErrorState, Skeleton } from '@ctrl-fiscal/ui';

// Telas carregadas sob demanda (code splitting): o pacote inicial leva só o shell.
const DashboardPage = lazy(() => import('./pages/DashboardPage.js').then((m) => ({ default: m.DashboardPage })));
const NovaNfsePage = lazy(() => import('./pages/NovaNfsePage.js').then((m) => ({ default: m.NovaNfsePage })));
const DraftsPage = lazy(() => import('./pages/DraftsPage.js').then((m) => ({ default: m.DraftsPage })));
const BatchSchedulingPage = lazy(() => import('./pages/BatchSchedulingPage.js').then((m) => ({ default: m.BatchSchedulingPage })));
const IssuedDocumentsPage = lazy(() => import('./pages/IssuedDocumentsPage.js').then((m) => ({ default: m.IssuedDocumentsPage })));
const ReceivedDocumentsPage = lazy(() => import('./pages/ReceivedDocumentsPage.js').then((m) => ({ default: m.ReceivedDocumentsPage })));
const ImportExportPage = lazy(() => import('./pages/ImportExportPage.js').then((m) => ({ default: m.ImportExportPage })));
const SyncPage = lazy(() => import('./pages/SyncPage.js').then((m) => ({ default: m.SyncPage })));
const CustomersPage = lazy(() => import('./pages/CustomersPage.js').then((m) => ({ default: m.CustomersPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage.js').then((m) => ({ default: m.SettingsPage })));
const AdminUsersPage = lazy(() => import('./pages/AdminUsersPage.js').then((m) => ({ default: m.AdminUsersPage })));
const AdminWorkspacesPage = lazy(() => import('./pages/AdminWorkspacesPage.js').then((m) => ({ default: m.AdminWorkspacesPage })));

const ROUTES = ['dashboard', 'nova-nfse', 'rascunhos', 'agendamentos', 'emitidas', 'recebidas', 'importar-exportar',
  'sincronizacao', 'clientes', 'configuracoes', 'admin-usuarios', 'admin-workspaces'];

/** Telas que não dependem de empresa ativa (ex.: SUPER_ADMIN sem vínculo com empresas). */
const COMPANYLESS_ROUTES = ['admin-usuarios', 'admin-workspaces'];

function getInitialRoute(): string {
  if (typeof window !== 'undefined') {
    const view = new URLSearchParams(window.location.search).get('view');
    if (view && ROUTES.includes(view)) return view;
    const hash = window.location.hash.replace('#', '');
    if (hash && ROUTES.includes(hash)) return hash;
  }
  return 'dashboard';
}

const PageFallback: React.FC = () => (
  <div aria-busy="true" aria-label="Carregando tela" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
    <Skeleton width="40%" height="1.5rem" />
    <Skeleton height="3rem" />
    <Skeleton height="12rem" />
  </div>
);

class PageBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed) {
      return <ErrorState title="Não foi possível abrir esta tela" message="Recarregue a página para tentar novamente."
        onRetry={() => window.location.reload()} />;
    }
    return this.props.children;
  }
}

const PainelFiscalContent: React.FC = () => {
  const { currentCompany, confirmLeave, setLeaveGuard, isLoading, user } = useFiscal();
  const canAdminUsers = !!user && (!!user.is_super_admin || (user.admin_company_ids?.length ?? 0) > 0);
  const [currentRoute, setCurrentRoute] = useState<string>(getInitialRoute);

  const handleRouteChange = (newRoute: string) => {
    if (newRoute === currentRoute) return;
    if (!confirmLeave()) return;
    setLeaveGuard(null);
    setCurrentRoute(newRoute);
    const url = new URL(window.location.href);
    // Cada tela guarda seus próprios filtros na URL; ao trocar de tela, mantém só a empresa.
    const company = url.searchParams.get('company_id');
    url.search = '';
    url.searchParams.set('view', newRoute);
    if (company) url.searchParams.set('company_id', company);
    window.history.pushState({}, '', url.toString());
    document.getElementById('conteudo')?.focus();
  };

  useEffect(() => {
    const onPop = () => setCurrentRoute(getInitialRoute());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const page = (() => {
    switch (currentRoute) {
      case 'nova-nfse': return <NovaNfsePage onNavigate={handleRouteChange} />;
      case 'rascunhos': return <DraftsPage onNavigate={handleRouteChange} />;
      case 'agendamentos': return <BatchSchedulingPage onNavigate={handleRouteChange} />;
      case 'emitidas': return <IssuedDocumentsPage onNavigate={handleRouteChange} />;
      case 'recebidas': return <ReceivedDocumentsPage onNavigate={handleRouteChange} />;
      case 'importar-exportar': return <ImportExportPage />;
      case 'sincronizacao': return <SyncPage />;
      case 'clientes': return <CustomersPage />;
      case 'configuracoes': return <SettingsPage />;
      case 'admin-usuarios': return <AdminUsersPage />;
      case 'admin-workspaces': return <AdminWorkspacesPage />;
      default: return <DashboardPage onNavigate={handleRouteChange} />;
    }
  })();

  return (
    <Layout currentRoute={currentRoute} onRouteChange={handleRouteChange}>
      {/* Só monta a tela depois que a empresa ativa carregou: evita remontar (e perder o que foi digitado)
          quando a empresa chega após o primeiro render. */}
      {isLoading ? <PageFallback /> : COMPANYLESS_ROUTES.includes(currentRoute) ? (
        // Administração de usuários não depende da empresa ativa: não remonta ao trocar de empresa.
        <PageBoundary key={`admin:${currentRoute}`}>
          <Suspense fallback={<PageFallback />}>{page}</Suspense>
        </PageBoundary>
      ) : !currentCompany ? (
        <div className="pf-stack">
          <ErrorState title="Nenhuma empresa disponível" message="Sua conta não tem acesso a nenhuma empresa. Fale com o administrador." />
          {canAdminUsers && (
            <div style={{ textAlign: 'center' }}>
              <Button variant="primary" onClick={() => handleRouteChange('admin-usuarios')}>Ir para Administração → Usuários</Button>
            </div>
          )}
        </div>
      ) : (
      <PageBoundary key={`${currentCompany.id}:${currentRoute}`}>
        <Suspense fallback={<PageFallback />}>
          {/* key = empresa + tela: trocar de empresa remonta a tela e descarta dados e seleções da anterior */}
          <React.Fragment key={`${currentCompany?.id || 'sem-empresa'}:${currentRoute}`}>{page}</React.Fragment>
        </Suspense>
      </PageBoundary>
      )}
    </Layout>
  );
};

export const App: React.FC = () => (
  <FiscalProvider>
    <PainelFiscalContent />
  </FiscalProvider>
);

export default App;
