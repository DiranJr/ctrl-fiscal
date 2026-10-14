import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type { FiscalDocument } from '@ctrl-fiscal/api-client';

/**
 * Seleção ACUMULADA de notas para o download em lote: sobrevive a busca, filtros, ordenação, página, abrir a nota e
 * à navegação interna entre telas. Fica só em memória (some ao recarregar ou sair da conta). É ligada a um CONTEXTO
 * (workspace + recebidas/emitidas + ambiente): trocar qualquer um deles começa uma seleção vazia.
 */
export interface NotaSelecionada {
  id: string;
  number: string | null;
  access_key: string | null;
  issued_at: string | null;
  counterpart: string | null;
  counterpart_doc: string | null;
  amount: string | null;
  environment: string | null;
  xml_available: boolean;
  danfse_available: boolean;
}

export interface ContextoSelecao {
  company: string;
  direction: 'ISSUED' | 'RECEIVED';
  env: string;
}

export const chaveContexto = (c: ContextoSelecao) => `${c.company}|${c.direction}|${c.env}`;

interface Estado<F> {
  chave: string | null;
  itens: ReadonlyMap<string, NotaSelecionada>;
  /** Últimos filtros da consulta deste contexto: ao voltar à tela pela navegação interna, a consulta é retomada. */
  filtros: F | null;
}

export function resumo(d: FiscalDocument, direction: 'ISSUED' | 'RECEIVED'): NotaSelecionada {
  const cp = direction === 'ISSUED' ? d.recipient : d.issuer;
  return {
    id: d.id, number: d.number ?? null, access_key: d.access_key ?? null, issued_at: d.issued_at ?? null,
    counterpart: cp?.name ?? null, counterpart_doc: cp?.cnpj ?? null, amount: d.amount ?? null,
    environment: d.environment ?? null, xml_available: !!d.xml_available, danfse_available: !!d.danfse_available,
  };
}

export function rotuloNota(n: Pick<NotaSelecionada, 'number' | 'access_key' | 'id'>): string {
  if (n.number) return `NFS-e nº ${n.number}`;
  if (n.access_key) return `NFS-e ${n.access_key.slice(0, 10)}…${n.access_key.slice(-6)}`;
  return `Documento ${n.id.slice(0, 8)}`;
}

/** Armazenamento independente do React (testável); o hook abaixo só o assina. */
export class SelecaoNotas<F = unknown> {
  private estado: Estado<F> = { chave: null, itens: new Map(), filtros: null };
  private ouvintes = new Set<() => void>();

  assinar = (fn: () => void) => {
    this.ouvintes.add(fn);
    return () => { this.ouvintes.delete(fn); };
  };

  ler = () => this.estado;

  private trocar(e: Partial<Estado<F>>) {
    this.estado = { ...this.estado, ...e };
    this.ouvintes.forEach((fn) => fn());
  }

  /** Itens visíveis para o contexto pedido (vazio se a seleção guardada é de outro contexto). */
  itens(chave: string): ReadonlyMap<string, NotaSelecionada> {
    return this.estado.chave === chave ? this.estado.itens : VAZIO;
  }

  filtros(chave: string): F | null {
    return this.estado.chave === chave ? this.estado.filtros : null;
  }

  /** Entra num contexto: se é outro (workspace, direção ou ambiente), descarta a seleção anterior. */
  entrar(chave: string) {
    if (this.estado.chave !== chave) this.trocar({ chave, itens: new Map(), filtros: null });
  }

  /** Filtros guardados para o workspace e a direção (em qualquer ambiente), para retomar a consulta ao voltar. */
  ultimaConsulta(company: string, direction: string): F | null {
    return this.estado.chave?.startsWith(`${company}|${direction}|`) ? this.estado.filtros : null;
  }

  guardarFiltros(chave: string, filtros: F) {
    if (this.estado.chave === chave) this.estado.filtros = filtros; // mesmo objeto: não há o que re-renderizar
  }

