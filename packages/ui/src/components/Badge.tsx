import React from 'react';

export type FiscalStatusType =
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

const statusConfig: Record<
  FiscalStatusType,
  { label: string; bg: string; text: string; border: string }
> = {
  AUTHORIZED: {
    label: 'Autorizada',
    bg: 'var(--status-authorized-bg)',
    text: 'var(--status-authorized-text)',
    border: 'var(--status-authorized-border)',
  },
  SCHEDULED: {
    label: 'Agendada',
    bg: 'var(--status-scheduled-bg)',
    text: 'var(--status-scheduled-text)',
    border: 'var(--status-scheduled-border)',
  },
  PROCESSING: {
    label: 'Processando',
    bg: 'var(--status-processing-bg)',
    text: 'var(--status-processing-text)',
    border: 'var(--status-processing-border)',
  },
  DRAFT: {
    label: 'Rascunho',
    bg: 'var(--status-draft-bg)',
    text: 'var(--status-draft-text)',
    border: 'var(--status-draft-border)',
  },
  REJECTED: {
    label: 'Rejeitada',
    bg: 'var(--status-rejected-bg)',
    text: 'var(--status-rejected-text)',
    border: 'var(--status-rejected-border)',
  },
  AWAITING_CONFIRMATION: {
    label: 'Aguardando confirmação',
    bg: 'var(--status-processing-bg)',
    text: 'var(--status-processing-text)',
    border: 'var(--status-processing-border)',
  },
  WITHDRAWN: {
    label: 'Retirada da fila',
    bg: 'var(--status-withdrawn-bg)',
    text: 'var(--status-withdrawn-text)',
    border: 'var(--status-withdrawn-border)',
  },
  PENDING: {
    label: 'Pendente',
    bg: 'var(--status-processing-bg)',
    text: 'var(--status-processing-text)',
    border: 'var(--status-processing-border)',
  },
  REVIEWED: {
    label: 'Conferida',
    bg: 'var(--status-authorized-bg)',
    text: 'var(--status-authorized-text)',
    border: 'var(--status-authorized-border)',
  },
  DIVERGENT: {
    label: 'Divergente',
    bg: 'var(--status-divergent-bg)',
    text: 'var(--status-divergent-text)',
    border: 'var(--status-divergent-border)',
  },
  CANCELLED: {
    label: 'Cancelada',
    bg: 'var(--status-rejected-bg)',
    text: 'var(--status-rejected-text)',
    border: 'var(--status-rejected-border)',
  },
  UNKNOWN: {
    label: 'Desconhecida',
    bg: 'var(--status-withdrawn-bg)',
    text: 'var(--status-withdrawn-text)',
    border: 'var(--status-withdrawn-border)',
  },
  VALID: {
    label: 'Válido',
    bg: 'var(--status-authorized-bg)',
    text: 'var(--status-authorized-text)',
    border: 'var(--status-authorized-border)',
  },
  EXPIRED: {
    label: 'Expirado',
    bg: 'var(--status-rejected-bg)',
    text: 'var(--status-rejected-text)',
    border: 'var(--status-rejected-border)',
  },
  NOT_CONFIGURED: {
    label: 'Não Configurado',
    bg: 'var(--status-withdrawn-bg)',
    text: 'var(--status-withdrawn-text)',
    border: 'var(--status-withdrawn-border)',
  },
};

/** Ícone textual por estado: o status nunca depende só da cor (WCAG 1.4.1). */
const STATUS_ICON: Record<string, string> = {
  AUTHORIZED: '✓', REVIEWED: '✓', VALID: '✓', SUCCEEDED: '✓', OK: '✓',
  CANCELLED: '✕', REJECTED: '✕', EXPIRED: '✕', FAILED: '✕', ERROR: '✕',
  SCHEDULED: '◷', QUEUED: '◷',
  PROCESSING: '↻', RUNNING: '↻', AWAITING_CONFIRMATION: '↻', PENDING: '◔',
  DIVERGENT: '!', PARTIAL: '!', WARNING: '!',
  DRAFT: '✎', WITHDRAWN: '↩', UNKNOWN: '?', NOT_CONFIGURED: '?', INFO: 'i',
};

export interface BadgeProps {
  status: FiscalStatusType | string;
  customLabel?: string;
  size?: 'sm' | 'md';
  /** Ícone explícito; padrão = ícone do estado. Use '' para omitir. */
  icon?: string;
}

export const Badge: React.FC<BadgeProps> = ({ status, customLabel, size = 'sm', icon }) => {
  const config = (statusConfig as any)[status] || {
    label: status,
    bg: 'var(--status-draft-bg)',
    text: 'var(--status-draft-text)',
    border: 'var(--status-draft-border)',
  };

  const isSmall = size === 'sm';

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: isSmall ? '0.125rem 0.5rem' : '0.25rem 0.625rem',
        fontSize: isSmall ? '0.75rem' : '0.8125rem',
        fontWeight: 600,
        borderRadius: 'var(--radius-full)',
        backgroundColor: config.bg,
        color: config.text,
        border: `1px solid ${config.border}`,
        whiteSpace: 'nowrap',
        lineHeight: 1.25,
      }}
    >
      {(icon ?? STATUS_ICON[status] ?? '•') !== '' && (
        <span aria-hidden="true" style={{ marginRight: '0.3rem', fontWeight: 700 }}>{icon ?? STATUS_ICON[status] ?? '•'}</span>
      )}
      {customLabel || config.label}
    </span>
  );
};
