import React, { useId } from 'react';

/**
 * Seção de tela com título h2 (o h1 vem do PageHeader). Substitui o Card nas telas padronizadas
 * para manter a hierarquia de títulos h1 → h2 → h3.
 */
export const ScreenSection: React.FC<{
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  id?: string;
}> = ({ title, description, actions, children, id }) => {
  const headingId = useId();
  return (
    <section id={id} className="pf-section" aria-labelledby={headingId}>
      <div className="pf-section-head">
        <div style={{ minWidth: 0 }}>
          <h2 id={headingId} className="pf-section-title">{title}</h2>
          {description && <p className="pf-section-desc">{description}</p>}
        </div>
        {actions && <div className="pf-section-actions">{actions}</div>}
      </div>
      <div className="pf-section-body">{children}</div>
    </section>
  );
};

/** Bloco rótulo + valor (indicadores e dados de leitura). */
export const InfoTile: React.FC<{
  label: React.ReactNode;
  children: React.ReactNode;
  hint?: React.ReactNode;
  variant?: 'subtle' | 'outline';
}> = ({ label, children, hint, variant = 'subtle' }) => (
  <div className="pf-tile" data-variant={variant}>
    <div className="pf-tile-label">{label}</div>
    <div className="pf-tile-value">{children}</div>
    {hint && <div className="pf-tile-hint">{hint}</div>}
  </div>
);

/** Conteúdo de tabela vazia (sem emoji; texto + ação opcional). */
export const TableEmpty: React.FC<{ title: string; description?: string; action?: React.ReactNode }> = ({
  title, description, action,
}) => (
  <div>
    <div style={{ fontWeight: 600, color: 'var(--color-text-main)' }}>{title}</div>
    {description && <div style={{ fontSize: '0.8125rem', marginTop: '0.25rem' }}>{description}</div>}
    {action && <div style={{ marginTop: '0.75rem' }}>{action}</div>}
  </div>
);
