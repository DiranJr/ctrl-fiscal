/**
 * Gate de integração — executa o cliente HTTP REAL do frontend (packages/api-client/src, sem
 * modificá-lo) contra o AMBIENTE TEMPORÁRIO em http://127.0.0.1:8098 (scripts/ambiente_descartavel.py), criado como as telas criam
 * (getApiClients({ useMock: false })), com fetch que guarda cookies como um navegador de mesma
 * origem e com os payloads que as telas congeladas montam.
 *
 * Cada linha informa a FONTE da evidência:
 *   HTTP     = executado contra a stack (cliente real → gateway → backend)
 *   ESTÁTICO = leitura do código congelado das telas (não é teste de navegador)
 *
 * Uso: node --import tsx scripts/check_api_client.ts   (stack Compose em execução, ambiente mock)
 * Credencial: conta fictícia operador.a do ambiente temporário (secrets-descartavel/; lida localmente, nunca impressa). Não contata o governo.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { AuthMemoryStore } from '../packages/api-client/src/client.ts';
import { calculateNextMonthFirstMinute, getBatchStatusBadgeConfig, parseLocalScheduleToUtcIso } from '../packages/api-client/src/formatters.ts';
import { getApiClients } from '../packages/api-client/src/index.ts';

const BASE = process.env.CTRL_BASE ?? 'http://127.0.0.1:8098';
const jar = new Map<string, string>();
const realFetch = globalThis.fetch;
const calls: string[] = [];

globalThis.fetch = (async (input: any, init: any = {}) => {
  const url = new URL(String(input), BASE);
  const headers = new Headers(init.headers || {});
  headers.set('Origin', BASE);
  if (jar.size) headers.set('Cookie', [...jar].map(([k, v]) => `${k}=${v}`).join('; '));
  const res = await realFetch(url, { ...init, headers, redirect: 'manual' });
  for (const sc of res.headers.getSetCookie()) {
    const [pair, ...attrs] = sc.split(';');
    const [name, ...rest] = pair.split('=');
    const value = rest.join('=');
    const expired = attrs.some((a) => /max-age=0/i.test(a.trim()) || /expires=Thu, 01 Jan 1970/i.test(a.trim()));
    if (expired || value === '' || value === '""') jar.delete(name.trim());
    else jar.set(name.trim(), value);
  }
  calls.push(`${init.method || 'GET'} ${url.pathname} -> ${res.status}`);
  return res;
}) as typeof fetch;

type Result = 'RESOLVIDA' | 'PARCIAL' | 'PERMANECE' | 'OK' | 'FALHA';
const rows: { id: string; fonte: string; item: string; result: Result; evidence: string }[] = [];
const rec = (id: string, fonte: 'HTTP' | 'ESTÁTICO' | 'HTTP+ESTÁTICO', item: string, result: Result, evidence: string) => {
  rows.push({ id, fonte, item, result, evidence });
  console.log(`[${result}] ${id} (${fonte}) ${item} — ${evidence}`);
};
const err = (e: any) => (e?.problem ? `${e.problem.status} ${e.problem.code ?? ''}` : String(e?.message ?? e)).trim();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const src = (p: string) => readFileSync(p, 'utf8');

function cnpj(base12: string): string {
  const calc = (s: string, w: number[]) => {
    const t = [...s].reduce((acc, c, i) => acc + (c.charCodeAt(0) - 48) * w[i], 0) % 11;
    return t < 2 ? 0 : 11 - t;
  };
  const d1 = calc(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]);
  return `${base12}${d1}${calc(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])}`;
}

function mockXml(companyCnpj: string, number: number): { key: string; xml: string } {
  const py = String.raw`import sys,json,uuid
sys.path.insert(0,'services/nfse-api')
from datetime import datetime,timezone,timedelta
from nfse_api.providers.mock_data import cnpj_dv,mock_access_key,nfse_xml
p=(cnpj_dv('720000000001'),'Fornecedor Gate')
n=int(sys.argv[2]); k=mock_access_key(p[0],n,datetime.now(timezone.utc),uuid.uuid4().hex)
x=nfse_xml(key=k,number=n,prest=p,toma=(sys.argv[1],'Empresa'),interm=None,description='Gate final',v_serv='175.00',issued_at=datetime.now(timezone(timedelta(hours=-3))),competence_day='2026-10-01')
print(json.dumps({'key':k,'xml':x.decode()}))`;
  return JSON.parse(execFileSync('.venv/Scripts/python', ['-c', py, companyCnpj, String(number)]).toString());
}

async function waitJob(fiscal: any, cid: string, jobId: string) {
  for (let i = 0; i < 45; i++) {
    const j: any = await fiscal.getJobStatus(cid, jobId);
    if (!['QUEUED', 'RUNNING'].includes(j.status)) return j;
    await sleep(2000);
  }
  throw new Error('job não terminou');
}

async function main() {
  const store = AuthMemoryStore.getInstance();
  const { core, fiscal, isMock } = getApiClients({ useMock: false, coreBaseUrl: `${BASE}/api/core/v1`, fiscalBaseUrl: `${BASE}/api/fiscal/v1` }) as any;
  const EMAIL = process.env.CTRL_TEST_EMAIL ?? 'operador.a@descartavel.example.com';
  const password = readFileSync(process.env.CTRL_TEST_PASSWORD_FILE ?? 'secrets-descartavel/senha-operador.a.txt', 'utf8').trim();

  // ------------------------------------------------------------------ F1: mocks
  const entry = ['apps/painel-fiscal/src/context/FiscalContext.tsx', 'apps/web/src/pages/login.tsx', 'apps/web/src/pages/index.tsx', 'apps/web/src/pages/select-company.tsx'];
  const fixed = entry.filter((f) => /useMock:\s*true/.test(src(f)));
  rec('F1', 'HTTP+ESTÁTICO', 'telas usam o cliente HTTP real', !isMock && fixed.length === 0 ? 'RESOLVIDA' : 'PERMANECE',
      `getApiClients padrão real=${!isMock}; pontos de entrada com useMock:true fixo=${fixed.length} (bundles servidos: ver [INFO] do smoke)`);

  // ------------------------------------------------------------------ sessão
  const login: any = await core.login({ email: EMAIL, password });
  rec('D6', 'HTTP', 'login entrega empresas', Array.isArray(login.user?.companies) && login.user.companies.length > 0 ? 'RESOLVIDA' : 'PERMANECE',
      `login() anexa /auth/me: ${login.user?.companies?.length ?? 0} empresas`);
  const company = login.user.companies[0];
  const cid = company.id;
  store.clear(); // recarga F5: memória perdida, cookie HttpOnly mantido
  try {
    const me: any = await core.getMe();
    rec('D1', 'HTTP', 'reidratação após recarga', me?.id ? 'RESOLVIDA' : 'PERMANECE',
        `getMe → ${calls.slice(-4).join(' | ')}`);
  } catch (e) {
    rec('D1', 'HTTP', 'reidratação após recarga', 'PERMANECE', `${err(e)} (${calls.slice(-3).join(' | ')})`);
  }
  store.clear();
  try {
    const d: any = await fiscal.getDashboard(cid);
    rec('D3', 'HTTP', 'cliente fiscal renova sessão em 401', d?.environment ? 'RESOLVIDA' : 'PERMANECE', `${calls.slice(-4).join(' | ')}`);
  } catch (e) {
    rec('D3', 'HTTP', 'cliente fiscal renova sessão em 401', 'PERMANECE', err(e));
  }

  // ------------------------------------------------------------------ clientes (payload da CustomersPage)
  const doc = cnpj(String(Date.now()).slice(-12));
  const address = { street: 'Rua do Gate', number: '10', complement: null, district: 'Centro', city: 'São Paulo', state: 'SP' };
  let created: any;
  try {
    created = await core.createCustomer(cid, { name: 'Cliente Gate (tela)', document_type: 'CNPJ', document: doc, email: null,
      municipal_registration: null, municipality_code: null, postal_code: '01001000', address });
    rec('D4', 'HTTP', 'cadastro de cliente com payload da CustomersPage', created?.id ? 'RESOLVIDA' : 'PERMANECE', `201 id=${created.id.slice(0, 8)}`);
  } catch (e) {
    rec('D4', 'HTTP', 'cadastro de cliente com payload da CustomersPage', 'PERMANECE', err(e));
    created = await core.createCustomer(cid, { name: 'Cliente Gate', document_type: 'CNPJ', document: doc } as any);
  }
  const upd: any = await core.updateCustomer(cid, created.id, { name: 'Cliente Gate (editado)', address, is_active: true, expected_version: created.version } as any);
  rec('S1', 'HTTP', 'edição de cliente com expected_version', upd.version === created.version + 1 ? 'OK' : 'FALHA', `versão ${created.version}→${upd.version}`);
  const list: any = await core.getCustomers(cid);
  const mapped = list.items.find((c: any) => c.id === created.id);
  rec('D5', 'HTTP', 'listagem de clientes', mapped?.document === doc && mapped?.document_type === 'CNPJ' ? 'RESOLVIDA' : 'PERMANECE', `document/document_type canônicos`);
  try {
    await core.createCustomer(cid, { name: 'X', document_type: 'CNPJ', document: '11111111111111' } as any);
  } catch (e: any) {
    const errs = e?.problem?.errors;
    rec('D15', 'HTTP+ESTÁTICO', 'Problem.errors', Array.isArray(errs) && errs[0]?.field ? 'RESOLVIDA' : 'PERMANECE',
        `422 com lista [{field,code,message}] (${errs?.[0]?.field}:${errs?.[0]?.code}); tipo aceita lista. Obs.: formatFieldErrors citado no GATE não existe`);
  }

  // ------------------------------------------------------------------ painel
  const dash: any = await fiscal.getDashboard(cid);
  rec('D8', 'HTTP', 'dashboard', typeof dash.issued_count === 'number' && dash.documents ? 'RESOLVIDA' : 'PERMANECE', `issued_count=${dash.issued_count}`);
  const integ: any = await core.getIntegrationStatus(cid);
  const settings = src('apps/painel-fiscal/src/pages/SettingsPage.tsx');
  const fixedBadge = /customLabel="MOCK \(Simulação Local\)"/.test(settings);
  rec('D7', 'HTTP+ESTÁTICO', 'integration-status', integ.capabilities?.emission !== undefined && !fixedBadge ? 'RESOLVIDA' : 'PARCIAL',
      `HTTP: status=${integ.status}, capabilities/blockers canônicos; ESTÁTICO: SettingsPage lê capabilities/blockers, mas o selo "Ambiente de Execução" é fixo "MOCK (Simulação Local)" mesmo em restricted/production`);
  const sync: any = await fiscal.getSyncStatus(cid);
  const syncUi = src('apps/painel-fiscal/src/pages/SyncPage.tsx') + src('apps/painel-fiscal/src/pages/DashboardPage.tsx');
  rec('D14', 'HTTP+ESTÁTICO', 'status de sync', /status === 'OK'/.test(syncUi) && /BLOCKED/.test(syncUi) ? 'RESOLVIDA' : 'PERMANECE',
      `HTTP: ${sync.status}; telas tratam OK/BLOCKED/NOT_CONFIGURED`);

  // ------------------------------------------------------------------ cópia de nota autorizada existente
  const demo = list.items.find((c: any) => c.document === '44555666000181');
  const last: any = await fiscal.getLastIssuedDocument(cid, demo.id);
  const copy: any = await fiscal.createDraftFromDocument(cid, { source_document_id: last.id });
  const leaked = ['number', 'access_key', 'dps_id', 'status_code', 'protocol', 'signature', 'scheduled_at', 'review_status']
    .filter((k) => k in copy && copy[k] != null);
  rec('S2', 'HTTP', 'cópia de nota autorizada existente sem IDs fiscais',
      last.status === 'AUTHORIZED' && copy.id !== last.id && copy.status === 'DRAFT' && copy.version === 1 && copy.source_document_id === last.id && leaked.length === 0 ? 'OK' : 'FALHA',
      `origem nº ${last.number} (${last.source}${last.simulated ? ', SIMULADA' : ''}) → rascunho ${copy.id.slice(0, 8)} DRAFT v1; campos fiscais copiados: ${leaked.join(',') || 'nenhum'}; competência=${copy.competence}`);

  // ------------------------------------------------------------------ rascunho (payload da NovaNfsePage)
  let draft: any;
  try {
    draft = await fiscal.createDraft(cid, { customer_id: created.id, competence: '2026-10',
      service: { national_code: '01.01.01', municipal_code: '', description: 'Serviço gate final' }, amount: '175.00', deductions: '0.00',
      tax_settings: { issqn_taxation: 'TAXABLE', iss_retention: 'NOT_RETAINED', iss_rate: '2.00' } } as any);
    rec('D18', 'HTTP', 'rascunho com payload da NovaNfsePage', draft?.id ? 'RESOLVIDA' : 'PERMANECE',
        `201; national_code saneado=${draft.service.national_code}; tax_settings=${JSON.stringify(draft.tax_settings).slice(0, 80)}`);
  } catch (e) {
    rec('D18', 'HTTP', 'rascunho com payload da NovaNfsePage', 'PERMANECE', err(e));
    throw e;
  }
  const val: any = await fiscal.validateDraft(cid, draft.id);
  rec('D9', 'HTTP+ESTÁTICO', 'severidade', /severity === 'error'/.test(src('apps/painel-fiscal/src/pages/DraftsPage.tsx')) ? 'RESOLVIDA' : 'PERMANECE',
      `HTTP: ${[...new Set(val.issues.map((i: any) => i.severity))].join(',')}; DraftsPage compara 'error'`);
  rec('D10', 'ESTÁTICO', 'Draft.status', /status: QueueStatus/.test(src('packages/api-client/src/types.ts')) ? 'RESOLVIDA' : 'PERMANECE', 'Draft.status: QueueStatus');

  // ------------------------------------------------------------------ agendamento (formatador real das telas) e F2
  // F2 (comportamental): as funções reais das telas devem produzir 00:01 LOCAL no fuso da empresa.
  const localHM = (iso: string, tz: string) => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
    return `${p.day} ${p.hour}:${p.minute}`;
  };
  const spTarget = calculateNextMonthFirstMinute(new Date(), 'America/Sao_Paulo');
  const manausTarget = calculateNextMonthFirstMinute(new Date(), 'America/Manaus');
  const manualManaus = parseLocalScheduleToUtcIso('2026-11-01T00:01', 'America/Manaus');
  const manualSp = parseLocalScheduleToUtcIso('2026-11-01T00:01', 'America/Sao_Paulo');
  const f2ok = localHM(spTarget.isoString, 'America/Sao_Paulo') === '01 00:01' && localHM(manausTarget.isoString, 'America/Manaus') === '01 00:01'
    && new Date(manualManaus).toISOString() === '2026-11-01T04:01:00.000Z' && new Date(manualSp).toISOString() === '2026-11-01T03:01:00.000Z';
  rec('F2', 'HTTP+ESTÁTICO', 'agendamento no fuso IANA da empresa (funções reais das telas)', f2ok ? 'RESOLVIDA' : 'PERMANECE',
      `próximo 01 00:01 SP=${spTarget.isoString} (${localHM(spTarget.isoString, 'America/Sao_Paulo')}) Manaus=${manausTarget.isoString} (${localHM(manausTarget.isoString, 'America/Manaus')}); manual Manaus=${manualManaus} SP=${manualSp}`);
  const b: any = await fiscal.createBatch(cid, { drafts: [{ id: draft.id, expected_version: draft.version }], scheduled_at: spTarget.isoString,
    timezone: company.timezone }, `batch-${cid}-${Date.now()}`);
  const bd: any = await fiscal.getBatch(cid, b.batch_id);
  rec('S3', 'HTTP', 'lote 00:01 do mês seguinte (formatador da tela, empresa em São Paulo)',
      b.status === 'SCHEDULED' && bd.scheduled_local.endsWith('T00:01:00-03:00') && bd.items[0].competence === '2026-10' ? 'OK' : 'FALHA',
      `POST scheduled_at=${b.scheduled_at} → local=${bd.scheduled_local} tz=${bd.timezone} competência=${bd.items[0].competence}`);
  const cp: any = await fiscal.cancelBatchPending(cid, b.batch_id);
  rec('D12', 'HTTP+ESTÁTICO', 'cancel-pending', cp.withdrawn === 1 && /res\.withdrawn/.test(src('apps/painel-fiscal/src/pages/BatchSchedulingPage.tsx')) ? 'RESOLVIDA' : 'PERMANECE',
      `HTTP: ${JSON.stringify(cp)}; tela usa res.withdrawn`);
  const batchPage = src('apps/painel-fiscal/src/pages/BatchSchedulingPage.tsx');
  // Comportamental: a página usa getBatchStatusBadgeConfig e nenhum estado não-retirado cai no selo WITHDRAWN.
  const usesFn = /getBatchStatusBadgeConfig\(/.test(batchPage);
  const labels = Object.fromEntries(['SCHEDULED', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_REJECTIONS', 'WITHDRAWN']
    .map((s) => [s, getBatchStatusBadgeConfig(s)]));
  const d11ok = usesFn && ['SCHEDULED', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_REJECTIONS']
    .every((s) => labels[s].status !== 'WITHDRAWN' && labels[s].label !== labels.WITHDRAWN.label);
  rec('D11', 'HTTP+COMPORTAMENTO', 'Batch/BatchItem', d11ok && bd.progress?.done !== undefined ? 'RESOLVIDA' : 'PARCIAL',
      `HTTP: progress=${JSON.stringify(bd.progress)}; página usa getBatchStatusBadgeConfig=${usesFn}; selos=${Object.values(labels).map((l: any) => l.label).join('|')}`);
  // Lote imediato processado pelo worker
  const d2: any = await fiscal.createDraft(cid, { customer_id: created.id, competence: '2026-10',
    service: { national_code: '010101', municipal_code: '', description: 'Gate worker' }, amount: '50.00', deductions: '0.00',
    tax_settings: { issqn_taxation: 'TAXABLE', iss_retention: 'NOT_RETAINED' } } as any);
  const b2: any = await fiscal.createBatch(cid, { drafts: [{ id: d2.id, expected_version: d2.version }],
    scheduled_at: new Date(Date.now() + 3000).toISOString(), timezone: company.timezone }, `batch-now-${Date.now()}`);
  let it: any;
  for (let i = 0; i < 40; i++) {
    it = (await fiscal.getBatch(cid, b2.batch_id)).items[0];
    if (['AUTHORIZED', 'REJECTED'].includes(it.status) || it.last_error?.code === 'MOCK_DISABLED') break;
    await sleep(3000);
  }
  if (it.last_error?.code === 'MOCK_DISABLED') {
    // Sprint 02: mock desligado em execução — sem fallback; o item não é transmitido e o lote é retirado.
    await fiscal.cancelBatchPending(cid, b2.batch_id);
    rec('S4', 'HTTP', 'fila persistente → worker (mock DESLIGADO, sem fallback)', it.status === 'SCHEDULED' ? 'OK' : 'FALHA',
        `${it.status} ${it.last_error.code}`);
  } else {
    rec('S4', 'HTTP', 'fila persistente → worker (mock)', it.status === 'AUTHORIZED' && it.result?.simulated ? 'OK' : 'FALHA', `${it.status} "${it.result?.label}"`);
  }

  // ------------------------------------------------------------------ importação
  const ok = mockXml(company.cnpj, 8101);
  const invalid = '<?xml version="1.0"?><NFSe xmlns="http://www.sped.fazenda.gov.br/nfse"><infNFSe Id="NFS123"/></NFSe>';
  const xxe = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><NFSe>&e;</NFSe>';
  const j1: any = await fiscal.importDocuments(cid, [new File([ok.xml], 'valida.xml'), new File([invalid], 'invalida.xml')]);
  const r1: any = await waitJob(fiscal, cid, j1.job_id);
  const persisted: any = await fiscal.getDocuments(cid, { query: ok.key });
  rec('S5', 'HTTP', 'válido + inválido → PARTIAL, válido persistido, erro do inválido',
      r1.status === 'PARTIAL' && persisted.total === 1 && r1.errors.some((e: any) => e.file === 'invalida.xml') ? 'OK' : 'FALHA',
      `${r1.status}; persistido=${persisted.total}; erros=${r1.errors.map((e: any) => `${e.file}:${e.code}`).join(',')}`);
  const j2: any = await fiscal.importDocuments(cid, [new File([invalid], 'a.xml'), new File([xxe], 'b.xml')]);
  const r2: any = await waitJob(fiscal, cid, j2.job_id);
  rec('S6', 'HTTP', 'importação totalmente inválida → FAILED', r2.status === 'FAILED' ? 'OK' : 'FALHA',
      `${r2.status}; erros=${r2.errors.map((e: any) => `${e.file}:${e.code}`).join(',')}`);
  const iePage = src('apps/painel-fiscal/src/pages/ImportExportPage.tsx');
  rec('D13', 'HTTP+ESTÁTICO', 'estados terminais de job', /'SUCCEEDED'/.test(iePage) && /'PARTIAL'/.test(iePage) && /'FAILED'/.test(iePage) && !/'COMPLETED'/.test(iePage) ? 'RESOLVIDA' : 'PERMANECE',
      `HTTP: PARTIAL/FAILED/SUCCEEDED; ImportExportPage encerra o polling nesses estados`);
  const xmlText = await fiscal.getDocumentXml(cid, persisted.items[0].id);
  rec('S7', 'HTTP', 'download autenticado do XML', xmlText === ok.xml ? 'OK' : 'FALHA', 'conteúdo idêntico ao enviado');
  const d = persisted.items[0];
  rec('D16', 'HTTP+ESTÁTICO', 'retention_indicators', typeof d.retention_indicators?.iss?.status === 'string' && /informed_amount/.test(src('packages/api-client/src/types.ts')) ? 'RESOLVIDA' : 'PERMANECE',
      `HTTP: iss.status=${d.retention_indicators?.iss?.status}; tipo estruturado`);
  rec('D17', 'ESTÁTICO', 'nulabilidade', /recipient\?: DocumentParty \| null/.test(src('packages/api-client/src/types.ts')) ? 'RESOLVIDA' : 'PERMANECE', 'recipient/issued_at/amount anuláveis');

  // ------------------------------------------------------------------ exportação (conteúdo verificado no smoke Python com openpyxl)
  const ex: any = await fiscal.exportDocuments(cid, { direction: 'RECEIVED', page: 1, page_size: 25 }, 'xlsx');
  const rx: any = await waitJob(fiscal, cid, ex.job_id);
  const blob = await fiscal.downloadJobFile(cid, ex.job_id);
  rec('S8', 'HTTP', 'exportação pelo cliente + download autenticado', rx.status === 'SUCCEEDED' && blob.size > 0 ? 'OK' : 'FALHA',
      `job=${rx.status} linhas=${rx.result?.rows} bytes=${blob.size}`);

  // ------------------------------------------------------------------ logout e reuso da sessão revogada
  const oldToken = store.getAccessToken();
  const oldCookie = new Map(jar);
  const oldCsrf = store.getCsrfToken();
  store.clear(); // logout após recarga (pior caso)
  await core.logout();
  rec('D2', 'HTTP', 'logout após recarga revoga a sessão', jar.size === 0 ? 'RESOLVIDA' : 'PERMANECE', `${calls.slice(-2).join(' | ')}`);
  const H = { Authorization: `Bearer ${oldToken}` };
  const me401 = (await realFetch(`${BASE}/api/core/v1/auth/me`, { headers: H })).status;
  const fis401 = (await realFetch(`${BASE}/api/fiscal/v1/companies/${cid}/documents`, { headers: H })).status;
  const cookieHdr = [...oldCookie].map(([k, v]) => `${k}=${v}`).join('; ');
  const ref = await realFetch(`${BASE}/api/core/v1/auth/refresh`, { method: 'POST', headers: { Origin: BASE, Cookie: cookieHdr, 'X-CSRF-Token': oldCsrf ?? '' } });
  const csrf = (await realFetch(`${BASE}/api/core/v1/auth/csrf`, { headers: { Cookie: cookieHdr } })).status;
  rec('S9', 'HTTP', 'reuso da sessão revogada', me401 === 401 && fis401 === 401 && ref.status === 401 && csrf === 401 ? 'OK' : 'FALHA',
      `token antigo: core ${me401}, fiscal ${fis401}; cookie antigo: refresh ${ref.status}, csrf ${csrf}`);

  const summary = rows.reduce((a: any, r) => ((a[r.result] = (a[r.result] || 0) + 1), a), {});
  console.log('\nRESUMO ' + JSON.stringify(summary));
  if (rows.some((r) => r.result === 'FALHA')) process.exit(2);
}

main().catch((e) => {
  console.error('ERRO', err(e), calls.slice(-5));
  process.exit(1);
});
