import type { IssAliquotaDetails, TaxSettings, ValidationIssue } from '@ctrl-fiscal/api-client';

/**
 * Alíquota do ISS na tela: a de REFERÊNCIA (cálculo interno, nunca vai na DPS) é distinta do pAliq ENVIADO na DPS.
 * Se o pAliq é proibido/obrigatório vem da validação do servidor (Parametrização oficial: convênio do município de
 * incidência no ambiente — Anexo I, E0617/E0619); aqui só se apresenta esse resultado, sem presumir nada.
 */

const AMBIENTE: Record<string, string> = { production: 'Produção', restricted: 'Produção Restrita' };

export function pct(v?: string | null): string | null {
  if (v === null || v === undefined || String(v).trim() === '') return null;
  const n = Number(String(v).replace(',', '.'));
  if (Number.isNaN(n)) return null;
  return `${n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

/** Detalhes da Parametrização anexados às pendências de alíquota do ISS (a primeira que os traz). */
export function detalhesAliquotaIss(issues?: ValidationIssue[] | null): IssAliquotaDetails | null {
  for (const i of issues || []) {
    const d = i.details as IssAliquotaDetails | null | undefined;
    if (d && typeof d === 'object' && 'paliq_status' in d) return d;
  }
  return null;
}

function quando(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

/** Uma frase com o resultado oficial (ou a incerteza), para exibir junto aos campos e na revisão. */
export function textoSituacaoPaliq(d?: IssAliquotaDetails | null): string | null {
  if (!d) return null;
  const amb = AMBIENTE[d.environment || ''] || d.environment || '';
  const mun = d.municipio_nome || d.municipio || 'município de incidência não determinado';
  const consulta = d.consulted_at ? ` (Parametrização consultada em ${quando(d.consulted_at)})` : '';
  const conv = d.convenio === 'ACTIVE' ? 'convênio ATIVO' : d.convenio === 'INACTIVE' ? 'convênio NÃO ativo' : null;
  const municipal = d.municipal_rate
    ? ` Alíquota parametrizada pelo município${d.service_code ? ` para ${d.service_code}` : ''}: ${pct(d.municipal_rate)}.`
    : '';
  const regra = d.rule ? ` (${d.rule})` : '';
  switch (d.paliq_status) {
    case 'PROHIBITED':
      return `${mun}${conv ? ` — ${conv} em ${amb}` : ''}${consulta}: pAliq não pode ir na DPS${regra}; a alíquota é determinada pelo município.${municipal}`;
    case 'REQUIRED':
      return `${mun}${conv ? ` — ${conv} em ${amb}` : ''}${consulta}: pAliq obrigatório na DPS${regra}.${municipal}`;
    case 'OPTIONAL':
      return `${mun}${conv ? ` — ${conv} em ${amb}` : ''}${consulta}: pAliq opcional.${municipal}`;
    default:
      return d.possible_rules && d.possible_rules.length
        ? `Situação do convênio de ${mun} não confirmada${amb ? ` em ${amb}` : ''}: não se sabe se o pAliq é proibido ou obrigatório (${d.possible_rules.join('/')}). A regra é conferida na transmissão.`
        : null;
  }
}

export interface LinhaIss {
  k: string;
  v: string;
}

/** Linhas da revisão: referência interna, pAliq na DPS e a situação oficial — sempre separadas. */
export function linhasRevisaoIss(tax?: TaxSettings | null, d?: IssAliquotaDetails | null): LinhaIss[] {
  const t = tax || {};
  const ref = pct(t.iss_reference_rate);
  const paliq = pct(t.iss_rate);
  const out: LinhaIss[] = [
    { k: 'Alíquota de referência (cálculo interno)', v: ref ? `${ref} — usada só na estimativa; não vai na DPS` : 'Não informada' },
    {
      k: 'pAliq enviado na DPS',
      v: paliq
        ? `${paliq} — vai na DPS`
        : d?.paliq_status === 'PROHIBITED'
          ? 'Não enviado — a alíquota será determinada pelo município'
          : 'Não enviado — a Sefin aplica a alíquota parametrizada do município',
    },
  ];
  const sit = textoSituacaoPaliq(d);
  if (sit) out.push({ k: 'Situação oficial do pAliq', v: sit });
  return out;
}

/** Origem da alíquota usada na estimativa (texto curto para o resumo de valores). */
export function origemAliquota(source?: string | null): string {
  if (source === 'DPS_PALIQ') return 'pAliq enviado na DPS';
  if (source === 'REFERENCE') return 'referência interna — não vai na DPS';
  return 'nenhuma informada';
}
