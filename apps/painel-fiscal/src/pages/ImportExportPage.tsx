import React, { useEffect, useRef, useState } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { Button, Select, Input, Badge, Icon, PageHeader, ProgressBar } from '@ctrl-fiscal/ui';
import { JobStatusResponse } from '@ctrl-fiscal/api-client';
import { ScreenSection } from '../components/screen/ScreenSection.js';
import { Notice } from '../components/screen/Notice.js';

export const ImportExportPage: React.FC = () => {
  // Intervalos de acompanhamento ativos: encerrados se o usuário sair da tela no meio do job.
  const timers = useRef<Set<ReturnType<typeof setInterval>>>(new Set());
  useEffect(() => () => {
    timers.current.forEach((id) => clearInterval(id));
    timers.current.clear();
  }, []);
  const { currentCompany, clients, showToast, refreshCounters } = useFiscal();

  // Estado de Importação
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [isImporting, setIsImporting] = useState(false);
  const [importJob, setImportJob] = useState<JobStatusResponse | null>(null);

  // Estado de Exportação
  const [exportDirection, setExportDirection] = useState<'ISSUED' | 'RECEIVED'>('ISSUED');
  const [exportCompetence, setExportCompetence] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const [exportJob, setExportJob] = useState<JobStatusResponse | null>(null);

  // Manipular Arquivos Selecionados
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      setSelectedFiles(Array.from(e.target.files));
    }
  };

  // Disparar Importação Multipart
  const handleStartImport = async () => {
    if (!currentCompany || selectedFiles.length === 0) {
      showToast('Selecione arquivos XML ou pacotes ZIP para importar.', 'error');
      return;
    }

    setIsImporting(true);
    setImportJob(null);

    try {
      const res = await clients.fiscal.importDocuments(currentCompany.id, selectedFiles);
      showToast(`Processamento iniciado! Job ID: ${res.job_id.slice(0, 8)}...`, 'info');

      // Polling para acompanhar o job com encerramento garantido em estados terminais (D13)
      const pollInterval = setInterval(async () => {
        try {
          const status = await clients.fiscal.getJobStatus(currentCompany.id, res.job_id);
          setImportJob(status);

          const isTerminal = status.status === 'SUCCEEDED' || status.status === 'PARTIAL' || status.status === 'FAILED';

          if (isTerminal) {
            clearInterval(pollInterval);
            setIsImporting(false);

            if (status.status === 'SUCCEEDED') {
              showToast('Importação de arquivos concluída com sucesso!', 'success');
              await refreshCounters();
            } else if (status.status === 'PARTIAL') {
              showToast(`Importação concluída parcialmente: ${status.error_count} erro(s) encontrado(s).`, 'warning');
              await refreshCounters();
            } else {
              showToast('Importação finalizada com erro total nos arquivos.', 'error');
            }
          }
        } catch (pollErr) {
          logSafe('importação', pollErr);
          clearInterval(pollInterval);
          setIsImporting(false);
        }
      }, 700);
      timers.current.add(pollInterval);
    } catch (err: any) {
      logSafe('importação', err);
      setIsImporting(false);
      showToast(err.message || 'Falha ao iniciar importação.', 'error');
    }
  };

  // Disparar Exportação Assíncrona Excel
  const handleStartExport = async () => {
    if (!currentCompany) return;
    setIsExporting(true);
    setExportJob(null);

    try {
      const res = await clients.fiscal.exportDocuments(
        currentCompany.id,
        {
          direction: exportDirection,
          competence: exportCompetence.trim() || undefined,
        },
        'xlsx'
      );

      showToast(`Exportação solicitada! Job ID: ${res.job_id.slice(0, 8)}...`, 'info');

      // Polling até estado terminal (D13)
      const pollInterval = setInterval(async () => {
        try {
          const status = await clients.fiscal.getJobStatus(currentCompany.id, res.job_id);
          setExportJob(status);

          const isTerminal = status.status === 'SUCCEEDED' || status.status === 'PARTIAL' || status.status === 'FAILED';

          if (isTerminal) {
            clearInterval(pollInterval);
            setIsExporting(false);

            if (status.status === 'SUCCEEDED' || (status.status === 'PARTIAL' && status.download_available)) {
              showToast('Relatório Excel pronto para download!', 'success');
            } else if (status.status === 'FAILED') {
              showToast('Falha na geração do relatório Excel.', 'error');
            }
          }
        } catch (pollErr) {
          logSafe('exportação', pollErr);
          clearInterval(pollInterval);
          setIsExporting(false);
        }
      }, 700);
      timers.current.add(pollInterval);
    } catch (err: any) {
      logSafe('exportação', err);
      setIsExporting(false);
      showToast(err.message || 'Erro ao solicitar exportação.', 'error');
    }
  };

  // Baixar Arquivo Excel Autenticado
  const handleDownloadExport = async () => {
    if (!currentCompany || !exportJob) return;
    try {
      const blob = await clients.fiscal.downloadJobFile(currentCompany.id, exportJob.id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `Relatorio_Fiscal_${exportDirection}_${currentCompany.cnpj.replace(/\D/g, '')}.xlsx`;
      a.click();
      window.URL.revokeObjectURL(url);
      showToast('Download do relatório Excel iniciado.', 'success');
    } catch (err: any) {
      logSafe('download exportação', err);
      showToast(err.message || 'Falha ao baixar arquivo.', 'error');
    }
  };


  const isTerminal = (j: JobStatusResponse | null) =>
    !!j && (j.status === 'SUCCEEDED' || j.status === 'PARTIAL' || j.status === 'FAILED');

  const importBadge = importJob
    ? importJob.status === 'SUCCEEDED'
      ? { status: 'VALID', label: 'Concluído com sucesso' }
      : importJob.status === 'PARTIAL'
      ? { status: 'DIVERGENT', label: `Parcial (${importJob.error_count} erro${importJob.error_count > 1 ? 's' : ''})` }
      : importJob.status === 'FAILED'
      ? { status: 'REJECTED', label: 'Falha total' }
      : { status: 'PROCESSING', label: 'Processando…' }
    : null;

  const exportBadge = exportJob
    ? exportJob.status === 'SUCCEEDED'
      ? { status: 'VALID', label: 'Pronto para download' }
      : exportJob.status === 'FAILED'
      ? { status: 'REJECTED', label: 'Falha' }
      : exportJob.status === 'PARTIAL'
      ? { status: 'DIVERGENT', label: 'Concluído com ressalvas' }
      : { status: 'PROCESSING', label: 'Processando dados…' }
    : null;

  // Texto curto anunciado por leitores de tela a cada mudança de estado dos jobs.
  const importAnnouncement = isImporting
    ? importJob
      ? `Importação em processamento: ${importJob.processed_count} arquivo(s) processado(s).`
      : 'Enviando arquivos para importação.'
    : importJob && importBadge
    ? `Importação: ${importBadge.label}.`
    : '';
  const exportAnnouncement = isExporting
    ? 'Gerando exportação Excel.'
    : exportJob && exportBadge
    ? `Exportação: ${exportBadge.label}.`
    : '';

  return (
    <div className="pf-stack">
      <PageHeader
        title="Importação e Exportação de Documentos"
        description="Carga de lotes XML/ZIP e exportação analítica em Excel com processamento em segundo plano."
      />

      <div className="pf-grid-2">
        {/* Importação (ação principal da tela) */}
        <ScreenSection
          title="Importar arquivos XML / pacotes ZIP"
          description="Envio seguro com análise e extração automatizada de NFS-e"
        >
          <div className="pf-dropzone">
            <input
              type="file"
              id="fileUploadInput"
              className="ui-sr-only"
              multiple
              accept=".xml,.zip"
              onChange={handleFileChange}
              aria-describedby="fileUploadHelp"
            />
            <label htmlFor="fileUploadInput">
              <Icon name="upload" size={18} /> Selecionar arquivos (.xml ou .zip)
            </label>
            <div id="fileUploadHelp" className="pf-help" style={{ marginTop: '0.5rem' }}>
              Suporta múltiplos arquivos XML individuais ou pacotes ZIP comprimidos
            </div>

            {selectedFiles.length > 0 && (
              <div style={{ marginTop: '0.75rem', fontSize: '0.8125rem', fontWeight: 600, color: 'var(--color-text-main)' }}>
                {selectedFiles.length} arquivo(s) selecionado(s):
                <div style={{ fontSize: '0.75rem', fontWeight: 400, color: 'var(--color-text-muted)', overflowWrap: 'anywhere' }}>
                  {selectedFiles.map((f) => f.name).join(', ')}
                </div>
              </div>
            )}
          </div>

          <Button variant="primary" disabled={selectedFiles.length === 0} isLoading={isImporting} onClick={handleStartImport}>
            {!isImporting && <Icon name="upload" size={16} />} Iniciar importação de arquivos
          </Button>

          <p className="ui-sr-only" role="status" aria-live="polite">{importAnnouncement}</p>

          {/* Progresso do job de importação */}
          {(isImporting || importJob) && (
            <div className="pf-job">
              <div className="pf-job-head">
                <h3 style={{ fontSize: '0.8125rem', fontWeight: 600 }}>Status do processamento</h3>
                {importBadge ? (
                  <Badge status={importBadge.status} customLabel={importBadge.label} />
                ) : (
                  <Badge status="QUEUED" customLabel="Enviando…" />
                )}
              </div>

              {isImporting && !isTerminal(importJob) && (
                <ProgressBar value={null} label={importJob ? 'Processando arquivos no servidor…' : 'Enviando arquivos…'} />
              )}

              {importJob && (
                <div style={{ fontSize: '0.8125rem' }}>
                  Arquivos processados: <strong>{importJob.processed_count}</strong> | Erros: <strong>{importJob.error_count}</strong>
                </div>
              )}

              {importJob?.errors && importJob.errors.length > 0 && (
                <div>
                  <div style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--status-rejected-text)', marginBottom: '0.25rem' }}>
                    Detalhamento dos erros ({importJob.errors.length} exibidos):
                  </div>
                  <ul className="pf-errors">
                    {importJob.errors.map((err, idx) => (
                      <li key={idx}>
                        <strong>{err.file || 'Arquivo'}:</strong> {err.message} ({err.code})
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </ScreenSection>

        {/* Exportação */}
        <ScreenSection
          title="Exportar dados fiscais em Excel"
          description="Gera planilha .xlsx formatada via fila de processamento assíncrona"
        >
          <Select
            label="Tipo de notas fiscais"
            options={[
              { value: 'ISSUED', label: 'Notas Emitidas (Serviços Prestados)' },
              { value: 'RECEIVED', label: 'Notas Recebidas (Serviços Tomados)' },
            ]}
            value={exportDirection}
            onChange={(e) => setExportDirection(e.target.value as 'ISSUED' | 'RECEIVED')}
          />

          <Input
            label="Competência específica (opcional)"
            value={exportCompetence}
            onChange={(e) => setExportCompetence(e.target.value)}
            placeholder="Ex: 2026-10"
            helperText="Deixe em branco para exportar todas as competências"
          />

          <Button variant="secondary" isLoading={isExporting} onClick={handleStartExport}>
            {!isExporting && <Icon name="file" size={16} />} Gerar exportação Excel
          </Button>

          <p className="ui-sr-only" role="status" aria-live="polite">{exportAnnouncement}</p>

          {/* Job de exportação */}
          {(isExporting || exportJob) && (
            <div className="pf-job">
              <div className="pf-job-head">
                <h3 style={{ fontSize: '0.8125rem', fontWeight: 600 }}>Job de exportação</h3>
                {exportBadge ? (
                  <Badge status={exportBadge.status} customLabel={exportBadge.label} />
                ) : (
                  <Badge status="QUEUED" customLabel="Solicitando…" />
                )}
              </div>

              {isExporting && !isTerminal(exportJob) && <ProgressBar value={null} label="Gerando planilha no servidor…" />}

              {exportJob && (exportJob.stale_documents ?? 0) > 0 && (
                <Notice variant="warning">
                  {exportJob.stale_documents} nota(s) da planilha foram canceladas ou substituídas depois da geração.
                  Gere a exportação novamente para obter a situação atual.
                </Notice>
              )}
              {exportJob && exportJob.download_available && !(exportJob.stale_documents ?? 0) && (
                <div>
                  <Button variant="secondary" size="sm" onClick={handleDownloadExport}>
                    <Icon name="download" size={16} /> Baixar arquivo Excel (.xlsx)
                  </Button>
                </div>
              )}
            </div>
          )}

          <Notice variant="info">
            Regra contratual: não geramos exportação direta no client-side com apenas a página atual.
            O backend processa a totalidade dos registros e fornece download autenticado.
          </Notice>
        </ScreenSection>
      </div>
    </div>
  );
};
