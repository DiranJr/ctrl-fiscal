/**
 * CTRL Fiscal - Definições de Tipos Alinhadas com os Contratos Canônicos:
 * - contracts/openapi-core.json (0.1.0)
 * - contracts/openapi-fiscal.json (0.1.0)
 * 
 * Resolução integral das divergências D1 a D18 do contrato (histórico no controle de versão)
 */

export interface User {
  id: string;
  name: string;
  email: string;
  /** GET /auth/me: administrador global de usuários (não dá acesso a documentos de empresas). */
  is_super_admin?: boolean;
  /** GET /auth/me: empresas em que o usuário tem a permissão 'admin'. */
  admin_company_ids?: string[];
  /** GET /auth/me: workspaces do usuário (um por empresa/CNPJ) com as permissões DAQUELE vínculo. */
  workspaces?: Workspace[];
  /** GET /auth/me: pode cadastrar workspaces novos (SUPER_ADMIN ou permissão create_workspace). */
  can_create_workspace?: boolean;
}

/** POST /admin/workspaces: empresa nova em PREPARAÇÃO. Só o SUPER_ADMIN indica outro administrador. */
export interface WorkspaceCreateRequest {
  name: string;
  company: { cnpj: string; name: string; municipality_code?: string | null; timezone?: string };
  admin?: { user_id?: string | null; new_user?: { name: string; email: string } | null } | null;
}
export interface WorkspaceCreated extends Workspace {
  admin_user_id: string | null;
  /** Link de ativação (uso único) quando o administrador é um usuário novo; exibido uma vez. */
  credential_link: CredentialLink | null;
}

/** Workspace = contexto de trabalho de UMA empresa (CNPJ), com o mesmo identificador da empresa. */
export type WorkspaceStatus = 'PREPARING' | 'ACTIVE' | 'SUSPENDED';
export interface Workspace {
  id: string;
  name: string;
  status: WorkspaceStatus | string;
  company: { id: string; name: string; cnpj: string; municipality_code: string | null };
  environment: 'mock' | 'restricted' | 'production' | string;
  permissions: string[] | null;
}

export interface ReadinessIssue { code: string; message: string; severity: 'error' | 'warning' | 'info' | string }
/** enabled = funcionalidade habilitada na instalação; ready = esta empresa pronta para a operação. */
export interface ReadinessOperation { operation: 'consulta' | 'emissao' | 'cancelamento'; enabled?: boolean; ready: boolean; issues: ReadinessIssue[] }
export interface ReadinessEnvironment {
  environment: 'production' | 'restricted' | string;
  certificate: { present?: boolean | null; subject_cnpj_matches?: boolean | null; not_after?: string | null; days_to_expiry?: number | null };
  operations: ReadinessOperation[];
}
export interface ReadinessSection { id: 'cadastro' | 'certificado' | 'perfil' | 'nota' | 'ambiente' | string; title: string; ok: boolean; items: ReadinessIssue[] }
export interface Readiness {
  workspace: Workspace | null;
  checked_at: string;
  environments: ReadinessEnvironment[];
  /** Prontidão agrupada: cadastro do prestador, certificado/integração, perfil do serviço, dados da nota e ambiente. */
  sections?: ReadinessSection[];
  note: string;
}

