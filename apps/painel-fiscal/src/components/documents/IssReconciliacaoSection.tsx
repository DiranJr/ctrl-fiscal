import React from 'react';
import type { IssReconciliation, IssReconciliationLine } from '@ctrl-fiscal/api-client';
import { formatCurrencyPtBr } from '@ctrl-fiscal/api-client';
import { pct } from '../aliquotaIss.js';

const STATUS: Record<IssReconciliationLine['status'], string> = {
  MATCH: 'Confere',
  DIFFERENT: 'Diferente',
  ESTIMATE_MISSING: 'Sem estimativa',
  OFFICIAL_MISSING: 'Sem valor oficial',
};

const GERAL: Record<IssReconciliation['status'], string> = {
  MATCH: 'A estimativa do agendamento confere com os valores oficiais.',
  DIFFERENT: 'Há diferença entre a estimativa e os valores oficiais: valem os oficiais.',
  PARTIAL: 'Comparação parcial: algum valor não estava disponível na estimativa ou na NFS-e.',
};

const fmt = (l: IssReconciliationLine, v?: string | null) => {
  if (v === null || v === undefined || v === '') return '—';
  return l.key === 'iss_rate' ? pct(v) : formatCurrencyPtBr(v);
};

/** NFS-e emitida pela plataforma: estimativa congelada no agendamento × valores OFICIAIS devolvidos pela Sefin. */
export const IssReconciliacaoSection: React.FC<{ rec: IssReconciliation }> = ({ rec }) => (
  <section aria-labelledby="iss-rec-title"
    style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
    <h3 id="iss-rec-title" style={{ fontSize: '0.875rem', marginBottom: '0.25rem' }}>ISS e totais: estimado × oficial (Sefin)</h3>
    <p style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)', marginBottom: '0.5rem' }}>
      {GERAL[rec.status]} pAliq na DPS: {rec.dps_rate ? pct(rec.dps_rate) : 'não enviado'} · Alíquota de referência
      (interna): {rec.reference_rate ? pct(rec.reference_rate) : 'não informada'}. {rec.note}
    </p>
    <table className="ui-table" style={{ width: '100%', fontSize: '0.8125rem' }}>
      <thead>
        <tr><th>Item</th><th>Estimado</th><th>Oficial (Sefin)</th><th>Situação</th></tr>
      </thead>
      <tbody>
        {rec.lines.map((l) => (
          <tr key={l.key} data-status={l.status}>
            <td>{l.label}</td>
            <td className="ui-num">{fmt(l, l.estimated)}</td>
            <td className="ui-num"><strong>{fmt(l, l.official)}</strong></td>
            <td>
              {STATUS[l.status]}
              {l.status === 'DIFFERENT' && l.difference ? ` (${l.key === 'iss_rate' ? pct(l.difference) : formatCurrencyPtBr(l.difference)})` : ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  </section>
);
