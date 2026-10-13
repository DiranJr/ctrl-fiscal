import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { IbsCbsFields, IbsCbsPreviewCard, IbsCbsValue, EMPTY_IBS_CBS } from '../components/IbsCbsFields.js';
import { IbsCbsGroupsState, EMPTY_IBS_GROUPS, groupsFromIbs, applyGroups, validateGroups } from '../components/IbsCbsGruposFields.js';
import { ObraFields, ObraState, EMPTY_OBRA, obraFromService, obraToService, validateObra, shouldShowObra } from '../components/ObraFields.js';
import { InfoComplFields, InfoComplState, EMPTY_INFO_COMPL, infoComplFromService, infoComplToPayload, validateInfoCompl } from '../components/InfoComplFields.js';
import { ServiceProfilePicker } from '../components/ServiceProfilePicker.js';
import { RevisaoNfse, ConfirmacaoRevisao } from '../components/RevisaoNfse.js';
import { ConfirmacaoProducao, montarConfirmacaoProducao, ModoEmissao } from '../components/ConfirmacaoProducao.js';
import { OperacaoTributaria } from '../components/OperacaoTributaria.js';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import { PendenciasRascunho } from '../components/PendenciasRascunho.js';
import { useUnsavedChanges } from '@ctrl-fiscal/ui';
import {
  Table,
  Button,
  Badge,
  Modal,
  Input,
  Textarea,
  Select,
  Alert,
  Column,
} from '@ctrl-fiscal/ui';
import {
  Customer,
  Draft,
  DraftService,
  TaxSettings,
  ApproxTax,
  ServiceProfile,
  ValidationIssue,
  formatCurrencyPtBr,
  parseCurrencyPtBrToDecimal,
  calculateNextMonthFirstMinute,
  parseLocalScheduleToUtcIso,
  ProblemDetailsException,
  CalculateSummaryParams,
} from '@ctrl-fiscal/api-client';
import { TributosRetencoesForm, TributosRetencoesState } from '../components/TributosRetencoesForm.js';
import { CanonicalRetentionSummaryCard } from '../components/CanonicalRetentionSummaryCard.js';
import { useCanonicalRetentions } from '../components/useCanonicalRetentions.js';

/** Campo devolvido pelo servidor -> trecho do rótulo do controle no editor (o rótulo traz a tag XML entre parênteses). */
const ROTULO_DO_CAMPO: [string, string][] = [
  ['service.description', 'xDescServ'], ['service.national_code', 'cTribNac'], ['service.municipal_code', 'cTribMun'],
  ['service.nbs_code', 'cNBS'], ['service.location_code', 'Local da prestação'], ['service.obra_code', 'cObra'],
  ['service.obra_property_registration', 'inscImobFisc'], ['service.obra', 'Identificação da obra'],
  ['competence', 'Competência'], ['service_date', 'dCompet'], ['amount', 'Valor Total do Serviço'],
  ['deductions', 'Deduções'], ['unconditional_discount', 'Desconto Incondicionado'],
  ['conditional_discount', 'Desconto Condicionado'], ['customer_id', 'Tomador do Serviço'],
  ['tax_settings.issqn_taxation', 'tribISSQN'], ['tax_settings.iss_retention', 'tpRetISSQN'],
  ['tax_settings.iss_rate', 'Alíquota do ISS'], ['tax_settings.immunity_type', 'tpImunidade'],
  ['tax_settings.irrf_withheld', 'vRetIRRF'], ['tax_settings.inss_withheld', 'vRetCP'],
  ['tax_settings.csll_withheld', 'vRetCSLL'], ['tax_settings.pis_cofins_retention', 'tpRetPisCofins'],
  ['tax_settings.pis_cofins_cst', 'PIS/COFINS (CST)'], ['tax_settings.pis_cofins_base', 'Base de Cálculo'],
  ['tax_settings.approx_tax', 'Modo de Informação'], ['tax_settings.ibs_cbs.operation_indicator', 'cIndOp'],
  ['tax_settings.ibs_cbs.class_code', 'cClassTrib'], ['tax_settings.ibs_cbs', 'cIndOp'],
];
const rotuloDoCampo = (field: string): string | null => {
  const achado = ROTULO_DO_CAMPO.filter(([k]) => field === k || field.startsWith(`${k}.`) || field.startsWith(`${k}[`))
    .sort((a, b) => b[0].length - a[0].length)[0];
  return achado ? achado[1] : null;
};

