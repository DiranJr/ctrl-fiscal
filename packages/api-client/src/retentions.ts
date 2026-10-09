import { TaxSettings, RetentionSummary } from './types.js';
import { formatCurrencyPtBr, parseCurrencyPtBrToDecimal } from './formatters.js';

export const ISS_RETENTION_OPTIONS = [
  { value: 'NOT_RETAINED', label: '1 - Não retido (Responsável: Prestador)', responsible: 'Prestador' },
  { value: 'RETAINED_BY_TOMADOR', label: '2 - Retido pelo Tomador (Responsável: Tomador)', responsible: 'Tomador' },
  { value: 'RETAINED_BY_INTERMEDIARY', label: '3 - Retido pelo Intermediário (Responsável: Intermediário)', responsible: 'Intermediário' },
];

export const TP_RET_PIS_COFINS_OPTIONS = [
  { value: '', label: 'Não informado (remover modalidade)' },
  { value: '0', label: '0 - PIS/COFINS/CSLL não retidos' },
  { value: '1', label: '1 - PIS/COFINS retidos' },
  { value: '2', label: '2 - PIS/COFINS não retidos' },
  { value: '3', label: '3 - PIS/COFINS/CSLL retidos (Total PCC)' },
  { value: '4', label: '4 - PIS/COFINS retidos, CSLL não retido' },
  { value: '5', label: '5 - PIS retido, COFINS/CSLL não retidos' },
  { value: '6', label: '6 - COFINS retido, PIS/CSLL não retidos' },
  { value: '7', label: '7 - PIS não retido, COFINS/CSLL retidos' },
  { value: '8', label: '8 - PIS/COFINS não retidos, CSLL retido' },
  { value: '9', label: '9 - COFINS não retido, PIS/CSLL retidos' },
];

export const PIS_COFINS_CST_OPTIONS = [
  { value: '', label: 'Não informado (remover grupo PIS/COFINS)' },
  { value: '01', label: '01 - Operação Tributável com Alíquota Básica' },
  { value: '02', label: '02 - Operação Tributável com Alíquota Diferenciada' },
  { value: '03', label: '03 - Operação Tributável por Unidade de Medida' },
  { value: '04', label: '04 - Operação Monofásica - Revenda a Alíquota Zero' },
  { value: '05', label: '05 - Operação com Substituição Tributária' },
  { value: '06', label: '06 - Operação Tributável a Alíquota Zero' },
  { value: '07', label: '07 - Operação Isenta da Contribuição' },
  { value: '08', label: '08 - Operação sem Incidência da Contribuição' },
  { value: '09', label: '09 - Operação com Suspensão da Contribuição' },
  { value: '49', label: '49 - Outras Operações de Saída' },
  { value: '99', label: '99 - Outras Operações' },
];

/**
 * Diferencia campo não informado (null, undefined ou vazio) de valor zero ("0.00" ou 0).
 */
export function formatOptionalCurrency(value: string | number | null | undefined, fallbackText = 'Não informado'): string {
  if (value === null || value === undefined || value === '') {
    return fallbackText;
  }
  const clean = typeof value === 'string' ? value.trim() : String(value);
  if (clean === '') {
    return fallbackText;
  }
  return formatCurrencyPtBr(clean);
}

/**
 * Diferencia alíquota não informada de percentual zero ("0.00" ou 0).
 */