  private mutar(chave: string, fn: (m: Map<string, NotaSelecionada>) => void) {
    if (this.estado.chave !== chave) return; // resposta/ação de um contexto que já não vale
    const m = new Map(this.estado.itens);
    fn(m);
    this.trocar({ itens: m });
  }

  alternar(chave: string, nota: NotaSelecionada) {
    this.mutar(chave, (m) => (m.has(nota.id) ? m.delete(nota.id) : m.set(nota.id, nota)));
  }

  /** Cabeçalho da tabela: marca ou desmarca SOMENTE as notas desta página, sem tocar nas demais. */
  marcarPagina(chave: string, notas: NotaSelecionada[], marcar: boolean) {
    this.mutar(chave, (m) => notas.forEach((n) => (marcar ? m.set(n.id, n) : m.delete(n.id))));
  }

  remover(chave: string, ids: string[]) {
    this.mutar(chave, (m) => ids.forEach((id) => m.delete(id)));
  }

  limpar(chave: string) {
    this.mutar(chave, (m) => m.clear());
  }
}

const VAZIO: ReadonlyMap<string, NotaSelecionada> = new Map();

/** Uma seleção por aba do navegador (memória do módulo). */
export const selecaoNotas = new SelecaoNotas<unknown>();

export function useSelecaoNotas(ctx: ContextoSelecao) {
  const chave = chaveContexto(ctx);
  useEffect(() => { selecaoNotas.entrar(chave); }, [chave]);
  const estado = useSyncExternalStore(selecaoNotas.assinar, selecaoNotas.ler);
  const itens = estado.chave === chave ? estado.itens : VAZIO;
  return {
    chave,
    itens,
    alternar: useCallback((n: NotaSelecionada) => selecaoNotas.alternar(chave, n), [chave]),
    marcarPagina: useCallback((ns: NotaSelecionada[], m: boolean) => selecaoNotas.marcarPagina(chave, ns, m), [chave]),
    remover: useCallback((ids: string[]) => selecaoNotas.remover(chave, ids), [chave]),
    limpar: useCallback(() => selecaoNotas.limpar(chave), [chave]),
  };
}

export interface Indisponivel {
  id: string;
  motivo: string;
  /** false = não deu para conferir (rede); a nota NÃO é oferecida para remoção. */
  definitivo: boolean;
}

/**
 * Descobre QUAIS notas não podem entrar no lote (o servidor recusa o lote informando só a quantidade). Cada nota é
 * conferida pelo detalhe, com a autorização, o workspace e o ambiente verificados no servidor.
 */
export async function conferirDisponibilidade(
  ids: string[], buscar: (id: string) => Promise<{ environment?: string | null }>, ambienteEsperado: (id: string) => string | null,
  paralelo = 6,
): Promise<Indisponivel[]> {
  const fora: Indisponivel[] = [];
  let i = 0;
  const trabalhador = async () => {
    while (i < ids.length) {
      const id = ids[i++];
      try {
        const d = await buscar(id);
        const esperado = ambienteEsperado(id);
        if (esperado && d.environment && d.environment !== esperado) {
          fora.push({ id, definitivo: true,
            motivo: `Pertence a outro ambiente (${d.environment}); o lote é gerado em um único ambiente.` });
        }
      } catch (err) {
        const st = (err as { problem?: { status?: number } })?.problem?.status;
        fora.push({
          id,
          definitivo: st === 404 || st === 403,
          motivo: st === 404 ? 'Não encontrada neste workspace (removida ou indisponível).'
            : st === 403 ? 'Sem permissão para acessar esta nota.'
              : 'Não foi possível conferir agora (falha de comunicação). Tente novamente.',
        });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(paralelo, ids.length) }, trabalhador));
  return ids.flatMap((id) => fora.filter((f) => f.id === id)); // ordem da seleção
}
