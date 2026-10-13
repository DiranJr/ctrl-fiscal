import React, { useState, useEffect, useCallback } from 'react';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { ItemLote } from '../components/ItemLote.js';
import {
  Table,
  Button,
  Badge,
  Card,
  Modal,
  Alert,
  Column,
} from '@ctrl-fiscal/ui';
import {
  Batch,
  getBatchStatusBadgeConfig,
} from '@ctrl-fiscal/api-client';

const SITUACOES: [string, string][] = [
  ['AUTHORIZED', 'autorizado(s)'], ['REJECTED', 'rejeitado(s)'], ['AWAITING_CONFIRMATION', 'com resultado incerto'],
  ['PROCESSING', 'em processamento'], ['SCHEDULED', 'pendente(s) na fila'], ['WITHDRAWN', 'retirado(s)'],
];

export const BatchSchedulingPage: React.FC<{ onNavigate: (route: string) => void }> = ({ onNavigate }) => {
  const { currentCompany, clients, showToast, refreshCounters } = useFiscal();

  const [batches, setBatches] = useState<Batch[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  // Visões pelos estados reais dos itens, aplicadas no SERVIDOR antes da paginação.
  const [view, setView] = useState<'in_progress' | 'attention' | 'history' | 'all'>('in_progress');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [viewCounts, setViewCounts] = useState<Record<string, number>>({});
  const PAGE_SIZE = 25;
  const companyRef = React.useRef<string | undefined>(currentCompany?.id);
  companyRef.current = currentCompany?.id;

  // Detalhes do Lote Selecionado
  const [selectedBatch, setSelectedBatch] = useState<Batch | null>(null);
  // Detalhe vem SEMPRE do servidor (a listagem não traz itens); falha nunca vira lista vazia.
  const [detail, setDetail] = useState<Batch | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isCancellingPending, setIsCancellingPending] = useState(false);

  const loadBatches = useCallback(async (silencioso = false) => {
    if (!currentCompany) return;
    const companyId = currentCompany.id;
    if (!silencioso) setIsLoading(true);
    try {
      const res = await clients.fiscal.getBatches(companyId, page, PAGE_SIZE, view);
      if (companyRef.current !== companyId) return; // resposta de outro workspace: descartada
      setBatches(res.items);
      setTotal(res.total);
      setViewCounts(res.counts || {});
      setListError(null);
    } catch (err: any) {
      if (!silencioso && companyRef.current === companyId) setListError(err.message || 'Falha ao listar emissões e agendamentos.');
    } finally {
      if (!silencioso) setIsLoading(false);
    }
  }, [currentCompany, clients.fiscal, page, view]);

  useEffect(() => {
    loadBatches();
  }, [loadBatches]);

  const loadDetail = async (batchId: string) => {
    if (!currentCompany) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await clients.fiscal.getBatch(currentCompany.id, batchId));
    } catch (err: any) {
      logSafe('detalhe do lote', err);
      setDetail(null);
      setDetailError(err?.message || 'Falha ao carregar os itens do lote.');
    } finally {
      setDetailLoading(false);
    }
  };

  // Atualiza (só leitura) enquanto houver emissão em andamento; para quando a aba fica oculta ou nada está ativo.
  const ativo = (viewCounts['in_progress'] || 0) > 0;
  useEffect(() => {
    if (!ativo) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      loadBatches(true);
      if (selectedBatch && detail && (detail.counts?.SCHEDULED || detail.counts?.PROCESSING || detail.counts?.AWAITING_CONFIRMATION)) {
        loadDetail(selectedBatch.id);
      }
    }, 20000);
    return () => window.clearInterval(timer);
  }, [ativo, loadBatches, selectedBatch, detail]); // eslint-disable-line react-hooks/exhaustive-deps

  const fuso = currentCompany?.timezone || 'America/Sao_Paulo';
  const dataHora = (v: string) => new Intl.DateTimeFormat('pt-BR', { timeZone: fuso, day: '2-digit', month: '2-digit',
    year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(v));
  const quando = (b: Batch) => (b.mode === 'NOW' ? `Solicitada em ${dataHora(b.created_at)}` : `Programada para ${dataHora(b.scheduled_at)}`);

  const openBatch = (b: Batch) => {
    setSelectedBatch(b);
    setDetail(null);
    loadDetail(b.id);
  };

  // Retirar itens pendentes da fila (cancel-pending)
  const handleCancelPending = async (batchId: string) => {
    if (!currentCompany) return;
    setIsCancellingPending(true);
    try {
      const res = await clients.fiscal.cancelBatchPending(currentCompany.id, batchId);
      showToast(`${res.withdrawn} item(ns) retirado(s) da fila (${res.not_withdrawn} mantido(s)).`, 'success');
      await loadBatches();
      // Recarrega lote selecionado
      await loadDetail(batchId);
      await refreshCounters();
    } catch (err: any) {
      showToast(err.message || 'Falha ao retirar itens da fila.', 'error');
    } finally {
      setIsCancellingPending(false);
    }
  };

  const columns: Column<Batch>[] = [
    {
      key: 'mode',
      header: 'Emissão',
      render: (b) => (
        <div>
          <Badge status={b.mode === 'NOW' ? 'PROCESSING' : 'SCHEDULED'} customLabel={b.mode === 'NOW' ? 'Emissão imediata' : 'Agendada'} size="sm" />
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>#{b.id.slice(0, 8)}</div>
        </div>
      ),
    },
    {
      key: 'when',
      header: 'Quando',
      render: (b) => <span>{quando(b)}</span>,
    },
    {
      key: 'items',
      header: 'Itens',
      render: (b) => (
        <span style={{ fontSize: '0.8125rem' }}>
          {b.total} item(ns){SITUACOES.filter(([k]) => (b.counts?.[k] || 0) > 0).length ? ': ' : ''}
          {SITUACOES.filter(([k]) => (b.counts?.[k] || 0) > 0).map(([k, l]) => `${b.counts[k]} ${l}`).join(' · ')}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Situação',
      render: (b) => {
        const cfg = getBatchStatusBadgeConfig(b.status);
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', alignItems: 'flex-start' }}>
            <Badge status={cfg.status} customLabel={cfg.label} />
            {b.needs_attention && <Badge status="REJECTED" customLabel="Exige atenção" size="sm" />}
          </div>
        );
      },
    },
    {
      key: 'actions',
      header: 'Ações',
      align: 'right',
      render: (b) => (
        <Button
          size="sm"
          variant="outline"
          onClick={(e) => {
            e.stopPropagation();
            openBatch(b);
          }}
        >
          Ver itens
        </Button>
      ),
    },
  ];


  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Title & Navigation */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0 }}>Emissões e agendamentos</h1>
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Acompanhe as emissões imediatas e agendadas, o que exige atenção e o histórico.
          </p>
        </div>

        <Button variant="primary" onClick={() => onNavigate('rascunhos')}>
          ➕ Emitir ou agendar rascunhos
        </Button>
      </div>

      {/* Information Banner */}
      <Alert variant="info" title="Como funciona">
        O agendamento define quando o processamento pode começar (no fuso {fuso}); a emissão imediata começa assim que
        confirmada. Retirar um item da fila só vale antes do envio — não cancela uma NFS-e já autorizada.
      </Alert>

      <div className="pf-filtro" role="group" aria-label="Filtrar emissões e agendamentos">
        {([['in_progress', 'Em andamento'], ['attention', 'Exigem atenção'], ['history', 'Histórico'], ['all', 'Todos']] as const).map(([v, l]) => (
          <button key={v} type="button" className="pf-filtro-btn" aria-pressed={view === v} onClick={() => { setView(v); setPage(1); }}>
            {l}{viewCounts[v] != null ? ` (${viewCounts[v]})` : ''}
          </button>
        ))}
      </div>
      {listError && (
        <Alert variant="error" title="Não foi possível carregar a lista">
          {listError} <Button size="sm" variant="secondary" onClick={() => loadBatches()}>Tentar novamente</Button>
        </Alert>
      )}

      {/* Batches Table */}
      <Table
        columns={columns}
        data={batches}
        keyExtractor={(b) => b.id}
        isLoading={isLoading}
        emptyMessage={listError ? 'Lista não carregada.' : view === 'in_progress' ? 'Nenhuma emissão em andamento.'
          : view === 'attention' ? 'Nada exige atenção.' : view === 'history' ? 'Nenhum lote encerrado.' : 'Nenhuma emissão ou agendamento.'}
        onRowClick={(b) => openBatch(b)}
      />
      {total > PAGE_SIZE && (
        <div className="pf-actions-end" aria-label="Paginação">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((n) => n - 1)}>Anterior</Button>
          <span className="pf-help">Página {page} de {Math.ceil(total / PAGE_SIZE)} · {total} lote(s)</span>
          <Button size="sm" variant="outline" disabled={page * PAGE_SIZE >= total} onClick={() => setPage((n) => n + 1)}>Próxima</Button>
        </div>
      )}

      {/* Modal com Detalhes dos Itens do Lote */}
      {selectedBatch && (
        <Modal
          isOpen={true}
          onClose={() => { setSelectedBatch(null); setDetail(null); }}
          title={`${selectedBatch.mode === 'NOW' ? 'Emissão imediata' : 'Emissão agendada'} #${selectedBatch.id.slice(0, 8)}`}
          description={`${quando(selectedBatch)} (fuso ${fuso})`}
          maxWidth="860px"
          footer={
            <div style={{ display: 'flex', justifyContent: 'space-between', width: '100%', alignItems: 'center' }}>
              <div>
                {(detail?.items || []).some((i) => i.status === 'SCHEDULED') && (
                  <Button
                    variant="danger"
                    size="sm"
                    isLoading={isCancellingPending}
                    onClick={() => handleCancelPending(selectedBatch.id)}
                  >
                    🚫 Retirar Pendentes da Fila
                  </Button>
                )}
              </div>
              <Button variant="outline" onClick={() => { setSelectedBatch(null); setDetail(null); }}>
                Fechar
              </Button>
            </div>
          }
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {detailLoading && (
              <p className="pf-help" role="status" aria-busy="true">Carregando os itens do lote…</p>
            )}
            {detailError && !detailLoading && (
              <Alert variant="error" title="Não foi possível carregar os itens do lote">
                <div>{detailError}</div>
                <div className="pf-help">Isto não significa que o lote esteja vazio: os itens não foram lidos.</div>
                <div style={{ marginTop: '0.5rem' }}>
                  <Button size="sm" variant="secondary" onClick={() => loadDetail(selectedBatch.id)}>Tentar novamente</Button>
                </div>
              </Alert>
            )}
            {detail && !detailLoading && (
              <>
                <div className="pf-help" aria-live="polite">
                  {detail.items?.length ?? 0} de {detail.total} item(ns):{' '}
                  {SITUACOES.filter(([k]) => (detail.counts?.[k] || 0) > 0).map(([k, l]) => `${detail.counts[k]} ${l}`).join(' · ') || '—'}
                </div>
                {(detail.items?.length ?? 0) !== detail.total && (
                  <Alert variant="warning" title="Itens divergentes do total">
                    O lote informa {detail.total} item(ns), mas {detail.items?.length ?? 0} foram devolvidos. Recarregue; se
                    persistir, não conclua nada sobre emissão por esta tela.
                  </Alert>
                )}
                {(detail.items || []).some((i) => i.status === 'AWAITING_CONFIRMATION') && (
                  <Alert variant="warning" title="Resultado incerto">
                    Há item(ns) aguardando a reconciliação oficial (consulta da DPS na Sefin). Não reenvie nem emita de novo
                    o mesmo serviço até a confirmação.
                  </Alert>
                )}
                <ul className="pf-lote-itens">
                  {(detail.items || []).map((i) => (
                    <ItemLote key={i.id} item={i} timezone={detail.timezone} companyId={currentCompany?.id} />
                  ))}
                </ul>
              </>
            )}

            <div style={{ padding: '0.75rem', backgroundColor: 'var(--color-bg-subtle)', borderRadius: 'var(--radius-md)', fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
              Aviso: "Retirar da fila" interrompe a transmissão dos itens ainda não iniciados. Itens já autorizados não podem ser cancelados por esta rota.
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};