export function formatOptionalPercent(value: string | number | null | undefined, fallbackText = 'Não informada'): string {
  if (value === null || value === undefined || value === '') {
    return fallbackText;
  }
  const clean = typeof value === 'string' ? value.trim() : String(value);
  if (clean === '') {
    return fallbackText;
  }
  const num = parseFloat(clean.replace(',', '.'));
  if (isNaN(num)) return fallbackText;
  return `${num.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
}

/** Centavos exatos (BigInt) a partir de "1234.56" ou "1.234,56"; sem float. */
function toCents(val: string | number | null | undefined): bigint {
  if (val === null || val === undefined || val === '') return 0n;
  const s = typeof val === 'string' ? parseCurrencyPtBrToDecimal(val) : val.toFixed(2);
  const neg = s.trim().startsWith('-');
  const [i, d = ''] = s.replace('-', '').split('.');
  const cents = BigInt(i || '0') * 100n + BigInt((d + '00').slice(0, 2));
  return neg ? -cents : cents;
}

function fromCents(cents: bigint): string {
  const neg = cents < 0n;
  const abs = neg ? -cents : cents;
  return `${neg ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
}

/** Alíquota "2.50" → centésimos de ponto percentual (250n). */
function rateHundredths(rate: string): bigint | null {
  const m = /^(\d{1,2})(?:[.,](\d{1,2}))?$/.exec(rate.trim());
  if (!m) return null;
  return BigInt(m[1]) * 100n + BigInt(((m[2] || '') + '00').slice(0, 2));
}

/** base(centavos) × alíquota% com arredondamento BANCÁRIO (ROUND_HALF_EVEN), igual a domain/retencoes.py. */
function issHalfEven(baseCents: bigint, rateH: bigint): bigint {
  const num = baseCents * rateH; // centavos × (taxa × 100) → dividir por 10000
  const q = num / 10000n;
  const r = num % 10000n;
  if (r * 2n > 10000n) return q + 1n;
  if (r * 2n < 10000n) return q;
  return q % 2n === 0n ? q : q + 1n;
}

export interface CalculateSummaryParams {
  amount: string | number | null | undefined;
  unconditionalDiscount?: string | number | null | undefined;
  conditionalDiscount?: string | number | null | undefined;
  deductions?: string | number | null | undefined;
  taxSettings?: TaxSettings | null;
}

/**
 * PRÉVIA LOCAL de retenções e valor líquido (resposta imediata ao digitar). NÃO é a fonte canônica:
 * a tela confere com POST /retention-preview (domain/retencoes.py) e exibe o valor do backend; o rascunho
 * salvo, a revisão do lote, o XML e o documento usam sempre o resumo calculado no backend.
 *
 * Fórmulas canônicas (DPS v1.01):
 * vBC = vServ − vDescIncond − vDR
 * vISSQN = vBC × pAliq / 100 (arredondamento bancário ROUND_HALF_EVEN)
 * vTotalRet = vRetCP + vRetIRRF + vRetCSLL + vISSQN (só se retido)
 * vLiq = vServ − vDescIncond − vDescCond − vTotalRet
 *
 * REGRA CANÔNICA DE NÃO-DUPLICAÇÃO:
 * - vRetCSLL (csll_withheld) é o total das contribuições retidas (PIS, COFINS e/ou CSLL) indicado por tpRetPisCofins.
 * - vPis e vCofins são débitos de apuração própria; fora da modalidade 1 não entram no total retido. Na modalidade 1 a NT 008 v1.02
 *   os apresenta como retidos; o cálculo do total ainda não está confirmado (ver federal.code1_pis_cofins).
 */
export function calculateRetentionSummary(params: CalculateSummaryParams): RetentionSummary {
  const vServCents = toCents(params.amount);
  const dIncCents = toCents(params.unconditionalDiscount);
  const dConCents = toCents(params.conditionalDiscount);
  const vDrCents = toCents(params.deductions);

  const tax = params.taxSettings || {};
  const retIss = tax.iss_retention || null;
  const issRetained = retIss === 'RETAINED_BY_TOMADOR' || retIss === 'RETAINED_BY_INTERMEDIARY';

  const baseCents = vServCents - dIncCents - vDrCents; // sem limitar a zero (igual ao backend)

  let issRateH: bigint | null = null;
  if (tax.iss_rate !== null && tax.iss_rate !== undefined && tax.iss_rate.trim() !== '') {
    issRateH = rateHundredths(tax.iss_rate);
  }

  let issAmountCents: bigint | null = null;
  let issSource: 'NOT_RETAINED' | 'ESTIMATED_FROM_RATE' | 'DETERMINED_BY_SEFIN' = 'NOT_RETAINED';

  if (!issRetained) {
    issAmountCents = null;
    issSource = 'NOT_RETAINED';
  } else if (issRateH === null) {
    issAmountCents = null;
    issSource = 'DETERMINED_BY_SEFIN';
  } else {
    issAmountCents = issHalfEven(baseCents, issRateH);
    issSource = 'ESTIMATED_FROM_RATE';
  }

  const inssCents = tax.inss_withheld ? toCents(tax.inss_withheld) : 0n;
  const irrfCents = tax.irrf_withheld ? toCents(tax.irrf_withheld) : 0n;
  const contribCents = tax.csll_withheld ? toCents(tax.csll_withheld) : 0n;

  const federalTotalCents = inssCents + irrfCents + contribCents;
  const issEffectiveCents = issAmountCents !== null ? issAmountCents : 0n;
  const totalWithheldCents = federalTotalCents + issEffectiveCents;
  const netCents = vServCents - dIncCents - dConCents - totalWithheldCents;

  const pending: string[] = [];
  if (issSource === 'DETERMINED_BY_SEFIN') {
    pending.push('iss.amount');
  }

  const tpCode = tax.pis_cofins_retention || null;
  if (tpCode === '1') pending.push('federal.contributions_code_1'); // NT 008 v1.02: ver domain/retencoes.py
  const tpOption = TP_RET_PIS_COFINS_OPTIONS.find((o) => o.value === tpCode);

  return {
    service_amount: fromCents(vServCents),
    unconditional_discount: fromCents(dIncCents),
    conditional_discount: fromCents(dConCents),
    deductions: fromCents(vDrCents),
    iss: {
      retention: retIss,
      retained: issRetained,
      base: fromCents(baseCents),
      rate: issRateH !== null ? (tax.iss_rate || null) : null,
      amount: issAmountCents !== null ? fromCents(issAmountCents) : null,
      amount_source: issSource,
    },
    federal: {
      inss: tax.inss_withheld ? fromCents(inssCents) : null,
      irrf: tax.irrf_withheld ? fromCents(irrfCents) : null,
      contributions: tax.csll_withheld ? fromCents(contribCents) : null,
      contributions_code: tpCode,
      contributions_label: tpOption ? tpOption.label : null,
      contributions_covered: null,
      total: fromCents(federalTotalCents),
      code1_pis_cofins: tpCode === '1'
        ? { pis: tax.pis_amount ? parseCurrencyPtBrToDecimal(tax.pis_amount) : null,
            cofins: tax.cofins_amount ? parseCurrencyPtBrToDecimal(tax.cofins_amount) : null, status: 'UNCONFIRMED_IN_TOTAL' }
        : null,
    },
    // Modalidade 1: vPis/vCofins não são "informados, não retidos" (a NT 008 v1.02 os apresenta como retidos).
    informed_not_withheld: tpCode === '1' ? { pis: null, cofins: null } : {
      pis: tax.pis_amount ? parseCurrencyPtBrToDecimal(tax.pis_amount) : null,
      cofins: tax.cofins_amount ? parseCurrencyPtBrToDecimal(tax.cofins_amount) : null,
    },
    total_withheld: fromCents(totalWithheldCents),
    net_amount: fromCents(netCents),
    complete: pending.length === 0,
    pending,
  };
}
