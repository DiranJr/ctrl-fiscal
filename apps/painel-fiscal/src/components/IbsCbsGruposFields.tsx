import React, { useId } from 'react';
import type { IbsCbs, IbsCbsRecipient, TaxClassification } from '@ctrl-fiscal/api-client';
import { Button, Input, Select } from '@ctrl-fiscal/ui';
import { Notice } from './screen/Notice.js';
import {
  EMPTY_ENDERECO, EnderecoFields, EnderecoState, enderecoFrom, enderecoHasValue, enderecoToObraAddress, validateEndereco,
} from './ObraFields.js';

/* ------------------------------------------------------------------ estado dos grupos condicionais do IBSCBS */
export type RealEstateMode = '' | 'cib' | 'address';
export type RecipientDocType = '' | 'CNPJ' | 'CPF' | 'NIF' | 'NO_NIF';
export interface IbsCbsGroupsState {
  regular: { class_code: string; cst: string };
  deferral: { uf: string; mun: string; cbs: string };
  real_estate: { mode: RealEstateMode; registration: string; cib: string; address: EnderecoState };
  recipient: {
    enabled: boolean;
    document_type: RecipientDocType;
    document: string;
    no_nif_reason: '' | '0' | '1' | '2';
    name: string;
    phone: string;
    email: string;
    with_address: boolean;
    address: EnderecoState;
  };
  /** cCredPres: sem campo de edição (nenhuma classificação vigente para NFS-e permite); só preservado/removido. */
  presumed_credit: string;
}

export const EMPTY_IBS_GROUPS: IbsCbsGroupsState = {
  regular: { class_code: '', cst: '' },
  deferral: { uf: '', mun: '', cbs: '' },
  real_estate: { mode: '', registration: '', cib: '', address: EMPTY_ENDERECO },
  recipient: {
    enabled: false, document_type: '', document: '', no_nif_reason: '', name: '', phone: '', email: '',
    with_address: false, address: EMPTY_ENDERECO,
  },
  presumed_credit: '',
};

/** cIndOp de operações com bem imóvel e subitens de obra em que o grupo imovel não se aplica (E0928/E0931/E0932). */
export const CINDOP_IMOVEL = ['020101', '020201', '020301'];
export const SUBITENS_OBRA_SEM_IMOVEL = ['070201', '070202', '070401', '070501', '070502', '070601', '070602', '070701', '070801', '071701', '071901'];

const digits = (v: string | null | undefined) => (v || '').replace(/\D/g, '');
const pctRe = /^(0|0\.\d{2}|[1-9]\d{0,2}(\.\d{2})?)$/;
const normPct = (v: string) => v.trim().replace(',', '.');

export const isRealEstateRelevant = (operationIndicator: string, nationalCode: string) =>
  CINDOP_IMOVEL.includes(digits(operationIndicator)) && !SUBITENS_OBRA_SEM_IMOVEL.includes(digits(nationalCode));

export const regularHasValue = (g: IbsCbsGroupsState) => !!g.regular.class_code;
export const deferralHasValue = (g: IbsCbsGroupsState) => !!(g.deferral.uf.trim() || g.deferral.mun.trim() || g.deferral.cbs.trim());
export const realEstateHasValue = (g: IbsCbsGroupsState) =>
  g.real_estate.mode !== '' || !!g.real_estate.registration.trim() || !!g.real_estate.cib.trim() || enderecoHasValue(g.real_estate.address);

export function groupsFromIbs(ibs?: IbsCbs | null): IbsCbsGroupsState {
  if (!ibs) return EMPTY_IBS_GROUPS;
  const re = ibs.real_estate;
  const r = ibs.recipient;
  return {
    regular: { class_code: ibs.regular?.class_code || '', cst: ibs.regular?.cst || '' },
    deferral: { uf: ibs.deferral?.uf || '', mun: ibs.deferral?.mun || '', cbs: ibs.deferral?.cbs || '' },
    real_estate: {
      mode: re?.cib ? 'cib' : re?.address ? 'address' : '',
      registration: re?.registration || '',
      cib: re?.cib || '',
      address: enderecoFrom(re?.address),
    },
    recipient: r ? {
      enabled: true,
      document_type: r.document_type,
      document: r.document || '',
      no_nif_reason: (r.no_nif_reason as '' | '0' | '1' | '2') || '',
      name: r.name || '',
      phone: r.phone || '',
      email: r.email || '',
      with_address: !!r.address,
      address: enderecoFrom(r.address),
    } : EMPTY_IBS_GROUPS.recipient,
    presumed_credit: ibs.presumed_credit || '',
  };
}

