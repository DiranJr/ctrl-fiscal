import {
  User,
  Company,
  LoginRequest,
  LoginResponse,
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
  DraftDeleted,
  CreateDraftRequest,
  UpdateDraftRequest,
  CreateDraftFromDocumentRequest,
  ValidateDraftResponse,
  ValidationIssue,
  Batch,
  BatchItem,
  CreateBatchRequest,
  CancelPendingOut,
  SyncStatus,
  SyncRunResponse,
  JobStatusResponse,
  DashboardCounters,
  PaginatedResponse,
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
  AdminMeta,
  AdminCompany,
  AdminUser,
  AdminUserFilter,
  CreateAdminUserRequest,
  CreateAdminUserResponse,
  UpdateAdminUserRequest,
  CredentialLink,
  Workspace,
  WorkspaceCreateRequest,
  WorkspaceCreated,
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
import { calculateRetentionSummary } from './retentions.js';
import { ICoreApiClient, IFiscalApiClient, AuthMemoryStore } from './client.js';
import { validateServiceCodes } from './formatters.js';

function generateUUID(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export class MockCoreApiClient implements ICoreApiClient {
  private companies: Company[] = [
    {
      id: 'cmp-alpha-1111-uuid',
      name: 'Alpha Serviços de Tecnologia LTDA',
      cnpj: '12.345.678/0001-90',
      timezone: 'America/Sao_Paulo',
      environment: 'mock',
      permissions: ['view', 'import', 'review', 'prepare', 'schedule', 'admin'],
    },
    {
      id: 'cmp-beta-2222-uuid',
      name: 'Beta Consultoria Empresarial LTDA',
      cnpj: '98.765.432/0001-10',
      timezone: 'America/Sao_Paulo',
      environment: 'mock',
      permissions: ['view', 'review', 'prepare'],
    },
  ];

  private currentUser: User = {
    id: 'usr-analista-fiscal-01',
    name: 'Analista Fiscal (simulação)',
    email: 'fiscal@ctrlfiscal.com.br',
    is_super_admin: false,
    admin_company_ids: ['cmp-alpha-1111-uuid'],
  };

  private adminUsers: AdminUser[] = [];

  private customers: Map<string, Customer[]> = new Map([
    [
      'cmp-alpha-1111-uuid',
      [
        {
          id: 'cust-tech-001',
          company_id: 'cmp-alpha-1111-uuid',
          name: 'Tech Solutions Participações S.A.',
          document: '11222333000144',
          cnpj_cpf: '11222333000144',
          document_type: 'CNPJ',
          email: 'financeiro@techsolutions.com.br',
          municipal_registration: '98765432',
          municipality_code: '3550308',
          postal_code: '01310100',
          address: {
            street: 'Av. Paulista',
            number: '1000',
            district: 'Bela Vista',
            city: 'São Paulo',
            state: 'SP',
          },
          is_active: true,
          version: 1,
          created_at: '2026-01-10T10:00:00Z',
          updated_at: '2026-01-10T10:00:00Z',
        },
        {
          id: 'cust-prisma-002',
          company_id: 'cmp-alpha-1111-uuid',
          name: 'Consultoria Prisma LTDA',
          document: '22333444000155',
          cnpj_cpf: '22333444000155',
          document_type: 'CNPJ',
          email: 'contato@prismaconsultoria.com.br',
          municipal_registration: '12345678',
          municipality_code: '3550308',
          postal_code: '04551060',
          address: {
            street: 'Rua Funchal',
            number: '418',
            district: 'Vila Olímpia',
            city: 'São Paulo',
            state: 'SP',
          },
          is_active: true,
          version: 1,
          created_at: '2026-02-15T14:30:00Z',
          updated_at: '2026-02-15T14:30:00Z',
        },
      ],
    ],
  ]);

  private authStore = AuthMemoryStore.getInstance();

  async login(req: LoginRequest): Promise<LoginResponse> {
    if (!req.email || !req.password) {
      throw new ProblemDetailsException({
        type: 'urn:problem:invalid-credentials',
        title: 'Credenciais Inválidas',
        status: 401,
        detail: 'Email e senha são obrigatórios.',
      });
    }

    const token = 'mock-jwt-access-token-' + generateUUID();
    this.authStore.setAccessToken(token);
    this.authStore.setCsrfToken('mock-csrf-token-' + generateUUID());

    return {
      access_token: token,
      token_type: 'Bearer',
      expires_in: 900,
      user: {
        ...this.currentUser,
        companies: this.companies,
      },
    };
  }

  async getCsrf(): Promise<CsrfResponse> {
    const csrf = 'mock-csrf-token-' + generateUUID();
    this.authStore.setCsrfToken(csrf);
    return { csrf_token: csrf };
  }

  async refresh(): Promise<LoginResponse> {
    const token = 'mock-jwt-refreshed-' + generateUUID();
    this.authStore.setAccessToken(token);
    return {
      access_token: token,
      token_type: 'Bearer',
      expires_in: 900,
      user: {
        ...this.currentUser,
        companies: this.companies,
      },
    };
  }

  async logout(): Promise<void> {
    this.authStore.clear();
  }

  async getMe(): Promise<User & { companies: Company[] }> {
    return {
      ...this.currentUser,
      companies: this.companies,
    };
  }

  async getCompanies(): Promise<PaginatedResponse<Company>> {
    return {
      items: this.companies,
      page: 1,
      page_size: 25,
      total: this.companies.length,
    };
  }

  async getCustomers(companyId: string): Promise<PaginatedResponse<Customer>> {
    const list = this.customers.get(companyId) || [];
    return {
      items: list,
      page: 1,
      page_size: 25,
      total: list.length,
    };
  }

  async createCustomer(companyId: string, req: CreateCustomerRequest): Promise<Customer> {
    const list = this.customers.get(companyId) || [];
    const newCust: Customer = {
      id: 'cust-' + generateUUID(),
      company_id: companyId,
      name: req.name,
      document: req.document,
      cnpj_cpf: req.document,
      document_type: req.document_type || 'CNPJ',
      email: req.email || null,
      phone: req.phone || null,
      municipal_registration: req.municipal_registration || null,
      municipality_code: req.municipality_code || null,
      postal_code: req.postal_code || null,
      address: req.address || null,
      is_active: true,
      version: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    list.push(newCust);
    this.customers.set(companyId, list);
    return newCust;
  }

  async updateCustomer(companyId: string, customerId: string, req: UpdateCustomerRequest): Promise<Customer> {
    const list = this.customers.get(companyId) || [];
    const idx = list.findIndex((c) => c.id === customerId);
    if (idx === -1) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Cliente Não Encontrado',
        status: 404,
        detail: `Cliente ${customerId} não foi localizado na empresa.`,
      });
    }
    const current = list[idx];
    const updated: Customer = {
      ...current,
      name: req.name ?? current.name,
      email: req.email !== undefined ? req.email : current.email,
      phone: req.phone !== undefined ? req.phone : current.phone,
      municipal_registration: req.municipal_registration !== undefined ? req.municipal_registration : current.municipal_registration,
      municipality_code: req.municipality_code !== undefined ? req.municipality_code : current.municipality_code,
      postal_code: req.postal_code !== undefined ? req.postal_code : current.postal_code,
      address: req.address !== undefined ? req.address : current.address,
      is_active: req.is_active !== undefined && req.is_active !== null ? req.is_active : current.is_active,
      version: current.version + 1,
      updated_at: new Date().toISOString(),
    };
    list[idx] = updated;
    return updated;
  }

  async getIntegrationStatus(companyId: string, environment?: string): Promise<IntegrationStatus> {
    const env = environment || 'mock';
    return {
      company_id: companyId,
      environment: env,
      status: env === 'mock' ? 'SIMULATED' : 'NOT_CONFIGURED',
      simulated: env === 'mock',
      provider: env === 'mock' ? 'mock' : 'official',
      certificate: {
        present: true,
        subject_cnpj_matches: true,
        not_before: '2026-01-01T00:00:00Z',
        not_after: '2027-05-18T23:59:59Z',
        days_to_expiry: 220,
      },
      capabilities: {
        emission: env !== 'production',
        distribution: true,
        cancellation: true,
        simulated: env === 'mock',
        can_issue: env !== 'production',
      },
      blockers: env === 'production' ? [{ code: 'PRODUCTION_EMISSION_BLOCKED', message: 'Produção: somente leitura' }] : [],
      checked_at: new Date().toISOString(),
    };
  }

  private serviceProfiles: Map<string, ServiceProfile[]> = new Map();

  async getWorkspaces(): Promise<Workspace[]> {
    return this.companies.map((c) => ({
      id: c.id, name: c.name, status: 'ACTIVE', environment: c.environment || 'mock', permissions: c.permissions,
      company: { id: c.id, name: c.name, cnpj: c.cnpj.replace(/\D/g, ''), municipality_code: '3550308' },
    }));
  }

  async getReadiness(companyId: string): Promise<Readiness> {
    const ws = (await this.getWorkspaces()).find((w) => w.id === companyId) || null;
    const op = (operation: 'consulta' | 'emissao' | 'cancelamento', issues: Readiness['environments'][number]['operations'][number]['issues']) =>
      ({ operation, ready: !issues.some((i) => i.severity === 'error'), issues });
    const simulated = { code: 'SIMULATED', message: 'Ambiente de simulação: sem conexão oficial.', severity: 'error' };
    return {
      workspace: ws, checked_at: new Date().toISOString(), note: 'Concluir o cadastro não libera produção.',
      environments: ['production', 'restricted'].map((environment) => ({
        environment, certificate: { present: false },
        operations: [op('consulta', [simulated]), op('emissao', [simulated]), op('cancelamento', [simulated])],
      })),
    };
  }

  async getCertificateStatus(_companyId: string): Promise<CertificateStatus> {
    return { status: 'ABSENT', active: null, history: [], note: 'Simulação: sem certificado.' };
  }

  async listServiceProfiles(companyId: string): Promise<ServiceProfile[]> {
    return [...(this.serviceProfiles.get(companyId) || [])];
  }

  async createServiceProfile(companyId: string, req: ServiceProfileInput): Promise<ServiceProfile> {
    const p: ServiceProfile = {
      id: generateUUID(), name: req.name, national_code: req.national_code, municipal_code: req.municipal_code ?? null,
      nbs_code: req.nbs_code ?? null, description: req.description ?? null, issqn_taxation: req.issqn_taxation ?? null,
      pis_cofins_cst: req.pis_cofins_cst ?? null, ibs_cbs: req.ibs_cbs ?? null, source: req.source ?? null,
      is_active: true, version: 1, updated_at: new Date().toISOString(), status: 'PENDING_REVIEW', origin_key: null,
      evidence: null, field_sources: {}, suggestions: [], valid_from: null, valid_to: null, approved_by: null,
      approved_at: null, descriptions: {}, pending: [], can_approve: true,
    };
    this.serviceProfiles.set(companyId, [...(this.serviceProfiles.get(companyId) || []), p]);
    return p;
  }

  async updateServiceProfile(companyId: string, profileId: string, req: ServiceProfilePatch): Promise<ServiceProfile> {
    const list = this.serviceProfiles.get(companyId) || [];
    const cur = list.find((x) => x.id === profileId);
    if (!cur) throw new Error('SERVICE_PROFILE_NOT_FOUND');
    const { expected_version: _v, ...rest } = req;
    const next: ServiceProfile = { ...cur, ...rest, version: cur.version + 1, updated_at: new Date().toISOString() } as ServiceProfile;
    this.serviceProfiles.set(companyId, list.map((x) => (x.id === profileId ? next : x)));
    return next;
  }

  async approveServiceProfile(companyId: string, profileId: string, expectedVersion: number): Promise<ServiceProfile> {
    return this.updateServiceProfile(companyId, profileId, { expected_version: expectedVersion }).then((p) => ({ ...p, status: 'APPROVED' }));
  }

  async setServiceProfileActive(companyId: string, profileId: string, active: boolean, expectedVersion: number): Promise<ServiceProfile> {
    return this.updateServiceProfile(companyId, profileId, { is_active: active, expected_version: expectedVersion })
      .then((p) => ({ ...p, status: active ? 'PENDING_REVIEW' : 'INACTIVE' }));
  }

  async getServiceProfileVersions(_companyId: string, _profileId: string): Promise<ServiceProfileVersionOut[]> {
    return [];
  }

  async resolveServiceProfileProposal(companyId: string, profileId: string, proposalId: string, req: ProposalResolveRequest): Promise<ServiceProfile> {
    const p = (this.serviceProfiles.get(companyId) || []).find((x) => x.id === profileId);
    if (!p) throw new Error('Perfil de serviço não encontrado.');
    const suggestions = p.suggestions.map((s) => (s.tipo === 'proposta_revisao' && 'id' in s && s.id === proposalId
      ? { ...s, status: req.decision === 'APPLY' ? 'APLICADA' : 'DESCARTADA', alternativa_aplicada: req.alternative ?? null, nota: req.note }
      : s));
    const updated = { ...p, suggestions, version: p.version + 1 };
    this.serviceProfiles.set(companyId, (this.serviceProfiles.get(companyId) || []).map((x) => (x.id === profileId ? updated : x)));
    return updated;
  }

  async getFiscalProfile(companyId: string): Promise<FiscalProfileOut> {
    return {
      company_id: companyId,
      cnpj: '12345678000190',
      name: 'Alpha Serviços de Tecnologia LTDA',
      timezone: 'America/Sao_Paulo',
      environment: 'mock',
      adn_environments: ['mock', 'restricted'],
      municipality_code: '3550308',
      municipal_registration: '98765432',
      simples_option: '1',
      simples_regime: null,
      special_regime: '0',
      phone: '11987654321',
      email: 'fiscal@alpha.local',
      source: 'MOCK',
      updated_at: new Date().toISOString(),
      complete_for_emission: true,
      missing: [],
    };
  }

  async updateFiscalProfile(companyId: string, patch: FiscalProfilePatch): Promise<FiscalProfileOut> {
    const current = await this.getFiscalProfile(companyId);
    return {
      ...current,
      ...patch,
      environment: patch.environment || current.environment,
      adn_environments: patch.adn_environments || current.adn_environments,
      updated_at: new Date().toISOString(),
    };
  }

  async uploadCertificate(
    companyId: string,
    file: File,
    password: string,
    environments: string[] = ['restricted', 'production']
  ): Promise<CertificateInfo> {
    return {
      company_id: companyId,
      environments,
      subject_cnpj_matches: true,
      not_before: '2026-01-01T00:00:00Z',
      not_after: '2027-01-01T23:59:59Z',
      days_to_expiry: 85,
      fingerprint_sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      accepted: true,
      problems: [],
    };
  }

  async deleteCertificate(companyId: string, environment?: string): Promise<void> {
    // mock no-op
  }

  // --- Administração de usuários (stubs mínimos em memória) ---

  private adminCompanies(): AdminCompany[] {
    return this.companies
      .filter((c) => (this.currentUser.admin_company_ids || []).includes(c.id))
      .map((c) => ({ id: c.id, name: c.name, cnpj: c.cnpj }));
  }

  private findAdminUser(userId: string): AdminUser {
    const u = this.adminUsers.find((x) => x.id === userId);
    if (!u) {
      throw new ProblemDetailsException({ type: 'about:blank', title: 'Não encontrado', status: 404, code: 'USER_NOT_FOUND' });
    }
    return u;
  }

  private touch(u: AdminUser): AdminUser {
    u.version += 1;
    u.updated_at = new Date().toISOString();
    return { ...u, memberships: u.memberships.map((m) => ({ ...m })) };
  }

  private mockLink(purpose: 'ACTIVATE' | 'RESET'): CredentialLink {
    return { purpose, expires_at: new Date(Date.now() + 72 * 3600 * 1000).toISOString(), path: `/ativar#token=mock-${generateUUID()}` };
  }

  async getAdminMeta(): Promise<AdminMeta> {
    return {
      is_super_admin: !!this.currentUser.is_super_admin,
      admin_company_ids: this.currentUser.admin_company_ids || [],
      permissions: [
        { id: 'view', label: 'Visualizar' },
        { id: 'import', label: 'Importar' },
        { id: 'review', label: 'Conferir' },
        { id: 'prepare', label: 'Preparar rascunhos' },
        { id: 'schedule', label: 'Agendar emissão' },
        { id: 'admin', label: 'Administrar empresa' },
      ],
    };
  }

  async getAdminCompanies(): Promise<AdminCompany[]> {
    return this.adminCompanies();
  }

  async listAdminUsers(filter: AdminUserFilter = {}): Promise<PaginatedResponse<AdminUser>> {
    const q = (filter.query || '').toLowerCase();
    const items = this.adminUsers.filter((u) =>
      (!q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)) &&
      (!filter.status || u.status === filter.status) &&
      (!filter.company_id || u.memberships.some((m) => m.company_id === filter.company_id)) &&
      (!filter.permission || u.memberships.some((m) => m.permissions.includes(filter.permission!))) &&
      (filter.super_admin === undefined || u.is_super_admin === filter.super_admin)
    );
    const page = filter.page || 1;
    const pageSize = filter.page_size || 25;
    return { items: items.slice((page - 1) * pageSize, page * pageSize), page, page_size: pageSize, total: items.length };
  }

  async getAdminUser(userId: string): Promise<AdminUser> {
    return { ...this.findAdminUser(userId) };
  }

  async createAdminUser(req: CreateAdminUserRequest): Promise<CreateAdminUserResponse> {
    const now = new Date().toISOString();
    const companies = this.adminCompanies();
    const user: AdminUser = {
      id: generateUUID(), name: req.name.trim(), email: req.email.trim(), status: 'PENDING', is_active: true,
      is_super_admin: false, version: 1,
      memberships: req.memberships.map((m) => {
        const c = companies.find((x) => x.id === m.company_id);
        return { company_id: m.company_id, company_name: c?.name || m.company_id, company_cnpj: c?.cnpj || '', company_active: true,
          permissions: m.permissions };
      }),
      other_companies: 0, can_manage_identity: true, active_sessions: 0, password_set_at: null, created_at: now, updated_at: now,
    };
    this.adminUsers.push(user);
    return { user: { ...user }, credential_link: this.mockLink('ACTIVATE') };
  }

  async updateAdminUser(userId: string, req: UpdateAdminUserRequest): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    if (req.expected_version !== u.version) {
      throw new ProblemDetailsException({ type: 'about:blank', title: 'Conflito', status: 409, code: 'VERSION_CONFLICT' });
    }
    if (req.name !== undefined) u.name = req.name.trim();
    if (req.email !== undefined) u.email = req.email.trim();
    if (req.is_active !== undefined) {
      u.is_active = req.is_active;
      u.status = req.is_active ? (u.password_set_at ? 'ACTIVE' : 'PENDING') : 'INACTIVE';
    }
    return this.touch(u);
  }

  async setAdminMembership(userId: string, companyId: string, permissions: string[]): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    const existing = u.memberships.find((m) => m.company_id === companyId);
    if (existing) existing.permissions = permissions;
    else {
      const c = this.adminCompanies().find((x) => x.id === companyId);
      u.memberships.push({ company_id: companyId, company_name: c?.name || companyId, company_cnpj: c?.cnpj || '', company_active: true, permissions });
    }
    return this.touch(u);
  }

  async removeAdminMembership(userId: string, companyId: string): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    u.memberships = u.memberships.filter((m) => m.company_id !== companyId);
    return this.touch(u);
  }

  async setCreateWorkspacePermission(userId: string, enabled: boolean): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    u.can_create_workspace = enabled;
    return this.touch(u);
  }

  async listAdminWorkspaces(): Promise<Workspace[]> {
    return [];
  }

  async createWorkspace(_req: WorkspaceCreateRequest): Promise<WorkspaceCreated> {
    throw new ProblemDetailsException({ type: 'about:blank', title: 'Indisponível', status: 501,
      detail: 'Criação de workspace não disponível no modo de demonstração.' });
  }

  async updateWorkspace(_workspaceId: string, _req: { name?: string; status?: string }): Promise<Workspace> {
    throw new ProblemDetailsException({ type: 'about:blank', title: 'Indisponível', status: 501,
      detail: 'Não disponível no modo de demonstração.' });
  }

  async setSuperAdmin(userId: string, enabled: boolean): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    u.is_super_admin = enabled;
    return this.touch(u);
  }

  async revokeUserSessions(userId: string): Promise<AdminUser> {
    const u = this.findAdminUser(userId);
    u.active_sessions = 0;
    return { ...u, memberships: u.memberships.map((m) => ({ ...m })) };
  }

  async issueCredentialLink(userId: string): Promise<CredentialLink> {
    const u = this.findAdminUser(userId);
    return this.mockLink(u.password_set_at ? 'RESET' : 'ACTIVATE');
  }

  async activateAccount(token: string, password: string): Promise<void> {
    if (!token) {
      throw new ProblemDetailsException({ type: 'about:blank', title: 'Link inválido', status: 400, code: 'ACTIVATION_INVALID' });
    }
    if (password.length < 12) {
      throw new ProblemDetailsException({ type: 'about:blank', title: 'Senha fraca', status: 422, code: 'PASSWORD_WEAK' });
    }
  }
}

