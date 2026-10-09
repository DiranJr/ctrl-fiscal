import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import { Card, Button } from '@ctrl-fiscal/ui';
import { getApiClients, Company, User } from '@ctrl-fiscal/api-client';

export default function SelectCompanyPage() {
  const router = useRouter();
  const clients = getApiClients({ useMock: false });

  const [user, setUser] = useState<User | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const init = async () => {
      try {
        // Prioridade 1: obter /auth/csrf antes de /auth/refresh
        await clients.core.getCsrf();
        await clients.core.refresh();
        const me = await clients.core.getMe();
        // Um único workspace: entra direto (o seletor só existe com mais de um).
        if ((me.companies || []).length === 1) {
          window.location.href = `/fiscal/?company_id=${me.companies[0].id}`;
          return;
        }
        setUser(me);
        setCompanies(me.companies || []);
      } catch {
        router.push('/login');
      } finally {
        setIsLoading(false);
      }
    };
    init();
  }, [clients, router]);

  const handleSelectCompany = (companyId: string) => {
    // Encaminhamento oficial para /fiscal/ com o parâmetro da empresa autorizada
    window.location.href = `/fiscal/?company_id=${companyId}`;
  };

  if (isLoading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '80vh' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--color-primary)' }}>
          <span
            style={{
              width: '1.5rem',
              height: '1.5rem',
              border: '2px solid currentColor',
              borderRightColor: 'transparent',
              borderRadius: '50%',
              animation: 'spin 0.75s linear infinite',
            }}
          />
          Verificando empresas autorizadas...
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: 'calc(100vh - 40px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '2rem 1rem',
      }}
    >
      <div style={{ width: '100%', maxWidth: '650px' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0, color: 'var(--color-text-main)' }}>
            Selecione o workspace
          </h1>
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Olá, <strong>{user?.name}</strong>. Cada workspace corresponde a uma empresa (CNPJ). Escolha em qual deseja trabalhar:
          </p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {companies.map((c) => (
            <Card
              key={c.id}
              style={{
                cursor: 'pointer',
                transition: 'border-color var(--transition-fast), transform var(--transition-fast)',
              }}
              onClick={() => handleSelectCompany(c.id)}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                  <div style={{ fontSize: '1.0625rem', fontWeight: 700, color: 'var(--color-primary)' }}>
                    {c.name}
                  </div>
                  <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)', marginTop: '0.25rem' }}>
                    CNPJ: <span style={{ fontFamily: 'var(--font-mono)' }}>{c.cnpj}</span> • Fuso: {c.timezone}
                  </div>
                  <div style={{ display: 'flex', gap: '0.35rem', marginTop: '0.5rem', flexWrap: 'wrap' }}>
                    {c.permissions.slice(0, 3).map((p) => (
                      <span
                        key={p}
                        style={{
                          fontSize: '0.6875rem',
                          backgroundColor: 'var(--color-bg-subtle)',
                          padding: '0.125rem 0.375rem',
                          borderRadius: '4px',
                          color: 'var(--color-text-subtle)',
                        }}
                      >
                        {p.replace('_', ' ')}
                      </span>
                    ))}
                    {c.permissions.length > 3 && (
                      <span style={{ fontSize: '0.6875rem', color: 'var(--color-text-subtle)' }}>
                        +{c.permissions.length - 3} permissões
                      </span>
                    )}
                  </div>
                </div>

                <Button variant="primary" size="sm" onClick={() => handleSelectCompany(c.id)}>
                  Acessar Painel Fiscal ➔
                </Button>
              </div>
            </Card>
          ))}
        </div>

        <div style={{ textAlign: 'center', marginTop: '1.5rem' }}>
          <Button variant="ghost" size="sm" onClick={() => router.push('/login')}>
            ← Trocar de Usuário
          </Button>
        </div>
      </div>
    </div>
  );
}
