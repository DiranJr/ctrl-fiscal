import React, { useState } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { Button, Badge, Icon, PageHeader, Skeleton } from '@ctrl-fiscal/ui';
import { ScreenSection } from '../components/screen/ScreenSection.js';
import { Notice } from '../components/screen/Notice.js';

export const DashboardPage: React.FC<{ onNavigate: (route: string) => void }> = ({ onNavigate }) => {
  const {
    currentCompany, dashboardCounters, syncStatus, refreshSyncStatus, refreshCounters, clients, showToast, isLoading,
  } = useFiscal();
  const [isSyncing, setIsSyncing] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);

  const handleTriggerSync = async () => {
    if (!currentCompany) return;
    setIsSyncing(true);
    try {
      const idempotencyKey = `sync-${currentCompany.id}-${Date.now()}`;
      const res = await clients.fiscal.triggerSync(currentCompany.id, idempotencyKey);
      showToast(`Consulta ao ADN disparada com sucesso! Job ID: ${res.job_id}`, 'success');
      await refreshSyncStatus();
    } catch (err: any) {
      logSafe('sincronização', err);
      showToast(err.message || 'Falha ao disparar sincronização com ADN.', 'error');
    } finally {
      setIsSyncing(false);
    }
  };

  // Nova tentativa de leitura dos dados do painel (mesmas chamadas já usadas pelo contexto).
  const handleRetry = async () => {
    setIsRetrying(true);
    try {
      await Promise.all([refreshCounters(), refreshSyncStatus()]);
    } finally {
      setIsRetrying(false);
    }
  };

  const statCards = [
    {
      title: 'Rascunhos pendentes',
      count: dashboardCounters?.drafts_count ?? 0,
      icon: 'draft',
      route: 'rascunhos',
      color: 'var(--color-primary)',
      sub: 'Ainda não emitidos (inclui em fila e rejeitados)',
    },
    {
      title: 'Emissões em andamento',
      count: dashboardCounters?.scheduled_count ?? 0,
      icon: 'clock',
      route: 'agendamentos',
      color: 'var(--status-scheduled-text)',
      sub: 'Agendadas, em fila ou aguardando confirmação',
    },
    {
      title: 'Notas emitidas',
      count: dashboardCounters?.issued_count ?? 0,
      icon: 'upload',
      route: 'emitidas',
      color: 'var(--status-authorized-text)',
      sub: 'Autorizadas no ambiente',
    },
    {
      title: 'Exigem atenção',
      count: dashboardCounters?.rejected_count ?? 0,
      icon: 'file',
      route: 'agendamentos',
      color: 'var(--status-rejected-text)',
      sub: 'Rejeições e bloqueios não resolvidos',
    },
    {
      title: 'Recebidas pendentes',
      count: dashboardCounters?.received_pending_count ?? 0,
      icon: 'download',
      route: 'recebidas',
      color: 'var(--status-processing-text)',
      sub: 'Aguardando conferência',
    },
  ];

  const countersLoading = isLoading && !dashboardCounters;
  const countersFailed = !isLoading && !dashboardCounters;
  const syncFailed = !isLoading && !syncStatus;

  const syncBadge =
    syncStatus?.status === 'OK'
      ? { status: 'VALID', label: 'Ativo e monitorado' }
      : syncStatus?.status === 'NOT_CONFIGURED'
      ? { status: 'NOT_CONFIGURED', label: 'Certificado não configurado' }
      : { status: 'PROCESSING', label: 'Sincronizando' };

  const workflows = [
    {
      title: 'Usar última nota como referência',
      sub: 'Clona tomador e serviços sem duplicar IDs fiscais',
      action: 'Iniciar',
      route: 'nova-nfse',
    },
    {
      title: 'Emitir ou agendar rascunhos',
      sub: 'Emissão imediata ou na data escolhida, no fuso da empresa',
      action: 'Abrir',
      route: 'rascunhos',
    },
    {
      title: 'Conferência de notas tomadas',
      sub: 'Audite retenções de ISS/IRRF/INSS/PCC com controle de versão',
      action: 'Conferir',
      route: 'recebidas',
    },
  ];

  return (
    <div className="pf-stack">
      <PageHeader
        title="Painel Fiscal"
        description={<>Visão geral da emissão e conferência de NFS-e para <strong>{currentCompany?.name}</strong></>}
        secondaryActions={
          <Button variant="outline" onClick={() => onNavigate('rascunhos')}>
            <Icon name="draft" size={16} /> Ver rascunhos
          </Button>
        }
        primaryAction={
          <Button variant="primary" onClick={() => onNavigate('nova-nfse')}>
            <Icon name="plus" size={16} /> Nova NFS-e
          </Button>
        }
      />

      {(countersFailed || syncFailed) && (
        <Notice
          variant="error"
          title="Não foi possível carregar todos os indicadores"
          action={
            <Button variant="outline" size="sm" isLoading={isRetrying} onClick={handleRetry}>
              Tentar novamente
            </Button>
          }
        >
          Os números abaixo podem estar desatualizados. Verifique a conexão e tente novamente.
        </Notice>
      )}

      {/* Indicadores */}
      <section aria-labelledby="dash-indicadores">
        <h2 id="dash-indicadores" className="ui-sr-only">Indicadores</h2>
        <ul className="pf-grid-tiles" style={{ listStyle: 'none' }} aria-busy={countersLoading || undefined}>
          {statCards.map((c) => (
            <li key={c.title} style={{ display: 'flex' }}>
              <button type="button" className="pf-stat" onClick={() => onNavigate(c.route)}>
                <span className="pf-stat-head">
                  <span>{c.title}</span>
                  <span style={{ color: c.color }}>
                    <Icon name={c.icon} size={20} />
                  </span>
                </span>
                {countersLoading ? (
                  <Skeleton width="3rem" height="1.875rem" />
                ) : (
                  <span className="pf-stat-count" style={{ color: c.color }}>
                    {countersFailed ? '—' : c.count}
                  </span>
                )}
                <span className="pf-stat-sub">{c.sub}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <div className="pf-grid-2">
        <ScreenSection
          title="Sincronização ADN Contribuintes"
          description="Consulta automática a cada 12 horas por CNPJ e ambiente"
          actions={
            <Button
              variant="secondary"
              size="sm"
              isLoading={isSyncing}
              onClick={handleTriggerSync}
              disabled={syncStatus?.status === 'NOT_CONFIGURED'}
            >
              <Icon name="sync" size={16} /> Consultar Agora
            </Button>
          }
        >
          {isLoading && !syncStatus ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }} aria-busy="true">
              <Skeleton height="1rem" />
              <Skeleton height="1rem" width="80%" />
              <Skeleton height="1rem" width="90%" />
              <Skeleton height="1rem" width="70%" />
            </div>
          ) : (
            <>
              <dl className="pf-dl">
                <dt>Estado da conexão</dt>
                <dd>
                  {syncStatus ? <Badge status={syncBadge.status} customLabel={syncBadge.label} /> : 'Indisponível'}
                </dd>
                <dt>Última consulta com sucesso</dt>
                <dd style={{ fontWeight: 600 }}>
                  {syncStatus?.last_success_at ? new Date(syncStatus.last_success_at).toLocaleString('pt-BR') : 'Nenhuma'}
                </dd>
                <dt>Próxima consulta agendada</dt>
                <dd style={{ fontWeight: 600 }}>
                  {syncStatus?.next_run_at ? new Date(syncStatus.next_run_at).toLocaleString('pt-BR') : 'Aguardando configuração'}
                </dd>
                <dt>Documentos recebidos no ADN</dt>
                <dd style={{ fontWeight: 700, color: 'var(--color-primary)' }}>
                  {syncStatus?.received_count ?? 0} notas capturadas
                </dd>
              </dl>

              <Notice variant="info">
                {syncStatus?.coverage_message || 'Documentos disponibilizados no ADN conforme direitos do certificado digital.'}
              </Notice>
            </>
          )}
        </ScreenSection>

        <ScreenSection title="Fluxos prioritários do emissor" description="Rotinas otimizadas para fechamento e virada de mês">
          <ul className="pf-list">
            {workflows.map((w) => (
              <li key={w.title} className="pf-task">
                <div style={{ minWidth: 0, flex: '1 1 12rem' }}>
                  <h3 className="pf-task-title">{w.title}</h3>
                  <div className="pf-help">{w.sub}</div>
                </div>
                <Button size="sm" variant="outline" aria-label={`${w.action}: ${w.title}`} onClick={() => onNavigate(w.route)}>
                  {w.action}
                </Button>
              </li>
            ))}
          </ul>
        </ScreenSection>
      </div>
    </div>
  );
};