/** Acrescenta ao grupo IBSCBS somente os subgrupos preenchidos (grupos vazios são omitidos; nada de string vazia). */
export function applyGroups(base: IbsCbs, g: IbsCbsGroupsState): IbsCbs {
  const out: IbsCbs = { ...base };
  if (regularHasValue(g)) out.regular = { class_code: g.regular.class_code, cst: g.regular.class_code.slice(0, 3) };
  if (deferralHasValue(g)) {
    out.deferral = { uf: normPct(g.deferral.uf), mun: normPct(g.deferral.mun), cbs: normPct(g.deferral.cbs) };
  }
  if (g.presumed_credit) out.presumed_credit = g.presumed_credit;
  if (realEstateHasValue(g)) {
    const re = g.real_estate;
    out.real_estate = {
      ...(re.registration.trim() ? { registration: re.registration.trim() } : {}),
      ...(re.mode === 'cib' ? { cib: re.cib.trim() } : {}),
      ...(re.mode === 'address' ? { address: enderecoToObraAddress(re.address) } : {}),
    };
  }
  if (g.recipient.enabled) {
    const r = g.recipient;
    const type = (r.document_type || 'CNPJ') as IbsCbsRecipient['document_type'];
    const rec: IbsCbsRecipient = { document_type: type, name: r.name.trim() };
    if (type === 'NO_NIF') rec.no_nif_reason = (r.no_nif_reason || null) as IbsCbsRecipient['no_nif_reason'];
    else rec.document = type === 'NIF' ? r.document.trim() : digits(r.document);
    if (digits(r.phone)) rec.phone = digits(r.phone);
    if (r.email.trim()) rec.email = r.email.trim();
    if (r.with_address) {
      const a = r.address;
      rec.address = {
        municipality_code: digits(a.municipality_code), postal_code: digits(a.postal_code), street: a.street.trim(),
        number: a.number.trim(), complement: a.complement.trim() || null, district: a.district.trim(),
      };
    }
    out.recipient = rec;
  }
  return out;
}

/** Validação local de formato/completude dos grupos preenchidos. A exigência por classificação/operação é
 *  sinalizada na tela e confirmada pela pré-validação da DPS. */
export function validateGroups(g: IbsCbsGroupsState): Record<string, string> {
  const out: Record<string, string> = {};
  const p = 'tax_settings.ibs_cbs';
  if (deferralHasValue(g)) {
    (['uf', 'mun', 'cbs'] as const).forEach((k) => {
      if (!pctRe.test(normPct(g.deferral[k]))) out[`${p}.deferral.${k}`] = 'Percentual de 0 a 999 com 2 casas (ex.: 0, 0,50 ou 100,00).';
    });
  }
  if (realEstateHasValue(g)) {
    const re = g.real_estate;
    if (re.mode === '') out[`${p}.real_estate`] = 'Escolha a identificação do imóvel: cCIB ou endereço.';
    if (re.mode === 'cib' && !/^[0-9A-Za-z]{8}$/.test(re.cib.trim())) out[`${p}.real_estate.cib`] = 'O cCIB tem exatamente 8 letras ou números.';
    if (re.mode === 'address') Object.assign(out, validateEndereco(re.address, `${p}.real_estate.address`));
    if (re.registration.trim().length > 30) out[`${p}.real_estate.registration`] = 'Até 30 caracteres.';
  }
  if (g.recipient.enabled) {
    const r = g.recipient;
    const q = `${p}.recipient`;
    if (!r.document_type) out[`${q}.document_type`] = 'Escolha o tipo de documento do destinatário.';
    if (r.document_type === 'CNPJ' && digits(r.document).length !== 14) out[`${q}.document`] = 'CNPJ com 14 dígitos.';
    if (r.document_type === 'CPF' && digits(r.document).length !== 11) out[`${q}.document`] = 'CPF com 11 dígitos.';
    if (r.document_type === 'NIF' && !(r.document.trim().length >= 1 && r.document.trim().length <= 40)) out[`${q}.document`] = 'Informe o NIF (até 40 caracteres).';
    if (r.document_type === 'NO_NIF' && !r.no_nif_reason) out[`${q}.no_nif_reason`] = 'Informe o motivo da ausência de NIF.';
    if (!r.name.trim()) out[`${q}.name`] = 'Informe o nome do destinatário.';
    else if (r.name.trim().length > 150) out[`${q}.name`] = 'Até 150 caracteres.';
    if (r.phone.trim() && !/^\d{6,20}$/.test(digits(r.phone))) out[`${q}.phone`] = 'Telefone com 6 a 20 dígitos.';
    if (r.email.trim() && (r.email.trim().length > 80 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(r.email.trim()))) out[`${q}.email`] = 'E-mail inválido (até 80 caracteres).';
    if (r.with_address) Object.assign(out, validateEndereco(r.address, `${q}.address`, true));
  }
  return out;
}

