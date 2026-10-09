import React, { useEffect, useRef, useState } from 'react';
import Head from 'next/head';
import { Card, Button, Input, Alert } from '@ctrl-fiscal/ui';
import { getApiClients, ProblemDetailsException } from '@ctrl-fiscal/api-client';

/**
 * Ativação de conta / redefinição de senha a partir do link de uso único ("/ativar#token=...").
 * O token vem no fragmento (nunca vai ao servidor na navegação), é lido uma vez, removido da barra de endereço
 * e mantido só em memória até o envio. Nada é registrado em log nem gravado no navegador.
 */

const MIN_LENGTH = 12;
const MIN_DISTINCT = 6;

type Phase = 'reading' | 'missing' | 'form' | 'invalid' | 'done';

function messageFor(err: unknown): { text: string; invalidLink?: boolean } {
  if (err instanceof ProblemDetailsException) {
    const { status, code, detail } = err.problem;
    if (status === 400 || code === 'ACTIVATION_INVALID') {
      return { text: 'Este link é inválido, expirou ou já foi utilizado. Peça um novo link ao administrador.', invalidLink: true };
    }
    if (status === 429 || code === 'ACTIVATION_RATE_LIMITED') {
      return { text: 'Muitas tentativas em pouco tempo. Aguarde alguns minutos e tente novamente.' };
    }
    if (code === 'PASSWORD_WEAK') {
      return { text: 'Escolha uma senha mais forte: no mínimo 12 caracteres, variada e diferente do seu e-mail.' };
    }
    if (status === 422) {
      const summary = err.formatErrorsSummary();
      return { text: summary.length ? `Verifique a senha: ${summary.join('; ')}` : detail || 'Senha não aceita. Revise as regras abaixo.' };
    }
    if (status === 403) {
      return { text: 'Não foi possível validar a origem da solicitação. Abra o link diretamente no navegador e tente de novo.' };
    }
    return { text: 'Não foi possível definir a senha agora. Tente novamente em instantes.' };
  }
  return { text: 'Falha de comunicação com o servidor. Verifique sua conexão e tente novamente.' };
}

const Rule: React.FC<{ ok: boolean; children: React.ReactNode }> = ({ ok, children }) => (
  <li style={{ display: 'flex', gap: '0.5rem', alignItems: 'baseline', color: ok ? 'var(--status-authorized-text)' : 'var(--color-text-muted)' }}>
    <span aria-hidden="true" style={{ fontWeight: 700, width: '1rem', display: 'inline-block' }}>{ok ? '✓' : '•'}</span>
    <span>
      {children}
      <span className="ui-sr-only">{ok ? ' (atendido)' : ' (pendente)'}</span>
    </span>
  </li>
);

