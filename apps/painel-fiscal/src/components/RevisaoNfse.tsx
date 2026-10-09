import React, { useId } from 'react';
import {
  DraftService,
  TaxSettings,
  IbsCbs,
  formatCurrencyPtBr,
  ISS_RETENTION_OPTIONS,
  TP_RET_PIS_COFINS_OPTIONS,
  PIS_COFINS_CST_OPTIONS,
} from '@ctrl-fiscal/api-client';

export interface RevisaoNfseProps {
  prestador: { name?: string | null; cnpj?: string | null };
  tomador: { name?: string | null; document?: string | null };
  competence?: string | null;
  serviceDate?: string | null;
  service: DraftService;
  amount: string;
  unconditionalDiscount?: string | null;
  conditionalDiscount?: string | null;
  deductions?: string | null;
  taxSettings?: TaxSettings | null;
  /** Resumo canônico já calculado (total retido e valor líquido). */
  retention?: { total_withheld?: string | null; net_amount?: string | null } | null;
}

const ISSQN_TAX: Record<string, string> = {
  TAXABLE: '1 — Operação tributável', IMMUNE: '2 — Imunidade', EXPORT: '3 — Exportação de serviço', NOT_INCIDENT: '4 — Não incidência',
};
const APPROX: Record<string, string> = {
  NOT_INFORMED: 'Não informado', VALUES: 'Valores em R$', PERCENT: 'Percentual (%)', SIMPLES_PERCENT: 'Percentual do Simples Nacional (%)',
};
const DOC_TYPE: Record<string, string> = { CNPJ: 'CNPJ', CPF: 'CPF', NIF: 'NIF', NO_NIF: 'Sem NIF' };

const optLabel = (opts: { value: string; label: string }[], v?: string | null) => (v ? opts.find((o) => o.value === v)?.label || v : null);
const money = (v?: string | null) => (v == null || v === '' ? null : formatCurrencyPtBr(v));
const NI = 'Não informado';

const Row: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
  <>
    <dt>{k}</dt>
    <dd>{v === null || v === undefined || v === '' ? <span className="pf-muted">{NI}</span> : v}</dd>
  </>
);

const Group: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
  const id = useId();
  return (
    <section className="pf-subpanel" aria-labelledby={id}>
      <h3 id={id} className="pf-subpanel-title">{title}</h3>
      <dl className="pf-dl">{children}</dl>
    </section>
  );
};

const obraText = (s: DraftService): string | null => {
  const ident = s.obra_code
    ? `Código da obra (cObra): ${s.obra_code}`
    : s.obra_cib
      ? `cCIB: ${s.obra_cib}`
      : s.obra_address
        ? `Endereço: ${s.obra_address.street}, ${s.obra_address.number}${s.obra_address.complement ? ` (${s.obra_address.complement})` : ''} — ${s.obra_address.district}, CEP ${s.obra_address.postal_code}`
        : null;
  if (!ident) return null;
  return s.obra_property_registration ? `${ident} · Inscrição imobiliária: ${s.obra_property_registration}` : ident;
};

const ibsGroups = (ibs: IbsCbs): React.ReactNode[] => {
  const out: React.ReactNode[] = [];
  if (ibs.regular) out.push(<Row key="reg" k="Tributação regular" v={`cClassTribReg ${ibs.regular.class_code} · situação ${ibs.regular.cst}`} />);
  if (ibs.deferral) out.push(<Row key="dif" k="Diferimento" v={`IBS UF ${ibs.deferral.uf}% · IBS Mun. ${ibs.deferral.mun}% · CBS ${ibs.deferral.cbs}%`} />);
  if (ibs.presumed_credit) out.push(<Row key="cp" k="Crédito presumido (cCredPres)" v={ibs.presumed_credit} />);
  if (ibs.real_estate) {
    const re = ibs.real_estate;
    const ident = re.cib ? `cCIB ${re.cib}` : re.address ? `${re.address.street}, ${re.address.number} — ${re.address.district}, CEP ${re.address.postal_code}` : '';
    out.push(<Row key="im" k="Imóvel da operação" v={`${ident}${re.registration ? ` · Inscrição ${re.registration}` : ''}`} />);
  }
  if (ibs.recipient) {
    const r = ibs.recipient;
    const doc = r.document_type === 'NO_NIF' ? `Sem NIF (motivo ${r.no_nif_reason ?? '—'})` : `${DOC_TYPE[r.document_type]} ${r.document ?? ''}`;
    out.push(<Row key="dest" k="Destinatário (≠ tomador)" v={`${r.name} · ${doc}${r.email ? ` · ${r.email}` : ''}`} />);
  } else {
    out.push(<Row key="dest" k="Destinatário" v="O próprio tomador" />);
  }
  return out;
};

