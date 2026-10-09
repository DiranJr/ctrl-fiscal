import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { Card, Button, Input, Alert } from '@ctrl-fiscal/ui';
import { getApiClients, ProblemDetailsException } from '@ctrl-fiscal/api-client';

export default function LoginPage() {
  const router = useRouter();
  const clients = getApiClients({ useMock: false });

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSessionExpired, setIsSessionExpired] = useState(false);

  useEffect(() => {
    if (router.query.expired === 'true') {
      setIsSessionExpired(true);
    }
  }, [router.query]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setErrorMessage('Por favor, informe seu e-mail e senha.');
      return;
    }

    setIsLoading(true);
    setErrorMessage(null);

    try {
      const res = await clients.core.login({ email, password });
      // Token é armazenado em memória no AuthMemoryStore (sem localStorage/sessionStorage)
      if (res.user.companies && res.user.companies.length > 1) {
        router.push('/select-company');
      } else if (res.user.companies && res.user.companies.length === 1) {
        // Redireciona diretamente para o painel fiscal
        window.location.href = `/fiscal/?company_id=${res.user.companies[0].id}`;
      } else {
        router.push('/select-company');
      }
    } catch (err: any) {
      if (err instanceof ProblemDetailsException) {
        setErrorMessage(err.problem.detail || err.problem.title);
      } else {
        setErrorMessage(err.message || 'Falha ao autenticar no servidor.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: 'calc(100vh - 40px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1.5rem',
        backgroundColor: 'var(--color-bg-app)',
      }}
    >
      <div style={{ width: '100%', maxWidth: '440px' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 style={{ margin: 0, display: 'flex', justifyContent: 'center' }}>
            <img src="/logo-ctrl-fiscal.svg" alt="CTRL Fiscal" width={240} height={46} style={{ height: 46, width: 'auto' }} />
          </h1>
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Emissão e Gestão Especializada de NFS-e
          </p>
        </div>

        <Card title="Acesso ao Sistema" subtitle="Digite suas credenciais de usuário para continuar">
          <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
            {isSessionExpired && (
              <Alert variant="warning" title="Sessão Expirada">
                Sua sessão foi encerrada por tempo de inatividade. Por favor, autentique-se novamente.
              </Alert>
            )}

            {errorMessage && (
              <Alert variant="error" title="Erro de Autenticação">
                {errorMessage}
              </Alert>
            )}

            <Input
              id="inputEmail"
              label="E-mail Corporativo"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="seu.email@empresa.com.br"
              required
            />

            <Input
              id="inputPassword"
              label="Senha de Acesso"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
              required
            />

            <Button id="btnSubmitLogin" type="submit" variant="primary" size="lg" isLoading={isLoading}>
              Entrar no Sistema
            </Button>

            <div
              style={{
                padding: '0.75rem',
                backgroundColor: 'var(--color-bg-subtle)',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.75rem',
                color: 'var(--color-text-subtle)',
                textAlign: 'center',
              }}
            >
              🔒 Token JWT mantido exclusivamente em memória com renovação HttpOnly.
              Nenhum dado sensível é persistido em localStorage.
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
