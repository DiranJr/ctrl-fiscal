import React, { useEffect, useId, useRef } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])';

/** Pilha de diálogos abertos: Escape e Tab agem só no diálogo do topo (ex.: visualizador sobre o painel da nota). */
const dialogStack: object[] = [];

/**
 * Foco controlado para diálogos: foca o primeiro elemento (ou o painel) ao abrir, mantém o Tab dentro do diálogo,
 * fecha com Escape e devolve o foco ao elemento que o abriu.
 */
export function useDialogFocus(isOpen: boolean, onClose: () => void, panelRef: React.RefObject<HTMLElement>) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!isOpen) return;
    const token = {};
    dialogStack.push(token);
    const previously = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>('[data-autofocus]') || panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first || panel)?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== token) return; // não é o diálogo do topo
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab' || !panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (items.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      const i = dialogStack.indexOf(token);
      if (i >= 0) dialogStack.splice(i, 1);
      document.body.style.overflow = prevOverflow;
      if (previously && typeof previously.focus === 'function') previously.focus();
    };
  }, [isOpen, panelRef]);
}

const closeButtonStyle: React.CSSProperties = {
  background: 'none',
  border: '1px solid transparent',
  borderRadius: 'var(--radius-md)',
  fontSize: '1.25rem',
  lineHeight: 1,
  cursor: 'pointer',
  color: 'var(--color-text-muted)',
  padding: '0.25rem 0.5rem',
  minWidth: '2rem',
  minHeight: '2rem',
};

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  maxWidth?: string;
}

export const Modal: React.FC<ModalProps> = ({ isOpen, onClose, title, description, children, footer, maxWidth = '550px' }) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descId = useId();
  useDialogFocus(isOpen, onClose, panelRef);
  if (!isOpen) return null;
  return (
    <div
      style={{
        position: 'fixed', inset: 0, backgroundColor: 'rgba(31, 42, 48, 0.55)', display: 'flex', alignItems: 'center',
        justifyContent: 'center', zIndex: 1000, padding: '1rem',
      }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        style={{
          backgroundColor: 'var(--color-bg-card)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-xl)',
          width: '100%', maxWidth, maxHeight: '90vh', display: 'flex', flexDirection: 'column', overflow: 'hidden',
          border: '1px solid var(--color-border)', outline: 'none',
        }}
      >
        <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--color-border)', display: 'flex',
          alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem' }}>
          <div>
            <h2 id={titleId} style={{ margin: 0, fontSize: '1.0625rem', fontWeight: 600, color: 'var(--color-text-main)' }}>{title}</h2>
            {description && (
              <p id={descId} style={{ margin: '0.25rem 0 0 0', fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>{description}</p>
            )}
          </div>
          <button type="button" onClick={onClose} aria-label="Fechar" style={closeButtonStyle}>&times;</button>
        </div>
        <div style={{ padding: '1.25rem', overflowY: 'auto', flex: 1 }}>{children}</div>
        {footer && (
          <div style={{ padding: '0.875rem 1.25rem', borderTop: '1px solid var(--color-border)',
            backgroundColor: 'var(--color-bg-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
            gap: '0.75rem', flexWrap: 'wrap' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};

export interface DrawerProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  width?: string;
  /** 'right' (padrão) para detalhes; 'left' para navegação no celular. */
  side?: 'left' | 'right';
}

export const Drawer: React.FC<DrawerProps> = ({ isOpen, onClose, title, subtitle, children, footer, width = '500px', side = 'right' }) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(isOpen, onClose, panelRef);
  if (!isOpen) return null;
  return (
    <div
      style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(31, 42, 48, 0.45)', zIndex: 1000, display: 'flex',
        justifyContent: side === 'left' ? 'flex-start' : 'flex-end' }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{
          width: '100%', maxWidth: width, height: '100%', backgroundColor: 'var(--color-bg-card)', boxShadow: 'var(--shadow-xl)',
          display: 'flex', flexDirection: 'column', outline: 'none',
          [side === 'left' ? 'borderRight' : 'borderLeft']: '1px solid var(--color-border)',
        }}
      >
        <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid var(--color-border)', display: 'flex',
          alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem' }}>
          <div style={{ minWidth: 0 }}>
            <h2 id={titleId} style={{ margin: 0, fontSize: '1.0625rem', fontWeight: 600, color: 'var(--color-text-main)' }}>{title}</h2>
            {subtitle && <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>{subtitle}</p>}
          </div>
          <button type="button" onClick={onClose} aria-label="Fechar painel" style={closeButtonStyle}>&times;</button>
        </div>
        <div style={{ padding: '1.25rem', overflowY: 'auto', flex: 1 }}>{children}</div>
        {footer && (
          <div style={{ padding: '0.875rem 1.25rem', borderTop: '1px solid var(--color-border)',
            backgroundColor: 'var(--color-bg-subtle)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
            gap: '0.75rem', flexWrap: 'wrap' }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};
