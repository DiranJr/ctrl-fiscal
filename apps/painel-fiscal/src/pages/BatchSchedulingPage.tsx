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

  // Detalhes do Lote Selecionado
  const [selectedBatch, setSelectedBatch] = useState<Batch | null>(null);
  // Detalhe vem SEMPRE do servidor (a listagem não traz itens); falha nunca vira lista vazia.
  const [detail, setDetail] = useState<Batch | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [isCancellingPending, setIsCancellingPending] = useState(false);

  const loadBatches = useCallback(async () => {
    if (!currentCompany) return;
    setIsLoading(true);
    try {
      const res = await clients.fiscal.getBatches(currentCompany.id);
      setBatches(res.items);
    } catch (err: any) {
      showToast(err.message || 'Falha ao listar lotes agendados.', 'error');
    } finally {
      setIsLoading(false);
    }
  }, [currentCompany, clients.fiscal, showToast]);

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
      key: 'id',
      header: 'ID do Lote',
      render: (b) => <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.8125rem' }}>{b.id.slice(0, 12)}...</span>,
    },
    {
      key: 'scheduled_at',
      header: 'Data/Hora de Início',
      render: (b) => {
        const localFormatted = new Intl.DateTimeFormat('pt-BR', {
          timeZone: b.timezone,
          day: '2-digit',
          month: '2-digit',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        }).format(new Date(b.scheduled_at));
        return (
          <div>
            <div style={{ fontWeight: 600 }}>{localFormatted}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
              Fuso: {b.timezone} {b.scheduled_local ? `(${b.scheduled_local})` : ''}
            </div>
          </div>
        );
      },
    },
    {
      key: 'total',
      header: 'Total de Notas',
      align: 'center',
      render: (b) => <span style={{ fontWeight: 600 }}>{b.total}</span>,
    },
    {
      key: 'status',
      header: 'Status do Lote',
      render: (b) => {
        const cfg = getBatchStatusBadgeConfig(b.status);
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            <div><Badge status={cfg.status} customLabel={cfg.label} /></div>
            {b.progress && (
              <span style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)' }}>
                Progresso: {b.progress.done}/{b.progress.total} ({b.progress.percent}%)
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: 'created_at',
      header: 'Criado em',
      render: (b) => <span>{new Date(b.created_at).toLocaleDateString('pt-BR')}</span>,
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
          👁️ Ver Itens
        </Button>
      ),
    },
  ];


  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Title & Navigation */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0 }}>Lotes Agendados</h1>
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Programação e acompanhamento de remessas para transmissão em 00:01
          </p>
        </div>

        <Button variant="primary" onClick={() => onNavigate('rascunhos')}>
          ➕ Agendar Novo Lote a partir dos Rascunhos
        </Button>
      </div>

      {/* Information Banner */}
      <Alert variant="info" title="Regra de Início Elegível para Transmissão">
        O horário agendado marca o momento em que os itens se tornam elegíveis para transmissão automática pelo worker no fuso {currentCompany?.timezone || 'America/Sao_Paulo'}.
        A retirada de itens pendentes não equivale ao cancelamento de NFS-e autorizada.
      </Alert>

      {/* Batches Table */}
      <Table
        columns={columns}
        data={batches}
        keyExtractor={(b) => b.id}
        isLoading={isLoading}
        emptyMessage="Nenhum lote programado no momento."
        onRowClick={(b) => openBatch(b)}
      />

      {/* Modal com Detalhes dos Itens do Lote */}
      {selectedBatch && (
        <Modal
          isOpen={true}
          onClose={() => { setSelectedBatch(null); setDetail(null); }}
          title={`Detalhes do Lote #${selectedBatch.id.slice(0, 8)}`}
          description={`Agendado para ${new Intl.DateTimeFormat('pt-BR', { timeZone: selectedBatch.timezone, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(selectedBatch.scheduled_at))} no fuso ${selectedBatch.timezone}`}
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
