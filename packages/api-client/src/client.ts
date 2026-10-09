import {
  User,
  Company,
  LoginRequest,
  LoginResponse,
  TokenResponse,
  CsrfResponse,
  Customer,
  CreateCustomerRequest,
  UpdateCustomerRequest,
  IntegrationStatus,
  FiscalProfileOut,
  FiscalProfilePatch,
  CertificateInfo,
  FiscalDocument,
  DocumentFilter,
  ReviewDocumentRequest,
  Draft,
  CreateDraftRequest,
  UpdateDraftRequest,
  CreateDraftFromDocumentRequest,
  ValidateDraftResponse,
  Batch,
  CreateBatchRequest,
  CancelPendingOut,
  SyncStatus,
  SyncRunResponse,
  JobStatusResponse,
  DashboardCounters,
  PaginatedResponse,
  ProblemDetails,
  ProblemDetailsException,
  BundleRequest,
  IbsCbsPreview,
  TaxClassificationPage,
  RecipientSuggestion,
  CancellationCreateRequest,
  CancellationInfo,
  CancellationRequestOut,
  DownloadTicket,
  RetentionPreviewRequest,
  RetentionSummary,
  ValidationIssue,
  AdminMeta,
  AdminCompany,
  AdminUser,
  AdminUserFilter,
  CreateAdminUserRequest,
  CreateAdminUserResponse,
  UpdateAdminUserRequest,
  CredentialLink,
  Workspace,
  Readiness,
  ServiceProfile,
  ServiceProfileInput,
  ServiceProfilePatch,
  ProposalResolveRequest,
  ServiceInventory,
  CertificateStatus,
  ServiceProfileVersionOut,
  PrepareProfilesResult,
  ProviderDataFromDocuments,
  ProviderDataApplyResult,
} from './types.js';
import { validateServiceCodes } from './formatters.js';

export interface ICoreApiClient {
  login(req: LoginRequest): Promise<LoginResponse>;
  getCsrf(): Promise<CsrfResponse>;
  refresh(): Promise<LoginResponse>;
  logout(): Promise<void>;
  getMe(): Promise<User & { companies: Company[] }>;
  getCompanies(): Promise<PaginatedResponse<Company>>;
  getCustomers(companyId: string): Promise<PaginatedResponse<Customer>>;
  createCustomer(companyId: string, req: CreateCustomerRequest): Promise<Customer>;
  updateCustomer(companyId: string, customerId: string, req: UpdateCustomerRequest): Promise<Customer>;
  getIntegrationStatus(companyId: string, environment?: string): Promise<IntegrationStatus>;
  getFiscalProfile(companyId: string): Promise<FiscalProfileOut>;
  updateFiscalProfile(companyId: string, patch: FiscalProfilePatch): Promise<FiscalProfileOut>;
  uploadCertificate(companyId: string, file: File, password: string, environments?: string[]): Promise<CertificateInfo>;
  deleteCertificate(companyId: string, environment?: string): Promise<void>;
  // Workspaces (um por empresa/CNPJ)
  getWorkspaces(): Promise<Workspace[]>;
  getReadiness(companyId: string): Promise<Readiness>;
  getCertificateStatus(companyId: string): Promise<CertificateStatus>;
  listServiceProfiles(companyId: string): Promise<ServiceProfile[]>;
  createServiceProfile(companyId: string, req: ServiceProfileInput): Promise<ServiceProfile>;
  updateServiceProfile(companyId: string, profileId: string, req: ServiceProfilePatch): Promise<ServiceProfile>;
  approveServiceProfile(companyId: string, profileId: string, expectedVersion: number, validFrom?: string | null, note?: string | null): Promise<ServiceProfile>;
  setServiceProfileActive(companyId: string, profileId: string, active: boolean, expectedVersion: number): Promise<ServiceProfile>;
  getServiceProfileVersions(companyId: string, profileId: string): Promise<ServiceProfileVersionOut[]>;
  resolveServiceProfileProposal(companyId: string, profileId: string, proposalId: string, req: ProposalResolveRequest): Promise<ServiceProfile>;
  // Administração de usuários
  getAdminMeta(): Promise<AdminMeta>;
  getAdminCompanies(): Promise<AdminCompany[]>;
  listAdminUsers(filter?: AdminUserFilter): Promise<PaginatedResponse<AdminUser>>;
  getAdminUser(userId: string): Promise<AdminUser>;
  createAdminUser(req: CreateAdminUserRequest): Promise<CreateAdminUserResponse>;
  updateAdminUser(userId: string, req: UpdateAdminUserRequest): Promise<AdminUser>;
  setAdminMembership(userId: string, companyId: string, permissions: string[]): Promise<AdminUser>;
  removeAdminMembership(userId: string, companyId: string): Promise<AdminUser>;
  setSuperAdmin(userId: string, enabled: boolean): Promise<AdminUser>;
  revokeUserSessions(userId: string): Promise<AdminUser>;
  /** Link de ativação/redefinição (exibir uma única vez; nunca armazenar nem registrar em log). */
  issueCredentialLink(userId: string): Promise<CredentialLink>;
  /** Define a senha a partir do token de ativação/redefinição (público; 204 em caso de sucesso). */
  activateAccount(token: string, password: string): Promise<void>;
}

