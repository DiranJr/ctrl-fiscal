import { useEffect } from 'react';
import { useRouter } from 'next/router';
import { getApiClients } from '@ctrl-fiscal/api-client';

export default function HomePage() {
  const router = useRouter();
  const clients = getApiClients({ useMock: false });

  useEffect(() => {
    const init = async () => {
      try {
        // Prioridade 1: obter /auth/csrf antes de /auth/refresh
        await clients.core.getCsrf();
        await clients.core.refresh();
        const me = await clients.core.getMe();
        if (me.companies && me.companies.length === 1) {
          window.location.href = `/fiscal/?company_id=${me.companies[0].id}`;
        } else {
          router.replace('/select-company');
        }
      } catch {
        router.replace('/login');
      }
    };
    init();
  }, [clients, router]);

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', backgroundColor: 'var(--color-bg-app)' }}>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem' }}>
        <img src="/marca-ctrl-fiscal.svg" alt="" aria-hidden="true" width={40} height={43} />
        <div style={{ fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
          Carregando CTRL Fiscal...
        </div>
      </div>
    </div>
  );
}