export interface ServiceProfileIbsCbs {
  operation_indicator: string;
  cst: string;
  class_code: string;
  personal_use?: '0' | '1' | null;
  regular?: { cst: string; class_code: string } | null;
}
export type ServiceProfileStatus = 'PENDING_REVIEW' | 'APPROVED' | 'INACTIVE';
/** Origem de um campo: XML oficial consistente, evidência histórica de nota anterior, tabela oficial ou usuário. */
export interface FieldSource {
  origem: 'xml' | 'xml_historico' | 'tabela' | 'usuario' | 'simulacao' | string;
  confirmado_por?: string | null; informado_por?: string | null; em?: string | null; nota?: string | null;
  documento?: string | null; chave?: string | null;
}
/** Evidência de uma VARIANTE de operação (rascunho por nota de referência ou preenchido do zero). */
export interface VariantEvidence {
  tipo: 'variante_operacao';
  origem: { kind: 'document' | 'manual' | string; draft_id?: string; document?: { id: string; number: string | null; access_key: string | null; competence: string | null } };
  diverge_de: { id: string; name: string; version: number; fields: string[] }[];
}
export interface ProfilePendency { field: string; label: string; code: string; message: string; severity: 'error' | 'warning' | 'info' | string }
export interface ProfileSuggestion { campo: string; codigo: string; texto: string; fonte: string; tipo: 'tabela_oficial' | 'dados_xml' | string }
/** Proposta de revisão: alternativas CONDICIONAIS; não altera o perfil e bloqueia a aprovação enquanto aberta. */
export interface ProfileProposalAlternative {
  id: string; rotulo: string; condicao: string; fundamento: string[];
  campos: { national_code?: string | null; municipal_code?: string | null; nbs_code?: string | null; issqn_taxation?: string | null; pis_cofins_cst?: string | null; ibs_cbs?: ServiceProfileIbsCbs | null };
}
export interface ProfileProposal {
  tipo: 'proposta_revisao';
  id: string;
  status: 'ABERTA' | 'APLICADA' | 'DESCARTADA' | string;
  resumo: string;
  alternativas: ProfileProposalAlternative[];
  perguntas: { id: string; texto: string; responsavel: string }[];
  proxima_acao: string;
  analise: Record<string, any>;
  registrada_em: string; registrada_por: string;
  alternativa_aplicada?: string | null; resolvida_por?: string | null; resolvida_em?: string | null; nota?: string | null;
}
/** Inventário dos serviços prestados (XML oficial das notas emitidas), agrupado por cTribNac + cNBS. */
export interface ServiceInventoryGroup {
  origin_key: string; cTribNac: string | null; cNBS: string | null;
  descricao_cTribNac: string | null; descricao_cNBS: string | null;
  total_notas: number; situacao: Record<string, number>; periodo: { de: string | null; ate: string | null };
  exemplos: { numero: string | null; competencia: string | null; chave: string; situacao: string; document_id: string }[];
  [k: string]: unknown;
}
export interface ServiceInventory { total_notas: number; periodo: { de: string | null; ate: string | null }; grupos: ServiceInventoryGroup[] }
export interface ProposalResolveRequest { expected_version: number; decision: 'APPLY' | 'DISCARD'; alternative?: string | null; note: string }
export interface InventoryVariant { valor?: string | null; notas: number; [k: string]: unknown }
export interface ProfileEvidence {
  total_notas: number;
  situacao: Record<string, number>;
  periodo: { de: string | null; ate: string | null };
  descricao_cTribNac?: string | null;
  descricao_cNBS?: string | null;
  descricoes_usadas: { texto: string; notas: number }[];
  cTribMun: InventoryVariant[];
  locais: { cLocPrestacao: string | null; prestacao: string | null; cLocIncid: string | null; incidencia: string | null; notas: number }[];
  iss: { tributacao: string | null; retencao: string | null; aliquota_aplicada: string | null; notas: number }[];
  pis_cofins: { CST: string | null; tpRetPisCofins: string | null; notas: number }[];
  retencoes_federais: Record<string, number>;
  obra: Record<string, number>;
  imovel: number;
  destinatario: number;
  info_complementar: number;
  ibs_cbs: { cIndOp: string; CST: string; cClassTrib: string; indFinal: string | null; notas: number; de: string | null; ate: string | null; cIndOp_descricao?: string | null; cIndOp_local?: string | null }[];
  ibs_cbs_ausente: number;
  exemplos: { numero: string | null; competencia: string | null; chave: string; situacao: string; document_id: string }[];
  divergencias: { code: string; message: string }[];
}
export interface ServiceProfile {
  id: string;
  name: string;
  national_code: string;
  municipal_code: string | null;
  nbs_code: string | null;
  description: string | null;
  issqn_taxation: 'TAXABLE' | 'IMMUNE' | 'EXPORT' | 'NOT_INCIDENT' | null;
  pis_cofins_cst: string | null;
  ibs_cbs: ServiceProfileIbsCbs | null;
  source: string | null;
  is_active: boolean;
  status: ServiceProfileStatus | string;
  origin_key: string | null;
  evidence: ProfileEvidence | VariantEvidence | null;
  field_sources: Record<string, FieldSource>;
  suggestions: Array<ProfileSuggestion | ProfileProposal>;
  valid_from: string | null;
  valid_to: string | null;
  approved_by: string | null;
  approved_at: string | null;
  version: number;
  updated_at: string;
  descriptions: Record<string, any>;
  pending: ProfilePendency[];
  can_approve: boolean;
}
export interface ServiceProfileVersionOut { version: number; action: string; snapshot: Record<string, any>; actor_type: string; actor_id: string | null; created_at: string }
export interface PrepareProfilesResult { groups: number; created: { origin_key: string; id: string }[]; existing: { origin_key: string; id: string | null; status: string }[] }
export interface ProviderDataField { tag: string; valores: { valor: string; notas: number }[]; consistente: boolean; notas_com_campo: number; referencia: { numero: string | null; chave: string } | null; origem?: string }
export interface ProviderDataFromDocuments { total_notas: number; campos: Record<string, ProviderDataField> }
export interface ProviderDataApplyResult { applied: Record<string, string>; equal: string[]; divergent: { field: string; cadastro: string; notas: string }[] }
export interface ServiceProfileInput {
  name: string;
  national_code: string;
  municipal_code?: string | null;
  nbs_code?: string | null;
  description?: string | null;
  issqn_taxation?: 'TAXABLE' | 'IMMUNE' | 'EXPORT' | 'NOT_INCIDENT' | null;
  pis_cofins_cst?: string | null;
  ibs_cbs?: ServiceProfileIbsCbs | null;
  source?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
}
export type ServiceProfilePatch = Partial<ServiceProfileInput> & { is_active?: boolean; expected_version: number; valid_from?: string | null; valid_to?: string | null };

/** Certificado do workspace — só metadados (nunca PFX, senha ou chave). */
export type CertificateState = 'ABSENT' | 'VALID' | 'EXPIRING' | 'EXPIRED' | 'CNPJ_MISMATCH';
export interface CertificateHistoryItem {
  environment: string;
  fingerprint_sha256: string;
  subject_cnpj: string | null;
  not_before: string;
  not_after: string;
  is_active: boolean;
  created_at: string;
  replaced_at: string | null;
  replaced_by_fingerprint: string | null;
  material_purged: boolean;
}
export interface CertificateStatus {
  status: CertificateState | string;
  active: { fingerprint_sha256: string; not_before: string; not_after: string; days_to_expiry: number; environments: string[]; subject_cnpj_matches: boolean } | null;
  history: CertificateHistoryItem[];
  note: string;
}

export interface Company {
  id: string;
  name: string;
  cnpj: string;
  timezone: string;
  environment?: 'mock' | 'restricted' | 'production' | string;
  permissions: string[];
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User & {
    companies?: Company[];
  };
}

export interface MeResponse {
  id: string;
  name: string;
  email: string;
  companies: Company[];
}

export interface CsrfResponse {
  csrf_token: string;
}

export interface Address {
  street?: string | null;
  number?: string | null;
  complement?: string | null;
  district?: string | null;
  city?: string | null;
  state?: string | null;
}

export interface Customer {
  id: string;
  company_id: string;
  name: string;
  document_type: 'CNPJ' | 'CPF';
  document: string;
  cnpj_cpf?: string; // Compatibilidade retroativa de leitura
  email?: string | null;
  phone?: string | null;
  municipal_registration?: string | null;
  municipality_code?: string | null;
  postal_code?: string | null;
  address?: Address | null;
  is_active: boolean;
  version: number;
  created_at: string;
  updated_at: string;
}

/** Tomador de uma nota emitida, lido do XML oficial (GET /documents/{id}/recipient). Nada é inventado: o que não
 * consta no XML vem nulo e é listado em `missing`. */
export interface RecipientSuggestion {
  document_type: 'CNPJ' | 'CPF' | null;
  document: string | null;
  name: string | null;
  municipal_registration: string | null;
  email: string | null;
  phone: string | null;
  municipality_code: string | null;
  postal_code: string | null;
  address: { street: string | null; number: string | null; complement: string | null; district: string | null };
  foreign: boolean;
  source: 'XML' | 'DOCUMENT';
  registered_customer_id: string | null;
  missing: string[];
}

export interface CreateCustomerRequest {
  name: string;
  document_type: 'CNPJ' | 'CPF';
  document: string;
  email?: string | null;
  phone?: string | null;
  municipal_registration?: string | null;
  municipality_code?: string | null;
  postal_code?: string | null;
  address?: Address | null;
}

