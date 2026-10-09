import { useEffect, useMemo, useState } from 'react';
import {
  CalculateSummaryParams,
  RetentionSummary,
  ValidationIssue,
  calculateRetentionSummary,
  parseCurrencyPtBrToDecimal,
} from '@ctrl-fiscal/api-client';
import { useDebouncedValue } from '@ctrl-fiscal/ui';
import { useFiscal, logSafe } from '../context/FiscalContext.js';

export type RetentionSource = 'backend' | 'local' | 'invalid';

export interface CanonicalRetentions {
  /** Resumo exibido: o do backend quando conferido para os valores atuais; senão a prévia local. */
  summary: RetentionSummary;
  source: RetentionSource;
  /** A prévia local divergiu do backend (o backend prevalece). */
  diverged: boolean;
  issues: ValidationIssue[];
}

const money = (v: string | number | null | undefined) =>
  v === null || v === undefined || String(v).trim() === '' ? null : parseCurrencyPtBrToDecimal(String(v));

/**
 * Fonte canônica dos totais = backend (POST /retention-preview → domain/retencoes.py, a mesma função do rascunho
 * salvo, da revisão do lote, do snapshot/XML e do documento). O cálculo local é só prévia instantânea.
 */
export function useCanonicalRetentions(params: CalculateSummaryParams): CanonicalRetentions {
  const { clients, currentCompany } = useFiscal();
  const local = useMemo(() => calculateRetentionSummary(params), [params]);
  const request = useMemo(() => {
    const tax = Object.fromEntries(Object.entries(params.taxSettings || {}).filter(([, v]) => v !== null && v !== undefined && v !== ''));
    return {
      amount: money(params.amount) || '0.00',
      deductions: money(params.deductions),
      unconditional_discount: money(params.unconditionalDiscount),
      conditional_discount: money(params.conditionalDiscount),
      tax_settings: tax,
    };
  }, [params]);
  const reqKey = JSON.stringify(request);
  const debouncedKey = useDebouncedValue(reqKey, 400);
  const [result, setResult] = useState<{ key: string; summary: RetentionSummary | null; issues: ValidationIssue[]; ok: boolean } | null>(null);

  useEffect(() => {
    if (!currentCompany) return;
    let cancelled = false;
    clients.fiscal
      .previewRetentions(currentCompany.id, JSON.parse(debouncedKey))
      .then((r) => !cancelled && setResult({ key: debouncedKey, summary: r.retention_summary, issues: r.issues || [], ok: true }))
      .catch((err) => {
        logSafe('prévia de retenções', err);
        if (!cancelled) setResult({ key: debouncedKey, summary: null, issues: [], ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedKey, currentCompany, clients.fiscal]);

  const fresh = result && result.key === reqKey;
  if (fresh && result!.ok && result!.summary) {
    const b = result!.summary;
    const diverged = b.total_withheld !== local.total_withheld || b.net_amount !== local.net_amount;
    return { summary: b, source: 'backend', diverged, issues: result!.issues };
  }
  return { summary: local, source: fresh && !result!.ok ? 'invalid' : 'local', diverged: false, issues: [] };
}
