import React, { useEffect, useId, useRef, useState } from 'react';
import { Button } from './Button.js';

/** Título da tela + ação principal destacada (uma por tela) + ações secundárias. */
export const PageHeader: React.FC<{
  title: string;
  description?: React.ReactNode;
  primaryAction?: React.ReactNode;
  secondaryActions?: React.ReactNode;
}> = ({ title, description, primaryAction, secondaryActions }) => (
  <header style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
    <div style={{ minWidth: 0 }}>
      <h1 style={{ fontSize: '1.375rem', fontWeight: 700, margin: 0, color: 'var(--color-text-main)' }}>{title}</h1>
      {description && <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>{description}</p>}
    </div>
    {(primaryAction || secondaryActions) && (
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        {secondaryActions}
        {primaryAction}
      </div>
    )}
  </header>
);

/** Painel de filtros avançados recolhível (disclosure acessível). */
export const FilterPanel: React.FC<{
  title?: string;
  activeCount?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}> = ({ title = 'Filtros avançados', activeCount = 0, defaultOpen = false, children }) => {
  const [open, setOpen] = useState(defaultOpen || activeCount > 0);
  const id = useId();
  return (
    <section style={{ border: '1px solid var(--color-border)', borderRadius: 'var(--radius-lg)', backgroundColor: 'var(--color-bg-card)' }}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
        style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.625rem 0.875rem',
          background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 600, color: 'var(--color-text-main)',
          fontFamily: 'inherit' }}>
        <span>
          {title}
          {activeCount > 0 && (
            <span style={{ marginLeft: '0.5rem', fontSize: '0.75rem', fontWeight: 600, color: 'var(--color-primary)' }}>
              ({activeCount} {activeCount === 1 ? 'ativo' : 'ativos'})
            </span>
          )}
        </span>
        <span aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      <div id={id} hidden={!open} style={{ padding: '0 0.875rem 0.875rem 0.875rem' }}>{children}</div>
    </section>
  );
};

/** Estado de erro com nova tentativa; a mensagem nunca inclui dados fiscais nem detalhes técnicos sensíveis. */
export const ErrorState: React.FC<{ title?: string; message: string; onRetry?: () => void }> = ({
  title = 'Não foi possível carregar', message, onRetry,
}) => (
  <div role="alert" style={{ padding: '2rem 1rem', textAlign: 'center', color: 'var(--color-text-main)' }}>
    <div aria-hidden="true" style={{ fontSize: '1.25rem', color: 'var(--color-danger)', fontWeight: 700 }}>✕</div>
    <h3 style={{ margin: '0.5rem 0 0.25rem 0', fontSize: '1rem' }}>{title}</h3>
    <p style={{ margin: 0, fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>{message}</p>
    {onRetry && <div style={{ marginTop: '1rem' }}><Button variant="outline" onClick={onRetry}>Tentar novamente</Button></div>}
  </div>
);

/** Progresso de operação demorada (job). value=null → indeterminado. */
export const ProgressBar: React.FC<{ value: number | null; label: string }> = ({ value, label }) => (
  <div>
    <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)', marginBottom: '0.375rem' }}>{label}</div>
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={value === null ? undefined : Math.round(value)}
      style={{ height: '0.5rem', borderRadius: 'var(--radius-full)', backgroundColor: 'var(--color-bg-muted)', overflow: 'hidden' }}>
      <div style={{ height: '100%', width: value === null ? '35%' : `${Math.max(2, Math.min(100, value))}%`,
        backgroundColor: 'var(--color-primary)', animation: value === null ? 'ui-pulse 1.2s ease-in-out infinite' : undefined,
        transition: 'width var(--transition-normal)' }} />
    </div>
  </div>
);

export function useDebouncedValue<T>(value: T, delayMs = 350): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return v;
}

/**
 * Aviso ao sair com alterações não salvas: fecha/recarrega a aba (beforeunload) e, via `confirmLeave`,
 * navegação interna do painel.
 */
export function useUnsavedChanges(isDirty: boolean, message = 'Há alterações não salvas neste rascunho. Sair mesmo assim?') {
  const dirtyRef = useRef(isDirty);
  dirtyRef.current = isDirty;
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);
  return {
    confirmLeave: () => !dirtyRef.current || window.confirm(message),
  };
}
