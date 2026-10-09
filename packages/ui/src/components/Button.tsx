import React from 'react';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  isLoading?: boolean;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  size = 'md',
  isLoading = false,
  disabled,
  children,
  className = '',
  style = {},
  ...props
}) => {
  const baseStyle: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '0.5rem',
    fontWeight: 500,
    borderRadius: 'var(--radius-md)',
    cursor: disabled || isLoading ? 'not-allowed' : 'pointer',
    opacity: disabled || isLoading ? 0.65 : 1,
    transition: 'background-color var(--transition-fast), border-color var(--transition-fast), transform var(--transition-fast)',
    border: '1px solid transparent',
    fontFamily: 'inherit',
    lineHeight: 1.25,
    textDecoration: 'none',
    ...style,
  };

  const sizeStyles: Record<string, React.CSSProperties> = {
    sm: { padding: '0.375rem 0.625rem', fontSize: '0.8125rem', minHeight: '2rem' },
    md: { padding: '0.5rem 0.875rem', fontSize: '0.875rem', minHeight: '2.375rem' },
    lg: { padding: '0.625rem 1.125rem', fontSize: '1rem' },
  };

  const variantStyles: Record<string, React.CSSProperties> = {
    primary: {
      backgroundColor: 'var(--color-primary)',
      color: '#ffffff',
      borderColor: 'var(--color-primary)',
    },
    secondary: {
      backgroundColor: 'var(--color-primary-light)',
      color: 'var(--color-primary)',
      borderColor: 'var(--color-primary-border)',
    },
    outline: {
      backgroundColor: 'transparent',
      color: 'var(--color-text-main)',
      borderColor: 'var(--color-border)',
    },
    ghost: {
      backgroundColor: 'transparent',
      color: 'var(--color-text-main)',
      borderColor: 'transparent',
    },
    danger: {
      backgroundColor: 'var(--color-danger)',
      color: '#ffffff',
      borderColor: 'var(--color-danger)',
    },
  };

  return (
    <button
      type="button"
      data-variant={variant}
      aria-busy={isLoading || undefined}
      disabled={disabled || isLoading}
      style={{
        ...baseStyle,
        ...sizeStyles[size],
        ...variantStyles[variant],
      }}
      className={`ui-button ${className}`}
      {...props}
    >
      {isLoading && (
        <span
          style={{
            display: 'inline-block',
            width: '1em',
            height: '1em',
            border: '2px solid currentColor',
            borderRightColor: 'transparent',
            borderRadius: '50%',
            animation: 'ui-spin 0.75s linear infinite',
          }}
        />
      )}
      {children}
    </button>
  );
};