export interface IFiscalApiClient {
  getDashboard(companyId: string, environment?: string): Promise<DashboardCounters>;
  getDocuments(companyId: string, filter?: DocumentFilter): Promise<PaginatedResponse<FiscalDocument>>;
  getDocument(companyId: string, documentId: string): Promise<FiscalDocument>;
  getDocumentXml(companyId: string, documentId: string): Promise<string>;
  importDocuments(companyId: string, files: File[]): Promise<{ job_id: string; status: 'QUEUED' }>;
  reviewDocument(companyId: string, documentId: string, req: ReviewDocumentRequest): Promise<FiscalDocument>;
  getLastIssuedDocument(companyId: string, customerId: string): Promise<FiscalDocument>;
  /** Idempotency-Key obrigatória: a mesma chave (clique repetido, nova tentativa após timeout) devolve o MESMO rascunho. */
  createDraftFromDocument(companyId: string, req: CreateDraftFromDocumentRequest, idempotencyKey: string): Promise<Draft>;
  getDocumentRecipient(companyId: string, documentId: string): Promise<RecipientSuggestion>;
  getTaxClassifications(companyId: string, on: string): Promise<TaxClassificationPage>;
  /** Cria perfis PENDENTES a partir das notas emitidas (idempotente; não sobrescreve). */
  prepareServiceProfiles(companyId: string): Promise<PrepareProfilesResult>;
  /** Serviços prestados lidos das notas emitidas (somente leitura). */
  getServiceInventory(companyId: string): Promise<ServiceInventory>;
  getProviderDataFromDocuments(companyId: string): Promise<ProviderDataFromDocuments>;
  /** Completa só campos vazios do cadastro; divergências voltam sem alteração. */
  applyProviderDataFromDocuments(companyId: string): Promise<ProviderDataApplyResult>;
  getIbsCbsPreview(companyId: string, draftId: string): Promise<IbsCbsPreview>;
  getDrafts(companyId: string, page?: number, pageSize?: number): Promise<PaginatedResponse<Draft>>;
  getDraft(companyId: string, draftId: string): Promise<Draft>;
  createDraft(companyId: string, req: CreateDraftRequest): Promise<Draft>;
  updateDraft(companyId: string, draftId: string, req: UpdateDraftRequest): Promise<Draft>;
  withdrawDraft(companyId: string, draftId: string, expectedVersion: number): Promise<Draft>;
  validateDraft(companyId: string, draftId: string): Promise<ValidateDraftResponse>;
  /** Registra (ou reutiliza a aprovada) a configuração tributária da operação do rascunho. Não aprova nem emite. */
  registerOperationConfig(companyId: string, draftId: string, expectedVersion: number): Promise<Draft>;
  createBatch(companyId: string, req: CreateBatchRequest, idempotencyKey: string): Promise<{ batch_id: string; status: 'SCHEDULED'; mode?: 'NOW' | 'SCHEDULED'; scheduled_at: string; total: number }>;
  getBatches(companyId: string, page?: number, pageSize?: number): Promise<PaginatedResponse<Batch>>;
  getBatch(companyId: string, batchId: string): Promise<Batch>;
  cancelBatchPending(companyId: string, batchId: string): Promise<CancelPendingOut>;
  getSyncStatus(companyId: string, environment?: string): Promise<SyncStatus>;
  triggerSync(companyId: string, idempotencyKey: string, environment?: string): Promise<SyncRunResponse>;
  getJobStatus(companyId: string, jobId: string): Promise<JobStatusResponse>;
  exportDocuments(companyId: string, filter: DocumentFilter, format: 'xlsx'): Promise<{ job_id: string; status: 'QUEUED' }>;
  downloadJobFile(companyId: string, jobId: string): Promise<Blob>;
  getDanfsePdf(companyId: string, documentId: string): Promise<Blob>;
  /** XML original byte a byte (para download), sem decodificar/recodificar. */
  getDocumentXmlBlob(companyId: string, documentId: string): Promise<Blob>;
  getCancellationInfo(companyId: string, documentId: string): Promise<CancellationInfo>;
  requestCancellation(companyId: string, documentId: string, req: CancellationCreateRequest, idempotencyKey: string): Promise<CancellationRequestOut>;
  getCancellation(companyId: string, requestId: string): Promise<CancellationRequestOut>;
  createBundle(companyId: string, req: BundleRequest): Promise<{ job_id: string; status: 'QUEUED' }>;
  createDownloadTicket(companyId: string, jobId: string): Promise<DownloadTicket>;
  /** Baixa o arquivo do job direto para o disco (POST de formulário com ticket de uso único; sem token na URL). */
  saveJobFileToDisk(companyId: string, jobId: string): Promise<void>;
  previewRetentions(companyId: string, req: RetentionPreviewRequest): Promise<{ retention_summary: RetentionSummary; issues: ValidationIssue[] }>;
}

export class AuthMemoryStore {
  private static instance: AuthMemoryStore;
  private accessToken: string | null = null;
  private csrfToken: string | null = null;
  private refreshPromise: Promise<string | null> | null = null;

  private constructor() {}

  public static getInstance(): AuthMemoryStore {
    if (!AuthMemoryStore.instance) {
      AuthMemoryStore.instance = new AuthMemoryStore();
    }
    return AuthMemoryStore.instance;
  }

  public setAccessToken(token: string | null) {
    this.accessToken = token;
  }

  public getAccessToken(): string | null {
    return this.accessToken;
  }

  public setCsrfToken(token: string | null) {
    this.csrfToken = token;
  }

  public getCsrfToken(): string | null {
    return this.csrfToken;
  }

  public clear() {
    this.accessToken = null;
    this.csrfToken = null;
    this.refreshPromise = null;
  }

  public async singleFlightRefresh(refreshFn: () => Promise<string | null>): Promise<string | null> {
    if (this.refreshPromise) {
      return this.refreshPromise;
    }
    this.refreshPromise = (async () => {
      try {
        const token = await refreshFn();
        this.accessToken = token;
        return token;
      } finally {
        this.refreshPromise = null;
      }
    })();
    return this.refreshPromise;
  }
}

export class HttpCoreApiClient implements ICoreApiClient {
  constructor(
    private baseUrl: string = '/api/core/v1',
    private store: AuthMemoryStore = AuthMemoryStore.getInstance()
  ) {}

  private async request<T>(
    path: string,
    options: RequestInit = {},
    requiresAuth: boolean = true
  ): Promise<T> {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');

    if (requiresAuth && this.store.getAccessToken()) {
      headers.set('Authorization', `Bearer ${this.store.getAccessToken()}`);
    }

    const csrf = this.store.getCsrfToken();
    if (csrf && (options.method === 'POST' || options.method === 'PATCH' || options.method === 'PUT' || options.method === 'DELETE')) {
      headers.set('X-CSRF-Token', csrf);
    }

    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    const fetchOptions: RequestInit = {
      ...options,
      headers,
      credentials: 'include', // Necessário para cookies HttpOnly de refresh
    };

    let response = await fetch(`${this.baseUrl}${path}`, fetchOptions);

    // Se 401 e requer autenticação, tenta refresh single-flight (D1 / D3)
    if (response.status === 401 && requiresAuth && !path.includes('/auth/login') && !path.includes('/auth/refresh')) {
      const newToken = await this.store.singleFlightRefresh(async () => {
        try {
          const res = await this.refresh();
          return res.access_token;
        } catch {
          this.store.clear();
          return null;
        }
      });

      if (newToken) {
        headers.set('Authorization', `Bearer ${newToken}`);
        const currentCsrf = this.store.getCsrfToken();
        if (currentCsrf && (options.method === 'POST' || options.method === 'PATCH' || options.method === 'PUT' || options.method === 'DELETE')) {
          headers.set('X-CSRF-Token', currentCsrf);
        }
        response = await fetch(`${this.baseUrl}${path}`, {
          ...options,
          headers,
          credentials: 'include',
        });
      }
    }

    if (!response.ok) {
      let problem: ProblemDetails;
      try {
        problem = await response.json();
      } catch {
        problem = {
          type: 'about:blank',
          title: `Erro HTTP ${response.status}`,
          status: response.status,
          detail: response.statusText,
        };
      }
      throw new ProblemDetailsException(problem);
    }

    if (response.status === 204) {
      return undefined as unknown as T;
    }

    return (await response.json()) as T;
  }