export interface UpdateCustomerRequest {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  municipal_registration?: string | null;
  municipality_code?: string | null;
  postal_code?: string | null;
  address?: Address | null;
  is_active?: boolean | null;
  expected_version?: number | null;
}

export interface FiscalProfileOut {
  company_id: string;
  cnpj: string;
  name: string;
  timezone: string;
  environment: 'mock' | 'restricted' | 'production' | string;
  adn_environments: string[];
  municipality_code: string | null;
  municipal_registration: string | null;
  simples_option: '1' | '2' | '3' | null;
  simples_regime: '1' | '2' | '3' | null;
  special_regime: '0' | '1' | '2' | '3' | '4' | '5' | '6' | '9' | null;
  phone: string | null;
  email: string | null;
  source: string | null;
  updated_at: string | null;
  complete_for_emission: boolean;
  missing: string[];
  /** Série da DPS do workspace (aplicativo próprio: 1 a 49999). */
  dps_series?: string | null;
  postal_code?: string | null;
  address?: Address | null;
  workspace_status?: WorkspaceStatus | string | null;
}

export interface FiscalProfilePatch {
  municipality_code?: string | null;
  municipal_registration?: string | null;
  simples_option?: '1' | '2' | '3' | null;
  simples_regime?: '1' | '2' | '3' | null;
  special_regime?: '0' | '1' | '2' | '3' | '4' | '5' | '6' | '9' | null;
  phone?: string | null;
  email?: string | null;
  source?: string | null;
  environment?: 'mock' | 'restricted' | null;
  adn_environments?: string[] | null;
  dps_series?: string | null;
  postal_code?: string | null;
  address?: Address | null;
}

export interface CertificateInfo {
  company_id: string;
  environments: string[];
  subject_cnpj_matches: boolean;
  not_before: string;
  not_after: string;
  days_to_expiry: number;
  fingerprint_sha256: string;
  accepted: boolean;
  problems: Array<Record<string, string>>;
}

export interface IntegrationCertificate {
  present: boolean;
  subject_cnpj_matches?: boolean | null;
  not_before?: string | null;
  not_after?: string | null;
  days_to_expiry?: number | null;
}

export interface IntegrationStatus {
  company_id: string;
  environment: 'mock' | 'restricted' | 'production' | string;
  status: 'SIMULATED' | 'NOT_CONFIGURED' | 'BLOCKED' | 'READY' | string;
  simulated: boolean;
  provider: 'mock' | 'official' | string;
  certificate: IntegrationCertificate | null;
  capabilities: {
    emission?: boolean;
    distribution?: boolean;
    cancellation?: boolean;
    simulated?: boolean;
    can_issue?: boolean;
    [key: string]: boolean | undefined;
  };
  /** Chaves de configuração da instalação (funcionalidade habilitada); não indicam empresa pronta. */
  policy?: { emission?: boolean; cancellation?: boolean; distribution?: boolean } | null;
  blockers: Array<{ code?: string; message?: string; [key: string]: string | undefined }>;
  checked_at: string;
}

export type DocumentDirection = 'ISSUED' | 'RECEIVED';
export type DocumentStatus = 'AUTHORIZED' | 'CANCELLED' | 'UNKNOWN' | string;
export type QueueStatus =
  | 'DRAFT'
  | 'SCHEDULED'
  | 'PROCESSING'
  | 'AUTHORIZED'
  | 'REJECTED'
  | 'AWAITING_CONFIRMATION'
  | 'WITHDRAWN';
export type ReviewStatus = 'PENDING' | 'REVIEWED' | 'DIVERGENT';

export interface Retentions {
  iss: string | null;
  irrf: string | null;
  inss: string | null;
  pis: string | null;
  cofins: string | null;
  csll: string | null;
  pcc_total: string | null;
}

export interface RetentionIndicatorItem {
  status:
    | 'RETAINED'
    | 'NOT_RETAINED'
    | 'RETAINED_BY_TOMADOR'
    | 'RETAINED_BY_INTERMEDIARY'
    | 'INFORMED'
    | 'NOT_INFORMED'
    | string;
  source?: string | null;
  informed_amount?: string | null;
  informed_field?: string | null;
}

export type RetentionIndicators = Record<string, RetentionIndicatorItem | any>;

export interface DocumentParty {
  name: string;
  document?: string;
  cnpj?: string;
}

export interface FiscalDocument {
  id: string;
  direction: DocumentDirection;
  company_role?: string;
  number?: string | null;
  access_key?: string | null;
  dps_id?: string | null;
  status: DocumentStatus;
  status_code?: string;
  competence?: string | null;
  service_date?: string | null;
  issued_at?: string | null;
  processed_at?: string | null;
  issuer: DocumentParty;
  recipient?: DocumentParty | null;
  intermediary?: DocumentParty | null;
  description?: string | null;
  service?: {
    national_code?: string | null;
    municipal_code?: string | null;
    nbs_code?: string | null;
  } | null;
  amount?: string | null;
  deductions?: string | null;
  net_amount?: string | null;
  iss_amount?: string | null;
  total_retained?: string | null;
  retentions?: Retentions;
  retention_indicators?: RetentionIndicators;
  review_hints?: Array<{ code?: string; message?: string; [key: string]: unknown }>;
  review_status?: ReviewStatus | null;
  review_note?: string | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  version: number;
  xml_available: boolean;
  /** DANFSe gerado pela plataforma a partir do XML oficial (NT 008 v1.02); nunca para simulação. */
  danfse_available?: boolean;
  danfse_watermark?: 'CANCELADA' | 'SUBSTITUÍDA' | null;
  retention_summary?: DocumentRetentionSummary;
  source: 'ADN' | 'IMPORT' | 'EMISSION' | string;
  simulated: boolean;
  environment: 'mock' | 'restricted' | 'production' | string;
  customer_id?: string | null;
  status_code_label?: string | null;
  cancellation_event_type?: string | null;
  substitution?: SubstitutionOut | null;
  /** GET /documents/{id}: IBS/CBS CALCULADOS pela Sefin (infNFSe/IBSCBS), texto exato do XML. Não é a prévia local. */
  ibs_cbs_official?: IbsCbsOfficial | null;
  /** GET /documents/{id}: NFS-e emitida pela plataforma — estimativa do agendamento × valores oficiais da Sefin. */
  iss_reconciliation?: IssReconciliation | null;
}

