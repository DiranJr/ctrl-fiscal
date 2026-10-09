import React from 'react';
import { Button } from './Button.js';

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  render?: (item: T, index: number) => React.ReactNode;
  align?: 'left' | 'center' | 'right';
  width?: string;
  /** Valores monetários/quantidades: alinhados à direita com algarismos tabulares. */
  numeric?: boolean;
  /** Esconde a coluna em telas estreitas (≤ 767 px). */
  hideOnMobile?: boolean;
}

export interface TableSelection {
  /** Chaves selecionadas (somente da página exibida; "todos os resultados" é tratado fora da tabela). */
  selected: Set<string>;
  onToggle: (key: string) => void;
  /** Marca/desmarca as linhas DESTA página. */
  onTogglePage: (checked: boolean) => void;
  rowLabel: (item: any) => string;
}

export interface TableProps<T> {
  columns: Column<T>[];
  data: T[];
  keyExtractor: (item: T, index: number) => string;
  isLoading?: boolean;
  emptyMessage?: string;
  /** Conteúdo exibido quando não há linhas (substitui emptyMessage). */
  emptyState?: React.ReactNode;
  page?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (newPage: number) => void;
  onRowClick?: (item: T) => void;
  /** Legenda acessível (pode ser visualmente oculta). */
  caption?: string;
  selection?: TableSelection;
}

const cell: React.CSSProperties = { padding: '0.75rem 0.875rem', color: 'var(--color-text-main)', verticalAlign: 'top' };

export function Table<T>({
  columns,
  data,
  keyExtractor,
  isLoading = false,
  emptyMessage = 'Nenhum registro encontrado.',
  emptyState,
  page,
  pageSize = 25,
  total,
  onPageChange,
  onRowClick,
  caption,
  selection,
}: TableProps<T>) {
  const totalPages = total !== undefined ? Math.max(1, Math.ceil(total / pageSize)) : undefined;
  const pageKeys = data.map((d, i) => keyExtractor(d, i));
  const allOnPage = selection ? pageKeys.length > 0 && pageKeys.every((k) => selection.selected.has(k)) : false;
  const someOnPage = selection ? pageKeys.some((k) => selection.selected.has(k)) : false;
  const colCount = columns.length + (selection ? 1 : 0);
  const headRef = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (headRef.current) headRef.current.indeterminate = someOnPage && !allOnPage;
  }, [someOnPage, allOnPage]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', borderRadius: 'var(--radius-lg)', border: '1px solid var(--color-border)',
      backgroundColor: 'var(--color-bg-card)', overflow: 'hidden', width: '100%', minWidth: 0, maxWidth: '100%' }}>
      {/* position: relative contém elementos .ui-sr-only (absolutos) na área rolável, sem alargar a página */}
      <div style={{ overflowX: 'auto', width: '100%', position: 'relative' }}>
        <table className="ui-table" aria-busy={isLoading || undefined}
          style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.875rem' }}>
          {caption && <caption className="ui-sr-only">{caption}</caption>}
          <thead>
            <tr style={{ backgroundColor: 'var(--color-bg-subtle)', borderBottom: '1px solid var(--color-border)' }}>
              {selection && (
                <th scope="col" style={{ ...cell, width: '2.5rem' }}>
                  <input ref={headRef} type="checkbox" checked={allOnPage} disabled={isLoading || data.length === 0}
                    aria-label="Selecionar todas as linhas desta página"
                    onChange={(e) => selection.onTogglePage(e.target.checked)} />
                </th>
              )}
              {columns.map((col) => (
                <th key={col.key} scope="col" className={col.hideOnMobile ? 'ui-only-desktop' : undefined}
                  style={{ ...cell, fontWeight: 600, color: 'var(--color-text-muted)', textAlign: col.numeric ? 'right' : col.align || 'left',
                    width: col.width, fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap' }}>
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              Array.from({ length: Math.min(pageSize, 6) }).map((_, i) => (
                <tr key={`sk-${i}`} style={{ borderBottom: '1px solid var(--color-border)' }}>
                  {Array.from({ length: colCount }).map((__, j) => (
                    <td key={j} style={cell}>
                      <div style={{ height: '0.875rem', borderRadius: 'var(--radius-sm)', backgroundColor: 'var(--color-bg-muted)',
                        animation: 'ui-pulse 1.4s ease-in-out infinite', width: j === 0 ? '60%' : '80%' }} />
                    </td>
                  ))}
                </tr>
              ))
            ) : data.length === 0 ? (
              <tr>
                <td colSpan={colCount} style={{ padding: '2.5rem 1rem', textAlign: 'center', color: 'var(--color-text-subtle)' }}>
                  {emptyState ?? emptyMessage}
                </td>
              </tr>
            ) : (
              data.map((item, index) => {
                const key = pageKeys[index];
                const isSel = selection?.selected.has(key) || false;
                return (
                  <tr key={key} data-clickable={onRowClick ? 'true' : undefined} data-selected={isSel ? 'true' : undefined}
                    tabIndex={onRowClick ? 0 : undefined}
                    onClick={() => onRowClick && onRowClick(item)}
                    onKeyDown={(e) => {
                      if (onRowClick && (e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) {
                        e.preventDefault();
                        onRowClick(item);
                      }
                    }}
                    style={{ borderBottom: '1px solid var(--color-border)', cursor: onRowClick ? 'pointer' : 'default' }}>
                    {selection && (
                      <td style={cell} onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={isSel} aria-label={`Selecionar ${selection.rowLabel(item)}`}
                          onChange={() => selection.onToggle(key)} />
                      </td>
                    )}
                    {columns.map((col) => (
                      <td key={col.key} className={[col.numeric ? 'ui-num' : '', col.hideOnMobile ? 'ui-only-desktop' : ''].join(' ').trim() || undefined}
                        style={{ ...cell, textAlign: col.numeric ? 'right' : col.align || 'left' }}>
                        {col.render ? col.render(item, index) : (item as any)[col.key]}
                      </td>
                    ))}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {totalPages !== undefined && onPageChange && page !== undefined && (total ?? 0) > 0 && (
        <nav aria-label="Paginação" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap',
          gap: '0.5rem', padding: '0.625rem 0.875rem', borderTop: '1px solid var(--color-border)', backgroundColor: 'var(--color-bg-subtle)',
          fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
          <div aria-live="polite">Página {page} de {totalPages} · {total} {total === 1 ? 'registro' : 'registros'}</div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <Button variant="outline" size="sm" disabled={page <= 1 || isLoading} onClick={() => onPageChange(page - 1)}
              aria-label="Página anterior">Anterior</Button>
            <Button variant="outline" size="sm" disabled={page >= totalPages || isLoading} onClick={() => onPageChange(page + 1)}
              aria-label="Próxima página">Próxima</Button>
          </div>
        </nav>
      )}
    </div>
  );
}
