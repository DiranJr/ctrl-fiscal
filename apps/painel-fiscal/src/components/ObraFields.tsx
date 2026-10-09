import React, { useId } from 'react';
import type { DraftService, ObraAddress } from '@ctrl-fiscal/api-client';
import { Button, Input, Select } from '@ctrl-fiscal/ui';
import { Notice } from './screen/Notice.js';

/* ------------------------------------------------------------------ endereço nacional (obra, imóvel, destinatário) */
export interface EnderecoState {
  municipality_code: string;
  postal_code: string;
  street: string;
  number: string;
  complement: string;
  district: string;
}
export const EMPTY_ENDERECO: EnderecoState = { municipality_code: '', postal_code: '', street: '', number: '', complement: '', district: '' };

const digits = (v: string | null | undefined) => (v || '').replace(/\D/g, '');

export const enderecoHasValue = (e: EnderecoState) => Object.values(e).some((v) => v.trim() !== '');

export function enderecoFrom(a?: Partial<ObraAddress> & { municipality_code?: string | null } | null): EnderecoState {
  return {
    municipality_code: a?.municipality_code || '',
    postal_code: a?.postal_code || '',
    street: a?.street || '',
    number: a?.number || '',
    complement: a?.complement || '',
    district: a?.district || '',
  };
}

export function enderecoToObraAddress(e: EnderecoState): ObraAddress {
  return {
    postal_code: digits(e.postal_code),
    street: e.street.trim(),
    number: e.number.trim(),
    complement: e.complement.trim() || null,
    district: e.district.trim(),
  };
}

/** Valida um endereço nacional; as chaves seguem o caminho do campo na API (prefixo + nome). */
export function validateEndereco(e: EnderecoState, prefix: string, withMunicipality = false): Record<string, string> {
  const out: Record<string, string> = {};
  if (withMunicipality && !/^\d{7}$/.test(digits(e.municipality_code))) out[`${prefix}.municipality_code`] = 'Informe o código IBGE do município (7 dígitos).';
  if (!/^\d{8}$/.test(digits(e.postal_code))) out[`${prefix}.postal_code`] = 'Informe o CEP com 8 dígitos.';
  if (!e.street.trim()) out[`${prefix}.street`] = 'Informe o logradouro.';
  else if (e.street.trim().length > 255) out[`${prefix}.street`] = 'Logradouro com até 255 caracteres.';
  if (!e.number.trim()) out[`${prefix}.number`] = 'Informe o número (ou "S/N").';
  else if (e.number.trim().length > 60) out[`${prefix}.number`] = 'Número com até 60 caracteres.';
  if (e.complement.trim().length > 156) out[`${prefix}.complement`] = 'Complemento com até 156 caracteres.';
  if (!e.district.trim()) out[`${prefix}.district`] = 'Informe o bairro.';
  else if (e.district.trim().length > 60) out[`${prefix}.district`] = 'Bairro com até 60 caracteres.';
  return out;
}

export const EnderecoFields: React.FC<{
  value: EnderecoState;
  onChange: (v: EnderecoState) => void;
  /** caminho do campo na API para localizar os erros (ex.: "service.obra_address") */
  errorPrefix: string;
  errors?: Record<string, string | undefined>;
  withMunicipality?: boolean;
  /** complemento do rótulo, ex.: "da obra" */
  suffix?: string;
}> = ({ value, onChange, errorPrefix, errors = {}, withMunicipality = false, suffix = '' }) => {
  const set = (k: keyof EnderecoState, v: string) => onChange({ ...value, [k]: v });
  const err = (k: string) => errors[`${errorPrefix}.${k}`];
  const sfx = suffix ? ` ${suffix}` : '';
  return (
    <div className="pf-form-grid">
      {withMunicipality && (
        <Input label={`Município${sfx} (IBGE) *`} value={value.municipality_code} inputMode="numeric" maxLength={7}
          onChange={(e) => set('municipality_code', e.target.value.replace(/\D/g, ''))} error={err('municipality_code')}
          helperText="Código IBGE com 7 dígitos." />
      )}
      <Input label={`CEP${sfx} *`} value={value.postal_code} inputMode="numeric" maxLength={9}
        onChange={(e) => set('postal_code', e.target.value)} error={err('postal_code')} helperText="8 dígitos." />
      <Input label={`Logradouro${sfx} *`} value={value.street} maxLength={255}
        onChange={(e) => set('street', e.target.value)} error={err('street')} helperText="Rua, avenida etc." />
      <Input label={`Número${sfx} *`} value={value.number} maxLength={60}
        onChange={(e) => set('number', e.target.value)} error={err('number')} helperText='Use "S/N" quando não houver.' />
      <Input label={`Complemento${sfx}`} value={value.complement} maxLength={156}
        onChange={(e) => set('complement', e.target.value)} error={err('complement')} helperText="Opcional." />
      <Input label={`Bairro${sfx} *`} value={value.district} maxLength={60}
        onChange={(e) => set('district', e.target.value)} error={err('district')} helperText="Até 60 caracteres." />
    </div>
  );
};

