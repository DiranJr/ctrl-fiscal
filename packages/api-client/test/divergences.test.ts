import test from 'node:test';
import assert from 'node:assert/strict';
import {
  AuthMemoryStore,
  HttpCoreApiClient,
  HttpFiscalApiClient,
  CreateCustomerRequest,
  TaxSettings,
  ProblemDetailsException,
  JobStatusResponse,
} from '../src/index.js';

test('D1 & D2: Refresh e Logout obtêm CSRF antes da chamada e não exigem Bearer no logout', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();

  let csrfCalls = 0;
  let refreshCalls = 0;
  let logoutCalls = 0;
  let lastCsrfSent: string | null = null;
  let logoutHadBearer = false;

  // Mock fetch global
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const headers = new Headers(init?.headers);

    if (url.includes('/auth/csrf')) {
      csrfCalls++;
      return new Response(JSON.stringify({ csrf_token: 'csrf-secret-123' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (url.includes('/auth/refresh')) {
      refreshCalls++;
      lastCsrfSent = headers.get('X-CSRF-Token');
      return new Response(
        JSON.stringify({
          access_token: 'new-token-456',
          token_type: 'Bearer',
          expires_in: 900,
          user: { id: 'u1', name: 'User 1', email: 'u1@test.com' },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    if (url.includes('/auth/logout')) {
      logoutCalls++;
      logoutHadBearer = headers.has('Authorization');
      lastCsrfSent = headers.get('X-CSRF-Token');
      return new Response(null, { status: 204 });
    }

    if (url.includes('/auth/me')) {
      return new Response(
        JSON.stringify({
          id: 'u1',
          name: 'User 1',
          email: 'u1@test.com',
          companies: [{ id: 'c1', name: 'Company 1', cnpj: '11222333000181', timezone: 'America/Sao_Paulo', permissions: ['admin'], environment: 'mock' }],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return new Response('Not Found', { status: 404 });
  };

  try {
    const core = new HttpCoreApiClient('/api/core/v1', store);

    // D1: Chamada de refresh com memória vazia (simulando F5)
    assert.equal(store.getCsrfToken(), null);
    const refreshed = await core.refresh();
    assert.equal(csrfCalls, 1, 'Deve obter CSRF antes de chamar refresh');
    assert.equal(refreshCalls, 1, 'Deve chamar /auth/refresh');
    assert.equal(lastCsrfSent, 'csrf-secret-123', 'Header X-CSRF-Token deve ser enviado no refresh');
    assert.equal(refreshed.access_token, 'new-token-456');

    // D2: Logout sem Bearer
    await core.logout();
    assert.equal(logoutCalls, 1);
    assert.equal(logoutHadBearer, false, 'Logout não deve exigir Bearer token');
    assert.equal(lastCsrfSent, 'csrf-secret-123', 'Logout deve enviar X-CSRF-Token');
    assert.equal(store.getAccessToken(), null, 'Store limpa após logout');
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D3: Cliente fiscal em 401 reutiliza single-flight refresh do core', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('expired-token');
  store.setCsrfToken('csrf-token');

  let fiscalAttempts = 0;
  let refreshCalls = 0;

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const headers = new Headers(init?.headers);

    if (url.includes('/auth/refresh')) {
      refreshCalls++;
      return new Response(
        JSON.stringify({
          access_token: 'fresh-token-789',
          token_type: 'Bearer',
          expires_in: 900,
          user: { id: 'u1', name: 'User 1', email: 'u1@test.com' },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    if (url.includes('/auth/me')) {
      return new Response(
        JSON.stringify({ id: 'u1', name: 'User 1', email: 'u1@test.com', companies: [] }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    if (url.includes('/companies/c1/dashboard')) {
      fiscalAttempts++;
      if (fiscalAttempts === 1) {
        return new Response(JSON.stringify({ title: 'Token expirado', status: 401 }), { status: 401, headers: { 'Content-Type': 'application/json' } });
      }
      assert.equal(headers.get('Authorization'), 'Bearer fresh-token-789');
      return new Response(
        JSON.stringify({
          environment: 'mock',
          simulated: true,
          documents: { issued_total: 10 },
          drafts: { DRAFT: 2 },
          queue: { SCHEDULED: 1 },
          next_scheduled_at: null,
          sync: { status: 'OK', simulated: true },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return new Response('Not Found', { status: 404 });
  };

  try {
    const core = new HttpCoreApiClient('/api/core/v1', store);
    const fiscal = new HttpFiscalApiClient('/api/fiscal/v1', store, core);

    const dash = await fiscal.getDashboard('c1');
    assert.equal(fiscalAttempts, 2, 'Fiscal client deve retentar após refresh bem sucedido');
    assert.equal(refreshCalls, 1, 'Core refresh deve ser chamado 1 vez');
    assert.equal(dash.drafts_count, 2);
    assert.equal(dash.scheduled_count, 1);
    assert.equal(dash.issued_count, 10);
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D4 & D5: CreateCustomer envia payload canônico estrito e sanitizado', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('valid-token');
  store.setCsrfToken('csrf-token');

  let sentBody: any = null;

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    if (url.includes('/customers') && init?.method === 'POST') {
      sentBody = JSON.parse(String(init.body));
      return new Response(
        JSON.stringify({
          id: 'cust-123',
          company_id: 'c1',
          name: sentBody.name,
          document_type: sentBody.document_type,
          document: sentBody.document,
          email: sentBody.email,
          postal_code: sentBody.postal_code,
          address: sentBody.address,
          is_active: true,
          version: 1,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response('Not Found', { status: 404 });
  };

  try {
    const core = new HttpCoreApiClient('/api/core/v1', store);
    const req: CreateCustomerRequest & { phone?: string; cnpj_cpf?: string } = {
      name: 'Cliente Canônico',
      document_type: 'CNPJ',
      document: '11.222.333/0001-81',
      cnpj_cpf: '11.222.333/0001-81', // Campo legado
      phone: '(11) 9999-8888', // Campo extra proibido
      postal_code: '01310-100',
      address: {
        street: 'Av Paulista',
        number: '1000',
        district: 'Bela Vista',
        city: 'São Paulo',
        state: 'SP',
      },
    };

    const customer = await core.createCustomer('c1', req as any);

    assert.equal(sentBody.name, 'Cliente Canônico');
    assert.equal(sentBody.document_type, 'CNPJ');
    assert.equal(sentBody.document, '11222333000181');
    assert.equal(sentBody.postal_code, '01310100');
    assert.equal(sentBody.address.district, 'Bela Vista');
    assert.equal(sentBody.phone, '1199998888', 'Campo phone canônico Sprint 02 deve ser enviado apenas com dígitos');
    assert.equal(sentBody.cnpj_cpf, undefined, 'Campo cnpj_cpf proibido não deve ser enviado');

    // D5: Resposta possui document e cnpj_cpf compatível
    assert.equal(customer.document, '11222333000181');
    assert.equal(customer.cnpj_cpf, '11222333000181');
    assert.equal(customer.version, 1);
    assert.equal(customer.is_active, true);
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D6: Login busca /auth/me para obter empresas do usuário', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url.includes('/auth/login')) {
      return new Response(
        JSON.stringify({
          access_token: 'tok-123',
          token_type: 'Bearer',
          expires_in: 900,
          user: { id: 'u1', name: 'User 1', email: 'u1@test.com' },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    if (url.includes('/auth/csrf')) {
      return new Response(JSON.stringify({ csrf_token: 'csrf-123' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url.includes('/auth/me')) {
      return new Response(
        JSON.stringify({
          id: 'u1',
          name: 'User 1',
          email: 'u1@test.com',
          companies: [
            { id: 'c1', name: 'Empresa A', cnpj: '11222333000181', timezone: 'America/Sao_Paulo', permissions: ['admin'], environment: 'mock' },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    }
    return new Response('Not Found', { status: 404 });
  };

  try {
    const core = new HttpCoreApiClient('/api/core/v1', store);
    const res = await core.login({ email: 'u1@test.com', password: 'password123456' });
    assert.ok(res.user.companies, 'Empresas não devem ser indefinidas');
    assert.equal(res.user.companies.length, 1);
    assert.equal(res.user.companies[0].name, 'Empresa A');
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D8: Dashboard counters calcula campos a partir da estrutura canônica', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('tok');

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (): Promise<Response> => {
    return new Response(
      JSON.stringify({
        environment: 'mock',
        simulated: true,
        documents: {
          issued_total: 42,
          received_total: 15,
          received_pending_review: 3,
          received_divergent: 1,
          cancelled: 2,
        },
        drafts: { DRAFT: 5, REJECTED: 1 },
        queue: { SCHEDULED: 4, PROCESSING: 2, REJECTED: 1 },
        next_scheduled_at: '2026-11-01T00:01:00-03:00',
        sync: {
          status: 'OK',
          last_success_at: '2026-10-07T10:00:00Z',
          simulated: true,
        },
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  };

  try {
    const fiscal = new HttpFiscalApiClient('/api/fiscal/v1', store);
    const d = await fiscal.getDashboard('c1');
    assert.equal(d.drafts_count, 5);
    assert.equal(d.scheduled_count, 6); // 4 SCHEDULED + 2 PROCESSING
    assert.equal(d.issued_count, 42);
    assert.equal(d.received_pending_count, 3);
    assert.equal(d.rejected_count, 1);
    assert.equal(d.last_sync_at, '2026-10-07T10:00:00Z');
    assert.equal(d.simulated, true);
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D12: cancelBatchPending retorna CancelPendingOut canônico', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('tok');
  store.setCsrfToken('csrf');

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (): Promise<Response> => {
    return new Response(
      JSON.stringify({
        batch_id: 'b-99',
        withdrawn: 3,
        not_withdrawn: 0,
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  };

  try {
    const fiscal = new HttpFiscalApiClient('/api/fiscal/v1', store);
    const res = await fiscal.cancelBatchPending('c1', 'b-99');
    assert.equal(res.batch_id, 'b-99');
    assert.equal(res.withdrawn, 3);
    assert.equal(res.not_withdrawn, 0);
    assert.equal(res.cancelled_count, 3); // Compatibilidade
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D13: JobStatusResponse suporta SUCCEEDED, PARTIAL e FAILED', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('tok');

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (): Promise<Response> => {
    return new Response(
      JSON.stringify({
        id: 'job-1',
        type: 'IMPORT',
        status: 'PARTIAL',
        environment: 'mock',
        processed_count: 5,
        error_count: 2,
        errors: [{ file: 'nota_errada.xml', code: 'UNSUPPORTED_LAYOUT', message: 'Leiaute desconhecido' }],
        download_available: true,
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  };

  try {
    const fiscal = new HttpFiscalApiClient('/api/fiscal/v1', store);
    const job = await fiscal.getJobStatus('c1', 'job-1');
    assert.equal(job.status, 'PARTIAL');
    assert.equal(job.error_count, 2);
    assert.equal(job.download_available, true);
    assert.equal(job.errors.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});

test('D18: CreateDraft envia TaxSettings estrito e sanitiza códigos de serviço', async () => {
  const store = AuthMemoryStore.getInstance();
  store.clear();
  store.setAccessToken('tok');
  store.setCsrfToken('csrf');

  let sentBody: any = null;

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    sentBody = JSON.parse(String(init?.body));
    return new Response(
      JSON.stringify({
        id: 'draft-created',
        customer_id: sentBody.customer_id,
        competence: sentBody.competence,
        service: sentBody.service,
        amount: sentBody.amount,
        deductions: sentBody.deductions,
        tax_settings: sentBody.tax_settings,
        status: 'DRAFT',
        version: 1,
        issues: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }),
      { status: 201, headers: { 'Content-Type': 'application/json' } }
    );
  };

  try {
    const fiscal = new HttpFiscalApiClient('/api/fiscal/v1', store);
    const draft = await fiscal.createDraft('c1', {
      customer_id: 'cust-1',
      competence: '2026-10',
      service: {
        national_code: '01.07.01', // Com pontos - deve ser sanitizado para 010701
        municipal_code: '269',
        description: 'Serviço consultoria',
      },
      amount: '1500.00',
      tax_settings: {
        issqn_taxation: 'TAXABLE',
        iss_retention: 'NOT_RETAINED',
        iss_rate: '2.00',
      },
    });

    assert.equal(sentBody.service.national_code, '010701');
    assert.equal(sentBody.tax_settings.issqn_taxation, 'TAXABLE');
    assert.equal(sentBody.tax_settings.iss_retention, 'NOT_RETAINED');
    assert.equal(sentBody.tax_settings.iss_rate, '2.00');
    assert.equal(draft.id, 'draft-created');
  } finally {
    globalThis.fetch = originalFetch;
    store.clear();
  }
});
