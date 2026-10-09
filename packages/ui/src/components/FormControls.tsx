import React, { useId } from 'react';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Input: React.FC<InputProps> = ({
  label,
  error,
  helperText,
  id,
  className = '',
  style = {},
  ...props
}) => {
  const autoId = useId();
  const inputId = id || autoId;
  const msgId = `${inputId}-msg`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', width: '100%' }}>
      {label && (
        <label
          htmlFor={inputId}
          style={{
            fontSize: '0.8125rem',
            fontWeight: 500,
            color: 'var(--color-text-main)',
          }}
        >
          {label}
          {props.required && <span aria-hidden="true" style={{ color: 'var(--color-danger)' }}> *</span>}
        </label>
      )}
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helperText ? msgId : undefined}
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          fontSize: '0.875rem',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${error ? 'var(--color-danger)' : 'var(--color-border-hover)'}`,
          backgroundColor: '#FFFFFF',
          color: 'var(--color-text-main)',
          boxSizing: 'border-box',
          fontFamily: 'inherit',
          ...style,
        }}
        className={`ui-input ${className}`}
        {...props}
      />
      {/* Espaço da mensagem SEMPRE reservado: erro/ajuda aparecem no mesmo lugar sem deslocar o formulário. */}
      <span id={msgId} role={error ? 'alert' : undefined} className="ui-field-msg"
        style={{ fontSize: '0.75rem', lineHeight: '1rem', minHeight: '1rem',
          color: error ? 'var(--color-danger)' : 'var(--color-text-subtle)', fontWeight: error ? 500 : 400 }}>
        {error || helperText || ''}
      </span>
    </div>
  );
};

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Textarea: React.FC<TextareaProps> = ({
  label,
  error,
  helperText,
  id,
  className = '',
  style = {},
  rows = 3,
  ...props
}) => {
  const autoId = useId();
  const textareaId = id || autoId;
  const msgId = `${textareaId}-msg`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', width: '100%' }}>
      {label && (
        <label
          htmlFor={textareaId}
          style={{
            fontSize: '0.8125rem',
            fontWeight: 500,
            color: 'var(--color-text-main)',
          }}
        >
          {label}
          {props.required && <span aria-hidden="true" style={{ color: 'var(--color-danger)' }}> *</span>}
        </label>
      )}
      <textarea
        id={textareaId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helperText ? msgId : undefined}
        rows={rows}
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          fontSize: '0.875rem',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${error ? 'var(--color-danger)' : 'var(--color-border-hover)'}`,
          backgroundColor: '#FFFFFF',
          color: 'var(--color-text-main)',
          boxSizing: 'border-box',
          fontFamily: 'inherit',
          resize: 'vertical',
          ...style,
        }}
        className={`ui-textarea ${className}`}
        {...props}
      />
      {/* Espaço da mensagem SEMPRE reservado: erro/ajuda aparecem no mesmo lugar sem deslocar o formulário. */}
      <span id={msgId} role={error ? 'alert' : undefined} className="ui-field-msg"
        style={{ fontSize: '0.75rem', lineHeight: '1rem', minHeight: '1rem',
          color: error ? 'var(--color-danger)' : 'var(--color-text-subtle)', fontWeight: error ? 500 : 400 }}>
        {error || helperText || ''}
      </span>
    </div>
  );
};

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  helperText?: string;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
}

export const Select: React.FC<SelectProps> = ({
  label,
  error,
  helperText,
  options,
  id,
  className = '',
  style = {},
  ...props
}) => {
  const autoId = useId();
  const selectId = id || autoId;
  const msgId = `${selectId}-msg`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', width: '100%' }}>
      {label && (
        <label
          htmlFor={selectId}
          style={{
            fontSize: '0.8125rem',
            fontWeight: 500,
            color: 'var(--color-text-main)',
          }}
        >
          {label}
          {props.required && <span aria-hidden="true" style={{ color: 'var(--color-danger)' }}> *</span>}
        </label>
      )}
      <select
        id={selectId}
        aria-invalid={error ? true : undefined}
        aria-describedby={error || helperText ? msgId : undefined}
        style={{
          width: '100%',
          padding: '0.5rem 0.75rem',
          fontSize: '0.875rem',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${error ? 'var(--color-danger)' : 'var(--color-border-hover)'}`,
          backgroundColor: '#FFFFFF',
          color: 'var(--color-text-main)',
          boxSizing: 'border-box',
          fontFamily: 'inherit',
          ...style,
        }}
        className={`ui-select ${className}`}
        {...props}
      >
        {options.map((opt) => (
          <option key={opt.value} value={opt.value} disabled={opt.disabled}>
            {opt.label}
          </option>
        ))}
      </select>
      {/* Espaço da mensagem SEMPRE reservado: erro/ajuda aparecem no mesmo lugar sem deslocar o formulário. */}
      <span id={msgId} role={error ? 'alert' : undefined} className="ui-field-msg"
        style={{ fontSize: '0.75rem', lineHeight: '1rem', minHeight: '1rem',
          color: error ? 'var(--color-danger)' : 'var(--color-text-subtle)', fontWeight: error ? 500 : 400 }}>
        {error || helperText || ''}
      </span>
    </div>
  );
};
