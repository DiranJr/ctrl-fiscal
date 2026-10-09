import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import type { CancellationInfo, CancellationKind, CancellationRequestOut, FiscalDocument } from '@ctrl-fiscal/api-client';
import { Alert, Badge, Button, Skeleton } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../../context/FiscalContext.js';

const CancellationDialog = lazy(() => import('./CancellationDialog.js'));
const ACTIVE = new Set(['QUEUED', 'PROCESSING', 'AWAITING_CONFIRMATION']);
const STATUS: Record<string, { status: string; label: string }> = {
  QUEUED: { status: 'SCHEDULED', label: 'Na fila' },
  PROCESSING: { status: 'PROCESSING', label: 'Enviando' },
  AWAITING_CONFIRMATION: { status: 'AWAITING_CONFIRMATION', label: 'Aguardando confirmação oficial' },
  REGISTERED: { status: 'AUTHORIZED', label: 'Evento registrado' },
  REJECTED: { status: 'REJECTED', label: 'Recusado' },
  BLOCKED: { status: 'WITHDRAWN', label: 'Não enviado (bloqueado)' },
  WITHDRAWN: { status: 'WITHDRAWN', label: 'Retirado (não enviado)' },
};

/**
 * Cancelamento da NFS-e EMITIDA, no painel da nota. Elegibilidade e permissão vêm do servidor (a tela não decide);
 * pedidos em andamento são acompanhados até o resultado oficial e, ao registrar o evento, a nota é recarregada
 * (situação, marca d'água do PDF e histórico de eventos).
 */
export const CancellationPanel: React.FC<{ doc: FiscalDocument; onDocumentChanged: () => void }> = ({ doc, onDocumentChanged }) => {
  const { clients, currentCompany } = useFiscal();
  const [info, setInfo] = useState<CancellationInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<CancellationKind | null>(null);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    if (!currentCompany) return;
    try {
      setInfo(await clients.fiscal.getCancellationInfo(currentCompany.id, doc.id));
      setError(null);
    } catch (err) {
      logSafe('cancellation.info', err);
      setError((err as { problem?: { detail?: string } })?.problem?.detail || 'Não foi possível consultar o cancelamento.');
    }
  }, [clients.fiscal, currentCompany, doc.id]);

  useEffect(() => { void load(); }, [load, tick]);

  const active = info?.requests.find((r) => ACTIVE.has(r.status));
  // Acompanha o pedido em andamento; ao terminar, recarrega a nota (situação e eventos vêm do evento oficial).
  useEffect(() => {
    if (!active || !currentCompany) return;
    const t = window.setInterval(async () => {
      try {
        const r = await clients.fiscal.getCancellation(currentCompany.id, active.id);
        if (!ACTIVE.has(r.status)) {
          setTick((x) => x + 1);
          onDocumentChanged();
        }
      } catch (err) {
        logSafe('cancellation.poll', err);
      }
    }, 4000);
    return () => window.clearInterval(t);
  }, [active, clients.fiscal, currentCompany, onDocumentChanged]);

  const onRequested = (r: CancellationRequestOut) => {
    setDialog(null);
    setInfo((cur) => (cur ? { ...cur, requests: [r, ...cur.requests.filter((x) => x.id !== r.id)] } : cur));
    setTick((x) => x + 1);
  };

  return (
    <section aria-labelledby="cancel-title" style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
      <h3 id="cancel-title" style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>Cancelamento</h3>
      {error && <Alert variant="error">{error}</Alert>}
      {!info && !error && <Skeleton height="3rem" />}
      {info && (
        <div className="pf-stack">
          {active ? (
            <Alert variant="info" title="Pedido em andamento">
              {STATUS[active.status]?.label}. A situação da nota só muda quando o evento oficial for registrado.
            </Alert>
          ) : info.eligible.CANCEL || info.eligible.ANALYSIS ? (
            info.can_request ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                {info.eligible.CANCEL && (
                  <Button variant="danger" size="sm" onClick={() => setDialog('CANCEL')}>Cancelar NFS-e…</Button>
                )}
                {info.eligible.ANALYSIS && (
                  <Button variant="outline" size="sm" onClick={() => setDialog('ANALYSIS')}>Solicitar análise fiscal…</Button>
                )}
              </div>
            ) : (
              <p className="pf-help">Seu usuário não tem a permissão de cancelamento nesta empresa.</p>
            )
          ) : (
            <p className="pf-help">{info.blockers.CANCEL[0]?.message || 'Cancelamento indisponível para esta nota.'}</p>
          )}

          {info.requests.length > 0 && (
            <div>
              <h4 style={{ fontSize: '0.8125rem', margin: '0.25rem 0' }}>Pedidos</h4>
              <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {info.requests.map((r) => (
                  <li key={r.id} style={{ fontSize: '0.8125rem' }}>
                    <Badge status={r.analysis ? (r.analysis.situacao === 'DEFERIDA' ? 'AUTHORIZED' : r.analysis.situacao === 'INDEFERIDA' ? 'REJECTED' : 'PROCESSING') : (STATUS[r.status]?.status || 'UNKNOWN')}
                      customLabel={r.analysis ? `Análise fiscal: ${r.analysis.label}` : (STATUS[r.status]?.label || r.status)} />{' '}
                    {r.kind === 'CANCEL' ? 'Cancelamento (101101)' : 'Análise fiscal (101103)'} — {new Date(r.requested_at).toLocaleString('pt-BR')}
                    {r.simulated && ' — SIMULADO'}
                    <div style={{ color: 'var(--color-text-subtle)' }}>Motivo {r.reason_code} ({r.reason_label}): {r.reason_text}</div>
                    {r.last_error_message && (
                      <div style={{ color: r.status === 'REJECTED' ? 'var(--color-danger, #B42318)' : 'var(--color-text-subtle)' }}>
                        {r.last_error_code ? `${r.last_error_code}: ` : ''}{r.last_error_message}
                      </div>
                    )}
                    {r.analysis?.situacao === 'PENDENTE' && (
                      <div>A solicitação foi registrada; a nota continua válida até a decisão do município (deferimento
                        cancela a nota; indeferimento a mantém). A plataforma consulta a decisão periodicamente
                        {r.analysis.consultado_em ? ` (última consulta: ${new Date(r.analysis.consultado_em).toLocaleString('pt-BR')})` : ''}.</div>
                    )}
                    {r.guidance && <div>{r.guidance}</div>}
                    {r.suggest_analysis && !r.guidance && (
                      <div>O município recusou o cancelamento direto: use “Solicitar análise fiscal”.</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {info.events.length > 0 && (
            <div>
              <h4 style={{ fontSize: '0.8125rem', margin: '0.25rem 0' }}>Eventos oficiais da nota</h4>
              <ul style={{ paddingLeft: '1.1rem', margin: 0, fontSize: '0.8125rem' }}>
                {info.events.map((e, i) => (
                  <li key={`${e.event_type}-${i}`}>
                    {e.event_type} — {e.description || 'Evento'}{e.occurred_at ? ` — ${new Date(e.occurred_at).toLocaleString('pt-BR')}` : ''}
                    {e.simulated ? ' (SIMULADO)' : ''}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {dialog && info && (
        <Suspense fallback={null}>
          <CancellationDialog info={info} initialKind={dialog} onClose={() => setDialog(null)} onRequested={onRequested} />
        </Suspense>
      )}
    </section>
  );
};