export class MockFiscalApiClient implements IFiscalApiClient {
  private documents: Map<string, FiscalDocument[]> = new Map();
  private drafts: Map<string, Draft[]> = new Map();
  private batches: Map<string, Batch[]> = new Map();
  private jobs: Map<string, JobStatusResponse> = new Map();

  constructor() {
    this.seedAlphaData();
  }

  private seedAlphaData() {
    const alphaId = 'cmp-alpha-1111-uuid';

    const docs: FiscalDocument[] = [
      {
        id: 'doc-emit-001',
        direction: 'ISSUED',
        number: '202600000000101',
        access_key: '35260912345678000190560010000000010100000101',
        status: 'AUTHORIZED',
        competence: '2026-09',
        issued_at: '2026-09-30T14:22:10-03:00',
        issuer: {
          name: 'Alpha Serviços de Tecnologia LTDA',
          cnpj: '12345678000190',
        },
        recipient: {
          name: 'Tech Solutions Participações S.A.',
          cnpj: '11222333000144',
        },
        description: 'Desenvolvimento e manutenção contínua de software sob demanda. Sprint 34.',
        amount: '12500.00',
        retentions: {
          iss: '250.00',
          irrf: '187.50',
          inss: null,
          pis: '81.25',
          cofins: '375.00',
          csll: '125.00',
          pcc_total: '581.25',
        },
        review_status: 'REVIEWED',
        version: 1,
        xml_available: true,
        source: 'SEFIN_NACIONAL',
        simulated: true,
        environment: 'mock',
      },
      {
        id: 'doc-rec-101',
        direction: 'RECEIVED',
        number: '00004521',
        access_key: '35260999888777000122560010000045210000004521',
        status: 'AUTHORIZED',
        competence: '2026-09',
        issued_at: '2026-09-28T10:15:00-03:00',
        issuer: {
          name: 'Cloud Data Hosting Provedores LTDA',
          cnpj: '99888777000122',
        },
        recipient: {
          name: 'Alpha Serviços de Tecnologia LTDA',
          cnpj: '12345678000190',
        },
        description: 'Locação e hospedagem de servidores dedicados em datacenter Tier 3.',
        amount: '3200.00',
        retentions: {
          iss: null,
          irrf: '48.00',
          inss: null,
          pis: '20.80',
          cofins: '96.00',
          csll: '32.00',
          pcc_total: '148.80',
        },
        review_status: 'PENDING',
        version: 1,
        xml_available: true,
        source: 'ADN_DISTRIBUICAO',
        simulated: true,
        environment: 'mock',
      },
    ];
    this.documents.set(alphaId, docs);

    const draftsList: Draft[] = [
      {
        id: 'draft-001-uuid',
        customer_id: 'cust-tech-001',
        customer_name: 'Tech Solutions Participações S.A.',
        source_document_id: 'doc-emit-001',
        status: 'DRAFT',
        version: 1,
        competence: '2026-10',
        service: {
          national_code: '010701',
          municipal_code: '269',
          description: 'Desenvolvimento e manutenção contínua de software sob demanda. Ciclo Outubro/2026.',
        },
        amount: '12500.00',
        deductions: '0.00',
        tax_settings: {
          issqn_taxation: 'TAXABLE',
          iss_retention: 'RETAINED_BY_TOMADOR',
          iss_rate: '2.00',
        },
        created_at: '2026-10-01T08:00:00Z',
        updated_at: '2026-10-01T08:00:00Z',
      },
    ];
    this.drafts.set(alphaId, draftsList);

    const batchList: Batch[] = [
      {
        id: 'batch-001-uuid',
        status: 'SCHEDULED',
        scheduled_at: '2026-11-01T00:01:00-03:00',
        scheduled_local: '2026-11-01 00:01:00',
        timezone: 'America/Sao_Paulo',
        environment: 'mock',
        simulated: true,
        total: 1,
        counts: { SCHEDULED: 1 },
        progress: { done: 0, total: 1, percent: 0 },
        created_at: '2026-10-05T18:00:00Z',
        items: [
          {
            id: 'bi-001',
            draft_id: 'draft-001-uuid',
            draft_version: 1,
            customer_name: 'Tech Solutions Participações S.A.',
            amount: '12500.00',
            competence: '2026-10',
            status: 'SCHEDULED',
            attempts: 0,
            simulated: true,
          },
        ],
      },
    ];
    this.batches.set(alphaId, batchList);
  }