  // D6: TokenResponse -> buscar /auth/me para obter empresas do usuário
  async login(req: LoginRequest): Promise<LoginResponse> {
    const res = await this.request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(req),
    }, false);
    this.store.setAccessToken(res.access_token);
    try {
      const csrf = await this.getCsrf();
      this.store.setCsrfToken(csrf.csrf_token);
    } catch {
      // Falha ao obter csrf inicial não bloqueia retorno de login
    }

    let companies: Company[] = [];
    try {
      const me = await this.getMe();
      companies = me.companies || [];
    } catch {
      // Empresas serão carregadas pelo /auth/me na transição
    }

    return {
      access_token: res.access_token,
      token_type: res.token_type || 'Bearer',
      expires_in: res.expires_in,
      user: {
        id: res.user.id,
        name: res.user.name,
        email: res.user.email,
        companies,
      },
    };
  }

  // D1: Obter CSRF (autenticado somente via cookie HttpOnly)
  async getCsrf(): Promise<CsrfResponse> {
    const res = await this.request<CsrfResponse>('/auth/csrf', { method: 'GET' }, false);
    this.store.setCsrfToken(res.csrf_token);
    return res;
  }

  // D1: Obter /auth/csrf antes de /auth/refresh quando não houver CSRF na memória
  async refresh(): Promise<LoginResponse> {
    if (!this.store.getCsrfToken()) {
      try {
        await this.getCsrf();
      } catch {
        // Prossegue: se o cookie for inválido, o refresh falhará apropriadamente
      }
    }
    const res = await this.request<TokenResponse>('/auth/refresh', {
      method: 'POST',
    }, false);
    this.store.setAccessToken(res.access_token);

    let companies: Company[] = [];
    try {
      const me = await this.getMe();
      companies = me.companies || [];
    } catch {
      // Empresas mantidas pelo caller
    }

    return {
      access_token: res.access_token,
      token_type: res.token_type || 'Bearer',
      expires_in: res.expires_in,
      user: {
        id: res.user.id,
        name: res.user.name,
        email: res.user.email,
        companies,
      },
    };
  }

  // D2: Logout exige X-CSRF-Token e Origin, não Bearer (requiresAuth: false)
  async logout(): Promise<void> {
    try {
      if (!this.store.getCsrfToken()) {
        try {
          await this.getCsrf();
        } catch {
          // ignore
        }
      }
      await this.request<void>('/auth/logout', { method: 'POST' }, false);
    } finally {
      this.store.clear();
    }
  }

  async getMe(): Promise<User & { companies: Company[] }> {
    return this.request<User & { companies: Company[] }>('/auth/me');
  }

  async getCompanies(): Promise<PaginatedResponse<Company>> {
    return this.request<PaginatedResponse<Company>>('/companies');
  }

  // D5: Customer canonical fields
  async getCustomers(companyId: string): Promise<PaginatedResponse<Customer>> {
    const res = await this.request<PaginatedResponse<Customer>>(`/companies/${companyId}/customers`);
    const mapped = res.items.map((c) => ({
      ...c,
      cnpj_cpf: c.document, // Compatibilidade retroativa de leitura
    }));
    return {
      ...res,
      items: mapped,
    };
  }

  // D4 & Sprint 02: CreateCustomerRequest canônico (inclui phone opcional)
  async createCustomer(companyId: string, req: CreateCustomerRequest): Promise<Customer> {
    const cleanAddress = req.address
      ? {
          street: req.address.street || null,
          number: req.address.number || null,
          complement: req.address.complement || null,
          district: req.address.district || null,
          city: req.address.city || null,
          state: req.address.state || null,
        }
      : null;

    const rawDoc = req.document || (req as any).cnpj_cpf || '';
    const cleanDoc = typeof rawDoc === 'string' ? rawDoc.replace(/\D/g, '') : '';
    const docType = req.document_type || (cleanDoc.length > 11 ? 'CNPJ' : 'CPF');

    const payload: Record<string, any> = {
      name: req.name.trim(),
      document_type: docType,
      document: cleanDoc,
    };

    if (req.email !== undefined) payload.email = req.email ? req.email.trim() : null;
    if (req.phone !== undefined) payload.phone = req.phone ? req.phone.replace(/\D/g, '') : null;
    if (req.municipal_registration !== undefined) payload.municipal_registration = req.municipal_registration ? req.municipal_registration.trim() : null;
    if (req.municipality_code !== undefined) payload.municipality_code = req.municipality_code ? req.municipality_code.trim() : null;
    if (req.postal_code !== undefined) payload.postal_code = req.postal_code ? req.postal_code.replace(/\D/g, '') : null;
    if (cleanAddress) payload.address = cleanAddress;

    const created = await this.request<Customer>(`/companies/${companyId}/customers`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    return {
      ...created,
      cnpj_cpf: created.document,
    };
  }

  // D4 & Sprint 02: UpdateCustomerRequest canônico
  async updateCustomer(companyId: string, customerId: string, req: UpdateCustomerRequest): Promise<Customer> {
    const payload: Record<string, any> = {};

    if (req.name !== undefined) payload.name = req.name ? req.name.trim() : null;
    if (req.email !== undefined) payload.email = req.email ? req.email.trim() : null;
    if (req.phone !== undefined) payload.phone = req.phone ? req.phone.replace(/\D/g, '') : null;
    if (req.municipal_registration !== undefined) payload.municipal_registration = req.municipal_registration ? req.municipal_registration.trim() : null;
    if (req.municipality_code !== undefined) payload.municipality_code = req.municipality_code ? req.municipality_code.trim() : null;
    if (req.postal_code !== undefined) payload.postal_code = req.postal_code ? req.postal_code.replace(/\D/g, '') : null;
    if (req.is_active !== undefined) payload.is_active = req.is_active;
    if (req.expected_version !== undefined) payload.expected_version = req.expected_version;

    if (req.address !== undefined) {
      payload.address = req.address
        ? {
            street: req.address.street || null,
            number: req.address.number || null,
            complement: req.address.complement || null,
            district: req.address.district || null,
            city: req.address.city || null,
            state: req.address.state || null,
          }
        : null;
    }

    const updated = await this.request<Customer>(`/companies/${companyId}/customers/${customerId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });

    return {
      ...updated,
      cnpj_cpf: updated.document,
    };
  }

  // D7 & Sprint 02: IntegrationStatus canonical (com suporte a ?environment=)
  async getIntegrationStatus(companyId: string, environment?: string): Promise<IntegrationStatus> {
    const qs = environment ? `?environment=${encodeURIComponent(environment)}` : '';
    return this.request<IntegrationStatus>(`/companies/${companyId}/integration-status${qs}`);
  }

  async getWorkspaces(): Promise<Workspace[]> {
    return this.request<Workspace[]>('/workspaces');
  }

  async approveServiceProfile(companyId: string, profileId: string, expectedVersion: number, validFrom?: string | null, note?: string | null): Promise<ServiceProfile> {
    return this.request<ServiceProfile>(`/companies/${companyId}/service-profiles/${profileId}/approve`, {
      method: 'POST', body: JSON.stringify({ expected_version: expectedVersion, valid_from: validFrom || null, note: note || null }),
    });
  }

  async setServiceProfileActive(companyId: string, profileId: string, active: boolean, expectedVersion: number): Promise<ServiceProfile> {
    return this.request<ServiceProfile>(`/companies/${companyId}/service-profiles/${profileId}/${active ? 'reactivate' : 'inactivate'}`, {
      method: 'POST', body: JSON.stringify({ expected_version: expectedVersion }),
    });
  }

  async getServiceProfileVersions(companyId: string, profileId: string): Promise<ServiceProfileVersionOut[]> {
    return this.request<ServiceProfileVersionOut[]>(`/companies/${companyId}/service-profiles/${profileId}/versions`);
  }

  async resolveServiceProfileProposal(companyId: string, profileId: string, proposalId: string, req: ProposalResolveRequest): Promise<ServiceProfile> {
    return this.request<ServiceProfile>(`/companies/${companyId}/service-profiles/${profileId}/proposals/${proposalId}/resolve`, {
      method: 'POST', body: JSON.stringify(req),
    });
  }

  async getReadiness(companyId: string): Promise<Readiness> {
    return this.request<Readiness>(`/companies/${companyId}/readiness`);
  }

  async getCertificateStatus(companyId: string): Promise<CertificateStatus> {
    return this.request<CertificateStatus>(`/companies/${companyId}/certificates`);
  }

  async listServiceProfiles(companyId: string): Promise<ServiceProfile[]> {
    return this.request<ServiceProfile[]>(`/companies/${companyId}/service-profiles`);
  }

  async createServiceProfile(companyId: string, req: ServiceProfileInput): Promise<ServiceProfile> {
    return this.request<ServiceProfile>(`/companies/${companyId}/service-profiles`, { method: 'POST', body: JSON.stringify(req) });
  }

  async updateServiceProfile(companyId: string, profileId: string, req: ServiceProfilePatch): Promise<ServiceProfile> {
    return this.request<ServiceProfile>(`/companies/${companyId}/service-profiles/${profileId}`, {
      method: 'PATCH', body: JSON.stringify(req),
    });
  }

  // Sprint 02: Perfil Fiscal da Empresa (GET)
  async getFiscalProfile(companyId: string): Promise<FiscalProfileOut> {
    return this.request<FiscalProfileOut>(`/companies/${companyId}/fiscal-profile`);
  }

  // Sprint 02: Perfil Fiscal da Empresa (PATCH)
  async updateFiscalProfile(companyId: string, patch: FiscalProfilePatch): Promise<FiscalProfileOut> {
    return this.request<FiscalProfileOut>(`/companies/${companyId}/fiscal-profile`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
  }

  // Sprint 02: Anexar Certificado A1 (POST multipart/form-data)
  // Nota: A UI nunca deve armazenar a senha em estado persistente nem em log.
  async uploadCertificate(
    companyId: string,
    file: File,
    password: string,
    environments?: string[]
  ): Promise<CertificateInfo> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('password', password);
    if (environments && environments.length > 0) {
      formData.append('environments', environments.join(','));
    }
    return this.request<CertificateInfo>(`/companies/${companyId}/certificate`, {
      method: 'POST',
      body: formData,
    });
  }

  // Sprint 02: Desativar Certificado A1 de um ambiente (DELETE)
  async deleteCertificate(companyId: string, environment?: string): Promise<void> {
    const env = environment || 'restricted';
    const qs = `?environment=${encodeURIComponent(env)}`;
    return this.request<void>(`/companies/${companyId}/certificate${qs}`, {
      method: 'DELETE',
    });
  }

  // ---------------------------------------------------------------------------
  // Administração de usuários
  // ---------------------------------------------------------------------------

  async getAdminMeta(): Promise<AdminMeta> {
    return this.request<AdminMeta>('/admin/meta');
  }

  async getAdminCompanies(): Promise<AdminCompany[]> {
    const res = await this.request<AdminCompany[] | { items: AdminCompany[] }>('/admin/companies');
    return Array.isArray(res) ? res : res?.items || [];
  }

  async listAdminUsers(filter: AdminUserFilter = {}): Promise<PaginatedResponse<AdminUser>> {
    const params = new URLSearchParams();
    if (filter.query) params.set('query', filter.query);
    if (filter.status) params.set('status', filter.status);
    if (filter.company_id) params.set('company_id', filter.company_id);
    if (filter.permission) params.set('permission', filter.permission);
    if (filter.super_admin !== undefined) params.set('super_admin', filter.super_admin ? 'true' : 'false');
    if (filter.page) params.set('page', String(filter.page));
    if (filter.page_size) params.set('page_size', String(filter.page_size));
    const qs = params.toString() ? `?${params.toString()}` : '';
    return this.request<PaginatedResponse<AdminUser>>(`/admin/users${qs}`);
  }

  async getAdminUser(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`);
  }

  async createAdminUser(req: CreateAdminUserRequest): Promise<CreateAdminUserResponse> {
    return this.request<CreateAdminUserResponse>('/admin/users', {
      method: 'POST',
      body: JSON.stringify({
        name: req.name.trim(),
        email: req.email.trim(),
        memberships: req.memberships,
      }),
    });
  }

  async updateAdminUser(userId: string, req: UpdateAdminUserRequest): Promise<AdminUser> {
    const payload: Record<string, unknown> = { expected_version: req.expected_version };
    if (req.name !== undefined) payload.name = req.name.trim();
    if (req.email !== undefined) payload.email = req.email.trim();
    if (req.is_active !== undefined) payload.is_active = req.is_active;
    return this.request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async setAdminMembership(userId: string, companyId: string, permissions: string[]): Promise<AdminUser> {
    return this.request<AdminUser>(
      `/admin/users/${encodeURIComponent(userId)}/memberships/${encodeURIComponent(companyId)}`,
      { method: 'PUT', body: JSON.stringify({ permissions }) }
    );
  }

  async removeAdminMembership(userId: string, companyId: string): Promise<AdminUser> {
    return this.request<AdminUser>(
      `/admin/users/${encodeURIComponent(userId)}/memberships/${encodeURIComponent(companyId)}`,
      { method: 'DELETE' }
    );
  }

  async setSuperAdmin(userId: string, enabled: boolean): Promise<AdminUser> {
    return this.request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}/super-admin`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    });
  }

  async revokeUserSessions(userId: string): Promise<AdminUser> {
    return this.request<AdminUser>(`/admin/users/${encodeURIComponent(userId)}/revoke-sessions`, { method: 'POST' });
  }

  async issueCredentialLink(userId: string): Promise<CredentialLink> {
    return this.request<CredentialLink>(`/admin/users/${encodeURIComponent(userId)}/credential-link`, { method: 'POST' });
  }

  // Público: sem Bearer; o navegador envia Origin automaticamente em POST same-origin.
  async activateAccount(token: string, password: string): Promise<void> {
    await this.request<void>('/auth/activate', {
      method: 'POST',
      body: JSON.stringify({ token, password }),
    }, false);
  }
}

export class HttpFiscalApiClient implements IFiscalApiClient {
  private coreClient?: ICoreApiClient;

  constructor(
    private baseUrl: string = '/api/fiscal/v1',
    private store: AuthMemoryStore = AuthMemoryStore.getInstance(),
    coreClient?: ICoreApiClient
  ) {
    this.coreClient = coreClient || new HttpCoreApiClient(this.baseUrl.replace(/\/fiscal\/v1$/, '/core/v1'), this.store);
  }

  /** Requisição autenticada que devolve a Response crua (PDF/XML/arquivos), com renovação de sessão em 401. */
  private async requestRaw(companyId: string, path: string, options: RequestInit = {}, accept = '*/*'): Promise<Response> {
    const doFetch = (token: string | null) => {
      const headers = new Headers(options.headers || {});
      headers.set('Accept', accept);
      if (token) headers.set('Authorization', `Bearer ${token}`);
      const csrf = this.store.getCsrfToken();
      if (csrf && options.method && options.method !== 'GET') headers.set('X-CSRF-Token', csrf);
      return fetch(`${this.baseUrl}/companies/${companyId}${path}`, { ...options, headers, credentials: 'include' });
    };
    let res = await doFetch(this.store.getAccessToken());
    if (res.status === 401 && this.coreClient) {
      const newToken = await this.store.singleFlightRefresh(async () => {
        try {
          return (await this.coreClient!.refresh()).access_token;
        } catch {
          this.store.clear();
          return null;
        }
      });
      if (newToken) res = await doFetch(newToken);
    }
    if (!res.ok) {
      let problem: ProblemDetails;
      try {
        problem = await res.json();
      } catch {
        problem = { type: 'about:blank', title: `Erro HTTP ${res.status}`, status: res.status, detail: res.statusText };
      }
      throw new ProblemDetailsException(problem);
    }
    return res;
  }

  private async request<T>(
    companyId: string,
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');

    const token = this.store.getAccessToken();
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const csrf = this.store.getCsrfToken();
    if (csrf && (options.method === 'POST' || options.method === 'PATCH' || options.method === 'PUT' || options.method === 'DELETE')) {
      headers.set('X-CSRF-Token', csrf);
    }

    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    const url = `${this.baseUrl}/companies/${companyId}${path}`;
    let response = await fetch(url, {
      ...options,
      headers,
      credentials: 'include',
    });

    // D3: Fiscal cliente recebendo 401 reutiliza single-flight do core
    if (response.status === 401 && this.coreClient) {
      const newToken = await this.store.singleFlightRefresh(async () => {
        try {
          const refreshed = await this.coreClient!.refresh();
          return refreshed.access_token;
        } catch {
          this.store.clear();
          return null;
        }
      });

      if (newToken) {
        headers.set('Authorization', `Bearer ${newToken}`);
        const newCsrf = this.store.getCsrfToken();
        if (newCsrf && (options.method === 'POST' || options.method === 'PATCH' || options.method === 'PUT' || options.method === 'DELETE')) {
          headers.set('X-CSRF-Token', newCsrf);
        }
        response = await fetch(url, {
          ...options,
          headers,
          credentials: 'include',
        });
      }
    }

    if (response.status === 401) {
      let problem: ProblemDetails;
      try {
        problem = await response.json();
      } catch {
        problem = {
          type: 'about:blank',
          title: 'Sessão Expirada',
          status: 401,
          detail: 'Por favor, autentique-se novamente.',
        };
      }
      throw new ProblemDetailsException(problem);
    }

    if (!response.ok) {
      let problem: ProblemDetails;
      try {
        problem = await response.json();
      } catch {
        problem = {
          type: 'about:blank',
          title: `Erro HTTP ${response.status}`,
          status: response.status,
          detail: response.statusText,
        };
      }
      throw new ProblemDetailsException(problem);
    }

    if (response.status === 204) {
      return undefined as unknown as T;
    }

    return (await response.json()) as T;
  }

  // D8 & Sprint 02: DashboardCounters com suporte a ?environment=
  async getDashboard(companyId: string, environment?: string): Promise<DashboardCounters> {
    const qs = environment ? `?environment=${encodeURIComponent(environment)}` : '';
    const raw = await this.request<any>(companyId, `/dashboard${qs}`);
    const draftsCount = (raw.drafts?.['DRAFT'] || 0);
    const scheduledCount = (raw.queue?.['SCHEDULED'] || 0) + (raw.queue?.['PROCESSING'] || 0);
    const issuedCount = raw.documents?.issued_total ?? 0;
    const receivedPendingCount = raw.documents?.received_pending_review ?? 0;
    const rejectedCount = raw.queue?.['REJECTED'] || 0;
    const lastSyncAt = raw.sync?.last_success_at ?? null;

    return {
      environment: raw.environment || 'mock',
      simulated: !!raw.simulated,
      documents: raw.documents || {},
      drafts: raw.drafts || {},
      queue: raw.queue || {},
      next_scheduled_at: raw.next_scheduled_at || null,
      sync: raw.sync || {
        enabled: false,
        interval_hours: 6,
        last_success_at: null,
        last_attempt_at: null,
        next_run_at: null,
        status: 'NOT_CONFIGURED',
        environment: 'mock',
        simulated: true,
        processed_count: 0,
        received_count: 0,
        pending_parse_count: 0,
        coverage_message: '',
        last_error: null,
        running: false,
      },
      drafts_count: raw.drafts_count ?? draftsCount,
      scheduled_count: raw.scheduled_count ?? scheduledCount,
      issued_count: raw.issued_count ?? issuedCount,
      rejected_count: raw.rejected_count ?? rejectedCount,
      received_pending_count: raw.received_pending_count ?? receivedPendingCount,
      last_sync_at: raw.last_sync_at ?? lastSyncAt,
    };
  }

  // D16/D17 & Sprint 02: FiscalDocument com filtro por ambiente
  async getDocuments(companyId: string, filter?: DocumentFilter): Promise<PaginatedResponse<FiscalDocument>> {
    const params = new URLSearchParams();
    if (filter) {
      if (filter.direction) params.set('direction', filter.direction);
      if (filter.query) params.set('query', filter.query);
      if (filter.status) params.set('status', filter.status);
      if (filter.review_status) params.set('review_status', filter.review_status);
      if (filter.competence) params.set('competence', filter.competence);
      if (filter.date_from) params.set('date_from', filter.date_from);
      if (filter.date_to) params.set('date_to', filter.date_to);
      if (filter.page) params.set('page', String(filter.page));
      if (filter.page_size) params.set('page_size', String(filter.page_size));
      if (filter.environment) params.set('environment', filter.environment);
      if (filter.retention) params.set('retention', filter.retention);
      for (const t of filter.retention_type || []) params.append('retention_type', t);
      if (filter.retention_match) params.set('retention_match', filter.retention_match);
      if (filter.withheld_min) params.set('withheld_min', filter.withheld_min);
      if (filter.withheld_max) params.set('withheld_max', filter.withheld_max);
    }
    const queryStr = params.toString() ? `?${params.toString()}` : '';
    return this.request<PaginatedResponse<FiscalDocument>>(companyId, `/documents${queryStr}`);
  }

  async getDocument(companyId: string, documentId: string): Promise<FiscalDocument> {
    return this.request<FiscalDocument>(companyId, `/documents/${documentId}`);
  }

  async getDocumentXml(companyId: string, documentId: string): Promise<string> {
    const res = await this.requestRaw(companyId, `/documents/${documentId}/xml`, {}, 'application/xml');
    return res.text();
  }

  async getDocumentXmlBlob(companyId: string, documentId: string): Promise<Blob> {
    const res = await this.requestRaw(companyId, `/documents/${documentId}/xml`, {}, 'application/xml');
    return res.blob();
  }

  async getDanfsePdf(companyId: string, documentId: string): Promise<Blob> {
    const res = await this.requestRaw(companyId, `/documents/${documentId}/danfse`, {}, 'application/pdf');
    return res.blob();
  }

  async createBundle(companyId: string, req: BundleRequest): Promise<{ job_id: string; status: 'QUEUED' }> {
    return this.request<{ job_id: string; status: 'QUEUED' }>(companyId, '/documents/bundles', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async getCancellationInfo(companyId: string, documentId: string): Promise<CancellationInfo> {
    return this.request<CancellationInfo>(companyId, `/documents/${documentId}/cancellation`);
  }

  /** Mesma chave de idempotência nas repetições do MESMO pedido (clique duplo, nova tentativa após falha de rede). */
  async requestCancellation(companyId: string, documentId: string, req: CancellationCreateRequest,
    idempotencyKey: string): Promise<CancellationRequestOut> {
    return this.request<CancellationRequestOut>(companyId, `/documents/${documentId}/cancellation`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(req),
    });
  }

  async getCancellation(companyId: string, requestId: string): Promise<CancellationRequestOut> {
    return this.request<CancellationRequestOut>(companyId, `/cancellations/${requestId}`);
  }

  async createDownloadTicket(companyId: string, jobId: string): Promise<DownloadTicket> {
    return this.request<DownloadTicket>(companyId, `/jobs/${jobId}/download-ticket`, { method: 'POST' });
  }

  async saveJobFileToDisk(companyId: string, jobId: string): Promise<void> {
    const t = await this.createDownloadTicket(companyId, jobId);
    // O navegador grava a resposta (Content-Disposition: attachment) direto no disco, sem carregar o arquivo na
    // memória da página. O ticket vai no CORPO do POST (nunca na URL), vale 60 s e uma única vez.
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = t.action;
    form.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = t.field;
    input.value = t.ticket;
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    form.remove();
  }

  async previewRetentions(companyId: string, req: RetentionPreviewRequest): Promise<{ retention_summary: RetentionSummary; issues: ValidationIssue[] }> {
    return this.request(companyId, '/retention-preview', { method: 'POST', body: JSON.stringify(req) });
  }

  async importDocuments(companyId: string, files: File[]): Promise<{ job_id: string; status: 'QUEUED' }> {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files', file);
    }
    return this.request<{ job_id: string; status: 'QUEUED' }>(companyId, '/documents/import', {
      method: 'POST',
      body: formData,
    });
  }

  async reviewDocument(companyId: string, documentId: string, req: ReviewDocumentRequest): Promise<FiscalDocument> {
    return this.request<FiscalDocument>(companyId, `/documents/${documentId}/review`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async getLastIssuedDocument(companyId: string, customerId: string): Promise<FiscalDocument> {
    return this.request<FiscalDocument>(companyId, `/customers/${customerId}/last-issued-document`);
  }

  async createDraftFromDocument(companyId: string, req: CreateDraftFromDocumentRequest, idempotencyKey: string): Promise<Draft> {
    return this.request<Draft>(companyId, '/drafts/from-document', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(req),
    });
  }

  async prepareServiceProfiles(companyId: string): Promise<PrepareProfilesResult> {
    return this.request<PrepareProfilesResult>(companyId, '/service-profiles/prepare', { method: 'POST' });
  }

  async getServiceInventory(companyId: string): Promise<ServiceInventory> {
    return this.request<ServiceInventory>(companyId, '/service-inventory');
  }

  async getProviderDataFromDocuments(companyId: string): Promise<ProviderDataFromDocuments> {
    return this.request<ProviderDataFromDocuments>(companyId, '/provider-data-from-documents');
  }

  async applyProviderDataFromDocuments(companyId: string): Promise<ProviderDataApplyResult> {
    return this.request<ProviderDataApplyResult>(companyId, '/provider-data-from-documents/apply', { method: 'POST' });
  }

  async getTaxClassifications(companyId: string, on: string): Promise<TaxClassificationPage> {
    return this.request<TaxClassificationPage>(companyId, `/tax-classifications?on=${encodeURIComponent(on)}`);
  }

  async getIbsCbsPreview(companyId: string, draftId: string): Promise<IbsCbsPreview> {
    return this.request<IbsCbsPreview>(companyId, `/drafts/${draftId}/ibs-cbs-preview`);
  }

  async getDocumentRecipient(companyId: string, documentId: string): Promise<RecipientSuggestion> {
    return this.request<RecipientSuggestion>(companyId, `/documents/${documentId}/recipient`);
  }

  async getDrafts(companyId: string, page = 1, pageSize = 25): Promise<PaginatedResponse<Draft>> {
    return this.request<PaginatedResponse<Draft>>(companyId, `/drafts?page=${page}&page_size=${pageSize}`);
  }

  async getDraft(companyId: string, draftId: string): Promise<Draft> {
    return this.request<Draft>(companyId, `/drafts/${draftId}`);
  }

  // D18 & Sprint 02: TaxSettings e ServiceIn com campos aditivos e validação estrita
  async createDraft(companyId: string, req: CreateDraftRequest): Promise<Draft> {
    if (req.service) {
      const check = validateServiceCodes(req.service.national_code, req.service.municipal_code);
      if (!check.valid) {
        throw new ProblemDetailsException({
          type: 'urn:problem:validation-error',
          title: 'Erro de Validação',
          status: 422,
          detail: check.error || 'Código de serviço inválido.',
        });
      }
    }

    const payload: Record<string, any> = {
      customer_id: req.customer_id,
      competence: req.competence,
      service: {
        description: req.service.description.trim(),
      },
      amount: req.amount,
      deductions: req.deductions || '0.00',
    };

    if (req.service_date) {
      payload.service_date = req.service_date;
    }

    if (req.service.national_code) {
      payload.service.national_code = req.service.national_code.replace(/\D/g, '') || null;
    }
    if (req.service.municipal_code) {
      payload.service.municipal_code = req.service.municipal_code.replace(/\D/g, '').padStart(3, '0') || null;
    }
    if (req.service.nbs_code) {
      payload.service.nbs_code = req.service.nbs_code.replace(/\D/g, '') || null;
    }
    if (req.service.location_code) {
      payload.service.location_code = req.service.location_code.replace(/\D/g, '') || null;
    }
    if (req.service.obra_code) {
      payload.service.obra_code = req.service.obra_code.trim();
    } else if (req.service.obra_address) {
      payload.service.obra_address = {
        postal_code: req.service.obra_address.postal_code.replace(/\D/g, ''),
        street: req.service.obra_address.street.trim(),
        number: req.service.obra_address.number.trim(),
        complement: req.service.obra_address.complement ? req.service.obra_address.complement.trim() : null,
        district: req.service.obra_address.district.trim(),
      };
    }

    if (req.tax_settings) {
      const ts: Record<string, any> = {};
      if (req.tax_settings.issqn_taxation !== undefined) ts.issqn_taxation = req.tax_settings.issqn_taxation;
      if (req.tax_settings.iss_retention !== undefined) ts.iss_retention = req.tax_settings.iss_retention;
      if (req.tax_settings.iss_rate !== undefined) ts.iss_rate = req.tax_settings.iss_rate;
      if (req.tax_settings.pis_cofins_retention !== undefined) ts.pis_cofins_retention = req.tax_settings.pis_cofins_retention;
      if (req.tax_settings.irrf_withheld !== undefined) ts.irrf_withheld = req.tax_settings.irrf_withheld;
      if (req.tax_settings.inss_withheld !== undefined) ts.inss_withheld = req.tax_settings.inss_withheld;
      if (req.tax_settings.csll_withheld !== undefined) ts.csll_withheld = req.tax_settings.csll_withheld;
      if (req.tax_settings.pis_cofins_cst !== undefined) ts.pis_cofins_cst = req.tax_settings.pis_cofins_cst;
      if (req.tax_settings.pis_cofins_base !== undefined) ts.pis_cofins_base = req.tax_settings.pis_cofins_base;
      if (req.tax_settings.pis_rate !== undefined) ts.pis_rate = req.tax_settings.pis_rate;
      if (req.tax_settings.cofins_rate !== undefined) ts.cofins_rate = req.tax_settings.cofins_rate;
      if (req.tax_settings.pis_amount !== undefined) ts.pis_amount = req.tax_settings.pis_amount;
      if (req.tax_settings.cofins_amount !== undefined) ts.cofins_amount = req.tax_settings.cofins_amount;
      if (req.tax_settings.approx_tax) ts.approx_tax = req.tax_settings.approx_tax;
      if (req.tax_settings.ibs_cbs) ts.ibs_cbs = req.tax_settings.ibs_cbs;
      payload.tax_settings = ts;
    }

    return this.request<Draft>(companyId, '/drafts', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // D18 & Sprint 02: UpdateDraft com campos aditivos
  async updateDraft(companyId: string, draftId: string, req: UpdateDraftRequest): Promise<Draft> {
    const payload: Record<string, any> = {
      expected_version: req.expected_version,
    };

    if (req.customer_id !== undefined) payload.customer_id = req.customer_id;
    if (req.competence !== undefined) payload.competence = req.competence;
    if (req.service_date !== undefined) payload.service_date = req.service_date;
    if (req.amount !== undefined) payload.amount = req.amount;
    if (req.deductions !== undefined) payload.deductions = req.deductions;

    if (req.unconditional_discount !== undefined) payload.unconditional_discount = req.unconditional_discount;
    if (req.conditional_discount !== undefined) payload.conditional_discount = req.conditional_discount;

    if (req.service !== undefined) {
      const check = validateServiceCodes(req.service.national_code, req.service.municipal_code);
      if (!check.valid) {
        throw new ProblemDetailsException({
          type: 'urn:problem:validation-error',
          title: 'Erro de Validação',
          status: 422,
          detail: check.error || 'Código de serviço inválido.',
        });
      }

      const sPayload: Record<string, any> = {
        description: req.service.description.trim(),
      };
      if (req.service.national_code !== undefined) {
        sPayload.national_code = req.service.national_code ? req.service.national_code.replace(/\D/g, '') || null : null;
      }
      if (req.service.municipal_code !== undefined) {
        sPayload.municipal_code = req.service.municipal_code ? req.service.municipal_code.replace(/\D/g, '').padStart(3, '0') || null : null;
      }
      if (req.service.nbs_code !== undefined) {
        sPayload.nbs_code = req.service.nbs_code ? req.service.nbs_code.replace(/\D/g, '') || null : null;
      }
      if (req.service.location_code !== undefined) {
        sPayload.location_code = req.service.location_code ? req.service.location_code.replace(/\D/g, '') || null : null;
      }
      if (req.service.obra_code !== undefined) {
        sPayload.obra_code = req.service.obra_code ? req.service.obra_code.trim() : null;
      }
      if (req.service.obra_address !== undefined) {
        sPayload.obra_address = req.service.obra_address
          ? {
              postal_code: req.service.obra_address.postal_code.replace(/\D/g, ''),
              street: req.service.obra_address.street.trim(),
              number: req.service.obra_address.number.trim(),
              complement: req.service.obra_address.complement ? req.service.obra_address.complement.trim() : null,
              district: req.service.obra_address.district.trim(),
            }
          : null;
      }
      payload.service = sPayload;
    }

    if (req.tax_settings !== undefined) {
      if (!req.tax_settings) {
        payload.tax_settings = null;
      } else {
        const ts: Record<string, any> = {};
        if (req.tax_settings.issqn_taxation !== undefined) ts.issqn_taxation = req.tax_settings.issqn_taxation;
        if (req.tax_settings.immunity_type !== undefined) ts.immunity_type = req.tax_settings.immunity_type;
        if (req.tax_settings.iss_retention !== undefined) ts.iss_retention = req.tax_settings.iss_retention;
        if (req.tax_settings.iss_rate !== undefined) ts.iss_rate = req.tax_settings.iss_rate;
        if (req.tax_settings.pis_cofins_retention !== undefined) ts.pis_cofins_retention = req.tax_settings.pis_cofins_retention;
        if (req.tax_settings.irrf_withheld !== undefined) ts.irrf_withheld = req.tax_settings.irrf_withheld;
        if (req.tax_settings.inss_withheld !== undefined) ts.inss_withheld = req.tax_settings.inss_withheld;
        if (req.tax_settings.csll_withheld !== undefined) ts.csll_withheld = req.tax_settings.csll_withheld;
        if (req.tax_settings.pis_cofins_cst !== undefined) ts.pis_cofins_cst = req.tax_settings.pis_cofins_cst;
        if (req.tax_settings.pis_cofins_base !== undefined) ts.pis_cofins_base = req.tax_settings.pis_cofins_base;
        if (req.tax_settings.pis_rate !== undefined) ts.pis_rate = req.tax_settings.pis_rate;
        if (req.tax_settings.cofins_rate !== undefined) ts.cofins_rate = req.tax_settings.cofins_rate;
        if (req.tax_settings.pis_amount !== undefined) ts.pis_amount = req.tax_settings.pis_amount;
        if (req.tax_settings.cofins_amount !== undefined) ts.cofins_amount = req.tax_settings.cofins_amount;
        if (req.tax_settings.approx_tax !== undefined) ts.approx_tax = req.tax_settings.approx_tax;
        if (req.tax_settings.ibs_cbs !== undefined) ts.ibs_cbs = req.tax_settings.ibs_cbs;
        payload.tax_settings = ts;
      }
    }

    return this.request<Draft>(companyId, `/drafts/${draftId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  }

  async withdrawDraft(companyId: string, draftId: string, expectedVersion: number): Promise<Draft> {
    return this.request<Draft>(companyId, `/drafts/${draftId}/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
  }

  async validateDraft(companyId: string, draftId: string): Promise<ValidateDraftResponse> {
    return this.request<ValidateDraftResponse>(companyId, `/drafts/${draftId}/validate`, {
      method: 'POST',
    });
  }

  async registerOperationConfig(companyId: string, draftId: string, expectedVersion: number): Promise<Draft> {
    return this.request<Draft>(companyId, `/drafts/${draftId}/operation-config`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: expectedVersion }),
    });
  }

  async createBatch(companyId: string, req: CreateBatchRequest, idempotencyKey: string): Promise<{ batch_id: string; status: 'SCHEDULED'; mode?: 'NOW' | 'SCHEDULED'; scheduled_at: string; total: number }> {
    return this.request<{ batch_id: string; status: 'SCHEDULED'; scheduled_at: string; total: number }>(companyId, '/batches', {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
      body: JSON.stringify(req),
    });
  }

  // D11: BatchOut
  async getBatches(companyId: string, page = 1, pageSize = 25): Promise<PaginatedResponse<Batch>> {
    return this.request<PaginatedResponse<Batch>>(companyId, `/batches?page=${page}&page_size=${pageSize}`);
  }

  // D11: BatchDetail (inclui items)
  async getBatch(companyId: string, batchId: string): Promise<Batch> {
    return this.request<Batch>(companyId, `/batches/${batchId}`);
  }

  // D12: CancelPendingOut
  async cancelBatchPending(companyId: string, batchId: string): Promise<CancelPendingOut> {
    const res = await this.request<CancelPendingOut>(companyId, `/batches/${batchId}/cancel-pending`, {
      method: 'POST',
    });
    return {
      ...res,
      cancelled_count: res.withdrawn,
    };
  }

  // D14 & Sprint 02: SyncStatus canonical com suporte a ?environment=
  async getSyncStatus(companyId: string, environment?: string): Promise<SyncStatus> {
    const qs = environment ? `?environment=${encodeURIComponent(environment)}` : '';
    return this.request<SyncStatus>(companyId, `/sync/status${qs}`);
  }

  async triggerSync(companyId: string, idempotencyKey: string, environment?: string): Promise<SyncRunResponse> {
    const qs = environment ? `?environment=${encodeURIComponent(environment)}` : '';
    return this.request<SyncRunResponse>(companyId, `/sync/run${qs}`, {
      method: 'POST',
      headers: {
        'Idempotency-Key': idempotencyKey,
      },
    });
  }

  // D13: JobStatusResponse canonical
  async getJobStatus(companyId: string, jobId: string): Promise<JobStatusResponse> {
    return this.request<JobStatusResponse>(companyId, `/jobs/${jobId}`);
  }

  async exportDocuments(companyId: string, filter: DocumentFilter, format: 'xlsx'): Promise<{ job_id: string; status: 'QUEUED' }> {
    return this.request<{ job_id: string; status: 'QUEUED' }>(companyId, '/exports', {
      method: 'POST',
      body: JSON.stringify({ ...filter, format }),
    });
  }

  async downloadJobFile(companyId: string, jobId: string): Promise<Blob> {
    const res = await this.requestRaw(companyId, `/jobs/${jobId}/download`);
    return res.blob();
  }
}
