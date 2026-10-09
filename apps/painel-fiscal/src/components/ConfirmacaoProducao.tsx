import React from 'react';
import { Alert } from '@ctrl-fiscal/ui';
import { formatCurrencyPtBr } from '@ctrl-fiscal/api-client';

export interface DocumentoConfirmado { id: string; version: number; amount: string; descricao: string }

/** Soma exata em centavos (sem ponto flutuante) dos valores "1234.56". */
export function totalCentavos(docs: DocumentoConfirmado[]): string {
  const c = docs.reduce((s, d) => s + BigInt(d.amount.replace('.', '')), 0n);
  const t = c.toString().padStart(3, '0');
  return `${t.slice(0, -2)}.${t.slice(-2)}`;
}

/** Corpo da confirmação conferido pelo servidor: empresa, ambiente, documentos (versão revisada e valor), quantidade
 *  e total. Qualquer edição posterior muda a versão e invalida a confirmação. */
export function montarConfirmacaoProducao(companyCnpj: string, docs: DocumentoConfirmado[]) {
  return {
    company_cnpj: companyCnpj.replace(/[^0-9A-Za-z]/g, '').toUpperCase(),
    environment: 'production' as const,
    count: docs.length,
    total_amount: totalCentavos(docs),
    documents: docs.map((d) => ({ id: d.id, version: d.version, amount: d.amount })),
  };
}

/** Confirmação individual exigida para emitir em PRODUÇÃO (validade fiscal real). */
export const ConfirmacaoProducao: React.FC<{
  empresa: { name: string; cnpj: string };
  docs: DocumentoConfirmado[];
  checked: boolean;
  onChange: (v: boolean) => void;
  /** Nota ainda será gravada ao agendar: a versão é a que for salva com estes dados. */
  versaoAoGravar?: boolean;
}> = ({ empresa, docs, checked, onChange, versaoAoGravar }) => (
  <section className="pf-subpanel" aria-label="Confirmação de emissão em produção">
    <Alert variant="warning" title="Produção: emissão com validade fiscal">
      As NFS-e serão assinadas com o certificado da empresa e enviadas à Sefin Nacional. Confira empresa, documentos e
      valores: a confirmação vale só para estas versões; qualquer alteração exige nova revisão.
    </Alert>
    <dl className="pf-dl">
      <dt>Empresa</dt><dd>{empresa.name} — CNPJ {empresa.cnpj}</dd>
      <dt>Ambiente</dt><dd><strong>Produção</strong></dd>
      <dt>Quantidade</dt><dd>{docs.length} nota(s)</dd>
      <dt>Valor total dos serviços</dt><dd className="ui-num" style={{ textAlign: 'left' }}>{formatCurrencyPtBr(totalCentavos(docs))}</dd>
    </dl>
    <ul className="pf-bullets" aria-label="Documentos confirmados">
      {docs.map((d) => (
        <li key={d.id}>{d.descricao} — {formatCurrencyPtBr(d.amount)} <span className="pf-help">({versaoAoGravar ? 'versão gravada com estes dados ao agendar' : `versão ${d.version}`})</span></li>
      ))}
    </ul>
    <label className="pf-confirm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>Confirmo a emissão destas {docs.length} NFS-e em PRODUÇÃO, com validade fiscal.</span>
    </label>
  </section>
);