  async getDashboard(companyId: string): Promise<DashboardCounters> {
    const docs = this.documents.get(companyId) || [];
    const drafts = this.drafts.get(companyId) || [];
    const batches = this.batches.get(companyId) || [];

    const issued = docs.filter((d) => d.direction === 'ISSUED');
    const received = docs.filter((d) => d.direction === 'RECEIVED');

    let scheduled = 0;
    batches.forEach((b) => {
      (b.items || []).forEach((item) => {
        if (item.status === 'SCHEDULED' || item.status === 'PROCESSING') scheduled++;
      });
    });

    return {
      environment: 'mock',
      simulated: true,
      documents: {
        issued_total: issued.length,
        received_total: received.length,
        received_pending_review: received.filter((d) => d.review_status === 'PENDING').length,
        received_divergent: received.filter((d) => d.review_status === 'DIVERGENT').length,
        cancelled: 0,
      },
      drafts: { DRAFT: drafts.length },
      queue: { SCHEDULED: scheduled },
      next_scheduled_at: '2026-11-01T00:01:00-03:00',
      sync: await this.getSyncStatus(companyId),
      drafts_count: drafts.length,
      scheduled_count: scheduled,
      issued_count: issued.length,
      rejected_count: 0,
      received_pending_count: received.filter((d) => d.review_status === 'PENDING').length,
      last_sync_at: '2026-10-07T08:00:00-03:00',
    };
  }

