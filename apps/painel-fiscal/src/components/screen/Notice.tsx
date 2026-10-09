import React from 'react';

export type NoticeVariant = 'info' | 'success' | 'warning' | 'error' | 'neutral';

/** Símbolo textual por variante: a mensagem nunca depende só da cor (WCAG 1.4.1). */
const GLYPH: Record<NoticeVariant, string> = {
  info: 'i',
  success: '✓',
  warning: '!',
  error: '✕',
  neutral: '•',
};

/**
 * Aviso informativo com tokens de estado. Diferente do Alert (role="alert"), não interrompe o leitor de tela;
 * use `live` quando o conteúdo muda durante uma operação (role="status", aria-live="polite").
 */
export const Notice: React.FC<{
  variant?: NoticeVariant;
  title?: React.ReactNode;
  children?: React.ReactNode;
  action?: React.ReactNode;
  live?: boolean;
}> = ({ variant = 'info', title, children, action, live }) => (
  <div className="pf-notice" data-variant={variant} role={live ? 'status' : undefined} aria-live={live ? 'polite' : undefined}>
    <span className="pf-notice-glyph" aria-hidden="true">{GLYPH[variant]}</span>
    <div className="pf-notice-body">
      {title && <div className="pf-notice-title">{title}</div>}
      {children && <div>{children}</div>}
    </div>
    {action && <div className="pf-notice-action">{action}</div>}
  </div>
);