export interface IssReconciliationLine {
  key: 'iss_rate' | 'iss_amount' | 'iss_retained' | 'total_withheld' | 'net_amount' | string;
  label: string;
  estimated?: string | null;
  official?: string | null;
  status: 'MATCH' | 'DIFFERENT' | 'ESTIMATE_MISSING' | 'OFFICIAL_MISSING';
  difference?: string | null;
}

export interface IssReconciliation {
  status: 'MATCH' | 'DIFFERENT' | 'PARTIAL';
  /** pAliq enviado na DPS (null = não enviado). */
  dps_rate?: string | null;
  /** Alíquota de referência interna (nunca enviada). */
  reference_rate?: string | null;
  rate_source?: 'DPS_PALIQ' | 'REFERENCE' | null;
  lines: IssReconciliationLine[];
  note: string;
}

export interface IbsCbsOfficial {
  /** caminho relativo ao grupo infNFSe/IBSCBS → texto do XML (ex.: "totCIBS/gCBS/vCBS": "132.30") */
  valores: Record<string, string>;
  /** códigos informados na DPS (cIndOp, CST, cClassTrib, indFinal, indDest, finNFSe) */
  dps: Record<string, string>;
  fonte: string;
}

export interface SubstitutionOut {
  substitutes_access_key?: string | null; // Chave da NFS-e substituída por esta (DPS/subst/chSubstda) -> esta nota é a substituta
  reason_code?: string | null; // subst/cMotivo
  substituted_by_access_key?: string | null; // Chave da NFS-e que substituiu esta -> esta nota é a substituída
}

export type RetentionClass = 'WITH' | 'WITHOUT' | 'UNKNOWN';
export type RetentionType = 'ISS' | 'INSS' | 'IRRF' | 'CONTRIB';
export type RetentionMatch = 'ANY' | 'ALL';

export interface DocumentFilter {
  direction?: DocumentDirection;
  query?: string;
  status?: DocumentStatus;
  review_status?: ReviewStatus;
  competence?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
  environment?: string;
  /** WITH = retenção efetiva; WITHOUT = sem retenção; UNKNOWN = informação desconhecida. */
  retention?: RetentionClass;
  /** Tipos de retenção efetiva. Com 2+ tipos, retention_match é obrigatório (ANY/ALL). */
  retention_type?: RetentionType[];
  retention_match?: RetentionMatch;
  withheld_min?: string;
  withheld_max?: string;
}

/** Resumo de retenções de NFS-e emitida/recebida (valores oficiais do XML; contribuições sem rateio). */
export interface DocumentRetentionSummary {
  classification: RetentionClass;
  iss: { retained: boolean; amount: string | null };
  inss: string | null;
  irrf: string | null;
  contributions: {
    amount: string | null;
    code: string | null;
    label: string | null;
    covered: { pis: boolean | null; cofins: boolean | null; csll: boolean | null } | null;
    consolidated: boolean;
    note: string;
  };
  informed_not_withheld: { pis: string | null; cofins: string | null };
  total_withheld: string | null;
  total_withheld_source: string | null;
}

export type BundleFormat = 'PDF' | 'XML' | 'PDF_XML';

export interface BundleRequest {
  format: BundleFormat;
  environment?: string;
  /** Seleção explícita OU filters (todos os resultados, além da página atual). */
  document_ids?: string[];
  filters?: Omit<DocumentFilter, 'page' | 'page_size' | 'environment'>;
}

export interface DownloadTicket {
  ticket: string;
  expires_at: string;
  method: 'POST';
  action: string;
  field: 'ticket';
}

export interface RetentionPreviewRequest {
  amount: string;
  deductions?: string | null;
  unconditional_discount?: string | null;
  conditional_discount?: string | null;
  tax_settings?: TaxSettings | null;
}

export interface ReviewDocumentRequest {
  review_status: ReviewStatus;
  note?: string;
  expected_version: number;
}

export interface ObraAddress {
  postal_code: string;
  street: string;
  number: string;
  complement?: string | null;
  district: string;
}

/** serv/infoCompl (TCInfoCompl) — todos opcionais. */
export interface AdditionalInfo {
  technical_doc?: string | null; // idDocTec (ART, RRT, DRT…), até 40
  document_reference?: string | null; // docRef, até 255
  order_number?: string | null; // xPed, até 60
  order_items?: string[] | null; // gItemPed/xItemPed, 1 a 99 itens de até 60
  notes?: string | null; // xInfComp, até 2000
}

export interface DraftService {
  national_code?: string | null;
  municipal_code?: string | null;
  description: string;
  nbs_code?: string | null;
  location_code?: string | null;
  /** serv/obra: escolha do XSD — cObra OU cCIB OU endereço (um só). */
  obra_code?: string | null;
  obra_address?: ObraAddress | null;
  obra_cib?: string | null; // cCIB (8 caracteres)
  obra_property_registration?: string | null; // inscImobFisc (opcional, só com a obra)
  additional_info?: AdditionalInfo | null;
}

/** Destino da correção de uma pendência (devolvido pelo servidor). */
export type IssueTarget =
  | { kind: 'draft_field'; field: string }
  | { kind: 'customer'; customer_id?: string | null }
  | { kind: 'service_profile'; profile_id?: string | null }
  | { kind: 'company' }
  | { kind: 'certificate' };

export interface ValidationIssue {
  field: string;
  code: string;
  message: string;
  severity: 'error' | 'warning' | 'info';
  /** Código da regra oficial (Anexo I) ou tag da nota de referência, quando houver. */
  rule?: string | null;
  /** Detalhamento de apresentação (não muda regra nem severidade). */
  section?: string | null;
  label?: string | null;
  /** true = impede agendar/emitir. */
  blocking?: boolean | null;
  /** Texto OFICIAL da regra do Anexo I (sem alteração), quando houver. */
  reason?: string | null;
  reason_source?: string | null;
  official_message?: string | null;
  xml_path?: string | null;
  action?: string | null;
  target?: IssueTarget | null;
  /** Dados estruturados (ex.: alíquota do ISS — ver IssAliquotaDetails). */
  details?: IssAliquotaDetails | Record<string, unknown> | null;
}

