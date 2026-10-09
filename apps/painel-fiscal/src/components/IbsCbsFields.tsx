import React, { useEffect, useState } from 'react';
import type { IbsCbsPreview, TaxClassification } from '@ctrl-fiscal/api-client';
import { Alert, Badge, Input, Select } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { IbsCbsGruposFields, IbsCbsGroupsState } from './IbsCbsGruposFields.js';

export interface IbsCbsValue { operation_indicator: string; cst: string; class_code: string; personal_use: '' | '0' | '1' }
export const EMPTY_IBS_CBS: IbsCbsValue = { operation_indicator: '', cst: '', class_code: '', personal_use: '' };

/**
 * Campos do grupo IBS/CBS da DPS. NENHUM valor padrão: cIndOp e cClassTrib são definidos pela contabilidade para o
 * serviço; o cClassTrib é escolhido na tabela OFICIAL vigente na data do fato gerador e o CST é derivado dele
 * (3 primeiros dígitos — regra E0959). A DPS não leva alíquota nem valor: a Sefin calcula.
 */
export const IbsCbsFields: React.FC<{
  serviceDate: string;
  value: IbsCbsValue;
  onChange: (v: IbsCbsValue) => void;
  errors?: Record<string, string | undefined>;
  /** Grupos condicionais (gTribRegular, gDif, imovel, dest). Sem estas props, só os campos básicos são exibidos. */
  groups?: IbsCbsGroupsState;
  onGroupsChange?: (g: IbsCbsGroupsState) => void;
  /** cTribNac do serviço: decide se o grupo imóvel se aplica (E0928/E0931/E0932). */
  nationalCode?: string;
}> = ({ serviceDate, value, onChange, errors = {}, groups, onGroupsChange, nationalCode = '' }) => {
  const { clients, currentCompany } = useFiscal();
  const [items, setItems] = useState<TaxClassification[]>([]);
  const [fonte, setFonte] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!currentCompany || !/^\d{4}-\d{2}-\d{2}$/.test(serviceDate || '')) { setItems([]); return; }
    clients.fiscal.getTaxClassifications(currentCompany.id, serviceDate)
      .then((p) => { setItems(p.items); setFonte(`${p.fonte} (publicação ${p.publicacao})`); setLoadError(null); })
      .catch((err) => { logSafe('cclasstrib', err); setLoadError('Não foi possível carregar a tabela oficial de classificação.'); });
  }, [clients.fiscal, currentCompany, serviceDate]);

  const selected = items.find((c) => c.codigo === value.class_code);
  return (
    <div className="pf-stack">
      <div className="pf-form-grid">
        <Input label="Indicador da operação (cIndOp) *" value={value.operation_indicator} inputMode="numeric" maxLength={6}
          onChange={(e) => onChange({ ...value, operation_indicator: e.target.value.replace(/\D/g, '') })}
          error={errors['tax_settings.ibs_cbs.operation_indicator']}
          helperText="6 dígitos do Anexo C, definidos pela contabilidade para este serviço." />
        <Select label="Classificação tributária (cClassTrib) *" value={value.class_code}
          disabled={!items.length}
          onChange={(e) => {
            const c = items.find((x) => x.codigo === e.target.value);
            onChange({ ...value, class_code: e.target.value, cst: c ? c.cst : '' });
          }}
          error={errors['tax_settings.ibs_cbs.class_code']}
          helperText={serviceDate ? (fonte ? `Tabela oficial: ${fonte}` : 'Carregando…') : 'Informe primeiro a data do fato gerador (dCompet).'}
          options={[{ value: '', label: 'Selecione (definido pela contabilidade)…' },
            ...items.map((c) => ({ value: c.codigo, label: `${c.codigo} — ${c.nome}${c.tributacao_integral ? '' : ' (redução/regime específico)'}` }))]} />
        <Input label="CST (derivado do cClassTrib)" value={value.cst} readOnly
          error={errors['tax_settings.ibs_cbs.cst']} helperText="3 primeiros dígitos do cClassTrib (regra E0959)." />
        <Select label="Destinado a uso/consumo pessoal (indFinal)" value={value.personal_use}
          onChange={(e) => onChange({ ...value, personal_use: e.target.value as IbsCbsValue['personal_use'] })}
          options={[{ value: '', label: 'Não informar' }, { value: '0', label: '0 — Não' }, { value: '1', label: '1 — Sim (art. 57)' }]} />
      </div>
      {loadError && <Alert variant="error">{loadError}</Alert>}
      {selected && !selected.tributacao_integral && (
        <Alert variant="warning">
          {selected.nome}: classificação com redução ({selected.reducao_ibs}% IBS / {selected.reducao_cbs}% CBS) ou regime
          específico. A alíquota-padrão não é aplicada na prévia; confirme com a contabilidade.
        </Alert>
      )}
      {groups && onGroupsChange ? (
        <IbsCbsGruposFields value={groups} onChange={onGroupsChange} classifications={items} selected={selected}
          operationIndicator={value.operation_indicator} nationalCode={nationalCode} errors={errors} />
      ) : selected?.exige_tributacao_regular && (
        <Alert variant="warning">Esta classificação exige o grupo de tributação regular (gTribRegular, E0964), informado no editor da NFS-e.</Alert>
      )}
    </div>
  );
};

