import { useCallback, useEffect, useState } from 'react';
import type { DocumentFilter, RetentionClass, RetentionMatch, RetentionType } from '@ctrl-fiscal/api-client';

/**
 * Filtros, página e nota aberta vivem na URL (?q=…&page=…&doc=…): sobrevivem a recarregar, abrir uma nota e
 * voltar (botão Voltar), e podem ser compartilhados. Filtros usam replaceState; abrir nota usa pushState.
 */
export interface DocFilters {
  q: string;
  comp: string;
  from: string;
  to: string;
  env: string;
  status: string;
  review: string;
  ret: '' | RetentionClass;
  rtype: RetentionType[];
  rmatch: '' | RetentionMatch;
  wmin: string;
  wmax: string;
  page: number;
}

export const EMPTY: DocFilters = {
  q: '', comp: '', from: '', to: '', env: '', status: '', review: '', ret: '', rtype: [], rmatch: '', wmin: '', wmax: '', page: 1,
};
const TYPES: RetentionType[] = ['ISS', 'INSS', 'IRRF', 'CONTRIB'];

export function readFilters(): DocFilters {
  const p = new URLSearchParams(window.location.search);
  const rtype = (p.get('rtype') || '').split(',').filter((t): t is RetentionType => (TYPES as string[]).includes(t));
  const ret = p.get('ret');
  const rmatch = p.get('rmatch');
  return {
    q: p.get('q') || '', comp: p.get('comp') || '', from: p.get('from') || '', to: p.get('to') || '',
    env: p.get('env') || '', status: p.get('status') || '', review: p.get('review') || '',
    ret: ret === 'WITH' || ret === 'WITHOUT' || ret === 'UNKNOWN' ? ret : '',
    rtype, rmatch: rmatch === 'ANY' || rmatch === 'ALL' ? rmatch : '',
    wmin: p.get('wmin') || '', wmax: p.get('wmax') || '', page: Math.max(1, Number(p.get('page')) || 1),
  };
}

function writeParams(f: DocFilters, doc: string | null, push: boolean) {
  const url = new URL(window.location.href);
  const keep = ['view', 'company_id'];
  for (const k of Array.from(url.searchParams.keys())) if (!keep.includes(k)) url.searchParams.delete(k);
  const set = (k: string, v: string) => v && url.searchParams.set(k, v);
  set('q', f.q); set('comp', f.comp); set('from', f.from); set('to', f.to); set('env', f.env); set('status', f.status);
  set('review', f.review); set('ret', f.ret); set('rtype', f.rtype.join(',')); set('rmatch', f.rmatch);
  set('wmin', f.wmin); set('wmax', f.wmax);
  if (f.page > 1) url.searchParams.set('page', String(f.page));
  if (doc) url.searchParams.set('doc', doc);
  window.history[push ? 'pushState' : 'replaceState']({}, '', url.toString());
}

export function useDocumentUrlState() {
  const [filters, setFiltersState] = useState<DocFilters>(readFilters);
  const [openDoc, setOpenDocState] = useState<string | null>(() => new URLSearchParams(window.location.search).get('doc'));

  useEffect(() => {
    const onPop = () => {
      setFiltersState(readFilters());
      setOpenDocState(new URLSearchParams(window.location.search).get('doc'));
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  /** Altera filtros (volta à página 1, salvo quando só a página muda). */
  const setFilters = useCallback((patch: Partial<DocFilters>) => {
    setFiltersState((prev) => {
      const onlyPage = Object.keys(patch).length === 1 && 'page' in patch;
      const next = { ...prev, ...patch, page: onlyPage ? patch.page! : 1 };
      writeParams(next, null, false);
      return next;
    });
    setOpenDocState(null);
  }, []);

  const openDocument = useCallback((id: string | null) => {
    setOpenDocState(id);
    setFiltersState((f) => {
      writeParams(f, id, id !== null);
      return f;
    });
  }, []);

  return { filters, setFilters, openDoc, openDocument };
}

export function activeAdvancedCount(f: DocFilters): number {
  return [f.status, f.review, f.ret, f.rtype.length ? 'x' : '', f.wmin, f.wmax].filter(Boolean).length;
}

/** Converte para o filtro da API (o mesmo objeto vai para a listagem e para o download de todos os resultados). */
export function toApiFilter(f: DocFilters, direction: 'ISSUED' | 'RECEIVED'): DocumentFilter {
  return {
    direction,
    query: f.q.trim() || undefined,
    competence: /^\d{4}-(0[1-9]|1[0-2])$/.test(f.comp) ? f.comp : undefined,
    date_from: f.from || undefined,
    date_to: f.to || undefined,
    status: (f.status || undefined) as DocumentFilter['status'],
    review_status: (f.review || undefined) as DocumentFilter['review_status'],
    retention: f.ret || undefined,
    retention_type: f.rtype.length ? f.rtype : undefined,
    retention_match: f.rtype.length > 1 ? (f.rmatch || 'ANY') : undefined,
    withheld_min: /^\d{1,13}(\.\d{1,2})?$/.test(f.wmin) ? f.wmin : undefined,
    withheld_max: /^\d{1,13}(\.\d{1,2})?$/.test(f.wmax) ? f.wmax : undefined,
  };
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setMatches(m.matches);
    m.addEventListener('change', on);
    return () => m.removeEventListener('change', on);
  }, [query]);
  return matches;
}