/* ------------------------------------------------------------------ componente */
const Panel: React.FC<{ title: string; help: React.ReactNode; children: React.ReactNode }> = ({ title, help, children }) => {
  const id = useId();
  return (
    <section className="pf-subpanel" aria-labelledby={id}>
      <div className="pf-subpanel-head">
        <h4 id={id} className="pf-subpanel-title">{title}</h4>
        <p className="pf-help">{help}</p>
      </div>
      {children}
    </section>
  );
};

/**
 * Grupos condicionais do IBSCBS conforme a classificação (cClassTrib) e a operação (cIndOp):
 * gTribRegular, gDif, imovel e dest. Cada grupo só aparece quando se aplica ou quando já tem dados (para remoção).
 */
export const IbsCbsGruposFields: React.FC<{
  value: IbsCbsGroupsState;
  onChange: (v: IbsCbsGroupsState) => void;
  classifications: TaxClassification[];
  selected?: TaxClassification;
  operationIndicator: string;
  nationalCode: string;
  errors?: Record<string, string | undefined>;
}> = ({ value, onChange, classifications, selected, operationIndicator, nationalCode, errors = {} }) => {
  const p = 'tax_settings.ibs_cbs';
  const err = (k: string) => errors[`${p}.${k}`];
  const regularRequired = !!selected?.exige_tributacao_regular;
  const deferralAllowed = !!selected?.permite_diferimento;
  const realEstateRelevant = isRealEstateRelevant(operationIndicator, nationalCode);
  const r = value.recipient;
  const setR = (patch: Partial<IbsCbsGroupsState['recipient']>) => onChange({ ...value, recipient: { ...r, ...patch } });
  const re = value.real_estate;
  const setRe = (patch: Partial<IbsCbsGroupsState['real_estate']>) => onChange({ ...value, real_estate: { ...re, ...patch } });

  return (
    <div className="pf-form-stack">
      {(regularRequired || regularHasValue(value)) && (
        <Panel title="Tributação regular (gTribRegular)"
          help="Exigida quando a classificação escolhida pede a tributação que se aplicaria sem o tratamento diferenciado (regras E0964/E0965). A situação é derivada do cClassTribReg (E0970).">
          {!regularRequired && selected && (
            <Notice variant="warning" title="A classificação atual não exige este grupo (E0965)"
              action={<Button size="sm" variant="outline" onClick={() => onChange({ ...value, regular: EMPTY_IBS_GROUPS.regular })}>Remover grupo</Button>}>
              Remova a tributação regular se ela não se aplica.
            </Notice>
          )}
          <div className="pf-form-grid">
            <Select label="Classificação da tributação regular (cClassTribReg) *" value={value.regular.class_code}
              disabled={!classifications.length}
              onChange={(e) => onChange({ ...value, regular: { class_code: e.target.value, cst: e.target.value.slice(0, 3) } })}
              error={err('regular.class_code') || err('regular')}
              helperText="Mesma tabela oficial de classificação, definida pela contabilidade."
              options={[{ value: '', label: 'Selecione (definido pela contabilidade)…' },
                ...classifications.map((c) => ({ value: c.codigo, label: `${c.codigo} — ${c.nome}` }))]} />
            <Input label="Situação tributária regular (derivada)" value={value.regular.class_code.slice(0, 3)} readOnly
              error={err('regular.cst')} helperText="CSTReg: 3 primeiros dígitos do cClassTribReg (E0970)." />
          </div>
        </Panel>
      )}

      {(deferralAllowed || deferralHasValue(value)) && (
        <Panel title="Diferimento (gDif)"
          help="Somente quando a classificação permite diferimento (regras E0971/E0972). Informe os três percentuais.">
          {!deferralAllowed && selected && (
            <Notice variant="warning" title="A classificação atual não permite diferimento (E0972)"
              action={<Button size="sm" variant="outline" onClick={() => onChange({ ...value, deferral: EMPTY_IBS_GROUPS.deferral })}>Remover grupo</Button>}>
              Remova o diferimento se ele não se aplica.
            </Notice>
          )}
          <div className="pf-form-grid">
            {([['uf', 'Diferimento do IBS estadual (pDifUF, %) *'], ['mun', 'Diferimento do IBS municipal (pDifMun, %) *'],
              ['cbs', 'Diferimento da CBS (pDifCBS, %) *']] as const).map(([k, label]) => (
              <Input key={k} label={label} value={value.deferral[k]} inputMode="decimal" className="pf-money" maxLength={6}
                onChange={(e) => onChange({ ...value, deferral: { ...value.deferral, [k]: e.target.value } })}
                error={err(`deferral.${k}`)} helperText="0 a 999, com 2 casas (ex.: 0 ou 50,00)." />
            ))}
          </div>
        </Panel>
      )}

      {(value.presumed_credit || selected?.permite_credito_presumido) && (
        <Notice variant="warning" title="Crédito presumido (cCredPres)"
          action={value.presumed_credit ? (
            <Button size="sm" variant="outline" onClick={() => onChange({ ...value, presumed_credit: '' })}>Remover cCredPres</Button>
          ) : undefined}>
          {value.presumed_credit
            ? `Este rascunho tem cCredPres ${value.presumed_credit}. Nenhuma classificação vigente para NFS-e admite crédito presumido; remova-o se não se aplica.`
            : 'Esta tela não informa crédito presumido; nenhuma classificação vigente para NFS-e o admite. Consulte a contabilidade.'}
        </Notice>
      )}

      {(realEstateRelevant || realEstateHasValue(value)) && (
        <Panel title="Bem imóvel da operação (imovel)"
          help="Exigido quando a operação (cIndOp 020101, 020201 ou 020301) envolve um imóvel e o serviço não é subitem de obra (E0932); não admitido nos subitens de obra (E0931) nem nas demais operações (E0928). Não é o grupo “obra” do serviço.">
          {!realEstateRelevant && (
            <Notice variant="warning" title="A operação atual não admite o grupo imóvel (E0928/E0931)"
              action={<Button size="sm" variant="outline" onClick={() => onChange({ ...value, real_estate: EMPTY_IBS_GROUPS.real_estate })}>Remover grupo</Button>}>
              Remova os dados do imóvel se eles não se aplicam.
            </Notice>
          )}
          <div className="pf-form-grid">
            <Select label="Identificação do imóvel *" value={re.mode}
              onChange={(e) => setRe({ mode: e.target.value as RealEstateMode })}
              error={err('real_estate')} helperText="Escolha uma só forma: cCIB ou endereço."
              options={[{ value: '', label: 'Selecione…' }, { value: 'cib', label: 'Cadastro Imobiliário Brasileiro (cCIB)' },
                { value: 'address', label: 'Endereço do imóvel' }]} />
            {re.mode === 'cib' && (
              <Input label="Código CIB do imóvel (cCIB) *" value={re.cib} maxLength={8}
                onChange={(e) => setRe({ cib: e.target.value.replace(/[^0-9A-Za-z]/g, '') })}
                error={err('real_estate.cib')} helperText="Exatamente 8 letras ou números." />
            )}
            <Input label="Inscrição imobiliária fiscal do imóvel" value={re.registration} maxLength={30}
              onChange={(e) => setRe({ registration: e.target.value })}
              error={err('real_estate.registration')} helperText="Opcional (inscImobFisc), até 30 caracteres." />
          </div>
          {re.mode === 'address' && (
            <EnderecoFields value={re.address} onChange={(a) => setRe({ address: a })}
              errorPrefix={`${p}.real_estate.address`} errors={errors} suffix="do imóvel" />
          )}
        </Panel>
      )}

      <section className="pf-subpanel" aria-label="Destinatário da operação">
        <label className="pf-check">
          <input type="checkbox" checked={r.enabled} onChange={(e) => setR({ enabled: e.target.checked })} />
          <span>O destinatário é diferente do tomador</span>
        </label>
        <p className="pf-help">
          Marque só quando quem recebe o serviço não é o tomador (grupo dest, indDest=1). Sem marcação, o tomador é o destinatário.
        </p>
        {r.enabled && (
          <>
            <div className="pf-form-grid">
              <Select label="Tipo de documento do destinatário *" value={r.document_type}
                onChange={(e) => setR({ document_type: e.target.value as RecipientDocType, document: '', no_nif_reason: '' })}
                error={err('recipient.document_type') || err('recipient')}
                helperText="NIF e sem NIF: destinatário no exterior."
                options={[{ value: '', label: 'Selecione…' }, { value: 'CNPJ', label: 'CNPJ' }, { value: 'CPF', label: 'CPF' },
                  { value: 'NIF', label: 'NIF (exterior)' }, { value: 'NO_NIF', label: 'Sem NIF (cNaoNIF)' }]} />
              {r.document_type && r.document_type !== 'NO_NIF' && (
                <Input label={`${r.document_type} do destinatário *`} value={r.document}
                  inputMode={r.document_type === 'NIF' ? 'text' : 'numeric'} maxLength={r.document_type === 'NIF' ? 40 : 18}
                  onChange={(e) => setR({ document: e.target.value })} error={err('recipient.document')}
                  helperText={r.document_type === 'NIF' ? 'Até 40 caracteres.' : 'Somente números; o dígito verificador é conferido.'} />
              )}
              {r.document_type === 'NO_NIF' && (
                <Select label="Motivo da ausência de NIF (cNaoNIF) *" value={r.no_nif_reason}
                  onChange={(e) => setR({ no_nif_reason: e.target.value as '' | '0' | '1' | '2' })}
                  error={err('recipient.no_nif_reason')} helperText="Conforme a situação do destinatário."
                  options={[{ value: '', label: 'Selecione…' }, { value: '0', label: '0 — Não informado na origem' },
                    { value: '1', label: '1 — Dispensado do NIF' }, { value: '2', label: '2 — NIF não exigido' }]} />
              )}
              <Input label="Nome ou razão social do destinatário *" value={r.name} maxLength={150}
                onChange={(e) => setR({ name: e.target.value })} error={err('recipient.name')} helperText="Até 150 caracteres." />
              <Input label="Telefone do destinatário" value={r.phone} inputMode="tel" maxLength={24}
                onChange={(e) => setR({ phone: e.target.value })} error={err('recipient.phone')} helperText="Opcional, 6 a 20 dígitos." />
              <Input label="E-mail do destinatário" type="email" value={r.email} maxLength={80}
                onChange={(e) => setR({ email: e.target.value })} error={err('recipient.email')} helperText="Opcional." />
            </div>
            <label className="pf-check">
              <input type="checkbox" checked={r.with_address} onChange={(e) => setR({ with_address: e.target.checked })} />
              <span>Informar endereço do destinatário (opcional)</span>
            </label>
            {r.with_address && (
              <EnderecoFields value={r.address} onChange={(a) => setR({ address: a })} withMunicipality
                errorPrefix={`${p}.recipient.address`} errors={errors} suffix="do destinatário" />
            )}
          </>
        )}
      </section>
    </div>
  );
};