export default function AtivarPage() {
  const clients = getApiClients({ useMock: false });
  const tokenRef = useRef<string | null>(null);
  const readOnce = useRef(false);

  const [phase, setPhase] = useState<Phase>('reading');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (readOnce.current) return; // StrictMode executa o efeito duas vezes em desenvolvimento
    readOnce.current = true;
    const hash = window.location.hash.replace(/^#/, '');
    const token = new URLSearchParams(hash).get('token');
    // Remove o token da barra de endereço/histórico imediatamente (fica só em memória).
    if (window.location.hash) {
      window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
    }
    if (token && token.trim()) {
      tokenRef.current = token.trim();
      setPhase('form');
    } else {
      setPhase('missing');
    }
    return undefined;
  }, []);

  const longEnough = password.length >= MIN_LENGTH;
  const varied = new Set(password).size >= MIN_DISTINCT;
  const matches = password.length > 0 && password === confirmation;
  const valid = longEnough && varied && matches;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    setErrorMessage(null);
    if (!valid) {
      setErrorMessage(!matches ? 'A confirmação não confere com a senha.' : 'A senha ainda não atende às regras abaixo.');
      return;
    }
    const token = tokenRef.current;
    if (!token) {
      setPhase('missing');
      return;
    }
    setIsLoading(true);
    try {
      await clients.core.activateAccount(token, password);
      tokenRef.current = null;
      setPassword('');
      setConfirmation('');
      setPhase('done');
    } catch (err) {
      const m = messageFor(err);
      if (m.invalidLink) {
        tokenRef.current = null;
        setPassword('');
        setConfirmation('');
        setPhase('invalid');
      }
      setErrorMessage(m.text);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      <Head>
        <title>Definir senha — CTRL Fiscal</title>
        <meta name="referrer" content="no-referrer" />
        <meta name="robots" content="noindex, nofollow" />
      </Head>
      <div
        style={{
          minHeight: 'calc(100vh - 40px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '1.5rem 1rem',
          backgroundColor: 'var(--color-bg-app)',
        }}
      >
        <main style={{ width: '100%', maxWidth: '440px' }}>
          <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
            <h1 style={{ margin: 0, display: 'flex', justifyContent: 'center' }}>
              <img src="/logo-ctrl-fiscal.svg" alt="CTRL Fiscal" width={240} height={46} style={{ height: 46, width: 'auto' }} />
            </h1>
            <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
              Emissão e Gestão Especializada de NFS-e
            </p>
          </div>

          {phase === 'reading' && (
            <Card title="Definir senha">
              <p aria-busy="true" style={{ margin: 0, fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>Verificando o link…</p>
            </Card>
          )}

          {(phase === 'missing' || phase === 'invalid') && (
            <Card title="Link inválido">
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <Alert variant="error" title={phase === 'invalid' ? 'Não foi possível usar este link' : 'Link incompleto'}>
                  {phase === 'invalid'
                    ? errorMessage || 'Este link é inválido, expirou ou já foi utilizado. Peça um novo link ao administrador.'
                    : 'O link de ativação está incompleto ou já foi aberto nesta aba. Abra novamente o link completo recebido do administrador ou peça um novo.'}
                </Alert>
                <Button variant="outline" onClick={() => { window.location.href = '/login'; }}>Ir para o login</Button>
              </div>
            </Card>
          )}

          {phase === 'done' && (
            <Card title="Senha definida">
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                <Alert variant="success" title="Tudo certo">
                  Sua senha foi definida. Entre com o seu e-mail e a nova senha.
                </Alert>
                <Button variant="primary" size="lg" onClick={() => { window.location.href = '/login'; }}>
                  Ir para o login
                </Button>
              </div>
            </Card>
          )}

          {phase === 'form' && (
            <Card title="Definir senha" subtitle="Crie a senha de acesso à sua conta">
              <form onSubmit={handleSubmit} noValidate style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                {errorMessage && (
                  <Alert variant="error" title="Não foi possível definir a senha">
                    {errorMessage}
                  </Alert>
                )}

                <Input
                  id="inputNewPassword"
                  label="Nova senha"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  minLength={MIN_LENGTH}
                  required
                  // Mantém a mensagem de erro do campo (id "<campo>-msg") e acrescenta as regras.
                  aria-describedby={touched && !(longEnough && varied) ? 'inputNewPassword-msg password-rules' : 'password-rules'}
                  error={touched && !(longEnough && varied) ? 'A senha não atende às regras.' : undefined}
                />

                <Input
                  id="inputConfirmPassword"
                  label="Confirmar nova senha"
                  type={showPassword ? 'text' : 'password'}
                  value={confirmation}
                  onChange={(e) => setConfirmation(e.target.value)}
                  autoComplete="new-password"
                  required
                  error={touched && confirmation.length > 0 && !matches ? 'A confirmação não confere.' : undefined}
                />

                <label htmlFor="inputShowPassword" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8125rem',
                  color: 'var(--color-text-main)', cursor: 'pointer' }}>
                  <input id="inputShowPassword" type="checkbox" checked={showPassword} onChange={(e) => setShowPassword(e.target.checked)} />
                  Mostrar senha
                </label>

                <div id="password-rules" style={{ padding: '0.75rem', backgroundColor: 'var(--color-bg-subtle)', borderRadius: 'var(--radius-md)',
                  fontSize: '0.8125rem' }}>
                  <div style={{ fontWeight: 600, marginBottom: '0.375rem', color: 'var(--color-text-main)' }}>Regras da senha</div>
                  <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                    <Rule ok={longEnough}>Pelo menos {MIN_LENGTH} caracteres</Rule>
                    <Rule ok={varied}>Variada (pelo menos {MIN_DISTINCT} caracteres diferentes)</Rule>
                    <Rule ok={matches}>Confirmação igual à senha</Rule>
                  </ul>
                  <p style={{ margin: '0.5rem 0 0 0', color: 'var(--color-text-subtle)' }}>
                    Não use o seu e-mail como senha. Prefira uma frase longa que só você conheça.
                  </p>
                </div>

                <Button id="btnSubmitActivation" type="submit" variant="primary" size="lg" isLoading={isLoading}>
                  Definir senha
                </Button>
              </form>
            </Card>
          )}
        </main>
      </div>
    </>
  );
}