/** Revisão agrupada da NFS-e antes de agendar/transmitir: Prestador, Tomador, Serviço, Valores e Tributos. */
export const RevisaoNfse: React.FC<RevisaoNfseProps> = (p) => {
  const s = p.service;
  const t = p.taxSettings || {};
  const info = s.additional_info;
  const ibs = t.ibs_cbs;
  return (
    <div className="pf-review">
      <div className="pf-review-grid">
        <Group title="Prestador">
          <Row k="Empresa" v={p.prestador.name} />
          <Row k="CNPJ" v={p.prestador.cnpj} />
        </Group>
        <Group title="Tomador">
          <Row k="Nome" v={p.tomador.name} />
          <Row k="CNPJ/CPF" v={p.tomador.document} />
          <Row k="Competência" v={p.competence} />
          <Row k="Data do fato gerador (dCompet)" v={p.serviceDate} />
        </Group>
      </div>

      <Group title="Serviço">
        <Row k="Código nacional (cTribNac)" v={s.national_code} />
        <Row k="Código municipal (cTribMun)" v={s.municipal_code} />
        <Row k="NBS" v={s.nbs_code} />
        <Row k="Local da prestação (IBGE)" v={s.location_code} />
        <Row k="Discriminação" v={s.description ? <span className="pf-review-text">{s.description}</span> : null} />
        <Row k="Obra" v={obraText(s) || 'Não se aplica'} />
        {info && (
          <>
            {info.technical_doc && <Row k="Documento técnico (ART/RRT/DRT)" v={info.technical_doc} />}
            {info.document_reference && <Row k="Referência a documento" v={info.document_reference} />}
            {info.order_number && <Row k="Pedido / ordem de compra" v={info.order_number} />}
            {info.order_items?.length ? <Row k="Itens do pedido" v={info.order_items.join(' · ')} /> : null}
            {info.notes && <Row k="Informações complementares" v={<span className="pf-review-text">{info.notes}</span>} />}
          </>
        )}
      </Group>

      <div className="pf-review-grid">
        <Group title="Valores">
          <Row k="Valor do serviço" v={money(p.amount)} />
          <Row k="Desconto incondicionado" v={money(p.unconditionalDiscount)} />
          <Row k="Desconto condicionado" v={money(p.conditionalDiscount)} />
          <Row k="Deduções" v={money(p.deductions)} />
          <Row k="Total retido" v={money(p.retention?.total_withheld)} />
          <Row k="Valor líquido" v={p.retention?.net_amount ? <strong>{money(p.retention.net_amount)}</strong> : null} />
        </Group>

        <Group title="Tributos">
          <Row k="ISSQN" v={t.issqn_taxation ? ISSQN_TAX[t.issqn_taxation] || t.issqn_taxation : null} />
          <Row k="Retenção do ISS" v={optLabel(ISS_RETENTION_OPTIONS, t.iss_retention)} />
          <Row k="Alíquota do ISS" v={t.iss_rate ? `${t.iss_rate}%` : null} />
          <Row k="IRRF retido" v={money(t.irrf_withheld)} />
          <Row k="Contribuição previdenciária retida" v={money(t.inss_withheld)} />
          <Row k="Contribuições retidas (CSLL/PCC)" v={money(t.csll_withheld)} />
          <Row k="PIS/COFINS — situação" v={optLabel(PIS_COFINS_CST_OPTIONS, t.pis_cofins_cst)} />
          <Row k="PIS/COFINS — retenção" v={optLabel(TP_RET_PIS_COFINS_OPTIONS, t.pis_cofins_retention)} />
          <Row k="Tributos aproximados" v={APPROX[t.approx_tax?.mode || 'NOT_INFORMED']} />
          {ibs ? (
            <>
              <Row k="IBS/CBS — operação (cIndOp)" v={ibs.operation_indicator} />
              <Row k="IBS/CBS — classificação" v={`cClassTrib ${ibs.class_code} · situação ${ibs.cst}`} />
              <Row k="Uso/consumo pessoal" v={ibs.personal_use === '1' ? 'Sim' : ibs.personal_use === '0' ? 'Não' : null} />
              {ibsGroups(ibs)}
            </>
          ) : (
            <Row k="IBS/CBS" v="Grupo não informado" />
          )}
        </Group>
      </div>
    </div>
  );
};

/** Confirmação explícita exigida antes da ação final (agendar/transmitir). */
export const ConfirmacaoRevisao: React.FC<{ checked: boolean; onChange: (v: boolean) => void }> = ({ checked, onChange }) => (
  <label className="pf-confirm">
    <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    <span>Conferi os dados acima</span>
  </label>
);