export const DraftsPage: React.FC<{ onNavigate: (route: string) => void }> = ({ onNavigate }) => {
  const { currentCompany, clients, showToast, refreshCounters, setLeaveGuard } = useFiscal();

  // Segurança do workspace: a resposta de um envio iniciado numa empresa é ignorada se a empresa mudou.
  const companyIdRef = React.useRef<string | undefined>(currentCompany?.id);
  const mountedRef = React.useRef(true);
  useEffect(() => { companyIdRef.current = currentCompany?.id; }, [currentCompany?.id]);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const isSameCompany = (id: string) => mountedRef.current && companyIdRef.current === id;
  const [editDirty, setEditDirtyState] = useState(false);
  const editDirtyRef = React.useRef(false);
  const setEditDirty = (v: boolean) => {
    editDirtyRef.current = v;
    setEditDirtyState(v);
  };

  const [drafts, setDrafts] = useState<Draft[]>([]);
  // Visão aplicada no SERVIDOR (antes da paginação), com a mesma classificação do painel.
  const [draftView, setDraftView] = useState<'pending' | 'issued' | 'all'>('pending');
  const [draftPage, setDraftPage] = useState(1);
  const [draftTotal, setDraftTotal] = useState(0);
  const [viewCounts, setViewCounts] = useState<Record<string, number>>({});
  const PAGE_SIZE = 25;
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [selectedDraftIds, setSelectedDraftIds] = useState<Set<string>>(new Set());

  // Rastreamento de rascunhos editados que necessitam de nova validação
  const [editedDraftIds, setEditedDraftIds] = useState<Set<string>>(new Set());

  // Retirada da fila (para rascunhos SCHEDULED)
  const [withdrawingDraftId, setWithdrawingDraftId] = useState<string | null>(null);
  // Excluir rascunho (exclusão lógica no servidor): confirmação com tomador, serviço e valor.
  const [deletingDraft, setDeletingDraft] = useState<Draft | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Painel persistente de pendências (lista) e validação dentro do editor.
  const [pendDraftId, setPendDraftId] = useState<string | null>(null);
  const [pendError, setPendError] = useState<string | null>(null);
  const [editValidating, setEditValidating] = useState(false);
  const [editValidateError, setEditValidateError] = useState<string | null>(null);
  const editorRef = React.useRef<HTMLDivElement | null>(null);
  const canPrepare = !!currentCompany?.permissions?.includes('prepare');
  // Só o que nunca entrou na fila (ou foi retirado antes do envio); o servidor confere de novo, com trava.
  const isDeletable = (d: Draft) => (d.status === 'DRAFT' || d.status === 'WITHDRAWN') && !d.current_item_id;

  // Validação
  const [validatingDraftId, setValidatingDraftId] = useState<string | null>(null);
  const [draftIssuesMap, setDraftIssuesMap] = useState<Map<string, ValidationIssue[]>>(new Map());

  // Modal de Edição Completo
  const [editingDraft, setEditingDraft] = useState<Draft | null>(null);
  useUnsavedChanges(!!editingDraft && editDirty, 'Há alterações não salvas neste rascunho. Descartar?');
  useEffect(() => {
    setLeaveGuard(editingDraft ? () => !editDirtyRef.current || window.confirm('Há alterações não salvas neste rascunho. Descartar?') : null);
    return () => setLeaveGuard(null);
  }, [editingDraft, setLeaveGuard]);
  const closeEditor = () => {
    if (editDirtyRef.current && !window.confirm('Há alterações não salvas neste rascunho. Descartar?')) return;
    setEditDirty(false);
    setEditingDraft(null);
  };
  const [activeTab, setActiveTab] = useState<'geral' | 'servico' | 'iss' | 'federais' | 'operacao'>('geral');
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [isConflict409, setIsConflict409] = useState(false);
  const [editFieldErrors, setEditFieldErrors] = useState<Record<string, string | undefined>>({});
  const editFieldErrorLists = useMemo(
    () => Object.fromEntries(Object.entries(editFieldErrors).filter(([, v]) => !!v).map(([k, v]) => [k, [v as string]])),
    [editFieldErrors]
  );

  // Campos do Formulário de Edição
  // Seção 1: Geral & Tomador
  const [editCustomerId, setEditCustomerId] = useState('');
  const [editCompetence, setEditCompetence] = useState('');
  const [editServiceDate, setEditServiceDate] = useState('');
  const [editAmount, setEditAmount] = useState('');
  const [editDeductions, setEditDeductions] = useState('');
  const [editUnconditionalDiscount, setEditUnconditionalDiscount] = useState('');
  const [editConditionalDiscount, setEditConditionalDiscount] = useState('');

  // Seção 2: Serviço & Obra
  const [editDescription, setEditDescription] = useState('');
  const [editServiceProfileId, setEditServiceProfileId] = useState<string | null>(null);
  const [editNationalCode, setEditNationalCode] = useState('');
  const [editMunicipalCode, setEditMunicipalCode] = useState('');
  const [editLocationCode, setEditLocationCode] = useState('');
  const [editNbsCode, setEditNbsCode] = useState('');

  // Obra (serv/obra) e informações complementares (serv/infoCompl)
  const [editObra, setEditObra] = useState<ObraState>(EMPTY_OBRA);
  const [editInfoCompl, setEditInfoCompl] = useState<InfoComplState>(EMPTY_INFO_COMPL);

  // Seção 3: Tributos e Retenções (ISSQN, IRRF, INSS/CP, CSLL, PIS/COFINS)
  const [editTributosState, setEditTributosState] = useState<TributosRetencoesState>({
    issqnTaxation: 'TAXABLE',
    issRetention: 'NOT_RETAINED',
    issRate: '',
    immunityType: '1',
    irrfWithheld: '',
    inssWithheld: '',
    csllWithheld: '',
    pisCofinsRetention: '',
    pisCofinsCst: '',
    pisCofinsBase: '',
    pisRate: '',
    cofinsRate: '',
    pisAmount: '',
    cofinsAmount: '',
  });

  const handleEditTributoChange = <K extends keyof TributosRetencoesState>(
    field: K,
    value: TributosRetencoesState[K]
  ) => {
    setEditTributosState((prev) => ({ ...prev, [field]: value }));
  };

  // Cálculo Canônico de Retenções do Modal de Edição
  const editRetentionSummaryParams = useMemo<CalculateSummaryParams>(() => {
    return ({
      amount: editAmount,
      unconditionalDiscount: editUnconditionalDiscount,
      conditionalDiscount: editConditionalDiscount,
      deductions: editDeductions,
      taxSettings: {
        issqn_taxation: editTributosState.issqnTaxation,
        iss_retention: editTributosState.issRetention,
        iss_rate: editTributosState.issRate.trim() || null,
        immunity_type: editTributosState.issqnTaxation === 'IMMUNE' ? editTributosState.immunityType : null,
        irrf_withheld: editTributosState.irrfWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.irrfWithheld) : null,
        inss_withheld: editTributosState.inssWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.inssWithheld) : null,
        csll_withheld: editTributosState.csllWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.csllWithheld) : null,
        pis_cofins_retention: editTributosState.pisCofinsRetention ? (editTributosState.pisCofinsRetention as any) : null,
        pis_cofins_cst: editTributosState.pisCofinsCst || null,
        pis_cofins_base: editTributosState.pisCofinsBase.trim() ? parseCurrencyPtBrToDecimal(editTributosState.pisCofinsBase) : null,
        pis_rate: editTributosState.pisRate.trim() || null,
        cofins_rate: editTributosState.cofinsRate.trim() || null,
        pis_amount: editTributosState.pisAmount.trim() ? parseCurrencyPtBrToDecimal(editTributosState.pisAmount) : null,
        cofins_amount: editTributosState.cofinsAmount.trim() ? parseCurrencyPtBrToDecimal(editTributosState.cofinsAmount) : null,
      },
    });
  }, [editAmount, editUnconditionalDiscount, editConditionalDiscount, editDeductions, editTributosState]);
  const editRetentionSummaryCanon = useCanonicalRetentions(editRetentionSummaryParams);
  const editRetentionSummary = editRetentionSummaryCanon.summary;

  // Seção 4: TotTrib & Reforma IBS/CBS
  const [editApproxMode, setEditApproxMode] = useState<'NOT_INFORMED' | 'VALUES' | 'PERCENT' | 'SIMPLES_PERCENT'>('NOT_INFORMED');
  const [editApproxFederal, setEditApproxFederal] = useState('');
  const [editApproxState, setEditApproxState] = useState('');
  const [editApproxMunicipal, setEditApproxMunicipal] = useState('');
  const [editApproxSimples, setEditApproxSimples] = useState('');

  const [editEnableIbsCbs, setEditEnableIbsCbs] = useState(false);
  // Sem valores padrão: cIndOp/cClassTrib são definidos pela contabilidade (nada é presumido).
  const [editIbs, setEditIbs] = useState<IbsCbsValue>(EMPTY_IBS_CBS);
  const [editIbsGroups, setEditIbsGroups] = useState<IbsCbsGroupsState>(EMPTY_IBS_GROUPS);

  // Modal de Agendamento em Lote
  const [isScheduleModalOpen, setIsScheduleModalOpen] = useState(false);
  const [scheduleDateTime, setScheduleDateTime] = useState('');
  const [isSchedulingBatch, setIsSchedulingBatch] = useState(false);
  const [batchReviewConfirmed, setBatchReviewConfirmed] = useState(false);
  const [prodConfirmed, setProdConfirmed] = useState(false);
  const isProd = currentCompany?.environment === 'production';

  const loadDrafts = useCallback(async (silencioso = false) => {
    if (!currentCompany) return;
    const companyId = currentCompany.id;
    if (!silencioso) setIsLoading(true);
    try {
      const res = await clients.fiscal.getDrafts(companyId, draftPage, PAGE_SIZE, draftView);
      if (companyIdRef.current !== companyId) return; // resposta de outro workspace: descartada
      setDrafts(res.items);
      setDraftTotal(res.total);
      setViewCounts(res.counts || {});
      // Emitidos não podem ser selecionados para emitir de novo: a seleção fica só com os da página.
      setSelectedDraftIds((prev) => new Set([...prev].filter((id) => res.items.some((d) => d.id === id && d.status === 'DRAFT'))));
    } catch (err: any) {
      if (!silencioso) showToast(err.message || 'Falha ao listar rascunhos.', 'error');
    } finally {
      if (!silencioso) setIsLoading(false);
    }
  }, [currentCompany, clients.fiscal, showToast, draftPage, draftView]);

  // Enquanto houver item em fila, processamento ou aguardando confirmação, atualiza a lista (leitura apenas).
  const emAndamento = drafts.some((d) => ['SCHEDULED', 'PROCESSING', 'AWAITING_CONFIRMATION'].includes(d.status));
  useEffect(() => {
    if (!emAndamento) return;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') loadDrafts(true); }, 15000);
    return () => window.clearInterval(timer);
  }, [emAndamento, loadDrafts]);

  const loadCustomers = useCallback(async () => {
    if (!currentCompany) return;
    try {
      const res = await clients.core.getCustomers(currentCompany.id);
      setCustomers(res.items);
    } catch (err: any) {
      logSafe('clientes', err);
    }
  }, [currentCompany, clients.core]);

  useEffect(() => {
    loadDrafts();
    loadCustomers();
  }, [loadDrafts, loadCustomers]);

  // Rascunho recém-criado a partir de uma nota (?draft=<id>): abre direto no editor e limpa o parâmetro.
  useEffect(() => {
    if (!currentCompany) return;
    const url = new URL(window.location.href);
    const id = url.searchParams.get('draft');
    if (!id) return;
    url.searchParams.delete('draft');
    window.history.replaceState({}, '', url.toString());
    clients.fiscal.getDraft(currentCompany.id, id).then((d) => populateEditForm(d)).catch((err) => {
      logSafe('rascunho', err);
      showToast('Rascunho criado, mas não foi possível abri-lo. Ele está na lista abaixo.', 'warning');
    });
  }, [currentCompany?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Toggle Seleção
  const toggleSelect = (id: string) => {
    const updated = new Set(selectedDraftIds);
    if (updated.has(id)) {
      updated.delete(id);
    } else {
      updated.add(id);
    }
    setSelectedDraftIds(updated);
  };

  const selecionaveis = drafts.filter((d) => d.status === 'DRAFT');
  const toggleSelectAll = () => {
    if (selecionaveis.length > 0 && selecionaveis.every((d) => selectedDraftIds.has(d.id))) {
      setSelectedDraftIds(new Set());
    } else {
      setSelectedDraftIds(new Set(selecionaveis.map((d) => d.id)));
    }
  };
  const verNota = (d: Draft) => {
    if (!currentCompany) return;
    const doc = d.issued?.document_id;
    window.location.href = `/fiscal/?view=emitidas&company_id=${encodeURIComponent(currentCompany.id)}${doc ? `&doc=${encodeURIComponent(doc)}` : ''}`;
  };

  // Validar Rascunho individualmente com o backend
  const handleValidateDraft = async (draftId: string) => {
    if (!currentCompany) return;
    setValidatingDraftId(draftId);
    try {
      setPendError(null);
      const res = await clients.fiscal.validateDraft(currentCompany.id, draftId);
      const newMap = new Map(draftIssuesMap);
      newMap.set(draftId, res.issues);
      setDraftIssuesMap(newMap);
      await loadDrafts(); // pendências, versão validada e situação vêm do servidor
      setPendDraftId(draftId);

      // Limpa marcação de revalidação pendente após rodar validação
      setEditedDraftIds((prev) => {
        const next = new Set(prev);
        next.delete(draftId);
        return next;
      });

      const bloqueios = res.issues.filter((i) => i.severity === 'error').length;
      const avisos = res.issues.filter((i) => i.severity === 'warning').length;
      showToast(res.valid
        ? `Validação concluída: nenhum bloqueio${avisos ? ` (${avisos} aviso(s))` : ''}.`
        : `Validação concluída: ${bloqueios} bloqueio(s) e ${avisos} aviso(s) — veja o detalhamento.`, res.valid ? 'success' : 'warning');
    } catch (err: any) {
      // Falha da API: o painel abre com o erro e mantém o que já se sabia da última validação.
      setPendError(err?.message || 'Falha na validação do rascunho.');
      setPendDraftId(draftId);
    } finally {
      setValidatingDraftId(null);
    }
  };

  // Helper para carregar todos os dados persistidos no modal de edição
  const populateEditForm = (draft: Draft) => {
    setEditingDraft(draft);
    setActiveTab('geral');
    setEditError(null);
    setIsConflict409(false);
    setEditFieldErrors({});

    // Geral & Tomador
    setEditCustomerId(draft.customer_id || (draft.customer?.id ?? ''));
    setEditCompetence(draft.competence || '');
    setEditServiceDate(draft.service_date || '');
    setEditAmount(draft.amount ? formatCurrencyPtBr(draft.amount, false) : '');
    setEditDeductions(draft.deductions && draft.deductions !== '0.00' ? formatCurrencyPtBr(draft.deductions, false) : '');
    setEditUnconditionalDiscount(draft.unconditional_discount ? formatCurrencyPtBr(draft.unconditional_discount, false) : '');
    setEditConditionalDiscount(draft.conditional_discount ? formatCurrencyPtBr(draft.conditional_discount, false) : '');

    // Serviço & Códigos
    setEditDescription(draft.service?.description || '');
    setEditNationalCode(draft.service?.national_code || '');
    setEditServiceProfileId(draft.service_profile_id || null);
    setEditMunicipalCode(draft.service?.municipal_code || '');
    setEditLocationCode(draft.service?.location_code || '');
    setEditNbsCode(draft.service?.nbs_code || '');

    // Obra (cObra | cCIB | endereço + inscImobFisc) e informações complementares
    setEditObra(obraFromService(draft.service));
    setEditInfoCompl(infoComplFromService(draft.service));

    // Tributos e Retenções (ISSQN, IRRF, INSS, CSLL, PIS/COFINS)
    const ts = draft.tax_settings;
    setEditTributosState({
      issqnTaxation: (ts?.issqn_taxation as any) || 'TAXABLE',
      immunityType: (ts?.immunity_type as any) || '1',
      issRetention: (ts?.iss_retention as any) || 'NOT_RETAINED',
      issRate: ts?.iss_rate || '',
      pisCofinsRetention: ts?.pis_cofins_retention !== null && ts?.pis_cofins_retention !== undefined ? String(ts.pis_cofins_retention) : '',
      irrfWithheld: ts?.irrf_withheld ? formatCurrencyPtBr(ts.irrf_withheld, false) : '',
      inssWithheld: ts?.inss_withheld ? formatCurrencyPtBr(ts.inss_withheld, false) : '',
      csllWithheld: ts?.csll_withheld ? formatCurrencyPtBr(ts.csll_withheld, false) : '',
      pisCofinsCst: ts?.pis_cofins_cst || '',
      pisCofinsBase: ts?.pis_cofins_base ? formatCurrencyPtBr(ts.pis_cofins_base, false) : '',
      pisRate: ts?.pis_rate || '',
      cofinsRate: ts?.cofins_rate || '',
      pisAmount: ts?.pis_amount ? formatCurrencyPtBr(ts.pis_amount, false) : '',
      cofinsAmount: ts?.cofins_amount ? formatCurrencyPtBr(ts.cofins_amount, false) : '',
    });

    // Tributos Aproximados
    if (ts?.approx_tax && ts.approx_tax.mode !== 'NOT_INFORMED') {
      setEditApproxMode(ts.approx_tax.mode);
      setEditApproxFederal(ts.approx_tax.federal ? (ts.approx_tax.mode === 'VALUES' ? formatCurrencyPtBr(ts.approx_tax.federal, false) : ts.approx_tax.federal) : '');
      setEditApproxState(ts.approx_tax.state ? formatCurrencyPtBr(ts.approx_tax.state, false) : '');
      setEditApproxMunicipal(ts.approx_tax.municipal ? formatCurrencyPtBr(ts.approx_tax.municipal, false) : '');
      setEditApproxSimples(ts.approx_tax.simples || '');
    } else {
      setEditApproxMode('NOT_INFORMED');
      setEditApproxFederal('');
      setEditApproxState('');
      setEditApproxMunicipal('');
      setEditApproxSimples('');
    }

    // IBS/CBS (inclui os grupos condicionais gTribRegular, gDif, imovel e dest)
    const ib = ts?.ibs_cbs;
    setEditEnableIbsCbs(!!ib);
    setEditIbs(ib ? {
      operation_indicator: ib.operation_indicator || '',
      cst: ib.cst || '',
      class_code: ib.class_code || '',
      personal_use: (ib.personal_use as '' | '0' | '1') || '',
    } : EMPTY_IBS_CBS);
    setEditIbsGroups(groupsFromIbs(ib));
  };

  // Abrir Modal de Edição
  const handleOpenEdit = (draft: Draft) => {
    setEditDirty(false);
    populateEditForm(draft);
  };

  // Retirar da fila para editar (Exclusivo para SCHEDULED)
  const handleWithdrawDraft = async (draft: Draft) => {
    if (!currentCompany) return;
    const companyId = currentCompany.id;
    setWithdrawingDraftId(draft.id);
    try {
      const withdrawn = await clients.fiscal.withdrawDraft(companyId, draft.id, draft.version);
      if (!isSameCompany(companyId)) return;
      showToast('Rascunho retirado da fila com sucesso!', 'success');
      await refreshCounters();
      await loadDrafts();
      // Abre imediatamente para edição
      populateEditForm(withdrawn);
    } catch (err: any) {
      if (isSameCompany(companyId)) showToast(err.message || 'Falha ao retirar rascunho da fila.', 'error');
    } finally {
      setWithdrawingDraftId(null);
    }
  };

  const irAoCampo = (field: string, label?: string | null) => {
    const aba = tabOfField(field);
    setActiveTab(aba);
    const trecho = rotuloDoCampo(field) || (label || '').replace(/\s*\(.*\)\s*$/, '');
    window.setTimeout(() => {
      const raiz = editorRef.current;
      const rotulo = raiz && trecho
        ? Array.from(raiz.querySelectorAll('label')).find((l) => (l.textContent || '').toLowerCase().includes(trecho.toLowerCase()))
        : null;
      const alvo = (rotulo?.htmlFor ? document.getElementById(rotulo.htmlFor) : null) as HTMLElement | null;
      const foco = alvo || (rotulo as HTMLElement | null);
      if (!foco) {
        showToast(`“${label || field}”: confira a aba aberta do editor.`, 'info');
        return;
      }
      foco.scrollIntoView({ block: 'center', behavior: 'smooth' });
      if (alvo) alvo.focus({ preventScroll: true });
      foco.classList.add('pf-campo-destacado');
      window.setTimeout(() => foco.classList.remove('pf-campo-destacado'), 2500);
    }, 120);
  };

  const handleValidateInEditor = async () => {
    if (!currentCompany || !editingDraft) return;
    const companyId = currentCompany.id;
    setEditValidating(true);
    setEditValidateError(null);
    try {
      await clients.fiscal.validateDraft(companyId, editingDraft.id);
      const latest = await clients.fiscal.getDraft(companyId, editingDraft.id);
      if (!isSameCompany(companyId)) return;
      // Só a situação da validação muda (validar não edita o rascunho); o formulário em edição é preservado.
      setEditingDraft((prev) => (prev ? { ...prev, issues: latest.issues, validation: latest.validation, version: latest.version } : prev));
      await loadDrafts();
    } catch (err: any) {
      if (isSameCompany(companyId)) setEditValidateError(err?.message || 'Falha na validação do rascunho.');
    } finally {
      setEditValidating(false);
    }
  };

  const handleAskDelete = (draft: Draft) => {
    setDeleteError(null);
    setDeletingDraft(draft);
  };

  const handleConfirmDelete = async () => {
    if (!currentCompany || !deletingDraft) return;
    const companyId = currentCompany.id;
    const alvo = deletingDraft;
    setIsDeleting(true);
    setDeleteError(null);
    try {
      await clients.fiscal.deleteDraft(companyId, alvo.id, alvo.version);
      if (!isSameCompany(companyId)) return;
      setDeletingDraft(null);
      if (editingDraft?.id === alvo.id) {
        // Excluído pelo editor: volta para a lista sem perguntar sobre alterações não salvas.
        setEditDirty(false);
        setEditingDraft(null);
      }
      setSelectedDraftIds((prev) => {
        const next = new Set(prev);
        next.delete(alvo.id);
        return next;
      });
      showToast('Rascunho excluído.', 'success');
      await refreshCounters();
      await loadDrafts();
    } catch (err: any) {
      if (!isSameCompany(companyId)) return;
      logSafe('excluir rascunho', err);
      const status = err instanceof ProblemDetailsException ? err.problem.status : undefined;
      const code = err instanceof ProblemDetailsException ? (err.problem as any).code : undefined;
      const motivo = err?.message || 'Falha ao excluir o rascunho.';
      setDeleteError(
        code === 'VERSION_CONFLICT'
          ? `${motivo} O rascunho foi alterado depois que você o abriu: feche, confira a versão atual e tente de novo.`
          : status === 403
            ? 'Você não tem permissão para excluir rascunhos neste workspace.'
            : motivo,
      );
    } finally {
      setIsDeleting(false);
    }
  };

  // Recarregar Dados do Servidor em Caso de Conflito 409
  const handleReloadServerData = async () => {
    if (!currentCompany || !editingDraft) return;
    try {
      const latest = await clients.fiscal.getDraft(currentCompany.id, editingDraft.id);
      populateEditForm(latest);
      showToast('Dados do servidor recarregados com sucesso!', 'info');
    } catch (err: any) {
      showToast(err.message || 'Erro ao recarregar dados do rascunho.', 'error');
    }
  };

  // Adotar versão atualizada mantendo alterações locais
  const handleAdoptLatestVersion = async () => {
    if (!currentCompany || !editingDraft) return;
    try {
      const latest = await clients.fiscal.getDraft(currentCompany.id, editingDraft.id);
      setEditingDraft((prev) => (prev ? { ...prev, version: latest.version } : null));
      setIsConflict409(false);
      setEditError(null);
      showToast(`Versão atualizada para v${latest.version}. Clique em Salvar para tentar novamente.`, 'info');
    } catch (err: any) {
      showToast(err.message || 'Erro ao obter versão atual.', 'error');
    }
  };

  // Configuração tributária da operação do rascunho em edição (sempre sobre a versão SALVA; não aprova sozinha nem emite).
  const [isOperatingEdit, setIsOperatingEdit] = useState(false);
  const registerEditOperation = async (): Promise<Draft | null> => {
    if (!currentCompany || !editingDraft) return null;
    const d = await clients.fiscal.registerOperationConfig(currentCompany.id, editingDraft.id, editingDraft.version);
    populateEditForm(d);
    setActiveTab('operacao');
    await loadDrafts();
    return d;
  };
  const handleRegisterEditOperation = async () => {
    setIsOperatingEdit(true);
    try {
      if (await registerEditOperation()) showToast('Configuração da operação registrada. Nada foi aprovado nem emitido.', 'success');
    } catch (err: any) {
      showToast(err.message || 'Erro ao registrar a configuração da operação.', 'error');
    } finally {
      setIsOperatingEdit(false);
    }
  };
  const handleApproveEditOperation = async (p: ServiceProfile) => {
    if (!currentCompany || !editingDraft) return;
    setIsOperatingEdit(true);
    try {
      await clients.core.approveServiceProfile(currentCompany.id, p.id, p.version, `${editingDraft.competence}-01`,
        'Aprovada na revisão do rascunho');
      // A aprovação gera nova versão do perfil: o rascunho é vinculado de novo à versão aprovada.
      if (await registerEditOperation()) showToast('Configuração aprovada e vinculada ao rascunho. Revise antes de agendar.', 'success');
    } catch (err: any) {
      showToast(err.message || 'Erro ao aprovar a configuração.', 'error');
    } finally {
      setIsOperatingEdit(false);
    }
  };

  // Aba de cada campo, para levar o usuário ao primeiro erro.
  const tabOfField = (k: string): 'geral' | 'servico' | 'iss' | 'federais' | 'operacao' => {
    if (k === 'service_profile_id') return 'operacao';
    if (k.startsWith('service')) return 'servico';
    if (k.startsWith('tax_settings.ibs_cbs') || k.startsWith('tax_settings.approx_tax')) return 'federais';
    if (k.startsWith('tax_settings')) return 'iss';
    return 'geral';
  };
  const showFieldErrors = (errs: Record<string, string | undefined>, message: string) => {
    setEditFieldErrors(errs);
    setEditError(message);
    const first = Object.keys(errs)[0];
    if (first) setActiveTab(tabOfField(first));
  };
  const clearEditErrors = (...prefixes: string[]) => {
    if (!Object.keys(editFieldErrors).some((k) => prefixes.some((p) => k === p || k.startsWith(`${p}.`)))) return;
    setEditFieldErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => !prefixes.some((p) => k === p || k.startsWith(`${p}.`)))));
  };

  // "Aplicar perfil de serviço" no editor: sobrescreve só os campos que o perfil define.
  const applyServiceProfile = (p: ServiceProfile) => {
    setEditDirty(true);
    setEditServiceProfileId(p.id);
    if (p.national_code) setEditNationalCode(p.national_code);
    if (p.municipal_code) setEditMunicipalCode(p.municipal_code);
    if (p.nbs_code) setEditNbsCode(p.nbs_code);
    if (p.description) setEditDescription(p.description);
    if (p.issqn_taxation || p.pis_cofins_cst) {
      setEditTributosState((prev) => ({
        ...prev,
        ...(p.issqn_taxation ? { issqnTaxation: p.issqn_taxation } : {}),
        ...(p.pis_cofins_cst ? { pisCofinsCst: p.pis_cofins_cst } : {}),
      }));
    }
    if (p.ibs_cbs) {
      const pi = p.ibs_cbs;
      setEditEnableIbsCbs(true);
      setEditIbs((prev) => ({
        operation_indicator: pi.operation_indicator || prev.operation_indicator,
        class_code: pi.class_code || prev.class_code,
        cst: pi.class_code ? (pi.cst || pi.class_code.slice(0, 3)) : prev.cst,
        personal_use: pi.personal_use ? pi.personal_use : prev.personal_use,
      }));
    }
    clearEditErrors('service.national_code', 'service.municipal_code', 'service.nbs_code', 'service.description', 'tax_settings.ibs_cbs');
  };

  // Salvar Edição com controle estrito de versão (expected_version)
  const handleSaveEdit = async () => {
    if (!currentCompany || !editingDraft) return;
    const companyId = currentCompany.id;
    const draftId = editingDraft.id;

    // Validações de UI (formato e completude; as regras fiscais são confirmadas pela pré-validação da DPS)
    const local: Record<string, string> = {};
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(editCompetence.trim())) local['competence'] = 'Informe a competência no formato AAAA-MM.';
    if (!editDescription.trim()) local['service.description'] = 'A descrição do serviço é obrigatória.';
    const cleanAmount = parseCurrencyPtBrToDecimal(editAmount);
    if (!cleanAmount || parseFloat(cleanAmount) <= 0 || isNaN(parseFloat(cleanAmount))) local['amount'] = 'O valor do serviço deve ser maior que zero.';
    if (editMunicipalCode.trim() && editMunicipalCode.trim().replace(/\D/g, '').length !== 3) {
      local['service.municipal_code'] = 'O Código de Tributação Municipal (cTribMun) deve ter exatamente 3 dígitos numéricos quando informado.';
    }
    if (editNationalCode.trim() && editNationalCode.trim().replace(/\D/g, '').length !== 6) {
      local['service.national_code'] = 'O cTribNac deve ter exatamente 6 dígitos numéricos.';
    }
    Object.assign(local, validateObra(editObra), validateInfoCompl(editInfoCompl));
    if (editEnableIbsCbs) {
      if (!/^[0-9]{6}$/.test(editIbs.operation_indicator.replace(/\D/g, ''))) local['tax_settings.ibs_cbs.operation_indicator'] = 'Indicador de operação deve ter 6 dígitos.';
      if (!/^[0-9]{6}$/.test(editIbs.class_code.replace(/\D/g, ''))) local['tax_settings.ibs_cbs.class_code'] = 'Selecione a classificação tributária.';
      Object.assign(local, validateGroups(editIbsGroups));
    }
    if (Object.keys(local).length) {
      const msgs = Object.values(local);
      showFieldErrors(local, msgs.length === 1 ? msgs[0] : `Corrija os ${msgs.length} campos destacados antes de salvar.`);
      return;
    }

    setIsSavingEdit(true);
    setEditError(null);
    setEditFieldErrors({});
    setIsConflict409(false);

    try {
      // 1. Serviço (PATCH: campos/grupos vazios vão como null = remoção)
      const cleanNational = editNationalCode.trim() ? editNationalCode.trim().replace(/\D/g, '') : null;
      const cleanMunicipal = editMunicipalCode.trim() ? editMunicipalCode.trim().replace(/\D/g, '').padStart(3, '0') : null;
      const cleanLocation = editLocationCode.trim() ? editLocationCode.trim().replace(/\D/g, '') : null;
      const cleanNbs = editNbsCode.trim() ? editNbsCode.trim().replace(/\D/g, '') : null;

      const servicePayload: DraftService = {
        description: editDescription.trim(),
        national_code: cleanNational,
        municipal_code: cleanMunicipal,
        location_code: cleanLocation,
        nbs_code: cleanNbs,
        // Obra: escolha estrita entre cObra, cCIB ou endereço (os demais vão como null)
        ...obraToService(editObra, true),
        additional_info: infoComplToPayload(editInfoCompl),
      };

      // 2. Tributação e Retenções (Tax Settings)
      const taxSettingsPayload: TaxSettings = {
        issqn_taxation: editTributosState.issqnTaxation,
        immunity_type: editTributosState.issqnTaxation === 'IMMUNE' ? editTributosState.immunityType : null,
        iss_retention: editTributosState.issRetention,
        iss_rate: editTributosState.issRate.trim() ? editTributosState.issRate.trim() : null,
        pis_cofins_retention: editTributosState.pisCofinsRetention !== '' ? (String(editTributosState.pisCofinsRetention) as any) : null, // tpRetPisCofins é CÓDIGO ('0'..'9'), não número
        irrf_withheld: editTributosState.irrfWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.irrfWithheld) : null,
        inss_withheld: editTributosState.inssWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.inssWithheld) : null,
        csll_withheld: editTributosState.csllWithheld.trim() ? parseCurrencyPtBrToDecimal(editTributosState.csllWithheld) : null,
        pis_cofins_cst: editTributosState.pisCofinsCst || null,
        pis_cofins_base: editTributosState.pisCofinsBase.trim() ? parseCurrencyPtBrToDecimal(editTributosState.pisCofinsBase) : null,
        pis_rate: editTributosState.pisRate.trim() ? editTributosState.pisRate.trim() : null,
        cofins_rate: editTributosState.cofinsRate.trim() ? editTributosState.cofinsRate.trim() : null,
        pis_amount: editTributosState.pisAmount.trim() ? parseCurrencyPtBrToDecimal(editTributosState.pisAmount) : null,
        cofins_amount: editTributosState.cofinsAmount.trim() ? parseCurrencyPtBrToDecimal(editTributosState.cofinsAmount) : null,
      };

      // Tributos Aproximados
      if (editApproxMode !== 'NOT_INFORMED') {
        const approx: ApproxTax = { mode: editApproxMode };
        if (editApproxMode === 'VALUES') {
          if (editApproxFederal.trim()) approx.federal = parseCurrencyPtBrToDecimal(editApproxFederal);
          if (editApproxState.trim()) approx.state = parseCurrencyPtBrToDecimal(editApproxState);
          if (editApproxMunicipal.trim()) approx.municipal = parseCurrencyPtBrToDecimal(editApproxMunicipal);
        } else if (editApproxMode === 'PERCENT') {
          if (editApproxFederal.trim()) approx.federal = editApproxFederal.trim();
        } else if (editApproxMode === 'SIMPLES_PERCENT') {
          if (editApproxSimples.trim()) approx.simples = editApproxSimples.trim();
        }
        taxSettingsPayload.approx_tax = approx;
      } else {
        taxSettingsPayload.approx_tax = null;
      }

      // Reforma Tributária: IBS/CBS (grupo substituído inteiro; subgrupos vazios são omitidos)
      taxSettingsPayload.ibs_cbs = editEnableIbsCbs
        ? applyGroups({
          operation_indicator: editIbs.operation_indicator.replace(/\D/g, ''),
          cst: editIbs.cst.replace(/\D/g, ''),
          class_code: editIbs.class_code.replace(/\D/g, ''),
          personal_use: editIbs.personal_use || null,
        }, editIbsGroups)
        : null;

      // 3. Montagem do payload de atualização
      const cleanDeductions = editDeductions.trim() ? parseCurrencyPtBrToDecimal(editDeductions) : '0.00';
      const cleanUnconditionalDiscount = editUnconditionalDiscount.trim() ? parseCurrencyPtBrToDecimal(editUnconditionalDiscount) : null;
      const cleanConditionalDiscount = editConditionalDiscount.trim() ? parseCurrencyPtBrToDecimal(editConditionalDiscount) : null;
      const cleanServiceDate = editServiceDate.trim() ? editServiceDate.trim() : null;

      await clients.fiscal.updateDraft(companyId, draftId, {
        expected_version: editingDraft.version,
        customer_id: editCustomerId || undefined,
        service_profile_id: editServiceProfileId,
        competence: editCompetence.trim(),
        service_date: cleanServiceDate,
        amount: cleanAmount,
        deductions: cleanDeductions,
        unconditional_discount: cleanUnconditionalDiscount,
        conditional_discount: cleanConditionalDiscount,
        service: servicePayload,
        tax_settings: taxSettingsPayload,
      });
      if (!isSameCompany(companyId)) return; // empresa trocada durante o envio: resposta ignorada

      showToast('Rascunho atualizado com sucesso! É necessária nova validação antes do envio.', 'success');

      // Registra que o rascunho requer nova validação
      setEditedDraftIds((prev) => new Set(prev).add(draftId));

      // Limpa validações anteriores em cache
      setDraftIssuesMap((prev) => {
        const next = new Map(prev);
        next.delete(draftId);
        return next;
      });

      setEditDirty(false);
      setEditingDraft(null);
      await loadDrafts();
    } catch (err: any) {
      if (!isSameCompany(companyId)) return;
      if (err instanceof ProblemDetailsException && err.problem.status === 409) {
        setIsConflict409(true);
        setEditError(
          `Conflito de Versão (HTTP 409): O rascunho foi modificado no servidor após a abertura. Suas alterações locais foram preservadas.`
        );
      } else if (err instanceof ProblemDetailsException) {
        const mapped = Object.fromEntries(Object.entries(err.formatFieldErrors()).map(([k, v]) => [k, v.join(', ')]));
        showFieldErrors(mapped, err.message || 'O servidor recusou os dados do rascunho.');
      } else {
        setEditError(err.message || 'Erro ao atualizar rascunho.');
      }
    } finally {
      setIsSavingEdit(false);
    }
  };

  // Atalho do próximo mês dia 01 às 00:01 no modal de agendamento
  const handleSetNextMonthShortcut = () => {
    if (!currentCompany) return;
    const target = calculateNextMonthFirstMinute(new Date(), currentCompany.timezone);
    setScheduleDateTime(target.localInputString);
  };

  // Emitir agora ou agendar o lote (as duas ações exigem revisão e confirmação; mesmas validações no servidor)
  const [batchMode, setBatchMode] = useState<ModoEmissao>('NOW');
  const handleConfirmBatchScheduling = async () => {
    if (!currentCompany || selectedDraftIds.size === 0 || !batchReviewConfirmed || (isProd && !prodConfirmed)) return;
    const companyId = currentCompany.id;
    const timezone = currentCompany.timezone;
    if (batchMode === 'SCHEDULED' && !scheduleDateTime) {
      showToast('Selecione a data e hora do agendamento.', 'error');
      return;
    }

    setIsSchedulingBatch(true);
    try {
      const selectedDraftsList = drafts.filter((d) => selectedDraftIds.has(d.id));
      const draftsPayload = selectedDraftsList.map((d) => ({
        id: d.id,
        expected_version: d.version,
      }));

      const scheduledAtIso = batchMode === 'SCHEDULED' ? parseLocalScheduleToUtcIso(scheduleDateTime, timezone) : undefined;
      // Chave estável para o MESMO pedido (mesmas versões e ação): clique repetido devolve o mesmo lote.
      const idempotencyKey = `batch-${companyId}-${batchMode}-${selectedDraftsList.map((d) => `${d.id}.${d.version}`).join('_')}`.slice(0, 200);

      await clients.fiscal.createBatch(
        companyId,
        {
          drafts: draftsPayload,
          mode: batchMode,
          ...(scheduledAtIso ? { scheduled_at: scheduledAtIso } : {}),
          timezone,
          ...(isProd ? {
            production_confirmation: montarConfirmacaoProducao(currentCompany.cnpj, selectedDraftsList.map((d) => ({
              id: d.id, version: d.version, amount: d.amount, descricao: d.customer?.name || d.customer_name || 'Tomador',
            })), batchMode),
          } : {}),
        },
        idempotencyKey
      );
      if (!isSameCompany(companyId)) return; // empresa trocada durante o envio: resposta ignorada

      showToast(batchMode === 'NOW'
        ? `Emissão de ${selectedDraftsList.length} nota(s) iniciada agora. Acompanhe em Agendamentos.`
        : `Lote com ${selectedDraftsList.length} nota(s) agendado com sucesso!`, 'success');
      setIsScheduleModalOpen(false);
      setSelectedDraftIds(new Set());
      await refreshCounters();
      onNavigate('agendamentos');
    } catch (err: any) {
      if (isSameCompany(companyId)) showToast(err.message || 'Falha ao agendar lote de rascunhos.', 'error');
    } finally {
      setIsSchedulingBatch(false);
    }
  };

  const columns: Column<Draft>[] = [
    {
      key: 'select',
      header: (
        <input
          type="checkbox"
          aria-label="Selecionar todos os rascunhos editáveis desta página"
          checked={selecionaveis.length > 0 && selecionaveis.every((d) => selectedDraftIds.has(d.id))}
          onChange={toggleSelectAll}
          disabled={selecionaveis.length === 0}
          style={{ width: '16px', height: '16px', accentColor: 'var(--color-primary)' }}
        />
      ),
      width: '40px',
      // Só rascunho editável entra em "Emitir ou agendar": emitidos, em fila ou rejeitados não são selecionáveis.
      render: (d) => d.status === 'DRAFT' ? (
        <input
          type="checkbox"
          aria-label={`Selecionar rascunho de ${d.customer?.name || 'tomador'}`}
          checked={selectedDraftIds.has(d.id)}
          onChange={() => toggleSelect(d.id)}
          style={{ width: '16px', height: '16px', accentColor: 'var(--color-primary)' }}
        />
      ) : null,
    },
    {
      key: 'customer',
      header: 'Tomador / Cliente',
      render: (d) => (
        <div>
          <div style={{ fontWeight: 600 }}>{d.customer?.name || d.customer_name || 'Cliente Selecionado'}</div>
          {d.source_document_id && (
            <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
              Ref: Doc #{d.source_document_id.slice(0, 8)}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'competence',
      header: 'Competência',
      width: '120px',
      render: (d) => (
        <div>
          <span style={{ fontWeight: 500 }}>{d.competence}</span>
          {d.service_date && (
            <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
              Data: {d.service_date}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'service',
      header: 'Serviço',
      render: (d) => (
        <div>
          <div
            style={{
              fontSize: '0.8125rem',
              color: 'var(--color-text-main)',
              maxWidth: '260px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {d.service.description}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
            Nac: {d.service.national_code || '-'} | Mun: {d.service.municipal_code || '-'}
          </div>
        </div>
      ),
    },
    {
      key: 'amount',
      header: 'Valor',
      align: 'right',
      width: '140px',
      render: (d) => {
        const net = d.retention_summary?.net_amount;
        return (
          <div style={{ textAlign: 'right' }}>
            <span style={{ fontWeight: 700, color: 'var(--color-text-main)' }}>
              {formatCurrencyPtBr(d.amount)}
            </span>
            {net && (
              <div style={{ fontSize: '0.75rem', color: 'var(--color-success, #10b981)', fontWeight: 600 }}>
                Líq: {formatCurrencyPtBr(net)}
              </div>
            )}
          </div>
        );
      },
    },
    {
      key: 'version',
      header: 'Versão',
      align: 'center',
      width: '80px',
      render: (d) => (
        <span style={{ fontSize: '0.75rem', color: 'var(--color-text-subtle)' }}>
          v{d.version}
        </span>
      ),
    },
    {
      key: 'status',
      header: 'Status / Validação',
      width: '160px',
      render: (d) => {
        // Se está agendado
        if (d.status === 'SCHEDULED') {
          return <Badge status="SCHEDULED" customLabel="Agendado" />;
        }
        if (d.status === 'PROCESSING') {
          return <Badge status="PROCESSING" customLabel="Processando" />;
        }
        if (d.status === 'AUTHORIZED') {
          const st = d.issued?.status;
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
              <Badge status="AUTHORIZED" customLabel="Emitida" />
              {st === 'CANCELLED' && <Badge status="CANCELLED" customLabel="NFS-e cancelada" size="sm" />}
              {st === 'SUBSTITUTED' && <Badge status="WITHDRAWN" customLabel="NFS-e substituída" size="sm" />}
              {d.issued?.number && <span className="pf-help">NFS-e nº {d.issued.number}</span>}
            </div>
          );
        }
        if (d.status === 'AWAITING_CONFIRMATION') {
          return <Badge status="PROCESSING" customLabel="Aguardando confirmação" />;
        }
        if (d.status === 'WITHDRAWN') {
          return <Badge status="WITHDRAWN" customLabel="Retirado da Fila" />;
        }
        if (d.status === 'REJECTED') {
          return <Badge status="REJECTED" customLabel="Rejeitado" />;
        }

        // Rascunho editado recentemente ou sem validação
        if (editedDraftIds.has(d.id) || d.validation?.status === 'NOT_VALIDATED') {
          return <Badge status="DIVERGENT" customLabel="Revalidação Necessária" />;
        }

        const issues = draftIssuesMap.get(d.id);
        if (issues && issues.length > 0) {
          const hasError = issues.some((i) => i.severity === 'error');
          return (
            <Badge
              status={hasError ? 'REJECTED' : 'DIVERGENT'}
              customLabel={hasError ? `${issues.length} Erro(s)` : `${issues.length} Alerta(s)`}
            />
          );
        }
        if (issues && issues.length === 0) {
          return <Badge status="REVIEWED" customLabel="Válido" />;
        }

        return <Badge status="DRAFT" customLabel="Rascunho" />;
      },
    },
    {
      key: 'actions',
      header: 'Ações',
      align: 'right',
      width: '210px',
      render: (d) => {
        const isScheduled = d.status === 'SCHEDULED';
        const isEditable = d.status === 'DRAFT' || d.status === 'REJECTED';

        if (isScheduled) {
          return (
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <Button
                size="sm"
                variant="outline"
                title="Retira o rascunho da fila de agendamento para permitir edições"
                isLoading={withdrawingDraftId === d.id}
                onClick={(e) => {
                  e.stopPropagation();
                  handleWithdrawDraft(d);
                }}
              >
                ↩️ Retirar da fila para editar
              </Button>
            </div>
          );
        }

        if (d.status === 'AUTHORIZED') {
          return (
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <Button size="sm" variant="outline" title="Abrir a NFS-e emitida" onClick={(e) => { e.stopPropagation(); verNota(d); }}>
                Ver nota
              </Button>
            </div>
          );
        }
        if (d.status === 'PROCESSING' || d.status === 'AWAITING_CONFIRMATION') {
          return (
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <span className="pf-help" style={{ textAlign: 'right' }}>
                {d.status === 'PROCESSING' ? 'Em processamento — aguarde.' : 'Aguardando a reconciliação oficial — não reenvie.'}
              </span>
            </div>
          );
        }
        if (!isEditable) {
          return (
            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <span
                style={{
                  fontSize: '0.75rem',
                  color: 'var(--color-text-subtle)',
                  fontStyle: 'italic',
                  padding: '0.25rem 0.5rem',
                }}
                title="Bloqueado para edição conforme o contrato fiscal"
              >
                🔒 Bloqueado
              </span>
            </div>
          );
        }

        return (
          <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
            <Button
              size="sm"
              variant="ghost"
              title="Validar rascunho com o backend"
              isLoading={validatingDraftId === d.id}
              onClick={(e) => {
                e.stopPropagation();
                handleValidateDraft(d.id);
              }}
            >
              ✓ Validar
            </Button>
            {d.validation?.validated_version != null && (
              <Button
                size="sm"
                variant="ghost"
                title="Ver o detalhamento das pendências da última validação"
                onClick={(e) => {
                  e.stopPropagation();
                  setPendError(null);
                  setPendDraftId(d.id);
                }}
              >
                ☰ Ver pendências
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              title="Editar rascunho completo"
              onClick={(e) => {
                e.stopPropagation();
                handleOpenEdit(d);
              }}
            >
              ✏️ Editar
            </Button>
            {canPrepare && isDeletable(d) && (
              <Button
                size="sm"
                variant="ghost"
                title="Excluir este rascunho (pede confirmação)"
                aria-label={`Excluir rascunho de ${d.customer?.name || 'tomador'}`}
                onClick={(e) => {
                  e.stopPropagation();
                  handleAskDelete(d);
                }}
              >
                🗑️ Excluir
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      {/* Title & Actions Bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '1rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0 }}>Rascunhos de NFS-e</h1>
          <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.875rem', color: 'var(--color-text-muted)' }}>
            Prepare, valide e edite as notas; depois emita agora ou agende a emissão. Notas emitidas ficam em “Emitidos”.
          </p>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <Button
            variant="secondary"
            disabled={selectedDraftIds.size === 0}
            onClick={() => {
              if (currentCompany) {
                const target = calculateNextMonthFirstMinute(new Date(), currentCompany.timezone);
                setScheduleDateTime(target.localInputString);
              }
              setBatchReviewConfirmed(false);
              setProdConfirmed(false);
              setBatchMode('NOW');
              setIsScheduleModalOpen(true);
            }}
          >
            ⏰ Emitir ou agendar ({selectedDraftIds.size})
          </Button>

          <Button variant="primary" onClick={() => onNavigate('nova-nfse')}>
            ➕ Novo Rascunho
          </Button>
        </div>
      </div>

      {/* Visões (no servidor, antes da paginação) */}
      <div className="pf-filtro" role="group" aria-label="Filtrar rascunhos">
        {([['pending', 'Pendentes'], ['issued', 'Emitidos'], ['all', 'Todos']] as const).map(([v, l]) => (
          <button key={v} type="button" className="pf-filtro-btn" aria-pressed={draftView === v}
            onClick={() => { setDraftView(v); setDraftPage(1); setSelectedDraftIds(new Set()); }}>
            {l}{viewCounts[v] != null ? ` (${viewCounts[v]})` : ''}
          </button>
        ))}
      </div>

      {/* Main Table */}
      <Table
        columns={columns}
        data={drafts}
        keyExtractor={(d) => d.id}
        isLoading={isLoading}
        emptyMessage={draftView === 'issued' ? 'Nenhuma nota emitida a partir de rascunhos.' : draftView === 'pending'
          ? 'Nenhum rascunho pendente.' : 'Nenhum rascunho encontrado para esta empresa.'}
      />
      {draftTotal > PAGE_SIZE && (
        <div className="pf-actions-end" aria-label="Paginação">
          <Button size="sm" variant="outline" disabled={draftPage <= 1} onClick={() => setDraftPage((n) => n - 1)}>Anterior</Button>
          <span className="pf-help">Página {draftPage} de {Math.ceil(draftTotal / PAGE_SIZE)} · {draftTotal} rascunho(s)</span>
          <Button size="sm" variant="outline" disabled={draftPage * PAGE_SIZE >= draftTotal} onClick={() => setDraftPage((n) => n + 1)}>Próxima</Button>
        </div>
      )}

      {/* Modal de Edição Completo do Rascunho */}
      {editingDraft && (
        <Modal
          isOpen={true}
          onClose={closeEditor}
          title={`Editar Rascunho #${editingDraft.id.slice(0, 8)} (v${editingDraft.version})`}
          description="Edição fiscal completa com controle estrito de versão concorrente. Limpar campos opcionais envia sua remoção ao backend."
          maxWidth="900px"
          footer={
            <div className="pf-actions-row" style={{ width: '100%' }}>
              <div className="pf-help">
                Versão esperada: <strong>v{editingDraft.version}</strong>
              </div>
              <div className="pf-actions-end">
                {canPrepare && isDeletable(editingDraft) && (
                  <Button variant="danger" onClick={() => handleAskDelete(editingDraft)} disabled={isSavingEdit}>
                    🗑️ Excluir rascunho
                  </Button>
                )}
                <Button variant="outline" onClick={closeEditor} disabled={isSavingEdit}>
                  Cancelar
                </Button>
                <Button variant="primary" isLoading={isSavingEdit} onClick={handleSaveEdit}>
                  Salvar Rascunho
                </Button>
              </div>
            </div>
          }
        >
          <div ref={editorRef} className="pf-form pf-form-stack" onChangeCapture={(e) => {
            // Marcações do painel da configuração da operação não editam o rascunho.
            if ((e.target as HTMLElement).closest?.('[data-no-dirty]')) return;
            if (!editDirtyRef.current) setEditDirty(true);
          }} style={{ gap: '1.25rem' }}>
            {/* Banner de Erro ou Conflito 409 */}
            {editError && (
              <Alert variant="error" title={isConflict409 ? 'Conflito de Versão (HTTP 409)' : 'Erro ao Salvar'}>
                <div>{editError}</div>
                {isConflict409 && (
                  <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
                    <Button size="sm" variant="secondary" onClick={handleReloadServerData}>
                      🔄 Recarregar Dados do Servidor
                    </Button>
                    <Button size="sm" variant="outline" onClick={handleAdoptLatestVersion}>
                      ⚡ Re-sincronizar Versão (Manter Minhas Edições)
                    </Button>
                  </div>
                )}
              </Alert>
            )}

            <details className="pf-subpanel" open={(editingDraft.issues || []).some((i) => i.severity === 'error') || undefined}>
              <summary>
                <strong>Pendências da validação</strong>{' '}
                <span className="pf-help">
                  {editingDraft.validation?.validated_version == null
                    ? '(não validado)'
                    : `(${(editingDraft.issues || []).filter((i) => i.severity === 'error').length} bloqueio(s), `
                      + `${(editingDraft.issues || []).filter((i) => i.severity === 'warning').length} aviso(s) · v${editingDraft.validation.validated_version})`}
                </span>
              </summary>
              <div style={{ marginTop: '0.75rem' }} data-no-dirty>
                <PendenciasRascunho
                  issues={editingDraft.issues || []}
                  companyId={currentCompany?.id}
                  validatedVersion={editingDraft.validation?.validated_version ?? null}
                  validatedAt={editingDraft.validation?.validated_at ?? null}
                  currentVersion={editingDraft.version}
                  validated={editingDraft.validation?.validated_version != null}
                  dirty={editDirty}
                  onGoToField={irAoCampo}
                  onRevalidate={handleValidateInEditor}
                  isRevalidating={editValidating}
                  error={editValidateError}
                  compact
                />
              </div>
            </details>

            {/* Navegação por Abas das Seções (botões; o texto é usado pelos testes de interface) */}
            <div className="pf-tabs">
              {[
                { id: 'geral', label: '📋 Geral & Valores' },
                { id: 'servico', label: '🛠️ Serviço & Obra' },
                { id: 'iss', label: '🏛️ Tributos e Retenções' },
                { id: 'federais', label: '📊 TotTrib & Reforma IBS/CBS' },
                { id: 'operacao', label: '🧾 Configuração da operação' },
              ].map((tab) => {
                const tabErrors = Object.entries(editFieldErrors).filter(([k, v]) => v && tabOfField(k) === tab.id).length;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className="pf-tab"
                    aria-current={activeTab === tab.id ? 'true' : undefined}
                    onClick={() => setActiveTab(tab.id as any)}
                  >
                    {tab.label}
                    {tabErrors > 0 && <span className="pf-tab-badge" aria-label={`${tabErrors} campo(s) com erro`}>{tabErrors}</span>}
                  </button>
                );
              })}
            </div>

            {/* CONTEÚDO DAS ABAS */}

            {/* ABA 1: Geral & Tomador */}
            {activeTab === 'geral' && (
              <div className="pf-form-stack">
                <div className="pf-form-grid">
                  <Select
                    label="Tomador do Serviço (Cliente)"
                    value={editCustomerId}
                    onChange={(e) => setEditCustomerId(e.target.value)}
                    error={editFieldErrors['customer_id']}
                    helperText="Cliente cadastrado. A alteração atualiza os dados do tomador no rascunho."
                    options={[
                      { value: '', label: 'Selecione um cliente...' },
                      ...customers.map((c) => ({
                        value: c.id,
                        label: `${c.name} (${c.document || c.cnpj_cpf || ''})`,
                      })),
                    ]}
                  />

                  <Input
                    label="Competência (AAAA-MM) *"
                    value={editCompetence}
                    onChange={(e) => setEditCompetence(e.target.value)}
                    error={editFieldErrors['competence']}
                    placeholder="AAAA-MM"
                    helperText="Mês e ano da prestação do serviço (obrigatório)."
                  />

                  <Input
                    label="Data Fiscal do Fato Gerador (dCompet)"
                    type="date"
                    value={editServiceDate}
                    onChange={(e) => setEditServiceDate(e.target.value)}
                    error={editFieldErrors['service_date']}
                    helperText="Opcional. Data de início da prestação (deve pertencer à competência)."
                  />
                </div>

                <section className="pf-subpanel" aria-label="Valores, deduções e descontos">
                  <h3 className="pf-subpanel-title">Valores</h3>
                  <div className="pf-form-grid">
                    <Input
                      label="Valor Total do Serviço (R$) *"
                      value={editAmount}
                      inputMode="decimal"
                      className="pf-money"
                      onChange={(e) => setEditAmount(e.target.value)}
                      error={editFieldErrors['amount']}
                      placeholder="10.000,00"
                      helperText="Valor bruto do serviço (obrigatório, maior que zero)."
                    />

                    <Input
                      label="Deduções da Base de Cálculo (R$)"
                      value={editDeductions}
                      inputMode="decimal"
                      className="pf-money"
                      onChange={(e) => setEditDeductions(e.target.value)}
                      error={editFieldErrors['deductions']}
                      placeholder="0,00"
                      helperText="Opcional. Limpe para não aplicar deduções."
                    />

                    <Input
                      label="Desconto Incondicionado (R$)"
                      value={editUnconditionalDiscount}
                      inputMode="decimal"
                      className="pf-money"
                      onChange={(e) => setEditUnconditionalDiscount(e.target.value)}
                      error={editFieldErrors['unconditional_discount']}
                      placeholder="0,00"
                      helperText="vDescIncond (opcional). Limpe para remover."
                    />

                    <Input
                      label="Desconto Condicionado (R$)"
                      value={editConditionalDiscount}
                      inputMode="decimal"
                      className="pf-money"
                      onChange={(e) => setEditConditionalDiscount(e.target.value)}
                      error={editFieldErrors['conditional_discount']}
                      placeholder="0,00"
                      helperText="vDescCond (opcional). Limpe para remover."
                    />
                  </div>
                </section>
              </div>
            )}

            {/* ABA 2: Serviço & Obra */}
            {activeTab === 'servico' && (
              <div className="pf-form-stack">
                <ServiceProfilePicker onApply={applyServiceProfile} selectedId={editServiceProfileId}
                  onClear={() => { setEditDirty(true); setEditServiceProfileId(null); }} />

                <div className="pf-form-grid">
                  <div className="pf-span-all">
                    <Textarea
                      label="Discriminação dos Serviços (xDescServ) *"
                      rows={3}
                      value={editDescription}
                      maxLength={2000}
                      onChange={(e) => { setEditDescription(e.target.value); clearEditErrors('service.description'); }}
                      error={editFieldErrors['service.description']}
                      placeholder="Descrição clara e detalhada dos serviços executados..."
                      helperText={`Obrigatório. ${editDescription.length}/2000 caracteres.`}
                    />
                  </div>

                  <Input
                    label="Cód. Tributação Nacional (cTribNac)"
                    value={editNationalCode}
                    inputMode="numeric"
                    onChange={(e) => { setEditNationalCode(e.target.value); clearEditErrors('service.national_code'); }}
                    error={editFieldErrors['service.national_code']}
                    placeholder="6 dígitos"
                    maxLength={6}
                    helperText="6 dígitos numéricos."
                  />

                  <Input
                    label="Cód. Tributação Municipal (cTribMun)"
                    value={editMunicipalCode}
                    inputMode="numeric"
                    onChange={(e) => { setEditMunicipalCode(e.target.value); clearEditErrors('service.municipal_code'); }}
                    error={editFieldErrors['service.municipal_code']}
                    placeholder="3 dígitos"
                    maxLength={3}
                    helperText="Exatamente 3 dígitos (zeros à esquerda preservados)."
                  />

                  <Input
                    label="Local da prestação — município (IBGE)"
                    value={editLocationCode}
                    inputMode="numeric"
                    onChange={(e) => { setEditLocationCode(e.target.value); clearEditErrors('service.location_code'); }}
                    error={editFieldErrors['service.location_code']}
                    placeholder="7 dígitos"
                    maxLength={7}
                    helperText="cLocPrestacao: 7 dígitos IBGE do município da prestação."
                  />

                  <Input
                    label="Código NBS (cNBS)"
                    value={editNbsCode}
                    inputMode="numeric"
                    onChange={(e) => { setEditNbsCode(e.target.value); clearEditErrors('service.nbs_code'); }}
                    error={editFieldErrors['service.nbs_code']}
                    placeholder="9 dígitos"
                    maxLength={9}
                    helperText="9 dígitos NBS (opcional; exigido se informado IBS/CBS)."
                  />
                </div>

                {/* Grupo obra (serv/obra): exibido para subitens de obra ou quando já há dados */}
                <ObraFields
                  value={editObra}
                  nationalCode={editNationalCode}
                  errors={editFieldErrors}
                  onChange={(v) => { setEditObra(v); clearEditErrors('service.obra_code', 'service.obra_cib', 'service.obra_address', 'service.obra_property_registration'); }}
                />
                {!shouldShowObra(editNationalCode, editObra) && editNationalCode.replace(/\D/g, '').length === 6 && (
                  <p className="pf-help">Grupo obra não se aplica a este código de serviço (exigido só nos subitens de construção civil — E0370).</p>
                )}

                <InfoComplFields
                  value={editInfoCompl}
                  errors={editFieldErrors}
                  onChange={(v) => { setEditInfoCompl(v); clearEditErrors('service.additional_info'); }}
                />
              </div>
            )}

            {/* ABA 3: Tributos e Retenções */}
            {activeTab === 'iss' && (
              <div className="pf-form-stack" style={{ gap: '1.25rem' }}>
                <TributosRetencoesForm
                  values={editTributosState}
                  onChange={handleEditTributoChange}
                  errors={editFieldErrorLists}
                  isCardWrapper={false}
                />

                <CanonicalRetentionSummaryCard
                  source={editRetentionSummaryCanon.source}
                  diverged={editRetentionSummaryCanon.diverged}
                  summary={editRetentionSummary}
                  title="Resumo Canônico de Retenções e Valor Líquido"
                  subtitle="Demonstrativo atualizado em tempo real com base nos valores e retenções deste rascunho"
                />
              </div>
            )}

            {/* ABA 4: TotTrib & Reforma IBS/CBS */}
            {activeTab === 'federais' && (
              <div className="pf-form-stack" style={{ gap: '1.25rem' }}>
                {/* Tributos Aproximados (Lei da Transparência) */}
                <section className="pf-subpanel" aria-label="Tributos aproximados">
                  <h3 className="pf-subpanel-title">Tributos Aproximados (totTrib / Lei 12.741/2012)</h3>
                  <div className="pf-form-grid">
                    <Select
                      label="Modo de Informação"
                      value={editApproxMode}
                      onChange={(e) => setEditApproxMode(e.target.value as any)}
                      helperText="Como os tributos aproximados constam na DPS."
                      options={[
                        { value: 'NOT_INFORMED', label: 'Não Informado (remover grupo)' },
                        { value: 'VALUES', label: 'Valores em Moeda (R$)' },
                        { value: 'PERCENT', label: 'Percentual Geral (%)' },
                        { value: 'SIMPLES_PERCENT', label: 'Percentual Simples Nacional (%)' },
                      ]}
                    />

                    {editApproxMode === 'VALUES' && (
                      <>
                        <Input label="Federal (R$)" value={editApproxFederal} inputMode="decimal" className="pf-money"
                          onChange={(e) => setEditApproxFederal(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                        <Input label="Estadual (R$)" value={editApproxState} inputMode="decimal" className="pf-money"
                          onChange={(e) => setEditApproxState(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                        <Input label="Municipal (R$)" value={editApproxMunicipal} inputMode="decimal" className="pf-money"
                          onChange={(e) => setEditApproxMunicipal(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                      </>
                    )}

                    {editApproxMode === 'PERCENT' && (
                      <Input label="Percentual Federal / Total (%)" value={editApproxFederal} inputMode="decimal" className="pf-money"
                        onChange={(e) => setEditApproxFederal(e.target.value)} placeholder="0.00" helperText="Percentual total estimado." />
                    )}

                    {editApproxMode === 'SIMPLES_PERCENT' && (
                      <Input label="Percentual Simples Nacional (%)" value={editApproxSimples} inputMode="decimal" className="pf-money"
                        onChange={(e) => setEditApproxSimples(e.target.value)} placeholder="0.00" helperText="Percentual do Simples Nacional." />
                    )}
                  </div>
                </section>

                {/* Reforma Tributária: IBS & CBS */}
                <section className="pf-subpanel" aria-label="Reforma Tributária: IBS e CBS">
                  <div className="pf-actions-row">
                    <h3 className="pf-subpanel-title">Reforma Tributária: IBS / CBS (DPS v1.01)</h3>
                    <label className="pf-check">
                      <input
                        type="checkbox"
                        checked={editEnableIbsCbs}
                        onChange={(e) => setEditEnableIbsCbs(e.target.checked)}
                      />
                      <span>Habilitar Grupo IBS/CBS</span>
                    </label>
                  </div>

                  {editEnableIbsCbs && (
                    <IbsCbsFields serviceDate={editServiceDate} nationalCode={editNationalCode}
                      value={editIbs}
                      errors={editFieldErrors}
                      onChange={(v) => { setEditIbs(v); clearEditErrors('tax_settings.ibs_cbs.operation_indicator', 'tax_settings.ibs_cbs.class_code', 'tax_settings.ibs_cbs.cst'); }}
                      groups={editIbsGroups}
                      onGroupsChange={(g) => { setEditIbsGroups(g); clearEditErrors('tax_settings.ibs_cbs.regular', 'tax_settings.ibs_cbs.deferral', 'tax_settings.ibs_cbs.real_estate', 'tax_settings.ibs_cbs.recipient', 'tax_settings.ibs_cbs.presumed_credit'); }} />
                  )}
                  {editingDraft && <IbsCbsPreviewCard draftId={editingDraft.id} version={editingDraft.version} />}
                </section>
              </div>
            )}

            {/* ABA 5: configuração tributária da operação (nota de referência ou preenchimento manual) */}
            {activeTab === 'operacao' && editingDraft && (
              <OperacaoTributaria draft={editingDraft} busy={isOperatingEdit}
                onRegister={handleRegisterEditOperation} onApprove={handleApproveEditOperation}
                blockedReason={editDirty ? 'Há alterações não salvas: salve o rascunho antes de registrar ou aprovar a configuração desta operação.' : null} />
            )}
          </div>
        </Modal>
      )}

      {/* Confirmação de exclusão: identifica tomador, serviço e valor; erro mantém o rascunho. */}
      {deletingDraft && (
        <Modal
          isOpen={true}
          onClose={() => !isDeleting && setDeletingDraft(null)}
          title="Excluir rascunho?"
          description="O rascunho sai da lista e não poderá mais ser editado nem emitido. O histórico e a auditoria são preservados; notas emitidas não são afetadas."
          maxWidth="560px"
          footer={
            <div className="pf-actions-end">
              <Button variant="outline" onClick={() => setDeletingDraft(null)} disabled={isDeleting}>
                Cancelar
              </Button>
              <Button variant="danger" isLoading={isDeleting} onClick={handleConfirmDelete}>
                Excluir rascunho
              </Button>
            </div>
          }
        >
          <dl className="pf-dl" aria-label="Rascunho a excluir">
            <dt>Tomador</dt>
            <dd>
              {deletingDraft.customer?.name || '—'}
              {deletingDraft.customer?.document ? ` · ${deletingDraft.customer.document}` : ''}
            </dd>
            <dt>Serviço</dt>
            <dd>
              {deletingDraft.service?.national_code ? `${deletingDraft.service.national_code} — ` : ''}
              {(deletingDraft.service?.description || '—').slice(0, 160)}
              {(deletingDraft.service?.description || '').length > 160 ? '…' : ''}
            </dd>
            <dt>Valor do serviço</dt>
            <dd><strong>{formatCurrencyPtBr(deletingDraft.amount)}</strong></dd>
            <dt>Competência</dt>
            <dd>{deletingDraft.competence} · versão v{deletingDraft.version}</dd>
          </dl>
          {deleteError && (
            <Alert variant="error" title="O rascunho não foi excluído">
              {deleteError}
            </Alert>
          )}
        </Modal>
      )}

      {/* Painel persistente das pendências do rascunho (fica aberto até ser fechado). */}
      {pendDraftId && (() => {
        const pd = drafts.find((x) => x.id === pendDraftId);
        if (!pd) return null;
        const podeEditar = pd.status === 'DRAFT' || pd.status === 'REJECTED';
        return (
          <Modal
            isOpen={true}
            onClose={() => setPendDraftId(null)}
            title="Pendências do rascunho"
            description={`${pd.customer?.name || 'Tomador'} · ${formatCurrencyPtBr(pd.amount)} · competência ${pd.competence}`}
            maxWidth="820px"
            footer={
              <div className="pf-actions-end">
                {podeEditar && (
                  <Button variant="outline" onClick={() => { setPendDraftId(null); handleOpenEdit(pd); }}>
                    ✏️ Abrir no editor
                  </Button>
                )}
                <Button variant="primary" onClick={() => setPendDraftId(null)}>Fechar</Button>
              </div>
            }
          >
            <PendenciasRascunho
              issues={pd.issues || []}
              companyId={currentCompany?.id}
              validatedVersion={pd.validation?.validated_version ?? null}
              validatedAt={pd.validation?.validated_at ?? null}
              currentVersion={pd.version}
              validated={pd.validation?.status !== 'NOT_VALIDATED' || pd.validation?.validated_version != null}
              onRevalidate={podeEditar ? () => handleValidateDraft(pd.id) : undefined}
              isRevalidating={validatingDraftId === pd.id}
              error={pendError}
            />
          </Modal>
        );
      })()}

      {/* Modal de Agendamento de Lote: revisão agrupada de cada nota + confirmação explícita */}
      <Modal
        isOpen={isScheduleModalOpen}
        onClose={() => setIsScheduleModalOpen(false)}
        title="Emitir ou agendar"
        description={`Transmissão de ${selectedDraftIds.size} rascunho(s) selecionado(s): agora ou na data agendada.`}
        maxWidth="900px"
        footer={
          <>
            <Button variant="outline" onClick={() => setIsScheduleModalOpen(false)}>
              Cancelar
            </Button>
            <Button variant="primary" isLoading={isSchedulingBatch} disabled={!batchReviewConfirmed || (isProd && !prodConfirmed)}
              onClick={handleConfirmBatchScheduling}
              title={!batchReviewConfirmed ? 'Marque “Conferi os dados acima” para continuar'
                : isProd && !prodConfirmed ? 'Confirme a emissão em produção' : undefined}>
              {batchMode === 'NOW' ? 'Emitir agora' : 'Confirmar Agendamento do Lote'}
            </Button>
          </>
        }
      >
        <div className="pf-form pf-form-stack" style={{ gap: '1.25rem' }}>
          <section className="pf-subpanel" aria-label="Data e hora do agendamento">
            <h3 className="pf-subpanel-title">Quando transmitir</h3>
            <label className="pf-confirm">
              <input type="radio" name="batch-mode" checked={batchMode === 'NOW'}
                onChange={() => { setBatchMode('NOW'); setProdConfirmed(false); }} />
              <span><strong>Emitir agora</strong> — o processamento começa imediatamente após a confirmação.</span>
            </label>
            <label className="pf-confirm">
              <input type="radio" name="batch-mode" checked={batchMode === 'SCHEDULED'}
                onChange={() => { setBatchMode('SCHEDULED'); setProdConfirmed(false); }} />
              <span><strong>Agendar emissão</strong> para a data e hora abaixo.</span>
            </label>
            {batchMode === 'SCHEDULED' && (<>
            <div>
              <Button variant="outline" size="sm" onClick={handleSetNextMonthShortcut}>
                Próximo mês, dia 01 às 00:01
              </Button>
              <p className="pf-help" style={{ marginTop: '0.25rem' }}>
                Calcula viradas de ano e meses com 28, 30 ou 31 dias no fuso {currentCompany?.timezone}.
              </p>
            </div>

            <div className="pf-form-grid">
              <Input
                label="Data e Hora do Início da Transmissão"
                type="datetime-local"
                value={scheduleDateTime}
                onChange={(e) => setScheduleDateTime(e.target.value)}
                helperText={`Fuso horário aplicado: ${currentCompany?.timezone || 'America/Sao_Paulo'}`}
              />
            </div>
            </>)}
          </section>

          <section className="pf-form-stack" aria-label="Revisão das notas do lote">
            <h3 className="pf-subpanel-title">Revisão das notas do lote</h3>
            {(() => {
              const sel = drafts.filter((d) => selectedDraftIds.has(d.id));
              const comBloqueio = sel.filter((d) => (d.issues || []).some((i) => i.severity === 'error')).length;
              const semValidar = sel.filter((d) => d.validation?.status === 'NOT_VALIDATED').length;
              return comBloqueio || semValidar ? (
                <Alert variant={comBloqueio ? 'error' : 'warning'} title="Pendências antes de emitir">
                  {comBloqueio ? `${comBloqueio} rascunho(s) com bloqueio(s): o servidor recusará a emissão até a correção. ` : ''}
                  {semValidar ? `${semValidar} rascunho(s) sem validação da versão atual: o servidor valida de novo ao emitir ou agendar.` : ''}
                </Alert>
              ) : null;
            })()}
            <div className="pf-review-batch">
              {drafts.filter((d) => selectedDraftIds.has(d.id)).map((d, idx, list) => (
                <details key={d.id} open={list.length === 1 || idx === 0}>
                  <summary>
                    {d.customer?.name || d.customer_name || 'Tomador'} — {formatCurrencyPtBr(d.amount)} — competência {d.competence} (v{d.version})
                  </summary>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div style={{ marginBottom: '0.75rem' }}>
                      <PendenciasRascunho
                        issues={d.issues || []}
                        companyId={currentCompany?.id}
                        validatedVersion={d.validation?.validated_version ?? null}
                        validatedAt={d.validation?.validated_at ?? null}
                        currentVersion={d.version}
                        validated={d.validation?.validated_version != null}
                        compact
                      />
                    </div>
                    <RevisaoNfse
                      prestador={{ name: currentCompany?.name, cnpj: currentCompany?.cnpj }}
                      tomador={{ name: d.customer?.name || d.customer_name, document: d.customer?.document }}
                      competence={d.competence}
                      serviceDate={d.service_date}
                      service={d.service}
                      amount={d.amount}
                      unconditionalDiscount={d.unconditional_discount}
                      conditionalDiscount={d.conditional_discount}
                      deductions={d.deductions}
                      taxSettings={d.tax_settings}
                      retention={d.retention_summary ? { total_withheld: d.retention_summary.total_withheld, net_amount: d.retention_summary.net_amount } : null}
                    />
                  </div>
                </details>
              ))}
            </div>
          </section>

          <Alert variant="info">
            Emitir agora: os itens entram na fila imediatamente. Agendar: tornam-se elegíveis no horário escolhido. Um
            rascunho só entra na fila uma vez; resultado incerto é consultado na Sefin antes de qualquer reenvio.
            Um cabeçalho Idempotency-Key estável é gerado para prevenir duplicidade de envio.
          </Alert>

          <ConfirmacaoRevisao checked={batchReviewConfirmed} onChange={setBatchReviewConfirmed} />
          {isProd && currentCompany && (
            <ConfirmacaoProducao empresa={{ name: currentCompany.name, cnpj: currentCompany.cnpj }}
              docs={drafts.filter((d) => selectedDraftIds.has(d.id)).map((d) => ({
                id: d.id, version: d.version, amount: d.amount, descricao: d.customer?.name || d.customer_name || 'Tomador',
              }))}
              checked={prodConfirmed} onChange={setProdConfirmed} modo={batchMode}
              quando={batchMode === 'SCHEDULED' ? scheduleDateTime.replace('T', ' ') : undefined} />
          )}
        </div>
      </Modal>
    </div>
  );
};