/** Resultado da Parametrização para a alíquota do ISS (pendências PALIQ_* / ISS_* da validação). */
export interface IssAliquotaDetails {
  paliq_status: 'PROHIBITED' | 'REQUIRED' | 'OPTIONAL' | 'UNKNOWN';
  rule?: string | null;
  possible_rules?: string[];
  convenio?: 'ACTIVE' | 'INACTIVE' | 'UNAVAILABLE' | 'NOT_APPLICABLE' | null;
  convenio_detail?: string | null;
  municipio?: string | null;
  municipio_nome?: string | null;
  environment?: string | null;
  consulted_at?: string | null;
  municipal_rate?: string | null;
  municipal_rate_status?: 'FOUND' | 'NOT_FOUND' | 'UNAVAILABLE' | 'NOT_REQUESTED' | 'NOT_APPLICABLE' | null;
  service_code?: string | null;
  competence?: string | null;
  dps_rate?: string | null;
  reference_rate?: string | null;
}

export interface CustomerSnapshot {
  id: string;
  name: string;
  document: string;
  municipal_registration?: string | null;
}

export interface ApproxTax {
  mode: 'VALUES' | 'PERCENT' | 'SIMPLES_PERCENT' | 'NOT_INFORMED';
  federal?: string | null;
  state?: string | null;
  municipal?: string | null;
  simples?: string | null;
}

export interface IbsCbsRegular { cst: string; class_code: string }
export interface IbsCbsDeferral { uf: string; mun: string; cbs: string }
/** IBSCBS/imovel: bem imóvel da operação (cIndOp 020101/020201/020301 fora dos subitens de obra). ≠ serv/obra. */
export interface IbsCbsRealEstate { registration?: string | null; cib?: string | null; address?: ObraAddress | null }
export interface RecipientAddress {
  municipality_code: string;
  postal_code: string;
  street: string;
  number: string;
  complement?: string | null;
  district: string;
}
/** IBSCBS/dest: destinatário ≠ tomador (indDest=1). Ausente = o tomador é o destinatário. */
export interface IbsCbsRecipient {
  document_type: 'CNPJ' | 'CPF' | 'NIF' | 'NO_NIF';
  document?: string | null;
  no_nif_reason?: '0' | '1' | '2' | null;
  name: string;
  address?: RecipientAddress | null;
  phone?: string | null;
  email?: string | null;
}

export interface IbsCbs {
  operation_indicator: string;
  cst: string;
  class_code: string;
  personal_use?: '0' | '1' | null;
  regular?: IbsCbsRegular | null; // gTribRegular — só quando a classificação exige
  deferral?: IbsCbsDeferral | null; // gDif — só quando a classificação permite
  presumed_credit?: string | null; // cCredPres — nenhuma classificação vigente para NFS-e permite
  real_estate?: IbsCbsRealEstate | null;
  recipient?: IbsCbsRecipient | null;
}

export interface TaxSettings {
  issqn_taxation?: 'TAXABLE' | 'IMMUNE' | 'EXPORT' | 'NOT_INCIDENT' | null;
  iss_retention?: 'NOT_RETAINED' | 'RETAINED_BY_TOMADOR' | 'RETAINED_BY_INTERMEDIARY' | null;
  /** pAliq ENVIADO na DPS. */
  iss_rate?: string | null;
  /** Alíquota de REFERÊNCIA (%): só para a estimativa de ISS, retenção e líquido; nunca vai na DPS. */
  iss_reference_rate?: string | null;
  pis_cofins_retention?: '0' | '1' | '2' | '3' | '4' | '5' | '6' | '7' | '8' | '9' | null;
  irrf_withheld?: string | null;
  inss_withheld?: string | null;
  csll_withheld?: string | null;
  pis_cofins_cst?: string | null;
  pis_cofins_base?: string | null;
  pis_rate?: string | null;
  cofins_rate?: string | null;
  pis_amount?: string | null;
  cofins_amount?: string | null;
  immunity_type?: '1' | '2' | '3' | '4' | '5' | null;
  approx_tax?: ApproxTax | null;
  ibs_cbs?: IbsCbs | null;
}

export interface RetentionIss {
  retention?: string | null;
  retained: boolean;
  base: string;
  /** Alíquota usada na ESTIMATIVA: o pAliq enviado ou, sem ele, a referência. */
  rate?: string | null;
  rate_source?: 'DPS_PALIQ' | 'REFERENCE' | null;
  /** pAliq enviado na DPS (null = não enviado). */
  dps_rate?: string | null;
  /** Alíquota de referência interna (não vai na DPS). */
  reference_rate?: string | null;
  /** vISSQN estimado (retido ou não). */
  estimated_amount?: string | null;
  amount?: string | null;
  amount_source: 'NOT_RETAINED' | 'ESTIMATED_FROM_RATE' | 'ESTIMATED_FROM_REFERENCE' | 'DETERMINED_BY_SEFIN' | string;
}

export interface RetentionCovered {
  pis?: boolean | null;
  cofins?: boolean | null;
  csll?: boolean | null;
}

export interface RetentionFederal {
  inss?: string | null;
  irrf?: string | null;
  contributions?: string | null;
  contributions_code?: string | null;
  contributions_label?: string | null;
  contributions_covered?: RetentionCovered | null;
  total: string;
  /** Modalidade 1: vPis/vCofins apresentados como retidos pela NT 008 v1.02; ainda não somados ao total da prévia. */
  code1_pis_cofins?: { pis: string | null; cofins: string | null; status: 'UNCONFIRMED_IN_TOTAL' } | null;
}

export interface RetentionInformed {
  pis?: string | null;
  cofins?: string | null;
}

export interface RetentionSummary {
  service_amount: string;
  unconditional_discount: string;
  conditional_discount: string;
  deductions: string;
  iss: RetentionIss;
  federal: RetentionFederal;
  informed_not_withheld: RetentionInformed;
  total_withheld: string;
  net_amount: string;
  complete: boolean;
  pending: string[];
  /** Sempre estimativa antes da autorização; o oficial vem da NFS-e (iss_reconciliation). */
  basis?: 'ESTIMATE';
}

export interface DraftValidation {
  status: 'NOT_VALIDATED' | 'VALID' | 'INVALID';
  validated_version?: number | null;
  validated_at?: string | null;
}

