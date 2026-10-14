import React, { useEffect, useRef, useState } from 'react';
import type { BundleFormat, BundleRequest, JobStatusResponse } from '@ctrl-fiscal/api-client';
import { Alert, Badge, Button, Modal, ProgressBar } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';
import { Indisponivel, NotaSelecionada, conferirDisponibilidade, rotuloNota } from './selecaoNotas.js';

export interface BundleScope {
  /** 'selection' = notas marcadas; 'all' = TODOS os resultados do filtro atual (além da página exibida). */
  kind: 'selection' | 'all';
  /** Workspace fixado ao abrir: trocar de workspace fecha o diálogo e nada é enviado para o outro. */
  companyId: string;
  count: number;
  ids?: string[];
  /** Identificação das notas selecionadas (para avisos e para nomear pendências). */
  itens?: NotaSelecionada[];
  filters?: BundleRequest['filters'];
  environment?: string;
}

const LIMITS: Record<BundleFormat, number> = { PDF: 1000, PDF_XML: 1000, XML: 5000 };
const FORMAT_LABEL: Record<BundleFormat, string> = { PDF: 'PDF (DANFSe)', XML: 'XML original', PDF_XML: 'PDF + XML' };

export const BundleDialog: React.FC<{ scope: BundleScope; onClose: () => void; onRemover?: (ids: string[]) => void }> = ({
  scope, onClose, onRemover,
}) => {
  const { clients, showToast } = useFiscal();
  const companyId = scope.companyId;
  const [indisponiveis, setIndisponiveis] = useState<Indisponivel[] | null>(null);
  const [conferindo, setConferindo] = useState(false);
  const porId = new Map((scope.itens || []).map((n) => [n.id, n] as const));
  const porChave = new Map((scope.itens || []).filter((n) => n.access_key).map((n) => [n.access_key!, n] as const));
  const [format, setFormat] = useState<BundleFormat>('PDF_XML');
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const poll = (jobId: string, delay = 1000) => {
    timer.current = window.setTimeout(async () => {
      try {
        const j = await clients.fiscal.getJobStatus(companyId, jobId);
        setJob(j);
        if (j.status === 'QUEUED' || j.status === 'RUNNING') poll(jobId, Math.min(delay * 1.4, 5000));
      } catch (err) {
        logSafe('lote', err);
        setError('Não foi possível acompanhar o processamento. Tente novamente em instantes.');
      }
    }, delay);
  };

  const start = async () => {
    setStarting(true);
    setError(null);
    setIndisponiveis(null);
    try {
      const req: BundleRequest = scope.kind === 'selection'
        ? { format, document_ids: scope.ids, environment: scope.environment }
        : { format, filters: scope.filters, environment: scope.environment };
      const r = await clients.fiscal.createBundle(companyId, req);
      setJob({ id: r.job_id, status: 'QUEUED', processed_count: 0, error_count: 0, errors: [] } as JobStatusResponse);
      poll(r.job_id, 600);
    } catch (err) {
      logSafe('lote', err);
      const p = (err as { problem?: { detail?: string; code?: string } })?.problem;
      setError(p?.detail || 'Não foi possível iniciar o download em lote.');
      // O servidor só informa QUANTAS notas não estão disponíveis: confere uma a uma para mostrar quais e por quê.
      if (p?.code === 'BUNDLE_DOCUMENTS_NOT_FOUND' && scope.ids?.length) {
        setConferindo(true);
        try {
          setIndisponiveis(await conferirDisponibilidade(scope.ids, (id) => clients.fiscal.getDocument(companyId, id),
            (id) => porId.get(id)?.environment ?? null));
        } finally {
          setConferindo(false);
        }
      }
    } finally {
      setStarting(false);
    }
  };

  const download = async () => {
    if (!job) return;
    try {
      await clients.fiscal.saveJobFileToDisk(companyId, job.id);
      showToast('Download iniciado. O arquivo será salvo pelo navegador.', 'success');
    } catch (err) {
      logSafe('lote', err);
      const p = (err as { problem?: { status?: number; detail?: string } })?.problem;
      setError(p?.status === 410 ? 'O arquivo expirou e foi removido. Gere novamente.' : p?.detail || 'Falha ao iniciar o download.');
    }
  };

  const running = job && (job.status === 'QUEUED' || job.status === 'RUNNING');
  const done = job && !running;
  const res = (job?.result || {}) as { documents?: number; pdf_files?: number; xml_files?: number; errors?: number };
  const total = res.documents ?? scope.count;
  const pct = job && total ? (job.processed_count / total) * 100 : null;
  const overLimit = scope.count > LIMITS[format];
  const itens = scope.itens || [];
  const semXml = format !== 'PDF' ? itens.filter((n) => !n.xml_available) : [];
  const semPdf = format !== 'XML' ? itens.filter((n) => !n.danfse_available) : [];
  const removiveis = (indisponiveis || []).filter((i) => i.definitivo).map((i) => i.id);
  const nomeDoErro = (file?: string | null) => {
    const n = (file && (porId.get(file) || porChave.get(file))) || null;
    return n ? rotuloNota(n) : (file || '').slice(-12);
  };

  return (
    <Modal isOpen onClose={onClose} title="Baixar notas em lote" maxWidth="560px"
      description={scope.kind === 'all'
        ? `Todos os ${scope.count} resultados do filtro atual (todas as páginas).`
        : `${scope.count} ${scope.count === 1 ? 'nota selecionada' : 'notas selecionadas'}.`}
      footer={
        !job ? (
          <>
            <Button variant="outline" onClick={onClose}>Cancelar</Button>
            <Button variant="primary" onClick={start} isLoading={starting} disabled={overLimit || scope.count === 0}>
              Gerar arquivo ZIP ({scope.count} {scope.count === 1 ? 'nota' : 'notas'}, {FORMAT_LABEL[format]})
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" onClick={onClose}>{running ? 'Fechar (continua em segundo plano)' : 'Fechar'}</Button>
            {done && job.download_available && <Button variant="primary" onClick={download}>Baixar ZIP</Button>}
          </>
        )
      }>
      {job && (job.stale_documents ?? 0) > 0 && (
        <Alert variant="warning" title="Arquivo desatualizado">
          {job.stale_documents} nota(s) foram canceladas ou substituídas depois da geração. O download foi bloqueado:
          gere o lote novamente para obter os arquivos com a situação atual.
        </Alert>
      )}
      {!job && (
        <fieldset style={{ border: 'none', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <legend style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.5rem' }}>Formato</legend>
          {(['PDF_XML', 'PDF', 'XML'] as BundleFormat[]).map((f) => (
            <label key={f} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', fontSize: '0.875rem' }}>
              <input type="radio" name="bundle-format" value={f} checked={format === f} onChange={() => setFormat(f)} />
              {FORMAT_LABEL[f]} <span style={{ color: 'var(--color-text-subtle)' }}>(até {LIMITS[f].toLocaleString('pt-BR')} notas)</span>
            </label>
          ))}
          <p style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)', marginTop: '0.5rem' }}>
            Um ZIP com uma pasta por nota e um manifesto (manifesto.json e manifesto.csv). O PDF é o DANFSe gerado pela
            plataforma a partir do XML oficial; o XML é o original. O conjunto de notas é fixado ao iniciar. O arquivo
            fica disponível por 24 horas.
          </p>
          {overLimit && <Alert variant="warning">Acima do limite deste formato. Refine os filtros ou escolha “XML original”.</Alert>}
          {(semXml.length > 0 || semPdf.length > 0) && (
            <Alert variant="warning" title="Arquivos indisponíveis em parte das notas">
              {semXml.length > 0 && <div>{semXml.length} sem XML original: {semXml.slice(0, 5).map(rotuloNota).join(', ')}{semXml.length > 5 ? '…' : ''}.</div>}
              {semPdf.length > 0 && <div>{semPdf.length} sem DANFSe (PDF): {semPdf.slice(0, 5).map(rotuloNota).join(', ')}{semPdf.length > 5 ? '…' : ''}.</div>}
              <div>Elas entram no manifesto como pendência, com o motivo; os demais arquivos são incluídos.</div>
            </Alert>
          )}
        </fieldset>
      )}
      {job && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {running && <ProgressBar value={pct} label={`Processando ${job.processed_count} de ${total} notas…`} />}
          {done && (
            <div aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.875rem' }}>
              <div>
                <Badge status={job.status} customLabel={job.status === 'SUCCEEDED' ? 'Concluído' : job.status === 'PARTIAL' ? 'Concluído com pendências' : 'Falhou'} />
              </div>
              <div>{res.pdf_files ?? 0} PDF · {res.xml_files ?? 0} XML · {res.errors ?? job.error_count} pendência(s)</div>
              {job.output_expires_at && (
                <div style={{ color: 'var(--color-text-muted)' }}>Disponível até {new Date(job.output_expires_at).toLocaleString('pt-BR')}.</div>
              )}
              {job.status === 'PARTIAL' && (
                <Alert variant="warning" title="Resultado parcial">
                  Os arquivos disponíveis foram incluídos (um PDF com falha não descarta o XML da nota). Detalhes por nota no manifesto.
                </Alert>
              )}
              {job.errors.length > 0 && (
                <ul style={{ paddingLeft: '1.25rem', maxHeight: '10rem', overflow: 'auto', fontSize: '0.8125rem' }}>
                  {job.errors.slice(0, 20).map((e, i) => (
                    <li key={i}><span className="ui-mono">{nomeDoErro(e.file)}</span> — {e.message}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
      {error && <div style={{ marginTop: '0.75rem' }}><Alert variant="error">{error} A seleção foi mantida.</Alert></div>}
      {conferindo && <p role="status" style={{ marginTop: '0.5rem', fontSize: '0.8125rem' }}>Conferindo quais notas estão indisponíveis…</p>}
      {indisponiveis && (
        <div role="region" aria-label="Notas indisponíveis" style={{ marginTop: '0.75rem', fontSize: '0.8125rem' }}>
          {indisponiveis.length === 0 ? (
            <p>Todas as notas estão disponíveis agora. Tente gerar novamente.</p>
          ) : (
            <>
              <p style={{ fontWeight: 600 }}>Notas que não podem entrar no lote:</p>
              <ul style={{ paddingLeft: '1.25rem', maxHeight: '10rem', overflow: 'auto' }}>
                {indisponiveis.map((i) => (
                  <li key={i.id}>{porId.get(i.id) ? rotuloNota(porId.get(i.id)!) : `Documento ${i.id.slice(0, 8)}`} — {i.motivo}</li>
                ))}
              </ul>
              {removiveis.length > 0 && onRemover && (
                <Button variant="outline" size="sm" onClick={() => { onRemover(removiveis); onClose(); }}>
                  Remover {removiveis.length} da seleção
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
};
