import { BatchStatus, QueueStatus } from './types.js';

/**
 * Utilitários de Formatação e Regras de Negócio do CTRL Fiscal
 */

/**
 * Converte valor numérico ou string decimal da API ("12500.00") para formato monetário pt-BR ("R$ 12.500,00" ou "12.500,00")
 */
export function formatCurrencyPtBr(value: string | number | null | undefined, includeSymbol = true): string {
  if (value === null || value === undefined || value === '') {
    return includeSymbol ? 'R$ 0,00' : '0,00';
  }

  const num = typeof value === 'number' ? value : parseFloat(String(value).replace(',', '.'));
  if (isNaN(num)) {
    return includeSymbol ? 'R$ 0,00' : '0,00';
  }

  const formatted = num.toLocaleString('pt-BR', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  return includeSymbol ? `R$ ${formatted}` : formatted;
}

/**
 * Converte string digitada pelo usuário em pt-BR ("12.500,50" ou "1000,5")
 * para string decimal pura ("12500.50") exigida pela API do backend.
 * Garante que não há perda de centavos.
 */
export function parseCurrencyPtBrToDecimal(input: string): string {
  if (!input || !input.trim()) return '0.00';

  // Remove caracteres que não sejam dígitos, vírgula ou ponto
  const clean = input.replace(/[^\d.,]/g, '').trim();

  // Se houver vírgula, tratamos como separador decimal pt-BR
  if (clean.includes(',')) {
    const parts = clean.split(',');
    // O que vem antes da última vírgula tem pontos removidos (separador de milhar)
    const integerPart = parts[0].replace(/\./g, '');
    const decimalPart = parts[1] || '00';
    // Garante 2 casas decimais normalizadas
    const normalizedDecimal = (decimalPart + '00').slice(0, 2);
    const finalInteger = integerPart === '' ? '0' : integerPart;
    return `${finalInteger}.${normalizedDecimal}`;
  }

  // Se houver apenas pontos
  if (clean.includes('.')) {
    const parts = clean.split('.');
    if (parts.length > 2) {
      // múltiplos pontos: ex "1.000.000" -> "1000000.00"
      const intPart = parts.join('');
      return `${intPart}.00`;
    }
    // Apenas um ponto: se tiver até 2 dígitos após o ponto, pode ser decimal
    const [intPart, decPart] = parts;
    const normalizedDecimal = (decPart + '00').slice(0, 2);
    return `${intPart || '0'}.${normalizedDecimal}`;
  }

  // Apenas dígitos
  return `${clean}.00`;
}

/**
 * Converte data/hora local em um fuso horário IANA específico para o instante UTC exato (Date).
 * Suporta qualquer fuso IANA (ex.: 'America/Sao_Paulo', 'America/Manaus').
 */
export function wallClockToUtcDate(
  year: number,
  month: number, // 1 a 12
  day: number,
  hour: number,
  minute: number,
  second: number = 0,
  timezone: string = 'America/Sao_Paulo'
): Date {
  const targetWallMs = Date.UTC(year, month - 1, day, hour, minute, second);
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    second: 'numeric',
    hourCycle: 'h23',
  });

  const getWallMs = (utcDate: Date) => {
    const parts = Object.fromEntries(fmt.formatToParts(utcDate).map((p) => [p.type, p.value]));
    return Date.UTC(
      parseInt(parts.year, 10),
      parseInt(parts.month, 10) - 1,
      parseInt(parts.day, 10),
      parseInt(parts.hour, 10),
      parseInt(parts.minute, 10),
      parseInt(parts.second, 10)
    );
  };

  let utcMs = targetWallMs;
  let wallMs = getWallMs(new Date(utcMs));
  let diff = targetWallMs - wallMs;
  utcMs += diff;
  wallMs = getWallMs(new Date(utcMs));
  if (wallMs !== targetWallMs) {
    utcMs += targetWallMs - wallMs;
  }
  return new Date(utcMs);
}

/**
 * Converte string 'YYYY-MM-DDTHH:mm' (de <input type="datetime-local">) no fuso da empresa para UTC ISO string ('Z').
 */
export function parseLocalScheduleToUtcIso(localDateTimeStr: string, timezone: string = 'America/Sao_Paulo'): string {
  const [datePart, timePart] = localDateTimeStr.split('T');
  const [y, m, d] = datePart.split('-').map(Number);
  const [hh, mm] = (timePart || '00:00').split(':').map(Number);
  const utcDate = wallClockToUtcDate(y, m, d, hh, mm, 0, timezone);
  return utcDate.toISOString();
}

/**
 * Formata um instante UTC ou string para exibição na hora local do fuso IANA da empresa.
 */
