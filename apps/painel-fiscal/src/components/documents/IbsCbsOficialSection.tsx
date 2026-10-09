import React from 'react';
import type { IbsCbsOfficial } from '@ctrl-fiscal/api-client';
import { formatCurrencyPtBr } from '@ctrl-fiscal/api-client';

/** Campos principais de infNFSe/IBSCBS (TCRTCIBSCBS), na ordem do leiaute. Os demais aparecem em "Outros campos". */
const PRINCIPAIS: Array<[string, string, 'money' | 'pct' | 'text']> = [
  ['cLocalidadeIncid', 'Localidade de incidência (IBGE)', 'text'],
  ['xLocalidadeIncid', 'Localidade de incidência', 'text'],
  ['valores/vBC', 'Base de cálculo (vBC)', 'money'],
  ['valores/uf/pIBSUF', 'Alíquota IBS estadual', 'pct'],
  ['valores/uf/pAliqEfetUF', 'Alíquota efetiva IBS estadual', 'pct'],
  ['valores/mun/pIBSMun', 'Alíquota IBS municipal', 'pct'],
  ['valores/mun/pAliqEfetMun', 'Alíquota efetiva IBS municipal', 'pct'],
  ['valores/fed/pCBS', 'Alíquota CBS', 'pct'],
  ['valores/fed/pAliqEfetCBS', 'Alíquota efetiva CBS', 'pct'],
  ['totCIBS/gIBS/gIBSUFTot/vIBSUF', 'IBS estadual (vIBSUF)', 'money'],
  ['totCIBS/gIBS/gIBSMunTot/vIBSMun', 'IBS municipal (vIBSMun)', 'money'],
  ['totCIBS/gIBS/vIBSTot', 'IBS total (vIBSTot)', 'money'],
  ['totCIBS/gCBS/vCBS', 'CBS (vCBS)', 'money'],
  ['totCIBS/vTotNF', 'Total da NFS-e com IBS/CBS (vTotNF)', 'money'],
];

const CODIGOS: Array<[string, string]> = [
  ['cIndOp', 'cIndOp'], ['CST', 'CST'], ['cClassTrib', 'cClassTrib'], ['indFinal', 'indFinal'], ['indDest', 'indDest'],
];

/** IBS/CBS CALCULADOS pela Sefin e autorizados nesta NFS-e — texto exato do XML, separado da prévia local. */
export const IbsCbsOficialSection: React.FC<{ of: IbsCbsOfficial }> = ({ of }) => {
  const v = of.valores || {};
  const fmt = (raw: string, kind: 'money' | 'pct' | 'text') =>
    kind === 'money' ? formatCurrencyPtBr(raw) : kind === 'pct' ? `${raw.replace('.', ',')}%` : raw;
  const usados = new Set(PRINCIPAIS.map(([k]) => k));
  const outros = Object.entries(v).filter(([k]) => !usados.has(k));
  return (
    <section aria-labelledby="ibscbs-oficial-title"
      style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
      <h3 id="ibscbs-oficial-title" style={{ fontSize: '0.875rem', marginBottom: '0.25rem' }}>IBS/CBS da NFS-e (calculados pela Sefin)</h3>
      <p style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)', marginBottom: '0.5rem' }}>
        Valores oficiais do grupo {of.fonte}, exatamente como constam no XML autorizado. Não se confundem com a prévia
        local do rascunho e não alteram o valor líquido.
      </p>
      <dl className="pf-dl">
        {PRINCIPAIS.filter(([k]) => v[k] !== undefined).map(([k, label, kind]) => (
          <React.Fragment key={k}>
            <dt>{label}</dt>
            <dd className={kind === 'text' ? undefined : 'ui-num'} style={{ textAlign: 'left' }}>{fmt(v[k], kind)}</dd>
          </React.Fragment>
        ))}
        {CODIGOS.filter(([k]) => of.dps?.[k]).map(([k, label]) => (
          <React.Fragment key={`dps-${k}`}>
            <dt>{label} (informado na DPS)</dt>
            <dd className="ui-mono">{of.dps[k]}</dd>
          </React.Fragment>
        ))}
      </dl>
      {outros.length > 0 && (
        <details style={{ marginTop: '0.5rem', fontSize: '0.75rem' }}>
          <summary>Outros campos do grupo oficial ({outros.length})</summary>
          <dl className="pf-dl">
            {outros.map(([k, val]) => (
              <React.Fragment key={k}><dt className="ui-mono">{k}</dt><dd className="ui-mono">{val}</dd></React.Fragment>
            ))}
          </dl>
        </details>
      )}
    </section>
  );
};