/* ------------------------------------------------------------------ grupo serv/obra */
export type ObraMode = 'none' | 'code' | 'cib' | 'address';
export interface ObraState {
  mode: ObraMode;
  code: string;
  cib: string;
  address: EnderecoState;
  property_registration: string;
}
export const EMPTY_OBRA: ObraState = { mode: 'none', code: '', cib: '', address: EMPTY_ENDERECO, property_registration: '' };

/** Códigos em que a tela oferece o grupo obra: subitens 07.xx e os casos indicados pela especificação. */
const OBRA_EXTRA = ['141403', '141404', '990101'];
export const isObraRelevant = (nationalCode: string) => {
  const c = digits(nationalCode);
  return c.startsWith('07') || OBRA_EXTRA.includes(c);
};
export const obraHasValue = (s: ObraState) =>
  s.mode !== 'none' || !!s.code.trim() || !!s.cib.trim() || !!s.property_registration.trim() || enderecoHasValue(s.address);
export const shouldShowObra = (nationalCode: string, s: ObraState) => isObraRelevant(nationalCode) || obraHasValue(s);

export function obraFromService(s?: DraftService | null): ObraState {
  const mode: ObraMode = s?.obra_code ? 'code' : s?.obra_cib ? 'cib' : s?.obra_address ? 'address' : 'none';
  return {
    mode,
    code: s?.obra_code || '',
    cib: s?.obra_cib || '',
    address: enderecoFrom(s?.obra_address),
    property_registration: s?.obra_property_registration || '',
  };
}

type ObraPayload = Pick<DraftService, 'obra_code' | 'obra_cib' | 'obra_address' | 'obra_property_registration'>;

/** Só a identificação escolhida é enviada (escolha do XSD). `patch`: campos vazios vão como null (remoção). */
export function obraToService(s: ObraState, patch: boolean): ObraPayload {
  const out: ObraPayload = {};
  const put = <K extends keyof ObraPayload>(k: K, v: ObraPayload[K] | '' | undefined) => {
    if (v) out[k] = v as ObraPayload[K];
    else if (patch) out[k] = null;
  };
  put('obra_code', s.mode === 'code' ? s.code.trim() : null);
  put('obra_cib', s.mode === 'cib' ? s.cib.trim() : null);
  put('obra_address', s.mode === 'address' ? enderecoToObraAddress(s.address) : null);
  put('obra_property_registration', s.mode !== 'none' ? s.property_registration.trim() : null);
  return out;
}

export function validateObra(s: ObraState): Record<string, string> {
  const out: Record<string, string> = {};
  if (s.mode === 'code') {
    if (!s.code.trim()) out['service.obra_code'] = 'Informe o código da obra (cObra).';
    else if (s.code.trim().length > 30) out['service.obra_code'] = 'Código da obra com até 30 caracteres.';
  }
  if (s.mode === 'cib' && !/^[0-9A-Za-z]{8}$/.test(s.cib.trim())) {
    out['service.obra_cib'] = 'O cCIB tem exatamente 8 letras ou números.';
  }
  if (s.mode === 'address') Object.assign(out, validateEndereco(s.address, 'service.obra_address'));
  if (s.mode !== 'none' && s.property_registration.trim().length > 30) {
    out['service.obra_property_registration'] = 'Inscrição imobiliária com até 30 caracteres.';
  }
  return out;
}