  async getDocuments(companyId: string, filter?: DocumentFilter): Promise<PaginatedResponse<FiscalDocument>> {
    let list = this.documents.get(companyId) || [];
    if (filter) {
      if (filter.direction) list = list.filter((d) => d.direction === filter.direction);
      if (filter.review_status) list = list.filter((d) => d.review_status === filter.review_status);
      if (filter.competence) list = list.filter((d) => d.competence === filter.competence);
    }
    return {
      items: list,
      page: 1,
      page_size: 25,
      total: list.length,
    };
  }

  async getDocument(companyId: string, documentId: string): Promise<FiscalDocument> {
    const list = this.documents.get(companyId) || [];
    const doc = list.find((d) => d.id === documentId);
    if (!doc) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Documento Não Encontrado',
        status: 404,
        detail: `Documento fiscal ${documentId} não existe.`,
      });
    }
    return doc;
  }

  async getDocumentXml(companyId: string, documentId: string): Promise<string> {
    const doc = await this.getDocument(companyId, documentId);
    return `<?xml version="1.0" encoding="UTF-8"?>
<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse">
  <infNFSe Id="NFS${doc.number || '000000'}">
    <xLocEmi>São Paulo</xLocEmi>
    <dCompet>${doc.competence || '2026-10'}-01</dCompet>
    <dhEmi>${doc.issued_at || new Date().toISOString()}</dhEmi>
    <chNFSe>${doc.access_key || 'SEM_CHAVE'}</chNFSe>
    <valores>
      <vServPrest>${doc.amount || '0.00'}</vServPrest>
    </valores>
    <xDiscServ>${doc.description || ''}</xDiscServ>
  </infNFSe>
</NFSe>`;
  }

  async importDocuments(companyId: string, files: File[]): Promise<{ job_id: string; status: 'QUEUED' }> {
    const jobId = 'job-imp-' + generateUUID();
    const hasInvalidFile = files.some((f) => f.name.endsWith('.exe') || f.name.endsWith('.sh'));

    this.jobs.set(jobId, {
      id: jobId,
      status: 'QUEUED',
      processed_count: 0,
      error_count: hasInvalidFile ? 1 : 0,
      errors: hasInvalidFile
        ? [{ file: 'arquivo_suspeito.exe', code: 'UNSUPPORTED_FORMAT', message: 'Apenas arquivos XML e ZIP são permitidos.' }]
        : [],
      download_available: false,
    });

    setTimeout(() => {
      const current = this.jobs.get(jobId);
      if (current) {
        current.status = hasInvalidFile ? 'PARTIAL' : 'SUCCEEDED';
        current.processed_count = files.length;
        this.jobs.set(jobId, current);
      }
    }, 1000);

    return { job_id: jobId, status: 'QUEUED' };
  }

  async reviewDocument(companyId: string, documentId: string, req: ReviewDocumentRequest): Promise<FiscalDocument> {
    const list = this.documents.get(companyId) || [];
    const idx = list.findIndex((d) => d.id === documentId);
    if (idx === -1) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Documento Não Encontrado',
        status: 404,
        detail: `Documento fiscal ${documentId} não existe.`,
      });
    }

    const doc = list[idx];
    if (req.expected_version !== undefined && doc.version !== req.expected_version) {
      throw new ProblemDetailsException({
        type: 'urn:problem:version-conflict',
        title: 'Conflito de Versão',
        status: 409,
        detail: `Versão esperada (${req.expected_version}) diverge da versão atual do documento (${doc.version}).`,
      });
    }
    const updated: FiscalDocument = {
      ...doc,
      review_status: req.review_status,
      review_note: req.note || null,
      reviewed_by: 'Analista Fiscal Conectado',
      reviewed_at: new Date().toISOString(),
      version: doc.version + 1,
    };

    list[idx] = updated;
    return updated;
  }

  async getLastIssuedDocument(companyId: string, customerId: string): Promise<FiscalDocument> {
    const list = this.documents.get(companyId) || [];
    if (customerId === 'cust-tech-001') {
      const found = list.find((d) => d.id === 'doc-emit-001');
      if (found) return found;
    }

    throw new ProblemDetailsException({
      type: 'urn:problem:not-found',
      title: 'Nenhuma Nota Anterior Encontrada',
      status: 404,
      detail: `Não existe nenhuma NFS-e autorizada anterior para este cliente no ambiente atual.`,
    });
  }

  async prepareServiceProfiles(_companyId: string): Promise<PrepareProfilesResult> {
    return { groups: 0, created: [], existing: [] };
  }

  async getServiceInventory(_companyId: string): Promise<ServiceInventory> {
    return { total_notas: 0, periodo: { de: null, ate: null }, grupos: [] };
  }

  async getProviderDataFromDocuments(_companyId: string): Promise<ProviderDataFromDocuments> {
    return { total_notas: 0, campos: {} };
  }

  async applyProviderDataFromDocuments(_companyId: string): Promise<ProviderDataApplyResult> {
    return { applied: {}, equal: [], divergent: [] };
  }

  async getTaxClassifications(_companyId: string, on: string): Promise<TaxClassificationPage> {
    return { fonte: 'simulação', publicacao: '', em: on, items: [] };
  }

  async getIbsCbsPreview(_companyId: string, _draftId: string): Promise<IbsCbsPreview> {
    throw new ProblemDetailsException({ type: 'urn:problem:ibscbs-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'A prévia de IBS/CBS exige o servidor (alíquotas com vigência).' });
  }

  async getDocumentRecipient(_companyId: string, _documentId: string): Promise<RecipientSuggestion> {
    throw new ProblemDetailsException({ type: 'urn:problem:recipient-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Leitura do tomador exige o XML oficial no servidor.' });
  }

  async createDraftFromDocument(companyId: string, req: CreateDraftFromDocumentRequest, _idempotencyKey?: string): Promise<Draft> {
    const list = this.documents.get(companyId) || [];
    const sourceDoc = list.find((d) => d.id === req.source_document_id);
    if (!sourceDoc) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Documento de Origem Não Encontrado',
        status: 404,
        detail: `Documento de referência ${req.source_document_id} não encontrado nesta empresa.`,
      });
    }

    const draftsList = this.drafts.get(companyId) || [];
    const newDraft: Draft = {
      id: 'draft-' + generateUUID(),
      customer_id: 'cust-tech-001',
      customer_name: sourceDoc.recipient?.name || 'Cliente Referência',
      source_document_id: sourceDoc.id,
      status: 'DRAFT',
      version: 1,
      competence: sourceDoc.competence || '2026-10',
      service: {
        national_code: '010701',
        municipal_code: '269',
        description: sourceDoc.description || 'Serviço prestado',
      },
      amount: sourceDoc.amount || '1000.00',
      deductions: '0.00',
      tax_settings: {
        issqn_taxation: 'TAXABLE',
        iss_retention: 'NOT_RETAINED',
        iss_rate: '2.00',
      },
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    draftsList.unshift(newDraft);
    this.drafts.set(companyId, draftsList);
    return newDraft;
  }

  async getDrafts(companyId: string, page = 1, pageSize = 25): Promise<PaginatedResponse<Draft>> {
    const list = this.drafts.get(companyId) || [];
    const start = (page - 1) * pageSize;
    return {
      items: list.slice(start, start + pageSize),
      page,
      page_size: pageSize,
      total: list.length,
    };
  }

  async getDraft(companyId: string, draftId: string): Promise<Draft> {
    const list = this.drafts.get(companyId) || [];
    const draft = list.find((d) => d.id === draftId);
    if (!draft) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Rascunho Não Encontrado',
        status: 404,
        detail: `Rascunho ${draftId} não encontrado.`,
      });
    }
    return draft;
  }

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

    const list = this.drafts.get(companyId) || [];
    const newDraft: Draft = {
      id: 'draft-' + generateUUID(),
      customer_id: req.customer_id,
      customer_name: 'Cliente Selecionado',
      source_document_id: null,
      status: 'DRAFT',
      version: 1,
      competence: req.competence,
      service_date: req.service_date || null,
      service: {
        national_code: req.service.national_code ? req.service.national_code.replace(/\D/g, '') || null : null,
        municipal_code: req.service.municipal_code ? req.service.municipal_code.replace(/\D/g, '').padStart(3, '0') || null : null,
        nbs_code: req.service.nbs_code ? req.service.nbs_code.replace(/\D/g, '') || null : null,
        location_code: req.service.location_code ? req.service.location_code.replace(/\D/g, '') || null : null,
        obra_code: req.service.obra_code || null,
        obra_address: req.service.obra_address || null,
        description: req.service.description.trim(),
      },
      amount: req.amount,
      deductions: req.deductions || '0.00',
      tax_settings: req.tax_settings,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    list.unshift(newDraft);
    this.drafts.set(companyId, list);
    return newDraft;
  }

  async updateDraft(companyId: string, draftId: string, req: UpdateDraftRequest): Promise<Draft> {
    const list = this.drafts.get(companyId) || [];
    const idx = list.findIndex((d) => d.id === draftId);
    if (idx === -1) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Rascunho Não Encontrado',
        status: 404,
        detail: `Rascunho ${draftId} não encontrado.`,
      });
    }

    const current = list[idx];
    if (req.expected_version !== undefined && current.version !== req.expected_version) {
      throw new ProblemDetailsException({
        type: 'urn:problem:version-conflict',
        title: 'Conflito de Versão no Rascunho',
        status: 409,
        detail: `Versão esperada (${req.expected_version}) diverge da versão atual do rascunho (${current.version}). Atualize antes de salvar.`,
      });
    }

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
    }

    const updated: Draft = {
      ...current,
      customer_id: req.customer_id ?? current.customer_id,
      competence: req.competence ?? current.competence,
      service_date: req.service_date !== undefined ? req.service_date : current.service_date,
      service: req.service !== undefined
        ? {
            national_code: req.service.national_code ? req.service.national_code.replace(/\D/g, '') || null : null,
            municipal_code: req.service.municipal_code ? req.service.municipal_code.replace(/\D/g, '').padStart(3, '0') || null : null,
            nbs_code: req.service.nbs_code ? req.service.nbs_code.replace(/\D/g, '') || null : null,
            location_code: req.service.location_code ? req.service.location_code.replace(/\D/g, '') || null : null,
            obra_code: req.service.obra_code || null,
            obra_address: req.service.obra_address || null,
            description: req.service.description.trim(),
          }
        : current.service,
      amount: req.amount ?? current.amount,
      deductions: req.deductions ?? current.deductions,
      unconditional_discount: req.unconditional_discount !== undefined ? req.unconditional_discount : current.unconditional_discount,
      conditional_discount: req.conditional_discount !== undefined ? req.conditional_discount : current.conditional_discount,
      tax_settings: req.tax_settings !== undefined ? req.tax_settings : current.tax_settings,
      version: current.version + 1,
      updated_at: new Date().toISOString(),
    };

    list[idx] = updated;
    return updated;
  }

  async deleteDraft(companyId: string, draftId: string, expectedVersion: number): Promise<DraftDeleted> {
    const list = this.drafts.get(companyId) || [];
    const current = list.find((d) => d.id === draftId);
    if (!current) {
      throw new ProblemDetailsException({ type: 'urn:problem:not-found', title: 'Rascunho Não Encontrado', status: 404,
        detail: `Rascunho ${draftId} não encontrado.` });
    }
    if (current.version !== expectedVersion) {
      throw new ProblemDetailsException({ type: 'urn:problem:version-conflict', title: 'Conflito de Versão', status: 409,
        detail: 'Rascunho alterado por outra pessoa. Recarregue antes de excluir.' });
    }
    if (!['DRAFT', 'WITHDRAWN'].includes(current.status) || current.current_item_id) {
      throw new ProblemDetailsException({ type: 'urn:problem:conflict', title: 'Rascunho não pode ser excluído', status: 409,
        detail: `Rascunho em ${current.status} não pode ser excluído.` });
    }
    this.drafts.set(companyId, list.filter((d) => d.id !== draftId));
    return { id: draftId, deleted: true, deleted_at: new Date().toISOString(), version: current.version + 1, already_deleted: false };
  }

  async withdrawDraft(companyId: string, draftId: string, expectedVersion: number): Promise<Draft> {
    const list = this.drafts.get(companyId) || [];
    const idx = list.findIndex((d) => d.id === draftId);
    if (idx === -1) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Rascunho Não Encontrado',
        status: 404,
        detail: `Rascunho ${draftId} não encontrado.`,
      });
    }

    const current = list[idx];
    if (current.version !== expectedVersion) {
      throw new ProblemDetailsException({
        type: 'urn:problem:version-conflict',
        title: 'Conflito de Versão',
        status: 409,
        detail: 'Rascunho alterado por outra pessoa. Recarregue antes de retirar.',
      });
    }

    if (current.status !== 'SCHEDULED') {
      throw new ProblemDetailsException({
        type: 'urn:problem:draft-not-withdrawable',
        title: 'Rascunho Não Retirável',
        status: 409,
        detail: `Rascunho em ${current.status}: não há agendamento retirável.`,
      });
    }

    const updated: Draft = {
      ...current,
      status: 'DRAFT',
      current_item_id: null,
      version: current.version + 1,
      issues: [],
      updated_at: new Date().toISOString(),
    };

    list[idx] = updated;
    return updated;
  }

  async registerOperationConfig(companyId: string, draftId: string, expectedVersion: number): Promise<Draft> {
    // Simulação local: não há perfis no servidor; registra só a tentativa (nada é aprovado nem emitido).
    const list = this.drafts.get(companyId) || [];
    const idx = list.findIndex((d) => d.id === draftId);
    if (idx < 0) {
      throw new ProblemDetailsException({ type: 'urn:problem:not-found', title: 'Rascunho não encontrado', status: 404 });
    }
    const current = list[idx];
    if (current.version !== expectedVersion) {
      throw new ProblemDetailsException({ type: 'urn:problem:version-conflict', title: 'Conflito de versão', status: 409,
        detail: 'Rascunho alterado por outra pessoa. Recarregue antes de continuar.' });
    }
    const updated: Draft = {
      ...current,
      origin: { ...(current.origin || { kind: 'manual' }), operation: { resolution: 'NOT_REGISTERED',
        errors: [{ field: 'service_profile_id', code: 'SIMULATION', message: 'Simulação local: configuração não registrada no servidor.' }] } },
      version: current.version + 1,
      issues: [],
      updated_at: new Date().toISOString(),
    };
    list[idx] = updated;
    return updated;
  }

  async validateDraft(companyId: string, draftId: string): Promise<ValidateDraftResponse> {
    const draft = await this.getDraft(companyId, draftId);
    const issues: ValidationIssue[] = [];

    if (!draft.customer_id) {
      issues.push({
        field: 'customer_id',
        code: 'REQUIRED',
        message: 'Tomador do serviço é obrigatório.',
        severity: 'error',
      });
    }

    if (!draft.amount || parseFloat(draft.amount) <= 0) {
      issues.push({
        field: 'amount',
        code: 'INVALID_VALUE',
        message: 'Valor total do serviço deve ser maior que zero.',
        severity: 'error',
      });
    }

    return {
      valid: issues.filter((i) => i.severity === 'error').length === 0,
      issues,
    };
  }

  async createBatch(companyId: string, req: CreateBatchRequest, idempotencyKey: string): Promise<{ batch_id: string; status: 'SCHEDULED'; mode?: 'NOW' | 'SCHEDULED'; scheduled_at: string; total: number }> {
    const draftsList = this.drafts.get(companyId) || [];
    const items: BatchItem[] = [];

    for (const itemReq of req.drafts) {
      const draft = draftsList.find((d) => d.id === itemReq.id);
      if (draft) {
        items.push({
          id: 'bi-' + generateUUID(),
          draft_id: draft.id,
          draft_version: draft.version,
          customer_name: draft.customer_name,
          amount: draft.amount,
          competence: draft.competence,
          status: 'SCHEDULED',
          attempts: 0,
          simulated: true,
        });
      }
    }

    const batchId = 'batch-' + generateUUID();
    const batchList = this.batches.get(companyId) || [];
    const mode = req.mode || 'SCHEDULED';
    const quando = mode === 'NOW' || !req.scheduled_at ? new Date().toISOString() : req.scheduled_at;

    const newBatch: Batch = {
      id: batchId,
      status: 'SCHEDULED',
      mode,
      scheduled_at: quando,
      scheduled_local: quando,
      timezone: req.timezone,
      environment: 'mock',
      simulated: true,
      total: items.length,
      counts: { SCHEDULED: items.length },
      progress: { done: 0, total: items.length, percent: 0 },
      items,
      created_at: new Date().toISOString(),
    };

    batchList.unshift(newBatch);
    this.batches.set(companyId, batchList);

    return {
      batch_id: batchId,
      status: 'SCHEDULED',
      mode,
      scheduled_at: quando,
      total: items.length,
    };
  }

  async getBatches(companyId: string, page = 1, pageSize = 25): Promise<PaginatedResponse<Batch>> {
    const list = this.batches.get(companyId) || [];
    const start = (page - 1) * pageSize;
    return {
      items: list.slice(start, start + pageSize),
      page,
      page_size: pageSize,
      total: list.length,
    };
  }

  async getBatch(companyId: string, batchId: string): Promise<Batch> {
    const list = this.batches.get(companyId) || [];
    const b = list.find((item) => item.id === batchId);
    if (!b) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Lote Não Encontrado',
        status: 404,
        detail: `Lote ${batchId} não localizado.`,
      });
    }
    return b;
  }

  async cancelBatchPending(companyId: string, batchId: string): Promise<CancelPendingOut> {
    const batch = await this.getBatch(companyId, batchId);
    let count = 0;
    (batch.items || []).forEach((item) => {
      if (item.status === 'SCHEDULED') {
        item.status = 'WITHDRAWN';
        count++;
      }
    });

    batch.status = 'WITHDRAWN';

    return {
      batch_id: batchId,
      withdrawn: count,
      not_withdrawn: 0,
      cancelled_count: count,
    };
  }

  async getSyncStatus(companyId: string, environment?: string): Promise<SyncStatus> {
    const env = environment || 'mock';
    return {
      enabled: true,
      interval_hours: 12,
      last_success_at: '2026-10-07T08:00:00-03:00',
      last_attempt_at: '2026-10-07T08:00:00-03:00',
      next_run_at: '2026-10-07T20:00:00-03:00',
      status: 'OK',
      environment: env,
      simulated: env === 'mock',
      processed_count: 142,
      received_count: 24,
      pending_parse_count: 0,
      coverage_message: 'Sincronização operando normalmente.',
      last_error: null,
      running: false,
    };
  }

  async triggerSync(companyId: string, idempotencyKey: string, environment?: string): Promise<SyncRunResponse> {
    const jobId = 'job-sync-' + generateUUID();
    this.jobs.set(jobId, {
      id: jobId,
      status: 'QUEUED',
      processed_count: 0,
      error_count: 0,
      errors: [],
      download_available: false,
    });

    setTimeout(() => {
      const current = this.jobs.get(jobId);
      if (current) {
        current.status = 'SUCCEEDED';
        current.processed_count = 3;
        this.jobs.set(jobId, current);
      }
    }, 1000);

    return { job_id: jobId, status: 'QUEUED' };
  }

  async getJobStatus(companyId: string, jobId: string): Promise<JobStatusResponse> {
    const job = this.jobs.get(jobId);
    if (!job) {
      throw new ProblemDetailsException({
        type: 'urn:problem:not-found',
        title: 'Job Não Encontrado',
        status: 404,
        detail: `Job ${jobId} não existe.`,
      });
    }
    return job;
  }

  async exportDocuments(companyId: string, filter: DocumentFilter, format: 'xlsx'): Promise<{ job_id: string; status: 'QUEUED' }> {
    const jobId = 'job-exp-' + generateUUID();
    this.jobs.set(jobId, {
      id: jobId,
      status: 'QUEUED',
      processed_count: 0,
      error_count: 0,
      errors: [],
      download_available: false,
    });

    setTimeout(() => {
      const current = this.jobs.get(jobId);
      if (current) {
        current.status = 'SUCCEEDED';
        current.processed_count = 10;
        current.download_available = true;
        this.jobs.set(jobId, current);
      }
    }, 1000);

    return { job_id: jobId, status: 'QUEUED' };
  }

  async downloadJobFile(companyId: string, jobId: string): Promise<Blob> {
    const job = await this.getJobStatus(companyId, jobId);
    if (job.status !== 'SUCCEEDED') {
      throw new ProblemDetailsException({
        type: 'urn:problem:job-not-ready',
        title: 'Arquivo Ainda Não Disponível',
        status: 409,
        detail: 'O processamento do arquivo ainda não foi concluído.',
      });
    }

    const csvContent = 'ID;Numero;Emissao;Tomador;Valor;Status\n1;202600000000101;2026-09-30;Tech Solutions;12500.00;AUTHORIZED';
    return new Blob([csvContent], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  }

  // Simulação (VITE_USE_MOCK): sem XML oficial nem DANFSe; nenhum arquivo é gerado.
  async getDocumentXmlBlob(companyId: string, documentId: string): Promise<Blob> {
    return new Blob([await this.getDocumentXml(companyId, documentId)], { type: 'application/xml' });
  }

  async getDanfsePdf(_companyId: string, _documentId: string): Promise<Blob> {
    throw new ProblemDetailsException({ type: 'urn:problem:danfse-simulated', title: 'DANFSe indisponível', status: 409,
      detail: 'Documento de simulação não gera DANFSe.' });
  }

  async getCancellationInfo(_companyId: string, _documentId: string): Promise<CancellationInfo> {
    throw new ProblemDetailsException({ type: 'urn:problem:cancellation-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Cancelamento exige o servidor (elegibilidade avaliada no backend).' });
  }

  async requestCancellation(_c: string, _d: string, _r: CancellationCreateRequest, _k: string): Promise<CancellationRequestOut> {
    throw new ProblemDetailsException({ type: 'urn:problem:cancellation-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Cancelamento exige o servidor.' });
  }

  async getCancellation(_companyId: string, _requestId: string): Promise<CancellationRequestOut> {
    throw new ProblemDetailsException({ type: 'urn:problem:cancellation-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Cancelamento exige o servidor.' });
  }

  async createBundle(_companyId: string, _req: BundleRequest): Promise<{ job_id: string; status: 'QUEUED' }> {
    throw new ProblemDetailsException({ type: 'urn:problem:bundle-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Download em lote exige documentos com XML oficial.' });
  }

  async createDownloadTicket(_companyId: string, _jobId: string): Promise<DownloadTicket> {
    throw new ProblemDetailsException({ type: 'urn:problem:bundle-simulated', title: 'Indisponível na simulação',
      status: 409, detail: 'Download em lote exige documentos com XML oficial.' });
  }

  async saveJobFileToDisk(companyId: string, jobId: string): Promise<void> {
    await this.createDownloadTicket(companyId, jobId);
  }

  async previewRetentions(_companyId: string, req: RetentionPreviewRequest): Promise<{ retention_summary: RetentionSummary; issues: ValidationIssue[] }> {
    return {
      retention_summary: calculateRetentionSummary({ amount: req.amount, unconditionalDiscount: req.unconditional_discount,
        conditionalDiscount: req.conditional_discount, deductions: req.deductions, taxSettings: req.tax_settings }),
      issues: [],
    };
  }
}
