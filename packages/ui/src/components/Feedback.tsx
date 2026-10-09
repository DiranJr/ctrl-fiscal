import React from 'react';

export interface AlertProps {
  variant?: 'info' | 'success' | 'warning' | 'error';
  title?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export const Alert: React.FC<AlertProps> = ({
  variant = 'info',
  title,
  children,
  action,
  className = '',
}) => {
  const styles: Record<string, { bg: string; border: string; text: string; icon: string }> = {
    info: {
      bg: '#EFF6FF',
      border: '#BFDBFE',
      text: '#1E40AF',
      icon: 'ℹ',
    },
    success: {
      bg: '#ECFDF5',
      border: '#A7F3D0',
      text: '#065F46',
      icon: '✓',
    },
    warning: {
      bg: '#FFFBEB',
      border: '#FDE68A',
      text: '#92400E',
      icon: '⚠',
    },
    error: {
      bg: '#FEF2F2',
      border: '#FECACA',
      text: '#991B1B',
      icon: '✕',
    },
  };

  const style = styles[variant];

  return (
    <div
      role="alert"
      style={{
        backgroundColor: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: 'var(--radius-md)',
        padding: '0.875rem 1rem',
        display: 'flex',
        alignItems: 'flex-start',
        gap: '0.75rem',
        fontSize: '0.875rem',
        color: style.text,
      }}
      className={`ui-alert ${className}`}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '1.25rem',
          height: '1.25rem',
          borderRadius: '50%',
          backgroundColor: style.border,
          color: style.text,
          fontSize: '0.75rem',
          fontWeight: 'bold',
          flexShrink: 0,
        }}
      >
        {style.icon}
      </span>
      <div style={{ flex: 1 }}>
        {title && <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>{title}</div>}
        <div style={{ lineHeight: 1.4 }}>{children}</div>
      </div>
      {action && <div>{action}</div>}
    </div>
  );
};

export interface MockBannerProps {
  message?: string;
}

export const MockBanner: React.FC<MockBannerProps> = ({
  message = 'Modo Simulação Contratual (Mock Provider Ativo) — Dados locais aderentes ao Contrato Compartilhado v0.1.',
}) => {
  return (
    <div
      style={{
        backgroundColor: '#FEF3C7',
        borderBottom: '1px solid #FDE68A',
        color: '#92400E',
        padding: '0.375rem 1rem',
        fontSize: '0.75rem',
        fontWeight: 500,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        letterSpacing: '0.01em',
        zIndex: 50,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <span
          style={{
            backgroundColor: '#D97706',
            color: '#FFFFFF',
            padding: '0.1rem 0.35rem',
            borderRadius: '3px',
            fontSize: '0.6875rem',
            fontWeight: 700,
          }}
        >
          MOCK SPRINT 01
        </span>
        <span>{message}</span>
      </div>
      <span style={{ fontSize: '0.6875rem', color: '#B45309' }}>
        Isolamento estrito por empresa • Sem transmissão governamental real
      </span>
    </div>
  );
};

export const Skeleton: React.FC<{ width?: string; height?: string; borderRadius?: string }> = ({
  width = '100%',
  height = '1rem',
  borderRadius = 'var(--radius-sm)',
}) => {
  return (
    <div
      style={{
        width,
        height,
        borderRadius,
        backgroundColor: 'var(--color-bg-muted)',
        animation: 'ui-pulse 1.5s ease-in-out infinite',
      }}
    />
  );
};

export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export const EmptyState: React.FC<EmptyStateProps> = ({ title, description, action }) => {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '3rem 1.5rem',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          width: '3rem',
          height: '3rem',
          borderRadius: '50%',
          backgroundColor: 'var(--color-primary-light)',
          color: 'var(--color-primary)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '1.25rem',
          marginBottom: '1rem',
        }}
      >
        📄
      </div>
      <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--color-text-main)' }}>
        {title}
      </h4>
      {description && (
        <p
          style={{
            margin: '0.5rem 0 1.25rem 0',
            fontSize: '0.875rem',
            color: 'var(--color-text-muted)',
            maxWidth: '400px',
            lineHeight: 1.4,
          }}
        >
          {description}
        </p>
      )}
      {action && <div>{action}</div>}
    </div>
  );
};
