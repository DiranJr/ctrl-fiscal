import React, { useState, useEffect } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { Button, Badge, Icon, PageHeader, ProgressBar, Skeleton } from '@ctrl-fiscal/ui';
import { JobStatusResponse } from '@ctrl-fiscal/api-client';
import { ScreenSection, InfoTile } from '../components/screen/ScreenSection.js';
import { Notice } from '../components/screen/Notice.js';

export const SyncPage: React.FC = () => {
  const { currentCompany, syncStatus, refreshSyncStatus, refreshCounters, clients, showToast, isLoading } = useFiscal();
  const [isTriggering, setIsTriggering] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<JobStatusResponse | null>(null);
  const [isPollingJob, setIsPollingJob] = useState(false);

  // Isolamento estrito entre empresas: limpa o job acompanhado ao alternar de empresa
  useEffect(() => {
    setActiveJobId(null);
    setActiveJob(null);
    setIsPollingJob(false);
  }, [currentCompany?.id]);

  // Acompanhamento contínuo do job_id recém-solicitado até atingir estado terminal
  useEffect(() => {
    if (!activeJobId || !currentCompany || !isPollingJob) return;

    let isMounted = true;
    let pollCount = 0;
    const maxPolls = 60; // 60 * 1500ms = 90s limite

    const pollJob = async () => {
      try {
        const job = await clients.fiscal.getJobStatus(currentCompany.id, activeJobId);
        if (!isMounted) return;
        setActiveJob(job);

        // Estados terminais do job
        if (job.status === 'SUCCEEDED' || job.status === 'PARTIAL' || job.status === 'FAILED') {
          setIsPollingJob(false);
          await refreshSyncStatus();
          await refreshCounters();

          if (job.status === 'SUCCEEDED') {
            showToast(
              `Sincronização #${job.id.slice(0, 8)} concluída com sucesso! (${job.processed_count} eventos processados)`,
              'success'
            );
          } else if (job.status === 'PARTIAL') {
            showToast(
              `Sincronização #${job.id.slice(0, 8)} concluída com ressalvas (${job.error_count} erros).`,
              'warning'
            );
          } else {
            const firstErr = job.errors?.[0]?.message || 'Erro durante a execução do job no backend';
            showToast(`Sincronização #${job.id.slice(0, 8)} falhou: ${firstErr}`, 'error');
          }
        }
      } catch (err: any) {
        logSafe('job', err);
        pollCount++;
        if (pollCount >= maxPolls) {
          if (isMounted) {
            setIsPollingJob(false);
            showToast('Tempo limite de acompanhamento do job atingido.', 'warning');
          }
        }
      }
    };

    // Consulta imediata na primeira iteração
    pollJob();

    const interval = setInterval(() => {
      pollCount++;
      if (pollCount >= maxPolls) {
        clearInterval(interval);
        if (isMounted) {
          setIsPollingJob(false);
          showToast('Tempo limite de acompanhamento do job atingido.', 'warning');
        }
        return;
      }
      pollJob();
    }, 1500);

    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [activeJobId, currentCompany?.id, isPollingJob, clients.fiscal, refreshSyncStatus, refreshCounters, showToast]);

  const handleManualSync = async () => {
    if (!currentCompany || isTriggering || isPollingJob) return;
    setIsTriggering(true);
    try {
      const idempotencyKey = `sync-${currentCompany.id}-${Date.now()}`;
      const res = await clients.fiscal.triggerSync(currentCompany.id, idempotencyKey);
      setActiveJobId(res.job_id);
      setActiveJob({
        id: res.job_id,
        status: (res.status as any) || 'QUEUED',
        processed_count: 0,
        error_count: 0,
        errors: [],
      });
      setIsPollingJob(true);
      showToast(`Job de sincronização #${res.job_id.slice(0, 8)} enfileirado no backend!`, 'info');
    } catch (err: any) {
      logSafe('sincronização', err);
      showToast(err.message || 'Falha ao solicitar sincronização.', 'error');
    } finally {
      setIsTriggering(false);
    }
  };

  const isConfigured = syncStatus?.status !== 'NOT_CONFIGURED';

  const scheduleBadge = {
    status:
      syncStatus?.status === 'OK'
        ? 'VALID'
        : syncStatus?.status === 'NOT_CONFIGURED'
        ? 'NOT_CONFIGURED'
        : syncStatus?.status === 'BLOCKED' || syncStatus?.status === 'ERROR'
        ? 'REJECTED'
        : syncStatus?.status === 'DRAINING'
        ? 'PENDING'
        : 'PROCESSING',
    label:
      syncStatus?.status === 'OK'
        ? 'Ativo e saudável (OK)'
        : syncStatus?.status === 'NOT_CONFIGURED'
        ? 'Não configurado'
        : syncStatus?.status === 'BLOCKED'
        ? 'Bloqueado'
        : syncStatus?.status === 'DRAINING'
        ? 'Drenando fila'
        : syncStatus?.status === 'RUNNING'
        ? 'Executando'
        : syncStatus?.status === 'PENDING'
        ? 'Pendente'
        : (syncStatus?.status as any) === 'IDLE'
        ? 'Ativo (OK)'
        : 'Erro',
  };

  const jobInProgress = !!activeJob && (activeJob.status === 'QUEUED' || activeJob.status === 'RUNNING');
  const jobShort = activeJob ? `#${activeJob.id.slice(0, 8)}` : '';
  const jobAnnouncement = !activeJob
    ? ''
    : activeJob.status === 'QUEUED'
    ? `Sincronização ${jobShort} na fila.`
    : activeJob.status === 'RUNNING'
    ? `Sincronização ${jobShort} em execução.`
    : activeJob.status === 'SUCCEEDED'
    ? `Sincronização ${jobShort} concluída com sucesso.`
    : activeJob.status === 'PARTIAL'
    ? `Sincronização ${jobShort} concluída parcialmente, com ${activeJob.error_count} erro(s).`
    : `Sincronização ${jobShort} falhou.`;
  const statusLoading = isLoading && !syncStatus;
  const statusFailed = !isLoading && !syncStatus;

  const handleRetryStatus = async () => {
    setIsRetrying(true);
    try {
      await refreshSyncStatus();
    } finally {
      setIsRetrying(false);
    }
  };

  return (
    <div className="pf-stack">
      <PageHeader
        title="Sincronização com o ADN Nacional"
        description="Monitoramento e captura contínua de NFS-e recebidas pelo Ambiente de Dados Nacional."
        primaryAction={
          <Button
            variant="primary"
            isLoading={isTriggering || isPollingJob}
            disabled={!isConfigured || isTriggering || isPollingJob}
            onClick={handleManualSync}
          >
            {!isTriggering && !isPollingJob && <Icon name="sync" size={16} />}
            {isTriggering ? 'Enfileirando…' : isPollingJob ? 'Acompanhando job…' : 'Sincronizar agora'}
          </Button>
        }
      />

      {!isConfigured && (
        <Notice variant="warning" title="Certificado digital ausente ou não habilitado">
          A captura automática está suspensa para esta empresa pois o certificado digital A1 ainda não foi parametrizado no backend.
          Nunca dizemos "sem notas" na ausência de configuração.
        </Notice>
      )}

      {statusFailed && (
        <Notice
          variant="error"
          title="Não foi possível carregar a situação da sincronização"
          action={
            <Button variant="outline" size="sm" isLoading={isRetrying} onClick={handleRetryStatus}>
              Tentar novamente
            </Button>
          }
        >
          Verifique a conexão e tente novamente.
        </Notice>
      )}

      {/* Região viva sempre presente: anuncia só a mudança de estado do job, não a seção inteira. */}
      <p className="ui-sr-only" role="status" aria-live="polite">{jobAnnouncement}</p>

      {/* Acompanhamento do job recém-solicitado (rastreamento estrito pelo job_id) */}
      <>
        {activeJob && (
          <ScreenSection title="Acompanhamento da sincronização solicitada" description={<>Job <span className="ui-mono">{activeJob.id}</span></>}>
            {jobInProgress && (
              <ProgressBar
                value={null}
                label={activeJob.status === 'QUEUED' ? 'Aguardando início pelo fiscal-worker…' : 'Consultando o ADN…'}
              />
            )}

            {activeJob.status === 'QUEUED' && (
              <Notice
                variant="info"
                title={<span className="pf-job-head"><span>1. Solicitação de sincronização aceita: Job {activeJob.id}</span><Badge status="QUEUED" customLabel="QUEUED (Na fila)" /></span>}
              >
                Pedido enfileirado com sucesso no backend (HTTP 202 Accepted). Aguardando alocação e início de execução pelo fiscal-worker.
              </Notice>
            )}

            {activeJob.status === 'RUNNING' && (
              <Notice
                variant="warning"
                title={<span className="pf-job-head"><span>2. Sincronização em execução: Job {activeJob.id}</span><Badge status="PROCESSING" customLabel="RUNNING (Em execução)" /></span>}
              >
                O fiscal-worker está no momento realizando a consulta ativa e download aos webservices do Ambiente de Dados Nacional (ADN).
              </Notice>
            )}

            {activeJob.status === 'SUCCEEDED' && (
              <Notice
                variant="success"
                title={<span className="pf-job-head"><span>3. Sincronização concluída com sucesso: Job {activeJob.id}</span><Badge status="VALID" customLabel="SUCCEEDED (Sucesso)" /></span>}
              >
                Ciclo de sincronização finalizado com êxito{activeJob.finished_at ? ` em ${new Date(activeJob.finished_at).toLocaleString('pt-BR')}` : ''}.
                Eventos processados neste job: <strong>{activeJob.processed_count}</strong>.
              </Notice>
            )}

            {activeJob.status === 'PARTIAL' && (
              <Notice
                variant="warning"
                title={<span className="pf-job-head"><span>3. Sincronização parcial: Job {activeJob.id}</span><Badge status="DIVERGENT" customLabel="PARTIAL (Parcial)" /></span>}
              >
                Processamento finalizado com ressalvas. Eventos processados: <strong>{activeJob.processed_count}</strong>, Erros encontrados:{' '}
                <strong>{activeJob.error_count}</strong>.
                {activeJob.errors && activeJob.errors.length > 0 && (
                  <div style={{ marginTop: '0.25rem', fontSize: '0.75rem' }}>
                    Detalhe do erro: [{activeJob.errors[0]?.code}] {activeJob.errors[0]?.message}
                  </div>
                )}
              </Notice>
            )}

            {activeJob.status === 'FAILED' && (
              <Notice
                variant="error"
                title={<span className="pf-job-head"><span>3. Sincronização com falha: Job {activeJob.id}</span><Badge status="REJECTED" customLabel="FAILED (Falha)" /></span>}
              >
                O processamento do job falhou no backend.
                {activeJob.errors && activeJob.errors.length > 0 && (
                  <span> Erro: <strong>[{activeJob.errors[0]?.code}] {activeJob.errors[0]?.message}</strong></span>
                )}
              </Notice>
            )}
          </ScreenSection>
        )}
      </>

      <ScreenSection
        title="Parâmetros do ciclo de sincronização"
        description="Rotina gerenciada pelo fiscal-worker com cursor durável e tolerância a falhas"
      >
        {statusLoading ? (
          <div className="pf-grid-tiles" aria-busy="true">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} height="4.25rem" borderRadius="var(--radius-md)" />
            ))}
          </div>
        ) : (
          <>
            <div className="pf-grid-tiles">
              <InfoTile label="Frequência automática" hint="Ciclos compartilhados com lease transacional">
                <span style={{ fontSize: '1rem', fontWeight: 700, color: 'var(--color-primary)' }}>A cada 12 horas</span>
              </InfoTile>
              <InfoTile label="Situação da agenda">
                <Badge status={scheduleBadge.status} customLabel={scheduleBadge.label} />
              </InfoTile>
              <InfoTile label="Último sucesso da agenda">
                {syncStatus?.last_success_at ? new Date(syncStatus.last_success_at).toLocaleString('pt-BR') : '—'}
              </InfoTile>
              <InfoTile label="Próxima execução agendada">
                {syncStatus?.next_run_at ? new Date(syncStatus.next_run_at).toLocaleString('pt-BR') : 'Aguardando agendamento'}
              </InfoTile>
            </div>

            {/* Estatísticas de captura */}
            <div className="pf-grid-tiles">
              <InfoTile label="Eventos ADN processados" variant="outline">
                <span className="pf-big ui-num">{syncStatus?.processed_count ?? 0}</span>
              </InfoTile>
              <InfoTile label="NFS-e tomadas capturadas" variant="outline">
                <span className="pf-big ui-num" style={{ color: 'var(--color-primary)' }}>{syncStatus?.received_count ?? 0}</span>
              </InfoTile>
              <InfoTile label="Em quarentena / pendentes" variant="outline">
                <span className="pf-big ui-num">{syncStatus?.pending_parse_count ?? 0}</span>
              </InfoTile>
            </div>
          </>
        )}

        {/* Mensagem de cobertura */}
        <Notice variant="info" title="Cobertura de documentos no ADN">
          {syncStatus?.coverage_message || 'Documentos disponibilizados no ADN conforme direitos do certificado digital da empresa.'}
        </Notice>

        {/* Estado do agendamento periódico de fundo (quando não há job ativo solicitado na sessão) */}
        {!activeJob && (
          <>
            {syncStatus?.running && (
              <Notice variant="warning">
                <strong>Rotina periódica em execução:</strong> O fiscal-worker está executando a consulta agendada aos webservices do
                Ambiente de Dados Nacional (ADN).
              </Notice>
            )}

            {!syncStatus?.running && syncStatus?.last_error && (
              <Notice variant="error">
                <strong>Último erro registrado pelo agendamento periódico:</strong> [{syncStatus.last_error.code}] {syncStatus.last_error.message}
              </Notice>
            )}

            {!syncStatus?.running && syncStatus?.last_success_at && (
              <Notice variant="neutral">
                <strong>Histórico da rotina periódica:</strong> Última execução do agendamento finalizada em{' '}
                {new Date(syncStatus.last_success_at).toLocaleString('pt-BR')}. ({syncStatus.received_count ?? 0} notas capturadas no acumulado).
              </Notice>
            )}
          </>
        )}
      </ScreenSection>

      {/* Guia de ciclo fiscal e estados */}
      <ScreenSection title="Esclarecimento de estados e ciclos operacionais">
        <ul className="pf-bullets pf-muted">
          <li><strong>Solicitação aceita:</strong> O backend enfileira o pedido assíncrono (HTTP 202) sem bloquear a interface.</li>
          <li><strong>Sincronização em execução:</strong> Worker realiza comunicação mTLS segura com o ADN.</li>
          <li><strong>Sincronização concluída ou falha:</strong> Retorno definitivo do processamento com cursor durável ou registro de erro.</li>
          <li><strong>Lote agendado:</strong> Rascunhos aguardando na fila o horário local programado; nenhuma nota foi enviada para o Fisco ainda.</li>
          <li><strong>Nota efetivamente autorizada:</strong> Documento transmitido com DPS assinada e chancelado pelo Fisco com protocolo oficial e cStat 100/101.</li>
        </ul>
      </ScreenSection>
    </div>
  );
};