/** Resultado de "Excluir rascunho" (exclusão lógica; repetir devolve o mesmo resultado). */
export interface DraftDeleted { id: string; deleted: boolean; deleted_at: string; version: number; already_deleted: boolean }
/** Se o rascunho pode ser excluído agora e, se não, o motivo (pelo histórico real dos itens de emissão). */
export interface DraftDeletion {
  allowed: boolean;
  /** DRAFT_SCHEDULED | DRAFT_PROCESSING | DRAFT_UNCERTAIN | DRAFT_AUTHORIZED | DRAFT_TRANSMITTED | DRAFT_HAS_SUBMISSION | DRAFT_LOCKED | DRAFT_DELETED */
  reason_code?: string | null;
  reason?: string | null;
}
export interface Draft {
  id: string;
  customer_id: string;
  customer?: CustomerSnapshot | null;
  customer_name?: string;
  source_document_id?: string | null;
  status: QueueStatus;
  version: number;
  competence: string; // YYYY-MM
  service_date?: string | null; // YYYY-MM-DD
  /** Perfil de serviço aplicado e a versão vigente na aplicação (registrada pelo servidor). */
  service_profile_id?: string | null;
  service_profile_version?: number | null;
  /** Origem: nota de referência (XML oficial) ou preenchimento manual, e a configuração tributária da operação. */
  origin?: DraftOrigin | null;
  service: DraftService;
  amount: string; // Decimal string
  deductions?: string; // Decimal string
  unconditional_discount?: string | null;
  conditional_discount?: string | null;
  tax_settings?: TaxSettings | null;
  retention_summary?: RetentionSummary;
  issues?: ValidationIssue[];
  validation?: DraftValidation;
  editable?: boolean;
  environment?: 'mock' | 'restricted' | 'production' | string;
  /** Último item de emissão vinculado; pode ser histórico (item encerrado). Para excluir, use `deletion`. */
  current_item_id?: string | null;
  deletion?: DraftDeletion | null;
  /** Rascunho emitido: nota e situação atual. */
  issued?: DraftIssued | null;
  created_at: string;
  updated_at: string;
}

export interface DraftOriginPending {
  field: string; code: string; message: string; severity: 'error' | 'warning' | 'info' | string;
  while_empty?: boolean; tag?: string | null;
}
/** Resolução da configuração tributária da operação do rascunho. */
export interface DraftOperation {
  resolution: 'REUSED_APPROVED' | 'LINKED_PENDING' | 'CREATED_VARIANT' | 'INACTIVE_EQUIVALENT' | 'NOT_REGISTERED' | string;
  profile_id?: string | null; profile_name?: string | null; profile_status?: string | null; profile_version?: number | null;
  divergent_from?: { id: string; name: string; version: number; fields: string[] }[];
  errors?: { field: string; code: string; message: string }[];
  at?: string;
}
export interface DraftOrigin {
  kind: 'document' | 'manual' | string;
  document?: { id: string; status: string; number: string | null; competence: string | null; access_key: string | null };
  /** Campo do rascunho → origem (xml, simulacao) e tag oficial lida. */
  fields?: Record<string, { origem: string; tag?: string; confirmar?: boolean }>;
  not_copied?: { campo: string; motivo: string }[];
  pending?: DraftOriginPending[];
  ibs_cbs_source?: Record<string, string>;
  operation?: DraftOperation;
}

export interface CreateDraftRequest {
  customer_id: string;
  competence: string;
  service_date?: string | null;
  service_profile_id?: string | null;
  service: DraftService;
  amount: string;
  deductions?: string;
  unconditional_discount?: string | null;
  conditional_discount?: string | null;
  tax_settings?: TaxSettings;
}

export interface UpdateDraftRequest {
  expected_version: number;
  customer_id?: string;
  competence?: string;
  service_date?: string | null;
  service_profile_id?: string | null;
  service?: DraftService;
  amount?: string;
  deductions?: string;
  unconditional_discount?: string | null;
  conditional_discount?: string | null;
  tax_settings?: TaxSettings;
}

export interface CreateDraftFromDocumentRequest {
  source_document_id: string;
}

export interface ValidateDraftResponse {
  valid: boolean;
  issues: ValidationIssue[];
  /** Versão do rascunho a que as pendências se referem. */
  validated_version?: number | null;
}

export type BatchStatus =
  | 'SCHEDULED'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'COMPLETED_WITH_REJECTIONS'
  | 'WITHDRAWN';

export interface BatchItemError {
  code: string;
  message: string;
  complement?: string | null;
}

export interface BatchItemResult {
  number?: string | null;
  access_key?: string | null;
  authorized_at?: string | null;
  simulated?: boolean;
  label?: string | null;
  errors?: BatchItemError[];
  [key: string]: unknown;
}

export interface BatchItem {
  id: string;
  draft_id: string;
  draft_version: number;
  status: QueueStatus;
  competence?: string | null;
  customer_name?: string | null;
  amount?: string | null;
  dps_id?: string | null;
  attempts: number;
  next_attempt_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  late_seconds?: number | null;
  last_error?: { code: string; message: string } | null;
  result?: BatchItemResult | null;
  document_id?: string | null;
  simulated: boolean;
  total_withheld?: string | null;
  net_amount?: string | null;
  /** Diagnóstico (somente leitura): etapa, erros com origem, referências, rascunho de origem e orientação. */
  stage?: 'QUEUED' | 'BLOCKED_BEFORE_SEND' | 'SENDING' | 'UNCERTAIN' | 'LOCAL_VALIDATION' | 'OFFICIAL_REJECTION' | 'AUTHORIZED' | 'WITHDRAWN' | string | null;
  stage_label?: string | null;
  needs_attention?: boolean | null;
  guidance?: string | null;
  /** false/true quando o fluxo garante; null = só a reconciliação oficial (consulta da DPS) pode dizer. */
  transmitted?: boolean | null;
  errors?: { code: string; message?: string | null; source: 'LOCAL' | 'OFFICIAL' | 'PROCESSING' | string }[];
  customer_document?: string | null;
  scheduled_at?: string | null;
  updated_at?: string | null;
  submission_id?: string | null;
  dps_series?: string | null;
  dps_number?: number | null;
  draft?: { id: string; current_version: number; status: string; deleted: boolean; linked: boolean } | null;
}

export interface Batch {
  id: string;
  status: BatchStatus;
  /** NOW = emitir agora; SCHEDULED = agendado. */
  mode?: 'NOW' | 'SCHEDULED';
  scheduled_at: string;
  scheduled_local?: string;
  timezone: string;
  environment: 'mock' | 'restricted' | 'production' | string;
  simulated: boolean;
  total: number;
  counts: Record<string, number>;
  progress: {
    done: number;
    total: number;
    percent: number;
  };
  created_at: string;
  created_by?: string;
  items?: BatchItem[];
  /** Há item rejeitado não resolvido ou bloqueado antes do envio. */
  needs_attention?: boolean | null;
}