export function formatScheduledDisplay(
  utcIsoOrLocalStr: string,
  timezone: string = 'America/Sao_Paulo'
): string {
  const d = new Date(utcIsoOrLocalStr);
  if (isNaN(d.getTime())) return utcIsoOrLocalStr;
  return new Intl.DateTimeFormat('pt-BR', {
    timeZone: timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/**
 * Calcula a data exata do dia 01 do mês subsequente às 00:01:00 no fuso IANA especificado.
 * Trata viradas de ano, meses de 28/29/30/31 dias e fusos com diferentes fusos horários (SP vs Manaus).
 */
export function calculateNextMonthFirstMinute(
  refDate: Date = new Date(),
  timezone: string = 'America/Sao_Paulo'
): {
  isoString: string;
  utcIsoString: string;
  displayFormatted: string;
  localInputString: string;
} {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
  });
  const parts = Object.fromEntries(fmt.formatToParts(refDate).map((p) => [p.type, p.value]));
  const currentYear = parseInt(parts.year, 10);
  const currentMonth = parseInt(parts.month, 10); // 1 a 12

  let nextYear = currentYear;
  let nextMonth = currentMonth + 1; // 2 a 13

  if (nextMonth > 12) {
    nextMonth = 1; // Janeiro
    nextYear = currentYear + 1; // Próximo ano
  }

  const utcDate = wallClockToUtcDate(nextYear, nextMonth, 1, 0, 1, 0, timezone);
  const utcIsoString = utcDate.toISOString();

  const mm = String(nextMonth).padStart(2, '0');
  const yyyy = String(nextYear);
  const displayFormatted = `01/${mm}/${yyyy} às 00:01`;
  const localInputString = `${yyyy}-${mm}-01T00:01`;

  return {
    isoString: utcIsoString,
    utcIsoString,
    displayFormatted,
    localInputString,
  };
}

/**
 * Valida códigos de serviço nacional (6 dígitos) e municipal (cTribMun: exatamente 3 dígitos).
 * Não trunca nem converte códigos inválidos silenciosamente; retorna erro de validação explicativo.
 * Preserva zeros à esquerda e exige conformidade estrita com o OpenAPI Sprint 02 (cTribMun: ^[0-9]{3}$).
 */
export function validateServiceCodes(
  nationalCode?: string | null,
  municipalCode?: string | null
): { valid: boolean; error?: string } {
  if (nationalCode !== undefined && nationalCode !== null && nationalCode.trim() !== '') {
    const trimmed = nationalCode.trim();
    // Verifica se possui caracteres não numéricos além de pontos de formatação
    if (!/^[0-9]+(\.[0-9]+)*$/.test(trimmed) && !/^[0-9]+$/.test(trimmed)) {
      return {
        valid: false,
        error: 'Código Nacional de Serviço inválido. Deve conter apenas dígitos numéricos (ex: 010701 ou 01.07.01).',
      };
    }
    const digits = trimmed.replace(/\D/g, '');
    if (digits.length !== 6) {
      return {
        valid: false,
        error: `Código Nacional de Serviço inválido. Esperado exatamente 6 dígitos numéricos (recebido: ${digits.length}).`,
      };
    }
  }

  if (municipalCode !== undefined && municipalCode !== null && municipalCode.trim() !== '') {
    const trimmed = municipalCode.trim();
    if (!/^[0-9]{3}$/.test(trimmed)) {
      return {
        valid: false,
        error: 'Código Municipal de Tributação (cTribMun) inválido. Deve conter exatamente 3 dígitos numéricos (ex: 001, 101, 269), preservando zeros à esquerda.',
      };
    }
  }

  return { valid: true };
}

export type FiscalBadgeStatus =
  | 'AUTHORIZED'
  | 'CANCELLED'
  | 'UNKNOWN'
  | 'DRAFT'
  | 'SCHEDULED'
  | 'PROCESSING'
  | 'REJECTED'
  | 'AWAITING_CONFIRMATION'
  | 'WITHDRAWN'
  | 'PENDING'
  | 'REVIEWED'
  | 'DIVERGENT'
  | 'VALID'
  | 'EXPIRED'
  | 'NOT_CONFIGURED';

export interface StatusBadgeConfig {
  status: FiscalBadgeStatus;
  label: string;
  isTestDocument?: boolean;
}

/**
 * D7: Mapeia ambiente e simulação retornados pelo backend para o badge visual.
 * Distingue:
 * - Produção (simulada ou real)
 * - Produção Restrita (homologação, simulada ou real)
 * - Simulação (Mock)
 */
export function getEnvironmentBadgeConfig(
  environment?: string | null,
  simulated: boolean = true
): StatusBadgeConfig {
  const env = (environment || 'mock').toLowerCase();
  if (env === 'production') {
    return {
      status: simulated ? 'PENDING' : 'VALID',
      label: simulated ? 'Produção (Simulada)' : 'Produção',
      isTestDocument: simulated,
    };
  }
  if (env === 'restricted') {
    return {
      status: 'PENDING',
      label: simulated ? 'Produção Restrita (Simulada)' : 'Produção Restrita',
      isTestDocument: true,
    };
  }
  return {
    status: 'NOT_CONFIGURED',
    label: 'Simulação (Mock)',
    isTestDocument: true,
  };
}

/**
 * Dimensão 1: Ambiente do Documento Fiscal
 * - Produção real: VALID / "PRODUÇÃO"
 * - Homologação (Produção Restrita): PENDING / "HOMOLOGAÇÃO" (com isTestDocument = true, sem validade fiscal)
 * - Simulação local (Mock): NOT_CONFIGURED / "SIMULAÇÃO" (isTestDocument = true)
 *
 * Desacoplado da autorização fiscal e da conferência: uma nota em homologação continua sendo documento de teste,
 * mesmo quando autorizada com sucesso.
 */
export function getDocumentEnvironmentBadgeConfig(
  environment?: string | null,
  simulated: boolean = true
): StatusBadgeConfig {
  const env = (environment || 'mock').toLowerCase();
  if (env === 'production' && !simulated) {
    return { status: 'VALID', label: 'PRODUÇÃO', isTestDocument: false };
  }
  if (env === 'restricted' || (env === 'production' && simulated)) {
    return { status: 'PENDING', label: 'HOMOLOGAÇÃO', isTestDocument: true };
  }
  return { status: 'NOT_CONFIGURED', label: 'SIMULADA', isTestDocument: true };
}

/**
 * Dimensão 2: Autorização Fiscal / Situação Cadastral
 * - AUTORIZADA, CANCELADA, REJEITADA, PROCESSANDO, AGUARDANDO_CONFIRMACAO
 * Desacoplado do ambiente: uma nota emitida em produção pode estar CANCELADA.
 */
export function getFiscalAuthorizationBadgeConfig(status: string): StatusBadgeConfig {
  switch (status) {
    case 'AUTHORIZED':
      return { status: 'AUTHORIZED', label: 'Autorizada' };
    case 'CANCELLED':
      return { status: 'CANCELLED', label: 'Cancelada' };
    case 'REJECTED':
      return { status: 'REJECTED', label: 'Rejeitada' };
    case 'PROCESSING':
      return { status: 'PROCESSING', label: 'Processando' };
    case 'AWAITING_CONFIRMATION':
      return { status: 'AWAITING_CONFIRMATION', label: 'Aguardando Confirmação' };
    default:
      return { status: 'UNKNOWN', label: status || 'Desconhecida' };
  }
}

/**
 * Dimensão 3: Conferência / Revisão Interna
 * - PENDING, REVIEWED, DIVERGENT
 * Desacoplado do status fiscal e do ambiente.
 */
export function getReviewStatusBadgeConfig(reviewStatus?: string | null): StatusBadgeConfig {
  switch (reviewStatus) {
    case 'REVIEWED':
      return { status: 'REVIEWED', label: 'Revisada' };
    case 'DIVERGENT':
      return { status: 'DIVERGENT', label: 'Divergente' };
    case 'PENDING':
    default:
      return { status: 'PENDING', label: 'Pendente Revisão' };
  }
}

/**
 * D11: Mapeia estados canônicos do LOTE (BatchOut.status) conforme OpenAPI:
 * - SCHEDULED
 * - PROCESSING
 * - COMPLETED
 * - COMPLETED_WITH_REJECTIONS
 * - WITHDRAWN
 *
 * Garante que PROCESSING e COMPLETED_WITH_REJECTIONS NUNCA apareçam como WITHDRAWN.
 */
export function getBatchStatusBadgeConfig(status: BatchStatus | string): StatusBadgeConfig {
  switch (status) {
    case 'SCHEDULED':
      return { status: 'SCHEDULED', label: 'Agendado' };
    case 'PROCESSING':
      return { status: 'PROCESSING', label: 'Processando' };
    case 'COMPLETED':
      return { status: 'VALID', label: 'Concluído' };
    case 'COMPLETED_WITH_REJECTIONS':
      return { status: 'DIVERGENT', label: 'Concluído com Rejeições' };
    case 'WITHDRAWN':
      return { status: 'WITHDRAWN', label: 'Retirado da Fila' };
    default:
      return { status: 'UNKNOWN', label: status || 'Desconhecido' };
  }
}

/**
 * D11: Mapeia estados do ITEM do lote (BatchItemOut.status / QueueStatus) separadamente dos estados do lote:
 * - DRAFT, SCHEDULED, PROCESSING, AUTHORIZED, REJECTED, AWAITING_CONFIRMATION, WITHDRAWN
 */
export function getBatchItemStatusBadgeConfig(status: QueueStatus | string): StatusBadgeConfig {
  switch (status) {
    case 'DRAFT':
      return { status: 'DRAFT', label: 'Rascunho' };
    case 'SCHEDULED':
      return { status: 'SCHEDULED', label: 'Agendado' };
    case 'PROCESSING':
      return { status: 'PROCESSING', label: 'Processando' };
    case 'AUTHORIZED':
      return { status: 'AUTHORIZED', label: 'Autorizada' };
    case 'REJECTED':
      return { status: 'REJECTED', label: 'Rejeitada' };
    case 'AWAITING_CONFIRMATION':
      return { status: 'AWAITING_CONFIRMATION', label: 'Aguardando Confirmação' };
    case 'WITHDRAWN':
      return { status: 'WITHDRAWN', label: 'Retirado da Fila' };
    default:
      return { status: 'UNKNOWN', label: status || 'Desconhecido' };
  }
}