/**
 * Grupo "obra" do serviço (serv/obra). Exibido quando o código nacional indica obra ou quando já há dados
 * (para permitir a remoção). Escolha do XSD: cObra OU cCIB OU endereço.
 */
export const ObraFields: React.FC<{
  value: ObraState;
  onChange: (v: ObraState) => void;
  nationalCode: string;
  errors?: Record<string, string | undefined>;
}> = ({ value, onChange, nationalCode, errors = {} }) => {
  const headingId = useId();
  if (!shouldShowObra(nationalCode, value)) return null;
  const relevant = isObraRelevant(nationalCode);
  const err = (k: string) => errors[k];
  return (
    <section className="pf-subpanel" aria-labelledby={headingId}>
      <div className="pf-subpanel-head">
        <h3 id={headingId} className="pf-subpanel-title">Obra de construção civil (obra)</h3>
        <p className="pf-help">
          Exigido nos subitens de construção civil (regra E0370) e não admitido nos demais serviços (E0372), exceto
          99.01.01. A pré-validação da DPS confirma a exigência para o código informado.
        </p>
      </div>
      {!relevant && (
        <Notice variant="warning" title="O código de serviço atual não indica obra"
          action={<Button size="sm" variant="outline" onClick={() => onChange(EMPTY_OBRA)}>Remover dados da obra</Button>}>
          O grupo obra não é admitido para este subitem (E0372). Remova os dados se eles não se aplicam.
        </Notice>
      )}
      {err('service.obra') && <Notice variant="error">{err('service.obra')}</Notice>}
      <div className="pf-form-grid">
        <Select label="Identificação da obra" value={value.mode}
          onChange={(e) => onChange({ ...value, mode: e.target.value as ObraMode })}
          helperText="Escolha uma só forma: código, cCIB ou endereço (escolha do leiaute)."
          error={err('service') /* OBRA_CHOICE (validação de modelo) */}
          options={[
            { value: 'none', label: 'Não informar' },
            { value: 'code', label: 'Código da obra — CNO/CEI (cObra)' },
            { value: 'cib', label: 'Cadastro Imobiliário Brasileiro (cCIB)' },
            { value: 'address', label: 'Endereço da obra (end)' },
          ]} />
        {value.mode === 'code' && (
          <Input label="Código da obra (cObra) *" value={value.code} maxLength={30}
            onChange={(e) => onChange({ ...value, code: e.target.value })} error={err('service.obra_code')}
            helperText="Inscrição no CNO/CEI ou código da obra, até 30 caracteres." />
        )}
        {value.mode === 'cib' && (
          <Input label="Código CIB da obra (cCIB) *" value={value.cib} maxLength={8}
            onChange={(e) => onChange({ ...value, cib: e.target.value.replace(/[^0-9A-Za-z]/g, '') })} error={err('service.obra_cib')}
            helperText="Exatamente 8 letras ou números." />
        )}
        {value.mode !== 'none' && (
          <Input label="Inscrição imobiliária fiscal (inscImobFisc)" value={value.property_registration} maxLength={30}
            onChange={(e) => onChange({ ...value, property_registration: e.target.value })}
            error={err('service.obra_property_registration')}
            helperText="Opcional, até 30 caracteres. Só acompanha uma identificação da obra." />
        )}
      </div>
      {value.mode === 'address' && (
        <EnderecoFields value={value.address} onChange={(a) => onChange({ ...value, address: a })}
          errorPrefix="service.obra_address" errors={errors} suffix="da obra" />
      )}
      {(err('service.obra_code') && value.mode === 'none') && <Notice variant="error">{err('service.obra_code')}</Notice>}
    </section>
  );
};
