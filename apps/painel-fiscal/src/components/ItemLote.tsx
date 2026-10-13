import React from 'react';
import { Badge } from '@ctrl-fiscal/ui';
import { BatchItem, formatCurrencyPtBr, getBatchItemStatusBadgeConfig } from '@ctrl-fiscal/api-client';

/** Um item do lote, como o servidor o devolve (somente leitura: nada aqui altera o item). */
const ORIGEM: Record<string, string> = {
  LOCAL: 'Validação local antes do envio',
  OFFICIAL: 'Rejeição oficial da Sefin',
  PROCESSING: 'Processamento / conector',
};
const SITUACAO_RASCUNHO: Record<string, string> = {
  DRAFT: 'rascunho', SCHEDULED: 'agendado', PROCESSING: 'em processamento', AUTHORIZED: 'autorizado',
  REJECTED: 'rejeitado', AWAITING_CONFIRMATION: 'aguardando confirmação', WITHDRAWN: 'retirado',
};
const dataHora = (v?: string | null, tz?: string) =>
  v ? new Intl.DateTimeFormat('pt-BR', { timeZone: tz, dateStyle: 'short', timeStyle: 'medium' }).format(new Date(v)) : null;

export const ItemLote: React.FC<{ item: BatchItem; timezone?: string; companyId?: string | null }> = ({ item: i, timezone, companyId }) => {
  const cfg = getBatchItemStatusBadgeConfig(i.status);
  const transmitido = i.transmitted === true ? 'Sim' : i.transmitted === false ? 'Não'
    : 'A confirmar pela reconciliação oficial (consulta da DPS)';
  const datas: [string, string | null][] = [
    ['Agendado para', dataHora(i.scheduled_at, timezone)],
    ['Início do processamento', dataHora(i.started_at, timezone)],
    ['Conclusão', dataHora(i.finished_at, timezone)],
    ['Próxima tentativa', dataHora(i.next_attempt_at, timezone)],
    ['Última atualização', dataHora(i.updated_at, timezone)],
  ];
  const refs: [string, React.ReactNode][] = [
    ['Tentativas', String(i.attempts ?? 0)],
    ['Id da DPS', i.dps_id ? <code>{i.dps_id}</code> : null],
    ['Série / número da DPS', i.dps_series && i.dps_number != null ? `${i.dps_series} / ${i.dps_number}` : null],
    ['Submissão', i.submission_id ? <code>{i.submission_id}</code> : null],
    ['NFS-e', i.result?.number ? `nº ${i.result.number}${i.result.access_key ? ` · chave ${i.result.access_key}` : ''}` : null],
  ];
  return (
    <li className="pf-lote-item" data-stage={i.stage || undefined}>
      <div className="pf-lote-item-cab">
        <div>
          <strong>{i.customer_name || 'Tomador não identificado no snapshot'}</strong>
          {i.customer_document ? <span className="pf-help"> · {i.customer_document}</span> : null}
          <div className="pf-help">
            Competência {i.competence || '—'} · valor {i.amount ? formatCurrencyPtBr(i.amount) : '—'}
            {i.net_amount ? ` · líquido ${formatCurrencyPtBr(i.net_amount)}` : ''}
          </div>
        </div>
        <Badge status={cfg.status} customLabel={cfg.label} />
      </div>
      <p className="pf-lote-etapa"><strong>Etapa:</strong> {i.stage_label || i.status} · <strong>Transmitido:</strong> {transmitido}</p>
      {(i.errors || []).length > 0 && (
        <ul className="pf-lote-erros" aria-label="Códigos e mensagens">
          {(i.errors || []).map((e, k) => (
            <li key={k} data-source={e.source}>
              <span className="pf-pend-tag">{ORIGEM[e.source] || e.source}</span>{' '}
              <code>{e.code}</code>{e.message ? <> — {e.message}</> : null}
            </li>
          ))}
        </ul>
      )}
      {i.guidance && <p className="pf-pend-acao"><strong>O que fazer:</strong> {i.guidance}</p>}
      <details className="pf-pend-tecnico">
        <summary>Datas, referências e rascunho de origem</summary>
        <dl className="pf-dl">
          {datas.filter(([, v]) => v).map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}
          {refs.filter(([, v]) => v).map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}
          <dt>Rascunho de origem</dt>
          <dd>
            <code>{i.draft_id.slice(0, 8)}</code> · versão agendada v{i.draft_version}
            {i.draft
              ? ` · hoje v${i.draft.current_version}, ${SITUACAO_RASCUNHO[i.draft.status] || i.draft.status}`
                + `${i.draft.deleted ? ' (excluído)' : ''}${i.draft.linked ? '' : ' (não está mais vinculado a este item)'}`
              : ' · não encontrado neste workspace'}
          </dd>
        </dl>
        {i.document_id && companyId && (
          <a className="pf-pend-link" href={`/fiscal/?company_id=${encodeURIComponent(companyId)}&view=emitidas`}
            target="_blank" rel="noopener noreferrer">Abrir Notas emitidas (nova aba) ↗</a>
        )}
      </details>
    </li>
  );
};