const money = (v?: string | null) => (v == null ? '—' : `R$ ${Number(v).toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`);
const pct = (v?: string) => (v == null ? '—' : `${Number(v).toLocaleString('pt-BR', { maximumFractionDigits: 4 })}%`);

/** Prévia LOCAL do IBS/CBS (vigência pelo fato gerador). O valor oficial é calculado pela Sefin. */
export const IbsCbsPreviewCard: React.FC<{ draftId: string; version: number }> = ({ draftId, version }) => {
  const { clients, currentCompany } = useFiscal();
  const [p, setP] = useState<IbsCbsPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!currentCompany) return;
    clients.fiscal.getIbsCbsPreview(currentCompany.id, draftId)
      .then((r) => { setP(r); setError(null); })
      .catch((err) => { logSafe('ibscbs', err); setError('Prévia de IBS/CBS indisponível.'); });
  }, [clients.fiscal, currentCompany, draftId, version]);
  if (error) return <Alert variant="error">{error}</Alert>;
  if (!p) return null;
  const label = p.status === 'CALCULADO' ? 'Prévia calculada' : p.status === 'NAO_APLICAVEL_SIMPLES' ? 'Não se aplica (Simples)' : 'Pendente';
  return (
    <section aria-labelledby="ibscbs-prev" style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-md)', padding: '0.75rem' }}>
      <h3 id="ibscbs-prev" style={{ fontSize: '0.875rem', marginBottom: '0.5rem' }}>
        IBS/CBS — prévia {' '}<Badge status={p.status === 'CALCULADO' ? 'VALID' : 'UNKNOWN'} customLabel={label} />
      </h3>
      <dl className="pf-dl">
        <dt>Fato gerador (dCompet)</dt><dd>{p.fato_gerador || '—'}</dd>
        {p.aliquotas && (<>
          <dt>Alíquotas-padrão vigentes</dt>
          <dd>IBS estadual {pct(p.aliquotas.ibs_uf)} · IBS municipal {pct(p.aliquotas.ibs_mun)} · CBS {pct(p.aliquotas.cbs)}
            <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
              Vigência {p.aliquotas.vigencia.join(' a ')}. {p.aliquotas.fonte}</div></dd>
        </>)}
        {p.classificacao && (<><dt>Classificação</dt><dd>{p.classificacao.codigo} — {p.classificacao.nome}</dd></>)}
        {p.base && (<><dt>Base (vBC)</dt><dd>{money(p.base.vBC)}
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>{p.base.formula}: {money(p.base.vServ)} − {money(p.base.descIncond)} − ISS {money(p.base.vISSQN)} − PIS {money(p.base.vPIS)} − COFINS {money(p.base.vCOFINS)}</div></dd></>)}
        {p.valores && (<>
          <dt>IBS (UF + Município)</dt><dd>{money(p.valores.vIBSUF)} + {money(p.valores.vIBSMun)} = {money(p.valores.vIBSTot)}</dd>
          <dt>CBS</dt><dd>{money(p.valores.vCBS)}</dd>
          <dt>Valor líquido</dt><dd>{money(p.valores.vLiq_inalterado)} (IBS/CBS por fora; não reduzem o líquido)</dd>
        </>)}
      </dl>
      {p.pendencias.map((x) => <Alert key={x.code} variant="warning">{x.message}</Alert>)}
      <ul style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)', paddingLeft: '1rem', marginTop: '0.5rem' }}>
        {p.observacoes.map((o) => <li key={o}>{o}</li>)}
        <li>{p.calculado_por}</li>
      </ul>
    </section>
  );
};