export interface CreateBatchRequest {
  drafts: Array<{
    id: string;
    expected_version: number;
  }>;
  /** NOW = emitir agora (instante do servidor; scheduled_at ignorado); SCHEDULED (padrão) = agendar. */
  mode?: 'NOW' | 'SCHEDULED';
  scheduled_at?: string;
  timezone: string;
  /** Obrigatória em PRODUÇÃO: o servidor confere empresa, ambiente, documentos (versão e valor), quantidade e total. */
  production_confirmation?: ProductionConfirmation;
}

export interface ProductionConfirmation {
  company_cnpj: string;
  environment: 'production';
  count: number;
  total_amount: string;
  documents: Array<{ id: string; version: number; amount: string }>;
  /** Ação confirmada: precisa ser a mesma do pedido (confirmar agendamento não autoriza emitir agora). */
  mode?: 'NOW' | 'SCHEDULED';
}

export interface CancelPendingOut {
  batch_id: string;
  withdrawn: number;
  not_withdrawn: number;
  cancelled_count?: number; // Compatibilidade de transição
}

export type SyncState =
  | 'PENDING'
  | 'NOT_CONFIGURED'
  | 'BLOCKED'
  | 'RUNNING'
  | 'DRAINING'
  | 'OK'
  | 'ERROR';

export interface SyncError {
  code: string;
  message: string;
  happened_at?: string;
}

export interface SyncStatus {
  enabled: boolean;
  interval_hours: number;
  last_success_at: string | null;
  last_attempt_at: string | null;
  next_run_at: string | null;
  status: SyncState;
  environment: 'mock' | 'restricted' | 'production' | string;
  simulated: boolean;
  processed_count: number;
  received_count: number;
  pending_parse_count: number;
  coverage_message: string;
  last_error: SyncError | null;
  running: boolean;
}

export interface SyncRunResponse {
  job_id: string;
  status: 'QUEUED';
}

export type JobStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED';

export interface JobError {
  file?: string | null;
  code: string;
  message: string;
}

export interface JobStatusResponse {
  id: string;
  type?: string;
  status: JobStatus;
  environment?: string;
  processed_count: number;
  error_count: number;
  errors: JobError[];
  errors_page?: number;
  errors_page_size?: number;
  errors_total_stored?: number;
  errors_truncated?: boolean;
  result?: Record<string, unknown> | null;
  download_available?: boolean;
  /** Notas do ZIP/XLSX cancelada(s) ou substituída(s) depois do início da geração: download recusado (409). */
  stale_documents?: number;
  stale_pdf_documents?: number;
  output_expires_at?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface DashboardDocumentsSummary {
  issued_total?: number;
  received_total?: number;
  received_pending_review?: number;
  received_divergent?: number;
  cancelled?: number;
  [key: string]: number | undefined;
}

export interface Dashboard {
  environment: 'mock' | 'restricted' | 'production' | string;
  simulated: boolean;
  documents: DashboardDocumentsSummary;
  drafts: Record<string, number>;
  queue: Record<string, number>;
  next_scheduled_at: string | null;
  sync: SyncStatus;
  /** Contagens pela classificação das listas (rascunhos, lotes e itens ativos/que exigem atenção). */
  views?: { drafts?: Record<string, number>; batches?: Record<string, number>; items?: Record<string, number> } | null;
}

export interface DashboardCounters extends Dashboard {
  drafts_count: number;
  scheduled_count: number;
  issued_count: number;
  rejected_count: number;
  received_pending_count: number;
  last_sync_at: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
  /** Visão aplicada no servidor e o total de cada visão (mesma classificação do painel). */
  view?: string;
  counts?: Record<string, number>;
}
export type DraftView = 'pending' | 'issued' | 'all';
export type BatchView = 'in_progress' | 'attention' | 'history' | 'all';
/** Nota de um rascunho emitido, com a situação ATUAL (o rascunho não é reaberto). */
export interface DraftIssued {
  document_id: string | null; number?: string | null; access_key?: string | null; authorized_at?: string | null;
  status: 'AUTHORIZED' | 'CANCELLED' | 'SUBSTITUTED' | string;
}

export interface ProblemFieldError {
  field?: string | null;
  code: string;
  message: string;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string | null;
  instance?: string | null;
  code?: string;
  trace_id?: string;
  errors?: ProblemFieldError[] | Record<string, string[]> | null;
}

export function formatFieldErrors(
  errors?: ProblemFieldError[] | Record<string, string[]> | null
): Record<string, string[]> {
  if (!errors) return {};
  if (Array.isArray(errors)) {
    const result: Record<string, string[]> = {};
    for (const err of errors) {
      const key = err.field || '_general';
      if (!result[key]) result[key] = [];
      result[key].push(err.message || err.code);
    }
    return result;
  }
  return errors;
}

export class ProblemDetailsException extends Error {
  constructor(public problem: ProblemDetails) {
    super(problem.detail || problem.title);
    this.name = 'ProblemDetailsException';
  }

  /**
   * Mapeia os erros por campo retornados pelo backend (ProblemFieldError[] ou Record<string, string[]>)
   * para um dicionário padronizado { [campo]: string[] }.
   */
  formatFieldErrors(): Record<string, string[]> {
    return formatFieldErrors(this.problem.errors);
  }

  /**
   * Retorna a mensagem de erro consolidada de um campo específico, ou null se não houver erro no campo.
   */
  getFieldErrorMessage(field: string): string | null {
    const mapped = this.formatFieldErrors();
    return mapped[field]?.join(', ') || null;
  }

