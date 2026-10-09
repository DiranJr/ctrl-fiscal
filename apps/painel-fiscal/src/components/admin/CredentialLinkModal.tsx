import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Modal } from '@ctrl-fiscal/ui';
import type { CredentialLink } from '@ctrl-fiscal/api-client';
import { formatDateTimePtBr } from './adminShared.js';

/**
 * Exibe o link de ativação/redefinição UMA única vez. O link vive só no estado deste diálogo (memória):
 * não vai para URL, armazenamento do navegador, toast ou log, e é descartado ao fechar.
 */
export const CredentialLinkModal: React.FC<{
  link: CredentialLink | null;
  personName: string;
  onClose: () => void;
}> = ({ link, personName, onClose }) => {
  const [copied, setCopied] = useState<'ok' | 'fail' | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setCopied(null);
  }, [link]);

  if (!link) return null;
  const fullUrl = `${window.location.origin}${link.path}`;
  const isReset = link.purpose === 'RESET';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fullUrl);
      setCopied('ok');
    } catch {
      // Sem API de área de transferência (ex.: contexto não seguro): seleciona para cópia manual.
      inputRef.current?.focus();
      inputRef.current?.select();
      setCopied('fail');
    }
  };

  return (
    <Modal isOpen onClose={onClose} maxWidth="600px"
      title={isReset ? 'Link de redefinição de senha' : 'Link de ativação da conta'}
      description={`Para: ${personName}`}
      footer={<Button variant="primary" onClick={onClose}>Já copiei, fechar</Button>}>
      <div className="pf-stack">
        <Alert variant="warning" title="Este link é exibido somente agora">
          Ele é de uso único, expira em {formatDateTimePtBr(link.expires_at)} e dá acesso à conta de quem o abrir.
          Envie-o à pessoa por um canal confiável (nunca em grupo ou canal público). Depois de fechar esta janela, não será
          possível vê-lo de novo — se necessário, gere um novo link.
        </Alert>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
          <label htmlFor="pf-credential-link" style={{ fontSize: '0.8125rem', fontWeight: 500 }}>
            {isReset ? 'Link de redefinição' : 'Link de ativação'}
          </label>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <input id="pf-credential-link" ref={inputRef} readOnly value={fullUrl} data-autofocus
              onFocus={(e) => e.currentTarget.select()}
              autoComplete="off" spellCheck={false}
              style={{ flex: '1 1 16rem', minWidth: 0, padding: '0.5rem 0.75rem', fontSize: '0.8125rem', fontFamily: 'var(--font-mono)',
                borderRadius: 'var(--radius-md)', border: '1px solid var(--color-border-hover)', background: 'var(--color-bg-subtle)',
                color: 'var(--color-text-main)' }} />
            <Button variant="secondary" onClick={copy}>Copiar link</Button>
          </div>
          <div aria-live="polite" style={{ fontSize: '0.75rem', minHeight: '1rem',
            color: copied === 'fail' ? 'var(--color-danger)' : 'var(--color-text-muted)' }}>
            {copied === 'ok' && '✓ Link copiado para a área de transferência.'}
            {copied === 'fail' && 'Não foi possível copiar automaticamente. O link está selecionado: use Ctrl+C.'}
          </div>
        </div>
        <dl className="pf-dl">
          <dt>Finalidade</dt>
          <dd>{isReset ? 'Redefinir senha (conta já ativada)' : 'Ativar conta e definir a primeira senha'}</dd>
          <dt>Expira em</dt>
          <dd>{formatDateTimePtBr(link.expires_at)}</dd>
        </dl>
      </div>
    </Modal>
  );
};