  /**
   * Formata lista legível de todos os erros de validação (ex: "nome: Mínimo 3 caracteres").
   */
  formatErrorsSummary(): string[] {
    const mapped = this.formatFieldErrors();
    return Object.entries(mapped).map(([field, msgs]) =>
      field === '_general' ? msgs.join(', ') : `${field}: ${msgs.join(', ')}`
    );
  }
}


// ---------------------------------------------------------------------------
// Administração de usuários (/api/core/v1/admin/*)
// ---------------------------------------------------------------------------

export type AdminPermission =
  | 'view' | 'import' | 'sync' | 'review' | 'prepare' | 'schedule' | 'emit' | 'cancel' | 'certificates' | 'admin' | string;

export type AdminUserStatus = 'ACTIVE' | 'INACTIVE' | 'PENDING';

export interface AdminMembership {
  company_id: string;
  company_name: string;
  company_cnpj: string;
  company_active: boolean;
  permissions: AdminPermission[];
}

export interface AdminUser {
  id: string;
  name: string;
  email: string;
  status: AdminUserStatus | string;
  is_active: boolean;
  is_super_admin: boolean;
  /** Permissão global de cadastrar workspaces (só o SUPER_ADMIN concede). */
  can_create_workspace?: boolean;
  version: number;
  /** Somente vínculos dentro do escopo de quem consulta. */
  memberships: AdminMembership[];
  /** Quantidade de vínculos fora do escopo de quem consulta (sem detalhes). */
  other_companies: number;
  can_manage_identity: boolean;
  active_sessions: number;
  password_set_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminPermissionOption {
  id: AdminPermission;
  label: string;
}

export interface AdminMeta {
  is_super_admin: boolean;
  admin_company_ids: string[];
  permissions: AdminPermissionOption[];
}

export interface AdminCompany {
  id: string;
  name: string;
  cnpj: string;
}

export interface CredentialLink {
  purpose: 'ACTIVATE' | 'RESET';
  expires_at: string;
  /** Caminho relativo com o token no fragmento (ex.: "/ativar#token=..."). Exibir uma única vez; nunca armazenar. */
  path: string;
}

export interface AdminUserFilter {
  query?: string;
  status?: AdminUserStatus | '';
  company_id?: string;
  permission?: string;
  super_admin?: boolean;
  page?: number;
  page_size?: number;
}

export interface AdminMembershipInput {
  company_id: string;
  permissions: AdminPermission[];
}

export interface CreateAdminUserRequest {
  name: string;
  email: string;
  memberships: AdminMembershipInput[];
}

export interface CreateAdminUserResponse {
  user: AdminUser;
  credential_link: CredentialLink;
}

export interface UpdateAdminUserRequest {
  expected_version: number;
  name?: string;
  email?: string;
  is_active?: boolean;
}


// ---------- Cancelamento de NFS-e (eventos do emitente 101101 / 101103) ----------
/** CANCEL = evento 101101 (cancelamento); ANALYSIS = evento 101103 (solicitação de análise fiscal ao município). */
export type CancellationKind = 'CANCEL' | 'ANALYSIS';
export type CancellationStatus =
  | 'QUEUED' | 'PROCESSING' | 'AWAITING_CONFIRMATION' | 'REGISTERED' | 'REJECTED' | 'BLOCKED' | 'WITHDRAWN';

export interface CancellationIssue { code: string; message: string }

export interface CancellationRequestOut {
  id: string;
  document_id: string;
  environment: 'mock' | 'restricted' | 'production';
  access_key: string;
  kind: CancellationKind;
  event_code: string;
  reason_code: string;
  reason_label: string | null;
  reason_text: string;
  status: CancellationStatus;
  simulated: boolean;
  attempts: number;
  requested_by: string;
  requested_at: string;
  finished_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  errors: { code?: string; message?: string; complement?: string | null }[];
  result: Record<string, unknown> | null;
  /** Recusa por parâmetro do município (E0822/E0823/E0824/E0827): cabe a análise fiscal. */
  suggest_analysis: boolean;
  /** Solicitação de análise fiscal registrada: situação acompanhada pelos eventos 105104/105105 do município. */
  analysis?: { situacao: 'PENDENTE' | 'DEFERIDA' | 'INDEFERIDA' | string; consultado_em?: string | null; label: string } | null;
  /** Orientação objetiva para o código de recusa (E0840, E0822…). */
  guidance?: string | null;
}

export interface CancellationInfo {
  document: {
    id: string; number: string | null; access_key: string; environment: 'mock' | 'restricted' | 'production';
    status: string; issued_at: string | null; amount: string | null; recipient_doc: string | null;
    recipient_name: string | null; issuer_doc: string | null; direction: string; simulated: boolean;
  };
  company: { id: string; name: string; cnpj: string };
  eligible: Record<CancellationKind, boolean>;
  blockers: Record<CancellationKind, CancellationIssue[]>;
  warnings: CancellationIssue[];
  events: { event_type: string; description: string | null; occurred_at: string | null; cancels_document: boolean; simulated: boolean }[];
  requests: CancellationRequestOut[];
  reasons: { code: string; label: string }[];
  kinds: { code: CancellationKind; label: string }[];
  substitution_note: string;
  /** O usuário tem a permissão "cancel" nesta empresa (a decisão final é sempre do servidor). */
  can_request: boolean;
}

export interface CancellationCreateRequest {
  kind: CancellationKind;
  reason_code: '1' | '2' | '9';
  reason_text: string;
  /** Eco do que foi exibido na confirmação; o servidor recusa (409 CONFIRMATION_MISMATCH) se divergir. */
  confirm: { access_key: string; environment: 'mock' | 'restricted' | 'production'; company_cnpj: string; document_number: string };
}


// ---------- IBS/CBS: classificação oficial, alíquotas com vigência e prévia (a DPS não leva alíquota/valor) ----------
export interface TaxClassification {
  codigo: string;
  cst: string;
  nome: string;
  cst_nome: string;
  reducao_ibs: string;
  reducao_cbs: string;
  tributacao_integral: boolean;
  exige_tributacao_regular: boolean;
  permite_diferimento?: boolean;
  permite_credito_presumido?: boolean;
}
export interface TaxClassificationPage { fonte: string; publicacao: string; em: string; items: TaxClassification[] }
export interface IbsCbsPreview {
  status: 'CALCULADO' | 'PENDENTE' | 'NAO_APLICAVEL_SIMPLES';
  fato_gerador: string | null;
  aliquotas: { ibs_uf: string; ibs_mun: string; cbs: string; unidade: string; vigencia: string[]; fonte: string; autorizacao: string } | null;
  base: Record<string, string> | null;
  valores: { vIBSUF: string; vIBSMun: string; vIBSTot: string; vCBS: string; vLiq_inalterado: string | null; vTotNF_2026: string | null } | null;
  classificacao: { codigo: string; cst: string; nome: string; reducao_ibs: string; reducao_cbs: string } | null;
  pendencias: { code: string; message: string }[];
  observacoes: string[];
  calculado_por: string;
}
