import React, { useState, useEffect, useMemo, useRef } from 'react';
import { IbsCbsFields, IbsCbsValue, EMPTY_IBS_CBS } from '../components/IbsCbsFields.js';
import { IbsCbsGroupsState, EMPTY_IBS_GROUPS, groupsFromIbs, applyGroups, validateGroups } from '../components/IbsCbsGruposFields.js';
import { ObraFields, ObraState, EMPTY_OBRA, obraFromService, obraToService, validateObra, shouldShowObra } from '../components/ObraFields.js';
import { InfoComplFields, InfoComplState, EMPTY_INFO_COMPL, infoComplFromService, infoComplToPayload, validateInfoCompl } from '../components/InfoComplFields.js';
import { ServiceProfilePicker } from '../components/ServiceProfilePicker.js';
import { RevisaoNfse, ConfirmacaoRevisao } from '../components/RevisaoNfse.js';
import { PendenciasRascunho } from '../components/PendenciasRascunho.js';
import { normalizarPercentual, trocaDeUnidade } from '../components/tributosAproximados.js';
import { conflitoPrestacao, diaNoFuso } from '../components/datasEmissao.js';
import { ConfirmacaoProducao, montarConfirmacaoProducao, ModoEmissao } from '../components/ConfirmacaoProducao.js';
import { OperacaoTributaria } from '../components/OperacaoTributaria.js';
import { ScreenSection } from '../components/screen/ScreenSection.js';
import { useFiscal, logSafe } from '../context/FiscalContext.js';
import {
  Button,
  Input,
  Textarea,
  Select,
  Alert,
  Badge,
} from '@ctrl-fiscal/ui';
import { useUnsavedChanges } from '@ctrl-fiscal/ui';
import {
  Customer,
  FiscalDocument,
  Draft,
  DraftService,
  TaxSettings,
  ApproxTax,
  ServiceProfile,
  ValidationIssue,
  formatCurrencyPtBr,
  parseCurrencyPtBrToDecimal,
  calculateNextMonthFirstMinute,
  getEnvironmentBadgeConfig,
  ProblemDetailsException,
  CalculateSummaryParams,
} from '@ctrl-fiscal/api-client';
import { TributosRetencoesForm, TributosRetencoesState } from '../components/TributosRetencoesForm.js';
import { detalhesAliquotaIss } from '../components/aliquotaIss.js';
import { CanonicalRetentionSummaryCard } from '../components/CanonicalRetentionSummaryCard.js';
import { useCanonicalRetentions } from '../components/useCanonicalRetentions.js';

const EMPTY_TRIBUTOS: TributosRetencoesState = {
  issqnTaxation: 'TAXABLE',
  issRetention: 'NOT_RETAINED',
  issRate: '',
  issReferenceRate: '',
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
};

const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

export const NovaNfsePage: React.FC<{ onNavigate: (route: string) => void }> = ({ onNavigate }) => {
  const { currentCompany, integrationStatus, clients, showToast, refreshCounters, setLeaveGuard } = useFiscal();

  // Aviso ao sair com alterações não salvas (navegação interna, troca de empresa e fechar/recarregar a aba).
  const [touched, setTouched] = useState(false);
  const touchedRef = useRef(false); // atualizado no próprio evento: a proteção vale mesmo antes do próximo render
  const savedRef = useRef(false);
  useUnsavedChanges(touched && !savedRef.current, 'Há alterações não salvas nesta nota. Sair mesmo assim?');
  useEffect(() => {
    setLeaveGuard(() => !touchedRef.current || savedRef.current || window.confirm('Há alterações não salvas nesta nota. Sair mesmo assim?'));
    return () => setLeaveGuard(null);
  }, [setLeaveGuard]);

  // Segurança do workspace: a resposta de um envio iniciado numa empresa é ignorada se a empresa mudou.
  const companyIdRef = useRef<string | undefined>(currentCompany?.id);
  const mountedRef = useRef(true);
  useEffect(() => { companyIdRef.current = currentCompany?.id; }, [currentCompany?.id]);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  const isSameCompany = (id: string) => mountedRef.current && companyIdRef.current === id;

  // Wizard Steps: 1: Preencher, 2: Revisar
  const [step, setStep] = useState<1 | 2>(1);
  const [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [prodConfirmed, setProdConfirmed] = useState(false);
  // Ação final: “Emitir agora” (processamento imediato) ou “Agendar emissão” (dia 01 às 00:01). Trocar a ação desfaz a
  // confirmação de produção, que é vinculada à ação escolhida.
  const [emitMode, setEmitMode] = useState<ModoEmissao>('NOW');

  // Clientes
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [selectedCustomerId, setSelectedCustomerId] = useState<string>('');

  // Competência e data do fato gerador (datas; nenhum código fiscal é pré-preenchido)
  const [serviceDate, setServiceDate] = useState(todayIso);
  const [competence, setCompetence] = useState(() => todayIso().slice(0, 7));

  // Classificação do Serviço (sem valores padrão)
  const [nationalCode, setNationalCode] = useState('');
  const [municipalCode, setMunicipalCode] = useState('');
  const [locationCode, setLocationCode] = useState(''); // 7 dígitos IBGE
  const [nbsCode, setNbsCode] = useState(''); // 9 dígitos NBS
  const [description, setDescription] = useState('');

  // Obra (serv/obra) e informações complementares (serv/infoCompl)
  const [obra, setObra] = useState<ObraState>(EMPTY_OBRA);
  const [infoCompl, setInfoCompl] = useState<InfoComplState>(EMPTY_INFO_COMPL);

  // Valores e Descontos
  const [amountInput, setAmountInput] = useState('');
  const [unconditionalDiscountInput, setUnconditionalDiscountInput] = useState('');
  const [conditionalDiscountInput, setConditionalDiscountInput] = useState('');
  const [deductionsInput, setDeductionsInput] = useState('');

  // Seção Tributos e Retenções (ISS, IRRF, INSS/CP, CSLL, PIS/COFINS)
  const [tributosState, setTributosState] = useState<TributosRetencoesState>(EMPTY_TRIBUTOS);

  const handleTributoChange = <K extends keyof TributosRetencoesState>(
    field: K,
    value: TributosRetencoesState[K]
  ) => {
    setTributosState((prev) => ({ ...prev, [field]: value }));
  };

  // Cálculo Canônico de Retenções e Valor Líquido em Tempo Real
  const retentionSummaryParams = useMemo<CalculateSummaryParams>(() => {
    return ({
      amount: amountInput,
      unconditionalDiscount: unconditionalDiscountInput,
      conditionalDiscount: conditionalDiscountInput,
      deductions: deductionsInput,
      taxSettings: {
        issqn_taxation: tributosState.issqnTaxation,
        iss_retention: tributosState.issRetention,
        iss_rate: tributosState.issRate.trim() || null,
        iss_reference_rate: tributosState.issReferenceRate.trim() || null,
        immunity_type: tributosState.issqnTaxation === 'IMMUNE' ? tributosState.immunityType : null,
        irrf_withheld: tributosState.irrfWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.irrfWithheld) : null,
        inss_withheld: tributosState.inssWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.inssWithheld) : null,
        csll_withheld: tributosState.csllWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.csllWithheld) : null,
        pis_cofins_retention: tributosState.pisCofinsRetention ? (tributosState.pisCofinsRetention as any) : null,
        pis_cofins_cst: tributosState.pisCofinsCst || null,
        pis_cofins_base: tributosState.pisCofinsBase.trim() ? parseCurrencyPtBrToDecimal(tributosState.pisCofinsBase) : null,
        pis_rate: tributosState.pisRate.trim() || null,
        cofins_rate: tributosState.cofinsRate.trim() || null,
        pis_amount: tributosState.pisAmount.trim() ? parseCurrencyPtBrToDecimal(tributosState.pisAmount) : null,
        cofins_amount: tributosState.cofinsAmount.trim() ? parseCurrencyPtBrToDecimal(tributosState.cofinsAmount) : null,
      },
    });
  }, [amountInput, unconditionalDiscountInput, conditionalDiscountInput, deductionsInput, tributosState]);
  const retentionSummaryCanon = useCanonicalRetentions(retentionSummaryParams);
  const retentionSummary = retentionSummaryCanon.summary;

  // Tributos Aproximados (totTrib / Lei da Transparência)
  const [approxMode, setApproxMode] = useState<'NOT_INFORMED' | 'VALUES' | 'PERCENT' | 'SIMPLES_PERCENT'>('NOT_INFORMED');
  const [approxFederal, setApproxFederal] = useState('');
  const [approxState, setApproxState] = useState('');
  const [approxMunicipal, setApproxMunicipal] = useState('');
  const [approxSimples, setApproxSimples] = useState('');
  const [approxAviso, setApproxAviso] = useState<string | null>(null);

  // Reforma Tributária: IBS / CBS (DPS v1.01). Sem valores padrão: cIndOp/cClassTrib vêm da contabilidade.
  const [enableIbsCbs, setEnableIbsCbs] = useState(false);
  const [ibs, setIbs] = useState<IbsCbsValue>(EMPTY_IBS_CBS);
  const [ibsGroups, setIbsGroups] = useState<IbsCbsGroupsState>(EMPTY_IBS_GROUPS);

  // Estado da Última Nota e Rascunho Ativo
  const [isLoadingLastDoc, setIsLoadingLastDoc] = useState(false);
  const [lastDocReference, setLastDocReference] = useState<FiscalDocument | null>(null);
  const [lastDocNotFound, setLastDocNotFound] = useState(false);
  const [activeDraft, setActiveDraft] = useState<Draft | null>(null);
  const [serviceProfileId, setServiceProfileId] = useState<string | null>(null);

  // Pré-validação e Erros Associados aos Campos
  const [validationIssues, setValidationIssues] = useState<ValidationIssue[]>([]);
  const [isValidating, setIsValidating] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [apiErrorsSummary, setApiErrorsSummary] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const errorText = useMemo(
    () => Object.fromEntries(Object.entries(fieldErrors).map(([k, v]) => [k, v.join(', ')])) as Record<string, string | undefined>,
    [fieldErrors]
  );

  // Carregar lista de clientes da empresa atual
  useEffect(() => {
    if (!currentCompany) return;
    let alive = true;
    clients.core
      .getCustomers(currentCompany.id)
      .then((res) => {
        if (!alive) return;
        setCustomers(res.items);
        if (res.items.length > 0) setSelectedCustomerId((prev) => prev || res.items[0].id);
      })
      .catch((err) => {
        logSafe('clientes', err);
      });
    return () => { alive = false; };
  }, [currentCompany, clients.core]);

  const clearFieldError = (fieldKey: string) => {
    if (fieldErrors[fieldKey]) {
      setFieldErrors((prev) => {
        const next = { ...prev };
        delete next[fieldKey];
        return next;
      });
    }
  };
  const clearFieldErrorsByPrefix = (...prefixes: string[]) => {
    if (!Object.keys(fieldErrors).some((k) => prefixes.some((p) => k === p || k.startsWith(`${p}.`)))) return;
    setFieldErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => !prefixes.some((p) => k === p || k.startsWith(`${p}.`)))));
  };

  // Carrega no formulário TODOS os campos de um rascunho do servidor (inclusive obra, infoCompl e grupos IBS/CBS).
  const loadDraftIntoForm = (d: Draft) => {
    setServiceProfileId(d.service_profile_id || null);
    setDescription(d.service.description || '');
    setAmountInput(formatCurrencyPtBr(d.amount, false));
    setCompetence(d.competence);
    if (d.service_date) setServiceDate(d.service_date);
    setNationalCode(d.service.national_code || '');
    setMunicipalCode(d.service.municipal_code || '');
    setLocationCode(d.service.location_code || '');
    setNbsCode(d.service.nbs_code || '');
    setObra(obraFromService(d.service));
    setInfoCompl(infoComplFromService(d.service));
    setUnconditionalDiscountInput(d.unconditional_discount ? formatCurrencyPtBr(d.unconditional_discount, false) : '');
    setConditionalDiscountInput(d.conditional_discount ? formatCurrencyPtBr(d.conditional_discount, false) : '');
    setDeductionsInput(d.deductions && d.deductions !== '0.00' ? formatCurrencyPtBr(d.deductions, false) : '');

    const ts = d.tax_settings;
    setTributosState(ts ? {
      issqnTaxation: ts.issqn_taxation || 'TAXABLE',
      issRetention: ts.iss_retention || 'NOT_RETAINED',
      issRate: ts.iss_rate || '',
      issReferenceRate: ts.iss_reference_rate || '',
      immunityType: ts.immunity_type || '1',
      irrfWithheld: ts.irrf_withheld ? formatCurrencyPtBr(ts.irrf_withheld, false) : '',
      inssWithheld: ts.inss_withheld ? formatCurrencyPtBr(ts.inss_withheld, false) : '',
      csllWithheld: ts.csll_withheld ? formatCurrencyPtBr(ts.csll_withheld, false) : '',
      pisCofinsRetention: ts.pis_cofins_retention !== null && ts.pis_cofins_retention !== undefined ? String(ts.pis_cofins_retention) : '',
      pisCofinsCst: ts.pis_cofins_cst || '',
      pisCofinsBase: ts.pis_cofins_base ? formatCurrencyPtBr(ts.pis_cofins_base, false) : '',
      pisRate: ts.pis_rate || '',
      cofinsRate: ts.cofins_rate || '',
      pisAmount: ts.pis_amount ? formatCurrencyPtBr(ts.pis_amount, false) : '',
      cofinsAmount: ts.cofins_amount ? formatCurrencyPtBr(ts.cofins_amount, false) : '',
    } : EMPTY_TRIBUTOS);

    const ap = ts?.approx_tax;
    if (ap && ap.mode !== 'NOT_INFORMED') {
      setApproxMode(ap.mode);
      // Valores (R$) no formato brasileiro; percentuais exatamente como gravados.
      const fmt = (v?: string | null) => (v ? (ap.mode === 'VALUES' ? formatCurrencyPtBr(v, false) : v) : '');
      setApproxFederal(fmt(ap.federal));
      setApproxState(fmt(ap.state));
      setApproxMunicipal(fmt(ap.municipal));
      setApproxSimples(ap.simples || '');
    } else {
      setApproxMode('NOT_INFORMED');
      setApproxFederal(''); setApproxState(''); setApproxMunicipal(''); setApproxSimples('');
    }

    const ib = ts?.ibs_cbs;
    setEnableIbsCbs(!!ib);
    setIbs(ib ? {
      operation_indicator: ib.operation_indicator || '',
      cst: ib.cst || '',
      class_code: ib.class_code || '',
      personal_use: (ib.personal_use as '' | '0' | '1') || '',
    } : EMPTY_IBS_CBS);
    setIbsGroups(groupsFromIbs(ib));
  };

  // Ação "Usar última nota" (Chama POST from-document no backend para produzir nova identidade)
  const handleUseLastDocument = async () => {
    if (!currentCompany || !selectedCustomerId) {
      showToast('Selecione um cliente primeiro.', 'error');
      return;
    }
    const companyId = currentCompany.id;

    setIsLoadingLastDoc(true);
    setLastDocReference(null);
    setLastDocNotFound(false);
    setActiveDraft(null);
    setFieldErrors({});
    setApiErrorsSummary([]);
    setValidationIssues([]);

    try {
      const lastDoc = await clients.fiscal.getLastIssuedDocument(companyId, selectedCustomerId);
      if (!isSameCompany(companyId)) return;
      setLastDocReference(lastDoc);

      // Chamada oficial: identidade do rascunho é criada no backend via from-document
      const draftFromBackend = await clients.fiscal.createDraftFromDocument(companyId, {
        source_document_id: lastDoc.id,
      }, `nova-nfse-${lastDoc.id}-${Date.now().toString(36)}`);
      if (!isSameCompany(companyId)) return;

      setActiveDraft(draftFromBackend);
      loadDraftIntoForm(draftFromBackend);

      showToast(`Rascunho criado no servidor (#${draftFromBackend.id.slice(0, 8)}) com base na Nota nº ${lastDoc.number}. Situação: Rascunho.`, 'success');
    } catch (err: any) {
      if (!isSameCompany(companyId)) return;
      if (err instanceof ProblemDetailsException && err.problem.status === 404) {
        setLastDocNotFound(true);
        showToast('Nenhuma nota anterior autorizada encontrada para este cliente.', 'info');
      } else {
        showToast(err.message || 'Falha ao buscar última nota.', 'error');
      }
    } finally {
      setIsLoadingLastDoc(false);
    }
  };

  // "Aplicar perfil de serviço": sobrescreve só os campos que o perfil define.
  const applyServiceProfile = (p: ServiceProfile) => {
    touchedRef.current = true;
    setTouched(true);
    setServiceProfileId(p.id);
    if (p.national_code) setNationalCode(p.national_code);
    if (p.municipal_code) setMunicipalCode(p.municipal_code);
    if (p.nbs_code) setNbsCode(p.nbs_code);
    if (p.description) setDescription(p.description);
    if (p.issqn_taxation || p.pis_cofins_cst) {
      setTributosState((prev) => ({
        ...prev,
        ...(p.issqn_taxation ? { issqnTaxation: p.issqn_taxation } : {}),
        ...(p.pis_cofins_cst ? { pisCofinsCst: p.pis_cofins_cst } : {}),
      }));
    }
    if (p.ibs_cbs) {
      const pi = p.ibs_cbs;
      setEnableIbsCbs(true);
      setIbs((prev) => ({
        operation_indicator: pi.operation_indicator || prev.operation_indicator,
        class_code: pi.class_code || prev.class_code,
        cst: pi.class_code ? (pi.cst || pi.class_code.slice(0, 3)) : prev.cst,
        personal_use: pi.personal_use ? pi.personal_use : prev.personal_use,
      }));
    }
    clearFieldErrorsByPrefix('service.national_code', 'service.municipal_code', 'service.nbs_code', 'service.description', 'tax_settings.ibs_cbs');
  };

  // Helper para preparar dados sanitizados garantindo preservação de zeros e valores decimais exatos.
  // `patch`: atualização parcial (PATCH) — campos/grupos removidos vão como null; na criação, são omitidos.
  const getPreparedPayload = (patch: boolean) => {
    // Preserva zeros à esquerda de cTribNac (6 dígitos) e cTribMun (exatamente 3 dígitos)
    const cleanNational = nationalCode && nationalCode.trim() ? nationalCode.trim().replace(/\D/g, '') : null;
    const cleanMunicipal = municipalCode && municipalCode.trim() ? municipalCode.trim().replace(/\D/g, '').padStart(3, '0') : null;
    const cleanLocation = locationCode && locationCode.trim() ? locationCode.trim().replace(/\D/g, '') : null;
    const cleanNbs = nbsCode && nbsCode.trim() ? nbsCode.trim().replace(/\D/g, '') : null;
    const cleanAmount = parseCurrencyPtBrToDecimal(amountInput);
    const cleanUnconditionalDiscount = unconditionalDiscountInput && unconditionalDiscountInput.trim() ? parseCurrencyPtBrToDecimal(unconditionalDiscountInput) : null;
    const cleanConditionalDiscount = conditionalDiscountInput && conditionalDiscountInput.trim() ? parseCurrencyPtBrToDecimal(conditionalDiscountInput) : null;
    const cleanDeductions = deductionsInput && deductionsInput.trim() ? parseCurrencyPtBrToDecimal(deductionsInput) : '0.00';

    const service: DraftService = {
      description,
      national_code: cleanNational,
      municipal_code: cleanMunicipal,
      location_code: cleanLocation,
      nbs_code: cleanNbs,
      ...obraToService(obra, patch),
    };
    const info = infoComplToPayload(infoCompl);
    if (info) service.additional_info = info;
    else if (patch) service.additional_info = null;

    const tax_settings: TaxSettings = {
      issqn_taxation: tributosState.issqnTaxation,
      iss_retention: tributosState.issRetention,
      iss_rate: tributosState.issRate && tributosState.issRate.trim() ? tributosState.issRate.trim() : null,
      iss_reference_rate: tributosState.issReferenceRate && tributosState.issReferenceRate.trim() ? tributosState.issReferenceRate.trim() : null,
      immunity_type: tributosState.issqnTaxation === 'IMMUNE' ? tributosState.immunityType : null,
      irrf_withheld: tributosState.irrfWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.irrfWithheld) : null,
      inss_withheld: tributosState.inssWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.inssWithheld) : null,
      csll_withheld: tributosState.csllWithheld.trim() ? parseCurrencyPtBrToDecimal(tributosState.csllWithheld) : null,
      pis_cofins_retention: tributosState.pisCofinsRetention !== '' ? (String(tributosState.pisCofinsRetention) as any) : null, // tpRetPisCofins é CÓDIGO ('0'..'9'), não número
      pis_cofins_cst: tributosState.pisCofinsCst || null,
      pis_cofins_base: tributosState.pisCofinsBase.trim() ? parseCurrencyPtBrToDecimal(tributosState.pisCofinsBase) : null,
      pis_rate: tributosState.pisRate && tributosState.pisRate.trim() ? tributosState.pisRate.trim() : null,
      cofins_rate: tributosState.cofinsRate && tributosState.cofinsRate.trim() ? tributosState.cofinsRate.trim() : null,
      pis_amount: tributosState.pisAmount.trim() ? parseCurrencyPtBrToDecimal(tributosState.pisAmount) : null,
      cofins_amount: tributosState.cofinsAmount.trim() ? parseCurrencyPtBrToDecimal(tributosState.cofinsAmount) : null,
    };

    if (approxMode !== 'NOT_INFORMED') {
      const approx: ApproxTax = { mode: approxMode };
      if (approxMode === 'VALUES') {
        if (approxFederal.trim()) approx.federal = parseCurrencyPtBrToDecimal(approxFederal);
        if (approxState.trim()) approx.state = parseCurrencyPtBrToDecimal(approxState);
        if (approxMunicipal.trim()) approx.municipal = parseCurrencyPtBrToDecimal(approxMunicipal);
      } else if (approxMode === 'PERCENT') {
        const pct = normalizarPercentual;
        if (approxFederal.trim()) approx.federal = pct(approxFederal);
        if (approxState.trim()) approx.state = pct(approxState);
        if (approxMunicipal.trim()) approx.municipal = pct(approxMunicipal);
      } else if (approxMode === 'SIMPLES_PERCENT') {
        if (approxSimples.trim()) approx.simples = approxSimples.trim();
      }
      tax_settings.approx_tax = approx;
    } else if (patch) {
      tax_settings.approx_tax = null;
    }

    if (enableIbsCbs) {
      tax_settings.ibs_cbs = applyGroups({
        operation_indicator: ibs.operation_indicator.replace(/\D/g, ''),
        cst: ibs.cst.replace(/\D/g, ''),
        class_code: ibs.class_code.replace(/\D/g, ''),
        personal_use: ibs.personal_use || null,
      }, ibsGroups);
    } else if (patch) {
      tax_settings.ibs_cbs = null;
    }

    return {
      service,
      amount: cleanAmount,
      unconditional_discount: cleanUnconditionalDiscount,
      conditional_discount: cleanConditionalDiscount,
      deductions: cleanDeductions,
      tax_settings,
    };
  };

  // Validação local e consistência das regras estritas
  const validateForm = (): Record<string, string[]> => {
    const errors: Record<string, string[]> = {};

    if (!selectedCustomerId) {
      errors['customer_id'] = ['Selecione o tomador do serviço.'];
    }
    if (!competence || !/^\d{4}-\d{2}$/.test(competence.trim())) {
      errors['competence'] = ['A competência é obrigatória no formato AAAA-MM (ex: 2026-10).'];
    }
    if (!serviceDate || !/^\d{4}-\d{2}-\d{2}$/.test(serviceDate.trim())) {
      errors['service_date'] = ['Informe a data fiscal do fato gerador no formato AAAA-MM-DD.'];
    }
    if (!description.trim()) {
      errors['service.description'] = ['A discriminação dos serviços é obrigatória.'];
    }
    const cleanAmount = parseCurrencyPtBrToDecimal(amountInput);
    if (parseFloat(cleanAmount) <= 0 || isNaN(parseFloat(cleanAmount))) {
      errors['amount'] = ['O valor do serviço deve ser um número positivo maior que zero.'];
    }

    // Regra estrita: cTribMun deve ter exatamente 3 dígitos se preenchido
    if (municipalCode && municipalCode.trim()) {
      const cleanMun = municipalCode.trim().replace(/\D/g, '');
      if (cleanMun.length !== 3) {
        errors['service.municipal_code'] = ['Código Municipal (cTribMun) deve ter exatamente 3 dígitos numéricos quando informado (ex: 001 ou 269).'];
      }
    }

    // cTribNac: exatamente 6 dígitos se preenchido
    if (nationalCode && nationalCode.trim()) {
      const cleanNac = nationalCode.trim().replace(/\D/g, '');
      if (cleanNac.length !== 6) {
        errors['service.national_code'] = ['Código Nacional (cTribNac) deve ter exatamente 6 dígitos numéricos.'];
      }
    }

    // location_code: exatamente 7 dígitos (IBGE) se informado
    if (locationCode && locationCode.trim()) {
      const cleanLoc = locationCode.trim().replace(/\D/g, '');
      if (cleanLoc.length !== 7) {
        errors['service.location_code'] = ['O município do local da prestação (IBGE) deve ter exatamente 7 dígitos numéricos.'];
      }
    }

    // nbs_code: exatamente 9 dígitos se informado
    if (nbsCode && nbsCode.trim()) {
      const cleanNbs = nbsCode.trim().replace(/\D/g, '');
      if (cleanNbs.length !== 9) {
        errors['service.nbs_code'] = ['Código NBS deve ter exatamente 9 dígitos numéricos.'];
      }
    }

    // Obra (escolha do XSD) e informações complementares
    for (const [k, v] of Object.entries({ ...validateObra(obra), ...validateInfoCompl(infoCompl) })) errors[k] = [v];

    // IBS/CBS
    if (enableIbsCbs) {
      if (!/^[0-9]{6}$/.test(ibs.operation_indicator.replace(/\D/g, ''))) {
        errors['tax_settings.ibs_cbs.operation_indicator'] = ['Indicador de operação deve ter 6 dígitos.'];
      }
      if (!/^[0-9]{3}$/.test(ibs.cst.replace(/\D/g, ''))) {
        errors['tax_settings.ibs_cbs.cst'] = ['CST IBS/CBS deve ter 3 dígitos.'];
      }
      if (!/^[0-9]{6}$/.test(ibs.class_code.replace(/\D/g, ''))) {
        errors['tax_settings.ibs_cbs.class_code'] = ['Código de classificação tributária deve ter 6 dígitos.'];
      }
      for (const [k, v] of Object.entries(validateGroups(ibsGroups))) errors[k] = [v];
    }

    return errors;
  };

  // Pré-validar Rascunho / DPS
  const handleValidateDraft = async () => {
    const localErrors = validateForm();
    if (Object.keys(localErrors).length > 0) {
      setFieldErrors(localErrors);
      showToast('Corrija os erros de validação antes da verificação.', 'error');
      return;
    }

    if (!currentCompany) return;
    const companyId = currentCompany.id;
    setIsValidating(true);
    setValidationIssues([]);

    try {
      if (activeDraft) {
        const res = await clients.fiscal.validateDraft(companyId, activeDraft.id);
        if (!isSameCompany(companyId)) return;
        setValidationIssues(res.issues);
        if (res.valid) {
          showToast('Rascunho validado com sucesso! Nenhuma pendência na DPS.', 'success');
        } else {
          showToast(`A validação da DPS apontou ${res.issues.length} pendências.`, 'warning');
        }
      } else {
        // Validação local bem-sucedida
        setValidationIssues([]);
        showToast('Campos locais validados com sucesso! Salve o rascunho para validar contra a DPS oficial.', 'info');
      }
    } catch (err: any) {
      if (isSameCompany(companyId)) showToast(err.message || 'Erro ao validar rascunho.', 'error');
    } finally {
      setIsValidating(false);
    }
  };

  // Avançar para Etapa 2 (Revisar)
  const handleProceedToReview = async () => {
    const errors = validateForm();
    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors);
      showToast('Corrija os campos obrigatórios destacados em vermelho.', 'error');
      return;
    }

    setFieldErrors({});
    setApiErrorsSummary([]);
    setReviewConfirmed(false);
    setProdConfirmed(false);
    setStep(2);
  };

  const handleApiError = (err: any, fallback: string, validationMsg: string) => {
    if (err instanceof ProblemDetailsException) {
      setFieldErrors(err.formatFieldErrors());
      setApiErrorsSummary(err.formatErrorsSummary());
      showToast(err.message || validationMsg, 'error');
    } else {
      showToast(err.message || fallback, 'error');
    }
  };

  // Salva (cria ou atualiza) o rascunho no servidor; devolve null se a empresa mudou durante o envio.
  const persistDraft = async (companyId: string): Promise<Draft | null> => {
    let saved: Draft;
    if (activeDraft) {
      const { service, amount, unconditional_discount, conditional_discount, deductions, tax_settings } = getPreparedPayload(true);
      saved = await clients.fiscal.updateDraft(companyId, activeDraft.id, {
        expected_version: activeDraft.version,
        customer_id: selectedCustomerId,
        competence,
        service_date: serviceDate,
        service_profile_id: serviceProfileId,
        service,
        amount,
        unconditional_discount,
        conditional_discount,
        deductions,
        tax_settings,
      });
    } else {
      const { service, amount, unconditional_discount, conditional_discount, deductions, tax_settings } = getPreparedPayload(false);
      saved = await clients.fiscal.createDraft(companyId, {
        customer_id: selectedCustomerId,
        competence,
        service_date: serviceDate,
        ...(serviceProfileId ? { service_profile_id: serviceProfileId } : {}),
        service,
        amount,
        ...(unconditional_discount ? { unconditional_discount } : {}),
        ...(conditional_discount ? { conditional_discount } : {}),
        deductions,
        tax_settings,
      });
    }
    return isSameCompany(companyId) ? saved : null;
  };

  // Configuração tributária da operação (mesma nos dois fluxos): salva o rascunho e registra/reutiliza a configuração.
  // Não aprova, não agenda e não emite; a versão do rascunho muda, então a revisão e as confirmações recomeçam.
  const [isOperating, setIsOperating] = useState(false);
  const registerOperation = async (): Promise<Draft | null> => {
    if (!currentCompany) return null;
    const companyId = currentCompany.id;
    const saved = await persistDraft(companyId);
    if (!saved) return null;
    const d = await clients.fiscal.registerOperationConfig(companyId, saved.id, saved.version);
    if (!isSameCompany(companyId)) return null;
    setActiveDraft(d);
    loadDraftIntoForm(d);
    setReviewConfirmed(false);
    setProdConfirmed(false);
    return d;
  };
  const handleRegisterOperation = async () => {
    setIsOperating(true);
    try {
      const d = await registerOperation();
      if (d) showToast('Configuração da operação registrada. Nada foi aprovado nem emitido.', 'success');
    } catch (err: any) {
      handleApiError(err, 'Erro ao registrar a configuração da operação.', 'Rascunho com dados inválidos.');
    } finally {
      setIsOperating(false);
    }
  };
  const handleApproveOperation = async (p: ServiceProfile) => {
    if (!currentCompany) return;
    setIsOperating(true);
    try {
      await clients.core.approveServiceProfile(currentCompany.id, p.id, p.version, `${competence}-01`,
        'Aprovada na revisão da Nova NFS-e');
      // A aprovação gera nova versão: o rascunho é vinculado de novo à versão aprovada.
      const d = await registerOperation();
      if (d) showToast('Configuração aprovada e vinculada ao rascunho. Revise e confirme antes de agendar.', 'success');
    } catch (err: any) {
      handleApiError(err, 'Erro ao aprovar a configuração.', 'A configuração ainda tem pendências.');
    } finally {
      setIsOperating(false);
    }
  };

  // Salvar Rascunho
  const handleSaveDraft = async () => {
    if (!currentCompany) return;
    const companyId = currentCompany.id;

    setIsSubmitting(true);
    setFieldErrors({});
    setApiErrorsSummary([]);

    try {
      const finalDraft = await persistDraft(companyId);
      if (!finalDraft) return; // empresa trocada durante o envio: resposta ignorada

      showToast(`Rascunho criado no servidor! ID: #${finalDraft.id.slice(0, 8)} (v${finalDraft.version}). Situação: Rascunho (Aguardando agendamento).`, 'success');
      await refreshCounters();
      savedRef.current = true;
      onNavigate('rascunhos');
    } catch (err: any) {
      if (!isSameCompany(companyId)) return;
      handleApiError(err, 'Erro ao salvar rascunho.', 'Erro de validação do formulário pelo backend.');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Emitir agora OU agendar para o dia 01 às 00:01 do próximo mês (as duas exigem a revisão e as confirmações)
  const handleEmit = async () => {
    const isProd = currentCompany?.environment === 'production';
    if (!currentCompany || !reviewConfirmed || (isProd && !prodConfirmed)) return;
    const companyId = currentCompany.id;
    const timezone = currentCompany.timezone;

    setIsSubmitting(true);
    setFieldErrors({});
    setApiErrorsSummary([]);

    try {
      const targetDraft = await persistDraft(companyId);
      if (!targetDraft) return;

      // Agendamento: dia 01 às 00:01 no fuso IANA da empresa. Emitir agora: o servidor usa o próprio relógio.
      const scheduleTarget = calculateNextMonthFirstMinute(new Date(), timezone);
      const idempotencyKey = `batch-${companyId}-${targetDraft.id}-${targetDraft.version}-${emitMode}`;

      await clients.fiscal.createBatch(
        companyId,
        {
          drafts: [{ id: targetDraft.id, expected_version: targetDraft.version }],
          mode: emitMode,
          ...(emitMode === 'SCHEDULED' ? { scheduled_at: scheduleTarget.utcIsoString } : {}),
          timezone,
          // Produção: confirmação vinculada à versão que acabou de ser gravada com o valor EXIBIDO na revisão; se o
          // servidor tiver gravado outro valor, recusa (PRODUCTION_CONFIRMATION_MISMATCH).
          ...(isProd ? {
            production_confirmation: montarConfirmacaoProducao(currentCompany.cnpj, [{
              id: targetDraft.id, version: targetDraft.version,
              amount: parseCurrencyPtBrToDecimal(amountInput), descricao: 'NFS-e',
            }], emitMode),
          } : {}),
        },
        idempotencyKey
      );
      if (!isSameCompany(companyId)) return;

      showToast(emitMode === 'NOW'
        ? 'Emissão iniciada: o processamento começou agora. Acompanhe o resultado em Agendamentos.'
        : `Lote agendado no servidor para ${scheduleTarget.displayFormatted} no fuso ${timezone}. Situação: Agendado na fila.`, 'success');
      await refreshCounters();
      savedRef.current = true;
      onNavigate('agendamentos');
    } catch (err: any) {
      if (!isSameCompany(companyId)) return;
      handleApiError(err, emitMode === 'NOW' ? 'Erro ao emitir a nota.' : 'Erro ao agendar nota.', 'Erro de validação do lote pelo backend.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const selectedCustomer = customers.find((c) => c.id === selectedCustomerId);
  const schedulePreview = currentCompany ? calculateNextMonthFirstMinute(new Date(), currentCompany.timezone) : null;
  // E0015: prestação posterior à emissão (hoje ou o dia 01 programado), no fuso da empresa.
  const hojeEmpresa = currentCompany ? diaNoFuso(new Date(), currentCompany.timezone) : '';
  const conflitoEmissao = currentCompany
    ? conflitoPrestacao([serviceDate], emitMode, hojeEmpresa, schedulePreview?.localInputString) : null;
  const agendamentoServe = !!schedulePreview
    && !conflitoPrestacao([serviceDate], 'SCHEDULED', hojeEmpresa, schedulePreview.localInputString);
  const envConfig = getEnvironmentBadgeConfig(integrationStatus?.environment, integrationStatus?.simulated !== false);
  const isRestricted = integrationStatus?.environment === 'restricted' || currentCompany?.environment === 'restricted';
  const reviewPayload = step === 2 ? getPreparedPayload(false) : null;

  return (
    <div className="pf-form" onChangeCapture={() => { touchedRef.current = true; if (!touched) setTouched(true); }}
      style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', maxWidth: '960px', margin: '0 auto', width: '100%', minWidth: 0 }}>
      {/* Header & Steps Indicator */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem', flexWrap: 'wrap', gap: '0.5rem' }}>
          <div>
            <h1 style={{ fontSize: '1.5rem', fontWeight: 700, margin: 0 }}>Nova NFS-e</h1>
            <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
              Emissão de NFS-e padrão nacional com validação rigorosa de contrato
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <Badge status={envConfig.status} customLabel={envConfig.label} />
            <span style={{ fontSize: '0.8125rem', color: 'var(--color-text-muted)' }}>
              Etapa {step} de 2
            </span>
          </div>
        </div>

        {/* Wizard Tabs */}
        <div className="pf-tabs" role="tablist" aria-label="Etapas da emissão">
          <button type="button" role="tab" className="pf-tab" aria-selected={step === 1} onClick={() => setStep(1)}>
            1. Preencher Dados e Parâmetros
          </button>
          <button type="button" role="tab" className="pf-tab" aria-selected={step === 2} onClick={handleProceedToReview}>
            2. Revisão Fiscal e Agendamento
          </button>
        </div>
      </div>

      {/* Alerta de Produção Restrita / Homologação (SEM VALIDADE FISCAL) */}
      {isRestricted && (
        <Alert variant="warning" title="HOMOLOGAÇÃO / PRODUÇÃO RESTRITA: NFS-e SEM VALIDADE FISCAL">
          <div style={{ fontSize: '0.8125rem' }}>
            A empresa ativa está conectada ao ambiente de <strong>Produção Restrita</strong>. Notas emitidas e autorizadas neste ambiente
            possuem caráter exclusivamente de teste tecnológico e <strong>não possuem validade jurídica ou fiscal</strong>.
          </div>
        </Alert>
      )}

      {/* Banner de Ambiente e Prontidão do Provedor Oficial */}
      {integrationStatus && (
        <Alert
          variant={integrationStatus.status === 'READY' || integrationStatus.status === 'SIMULATED' ? 'info' : 'warning'}
          title={`Ambiente Fiscal Ativo: ${envConfig.label} • Provedor: ${integrationStatus.provider === 'official' ? 'Oficial (Sefin/ADN)' : 'Simulado (Mock)'}`}
        >
          <div style={{ fontSize: '0.8125rem', display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            <div>
              Status de Conectividade: <strong>{integrationStatus.status}</strong> • Emissão:{' '}
              <strong>{integrationStatus.capabilities?.emission ? 'Pronta' : 'Bloqueada'}</strong> • Distribuição:{' '}
              <strong>{integrationStatus.capabilities?.distribution ? 'Pronta' : 'Bloqueada'}</strong>
            </div>
            {/* Funcionalidade habilitada na instalação ≠ empresa pronta (certificado, cadastro, perfil aprovado). */}
            {integrationStatus.policy && (
              <div>
                Funcionalidade de emissão neste ambiente:{' '}
                <strong>{integrationStatus.policy.emission ? 'habilitada na instalação' : 'desligada na instalação'}</strong>
                {integrationStatus.policy.emission && !integrationStatus.capabilities?.emission && ' — a empresa ainda tem pendências (veja abaixo).'}
                {integrationStatus.policy.emission && integrationStatus.capabilities?.emission && currentCompany?.environment === 'production'
                  && ' — cada nota ainda exige perfil de serviço aprovado e sua confirmação individual.'}
              </div>
            )}
            {integrationStatus.blockers && integrationStatus.blockers.length > 0 && (
              <div style={{ marginTop: '0.25rem' }}>
                <strong>Bloqueios e Capacidades Reais ({integrationStatus.blockers.length}):</strong>
                <ul style={{ margin: '0.25rem 0 0 1rem', padding: 0 }}>
                  {integrationStatus.blockers.map((b, idx) => (
                    <li key={idx}>
                      <code>{b.code}</code>: {b.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Alert>
      )}

      {/* Alerta de Inconsistências Apontadas pelo Backend */}
      {apiErrorsSummary.length > 0 && (
        <Alert variant="error" title="Inconsistências de Validação Retornadas pelo Backend:">
          <ul style={{ margin: '0.5rem 0 0 1rem', padding: 0, fontSize: '0.8125rem' }}>
            {apiErrorsSummary.map((msg, idx) => (
              <li key={idx}>{msg}</li>
            ))}
          </ul>
        </Alert>
      )}

      {/* Exibição de Issues da Pré-validação */}
      {validationIssues.length > 0 && (
        <div className="pf-subpanel">
          <PendenciasRascunho
            issues={validationIssues}
            companyId={currentCompany?.id}
            validatedVersion={activeDraft?.version ?? null}
            currentVersion={activeDraft?.version ?? null}
          />
        </div>
      )}

      {/* STEP 1: PREENCHER */}
      {step === 1 && (
        <div className="pf-form-stack" style={{ gap: '1.25rem' }}>
          {/* Tomador */}
          <ScreenSection
            title="1. Tomador do Serviço *"
            description="Selecione o cliente tomador cadastrado ou consulte a última nota emitida para referência"
            actions={(
              <Button variant="outline" isLoading={isLoadingLastDoc} onClick={handleUseLastDocument}>
                Usar Última Nota
              </Button>
            )}
          >
            <div className="pf-form-grid">
              <div className="pf-span-all">
                <Select
                  label="Cliente (Tomador) *"
                  options={[
                    ...(customers.length ? [] : [{ value: '', label: 'Nenhum cliente cadastrado' }]),
                    ...customers.map((c) => ({
                      value: c.id,
                      label: `${c.name} (${c.document || c.cnpj_cpf || ''})`,
                    })),
                  ]}
                  value={selectedCustomerId}
                  error={errorText['customer_id']}
                  helperText="Os dados do tomador vêm do cadastro de Clientes."
                  onChange={(e) => {
                    setSelectedCustomerId(e.target.value);
                    clearFieldError('customer_id');
                    setLastDocReference(null);
                    setLastDocNotFound(false);
                  }}
                />
              </div>
            </div>

            {lastDocReference && activeDraft && (
              <Alert
                variant="success"
                title={`Origem: NFS-e nº ${lastDocReference.number} de ${lastDocReference.issued_at ? new Date(lastDocReference.issued_at).toLocaleDateString('pt-BR') : 'Data não informada'} (Valor: ${formatCurrencyPtBr(lastDocReference.amount)})`}
              >
                <div>
                  <strong>Rascunho criado no servidor via POST from-document:</strong> ID <code>{activeDraft.id}</code> (versão {activeDraft.version}).
                  <br />
                  Nova identidade garantida. Nenhuma chave de acesso, protocolo, número ou aprovação anterior foi herdada.
                </div>
              </Alert>
            )}

            {lastDocNotFound && (
              <Alert variant="warning" title="Nenhuma nota anterior autorizada encontrada">
                Não consta emissão anterior para este cliente no ambiente. Preencha os campos abaixo.
              </Alert>
            )}
          </ScreenSection>

          {/* Competência e Data Fiscal */}
          <ScreenSection
            title="2. Competência e Data Fiscal *"
            description="Diferenciação obrigatória entre o mês de incidência fiscal e a data do fato gerador"
          >
            <div className="pf-form-grid">
              <Input
                label="Competência Fiscal (AAAA-MM) *"
                value={competence}
                error={errorText['competence']}
                onChange={(e) => {
                  setCompetence(e.target.value);
                  clearFieldError('competence');
                }}
                placeholder="AAAA-MM"
                helperText="Mês e ano da prestação do serviço (período de apuração)"
              />

              <Input
                label="Data Fiscal / Fato Gerador (AAAA-MM-DD) *"
                type="date"
                value={serviceDate}
                error={errorText['service_date']}
                onChange={(e) => {
                  setServiceDate(e.target.value);
                  clearFieldError('service_date');
                }}
                helperText="Data efetiva da realização do serviço (dCompet)"
              />

              <div className="pf-tile" data-variant="subtle">
                <div className="pf-tile-label">Horário do agendamento</div>
                <div className="pf-tile-value">
                  {schedulePreview ? schedulePreview.displayFormatted : '01 do mês às 00:01'}
                </div>
                <div className="pf-tile-hint">
                  Fuso <code>{currentCompany?.timezone || 'America/Sao_Paulo'}</code> (instante independente da competência).
                </div>
              </div>
            </div>
          </ScreenSection>

          {/* Classificação, Discriminação, Obra e Informações complementares */}
          <ScreenSection
            title="3. Discriminação do Serviço e Classificação Nacional"
            description="Preservação de zeros dos códigos cTribNac e cTribMun (exatos 3 dígitos) sem truncamento"
          >
            <ServiceProfilePicker onApply={applyServiceProfile} selectedId={serviceProfileId}
              onClear={() => { touchedRef.current = true; setServiceProfileId(null); }} />

            <div className="pf-form-grid">
              <Input
                label="Código Tributação Nacional (cTribNac — 6 dígitos)"
                value={nationalCode}
                inputMode="numeric"
                maxLength={6}
                error={(fieldErrors['service.national_code'] || fieldErrors['national_code'])?.join(', ')}
                onChange={(e) => {
                  setNationalCode(e.target.value);
                  clearFieldError('service.national_code');
                  clearFieldError('national_code');
                }}
                placeholder="6 dígitos"
                helperText="Exatamente 6 dígitos numéricos. Zeros preservados."
              />

              <Input
                label="Código Tributação Municipal (cTribMun — Exatos 3 dígitos)"
                value={municipalCode}
                inputMode="numeric"
                maxLength={3}
                error={(fieldErrors['service.municipal_code'] || fieldErrors['municipal_code'])?.join(', ')}
                onChange={(e) => {
                  setMunicipalCode(e.target.value);
                  clearFieldError('service.municipal_code');
                  clearFieldError('municipal_code');
                }}
                placeholder="3 dígitos"
                helperText="Opcional. Exatamente 3 dígitos quando informado (ex.: 001)."
              />

              <Input
                label="Local da prestação — município (7 dígitos IBGE)"
                value={locationCode}
                inputMode="numeric"
                maxLength={7}
                error={errorText['service.location_code']}
                onChange={(e) => {
                  setLocationCode(e.target.value);
                  clearFieldError('service.location_code');
                }}
                placeholder="7 dígitos"
                helperText="Código IBGE do município onde o serviço foi prestado."
              />

              <Input
                label="Código NBS (9 dígitos — Nomenclatura Brasileira de Serviços)"
                value={nbsCode}
                inputMode="numeric"
                maxLength={9}
                error={errorText['service.nbs_code']}
                onChange={(e) => {
                  setNbsCode(e.target.value);
                  clearFieldError('service.nbs_code');
                }}
                placeholder="9 dígitos"
                helperText="Opcional. 9 dígitos numéricos conforme tabela NBS"
              />

              <div className="pf-span-all">
                <Textarea
                  label="Discriminação dos Serviços Prestados *"
                  rows={4}
                  value={description}
                  maxLength={2000}
                  error={(fieldErrors['service.description'] || fieldErrors['description'])?.join(', ')}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    clearFieldError('service.description');
                    clearFieldError('description');
                  }}
                  placeholder="Descreva detalhadamente as atividades realizadas, escopo, referências contratuais..."
                  helperText={`Obrigatório. Transmitido com integridade na DPS (${description.length}/2000 caracteres).`}
                />
              </div>
            </div>

            <ObraFields
              value={obra}
              nationalCode={nationalCode}
              errors={errorText}
              onChange={(v) => { setObra(v); clearFieldErrorsByPrefix('service.obra_code', 'service.obra_cib', 'service.obra_address', 'service.obra_property_registration'); clearFieldError('service'); }}
            />
            {!shouldShowObra(nationalCode, obra) && nationalCode.replace(/\D/g, '').length === 6 && (
              <p className="pf-help">Grupo obra não se aplica a este código de serviço (exigido só nos subitens de construção civil — E0370).</p>
            )}

            <InfoComplFields
              value={infoCompl}
              errors={errorText}
              onChange={(v) => { setInfoCompl(v); clearFieldErrorsByPrefix('service.additional_info'); }}
            />
          </ScreenSection>

          {/* Valores e Descontos */}
          <ScreenSection
            title="4. Valores e Descontos"
            description="Valores monetários brutos e deduções com decimais exatos. Reduções incondicionadas impactam a base de cálculo."
          >
            <div className="pf-form-grid">
              <Input
                label="Valor do Serviço (R$ pt-BR) *"
                value={amountInput}
                inputMode="decimal"
                className="pf-money"
                error={errorText['amount']}
                onChange={(e) => {
                  setAmountInput(e.target.value);
                  clearFieldError('amount');
                }}
                placeholder="0,00"
                helperText={amountInput.trim() ? `Enviado como decimal exato: ${parseCurrencyPtBrToDecimal(amountInput)}` : 'Valor bruto do serviço (vServ).'}
              />

              <Input
                label="Desconto Incondicionado (R$)"
                value={unconditionalDiscountInput}
                inputMode="decimal"
                className="pf-money"
                error={errorText['unconditional_discount']}
                onChange={(e) => {
                  setUnconditionalDiscountInput(e.target.value);
                  clearFieldError('unconditional_discount');
                }}
                placeholder="0,00"
                helperText="Opcional. Reduz a base de cálculo do ISS e o valor líquido."
              />

              <Input
                label="Desconto Condicionado (R$)"
                value={conditionalDiscountInput}
                inputMode="decimal"
                className="pf-money"
                error={errorText['conditional_discount']}
                onChange={(e) => {
                  setConditionalDiscountInput(e.target.value);
                  clearFieldError('conditional_discount');
                }}
                placeholder="0,00"
                helperText="Opcional. Subtrai do líquido sem reduzir a base."
              />

              <Input
                label="Deduções Legais (R$)"
                value={deductionsInput}
                inputMode="decimal"
                className="pf-money"
                error={errorText['deductions']}
                onChange={(e) => {
                  setDeductionsInput(e.target.value);
                  clearFieldError('deductions');
                }}
                placeholder="0,00"
                helperText="Opcional. Deduções previstas em lei sobre a base."
              />
            </div>
          </ScreenSection>

          {/* Tributos e Retenções */}
          <ScreenSection
            title="5. Tributos e Retenções"
            description="ISSQN municipal, retenções federais (IRRF, CP/INSS, CSLL/PCC) e apuração própria PIS/COFINS"
          >
            <TributosRetencoesForm
              values={tributosState}
              onChange={handleTributoChange}
              errors={fieldErrors}
              isCardWrapper={false}
              aliquotaIss={detalhesAliquotaIss(validationIssues)}
            />
          </ScreenSection>

          {/* Resumo Canônico de Retenções e Valor Líquido */}
          <CanonicalRetentionSummaryCard
            source={retentionSummaryCanon.source}
            diverged={retentionSummaryCanon.diverged}
            summary={retentionSummary}
            title="6. Resumo Canônico de Retenções e Valor Líquido"
            subtitle="Demonstrativo com apuração discriminada, regra de não-duplicação PCC e cálculo oficial"
          />

          {/* Tributos Aproximados (totTrib) */}
          <ScreenSection
            title="7. Tributos Aproximados (totTrib / Lei da Transparência)"
            description="Atendimento à Lei nº 12.741/2012 na emissão nacional"
          >
            <div className="pf-form-grid">
              <Select
                label="Modalidade de Apuração"
                options={[
                  { value: 'NOT_INFORMED', label: 'Não informado na DPS' },
                  { value: 'VALUES', label: 'Valores em moeda (R$)' },
                  { value: 'PERCENT', label: 'Percentuais (%)' },
                  { value: 'SIMPLES_PERCENT', label: 'Percentual do Simples Nacional (%)' },
                ]}
                value={approxMode}
                onChange={(e) => {
                  const novo = e.target.value as typeof approxMode;
                  const aviso = trocaDeUnidade(approxMode, novo, [approxFederal, approxState, approxMunicipal]);
                  if (aviso) { setApproxFederal(''); setApproxState(''); setApproxMunicipal(''); }
                  setApproxAviso(aviso);
                  setApproxMode(novo);
                }}
                helperText="Escolha como os tributos aproximados constam na DPS."
              />
              {approxAviso && (
                <div role="status" style={{ gridColumn: '1 / -1' }}>
                  <Alert variant="warning" title="Preencha os tributos aproximados de novo">{approxAviso}</Alert>
                </div>
              )}

              {approxMode === 'VALUES' && (
                <>
                  <Input label="Tributos Federais (R$)" value={approxFederal} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxFederal(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                  <Input label="Tributos Estaduais (R$)" value={approxState} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxState(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                  <Input label="Tributos Municipais (R$)" value={approxMunicipal} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxMunicipal(e.target.value)} placeholder="0,00" helperText="Valor estimado." />
                </>
              )}

              {approxMode === 'PERCENT' && (
                <>
                  {/* pTotTrib exige os três percentuais (pTotTribFed, pTotTribEst, pTotTribMun). */}
                  <Input label="Tributos Federais (%)" value={approxFederal} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxFederal(e.target.value)} placeholder="0.00" helperText="pTotTribFed" />
                  <Input label="Tributos Estaduais (%)" value={approxState} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxState(e.target.value)} placeholder="0.00" helperText="pTotTribEst" />
                  <Input label="Tributos Municipais (%)" value={approxMunicipal} inputMode="decimal" className="pf-money"
                    onChange={(e) => setApproxMunicipal(e.target.value)} placeholder="0.00" helperText="pTotTribMun" />
                </>
              )}

              {approxMode === 'SIMPLES_PERCENT' && (
                <Input label="Alíquota Simples Nacional (%)" value={approxSimples} inputMode="decimal" className="pf-money"
                  onChange={(e) => setApproxSimples(e.target.value)} placeholder="0.00" helperText="Percentual do Simples Nacional." />
              )}
            </div>
          </ScreenSection>

          {/* Reforma Tributária IBS/CBS */}
          <ScreenSection
            title="8. Reforma Tributária: IBS e CBS (DPS v1.01 — A partir de 10/2026)"
            description="Grupo IBSCBS da DPS. Códigos definidos pela contabilidade; nada é presumido."
          >
            <label className="pf-check" htmlFor="enable_ibs_cbs">
              <input
                type="checkbox"
                id="enable_ibs_cbs"
                checked={enableIbsCbs}
                onChange={(e) => setEnableIbsCbs(e.target.checked)}
              />
              <span>Habilitar campos de IBS/CBS para esta emissão (DPS v1.01)</span>
            </label>

            {enableIbsCbs && (
              <div className="pf-subpanel" data-tone="subtle">
                <p className="pf-help">
                  Campos de transição da Reforma Tributária conforme a especificação técnica nacional. Os grupos
                  condicionais aparecem conforme a classificação e a operação escolhidas.
                </p>
                <IbsCbsFields serviceDate={serviceDate} nationalCode={nationalCode}
                  value={ibs}
                  errors={errorText}
                  onChange={(v) => {
                    setIbs(v);
                    clearFieldError('tax_settings.ibs_cbs.operation_indicator'); clearFieldError('tax_settings.ibs_cbs.class_code'); clearFieldError('tax_settings.ibs_cbs.cst');
                  }}
                  groups={ibsGroups}
                  onGroupsChange={(g) => {
                    setIbsGroups(g);
                    clearFieldErrorsByPrefix('tax_settings.ibs_cbs.regular', 'tax_settings.ibs_cbs.deferral', 'tax_settings.ibs_cbs.real_estate', 'tax_settings.ibs_cbs.recipient', 'tax_settings.ibs_cbs.presumed_credit');
                  }} />
              </div>
            )}
          </ScreenSection>

          {/* Action Bar: secundária à esquerda, uma ação primária */}
          <div className="pf-actions-row">
            <Button variant="outline" isLoading={isValidating} onClick={handleValidateDraft}>
              Pré-validar DPS
            </Button>

            <div className="pf-actions-end">
              <Button variant="ghost" onClick={() => onNavigate('dashboard')}>
                Cancelar
              </Button>
              <Button variant="primary" onClick={handleProceedToReview}>
                Revisar Dados da NFS-e ➔
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* STEP 2: REVISAR */}
      {step === 2 && reviewPayload && (
        <div className="pf-form-stack" style={{ gap: '1.25rem' }}>
          <ScreenSection
            title="Revisão dos Dados da NFS-e e Confirmação"
            description="Confira cada grupo antes de salvar o rascunho ou agendar a transmissão"
          >
            <RevisaoNfse
              prestador={{ name: currentCompany?.name, cnpj: currentCompany?.cnpj }}
              tomador={{ name: selectedCustomer?.name, document: selectedCustomer?.document || selectedCustomer?.cnpj_cpf }}
              competence={competence}
              serviceDate={serviceDate}
              service={reviewPayload.service}
              amount={reviewPayload.amount}
              unconditionalDiscount={reviewPayload.unconditional_discount}
              conditionalDiscount={reviewPayload.conditional_discount}
              deductions={reviewPayload.deductions}
              taxSettings={reviewPayload.tax_settings}
              retention={retentionSummary}
              aliquotaIss={detalhesAliquotaIss(validationIssues)}
            />

            <p className="pf-help">
              Agendamento: {schedulePreview ? schedulePreview.displayFormatted : '01 às 00:01'} no fuso {currentCompany?.timezone}.
            </p>
          </ScreenSection>

          {/* Demonstrativo Fiscal Canônico */}
          <CanonicalRetentionSummaryCard
            source={retentionSummaryCanon.source}
            diverged={retentionSummaryCanon.diverged}
            summary={retentionSummary}
            title="Resumo Canônico de Retenções e Valor Líquido Final"
            subtitle="Apuração discriminada dos tributos e retenções conforme regras da Receita Federal e Prefeitura"
          />

          <Alert variant="info" title="Aviso sobre o Ciclo de Vida Fiscal">
            Salvar gera ou atualiza um <strong>Rascunho</strong> no servidor e nunca transmite. A transmissão só acontece depois da sua confirmação: em <strong>Emitir agora</strong> o processamento começa imediatamente; em <strong>Agendar emissão</strong>, na data agendada.
          </Alert>

          <OperacaoTributaria draft={activeDraft} busy={isOperating}
            onRegister={handleRegisterOperation} onApprove={handleApproveOperation} />
          <fieldset className="pf-subpanel" aria-label="Como emitir">
            <legend className="pf-subtitle">Como emitir</legend>
            <label className="pf-confirm">
              <input type="radio" name="emit-mode" checked={emitMode === 'NOW'}
                onChange={() => { setEmitMode('NOW'); setProdConfirmed(false); }} />
              <span><strong>Emitir agora</strong> — o processamento começa imediatamente após a confirmação.</span>
            </label>
            <label className="pf-confirm">
              <input type="radio" name="emit-mode" checked={emitMode === 'SCHEDULED'}
                onChange={() => { setEmitMode('SCHEDULED'); setProdConfirmed(false); }} />
              <span><strong>Agendar emissão</strong> para {schedulePreview ? schedulePreview.displayFormatted : '01 às 00:01'} ({currentCompany?.timezone}).</span>
            </label>
            {conflitoEmissao && (
              <div style={{ marginTop: '0.5rem' }}>
                <Alert variant="error" title="Prestação posterior à emissão">
                  <p>{conflitoEmissao.mensagem}</p>
                  {emitMode === 'NOW' && agendamentoServe ? (
                    <Button variant="outline" size="sm" style={{ marginTop: '0.5rem' }}
                      onClick={() => { setEmitMode('SCHEDULED'); setProdConfirmed(false); }}>
                      Agendar para {schedulePreview?.displayFormatted}
                    </Button>
                  ) : (
                    <p style={{ marginTop: '0.5rem' }}>Salve como rascunho e agende em Rascunhos → “Emitir ou agendar”, escolhendo a data.</p>
                  )}
                </Alert>
              </div>
            )}
          </fieldset>
          <ConfirmacaoRevisao checked={reviewConfirmed} onChange={setReviewConfirmed} />
          {currentCompany?.environment === 'production' && (
            <ConfirmacaoProducao empresa={{ name: currentCompany.name, cnpj: currentCompany.cnpj }}
              docs={[{ id: activeDraft?.id || 'nova', version: activeDraft?.version || 1,
                       amount: parseCurrencyPtBrToDecimal(amountInput) || '0.00',
                       descricao: selectedCustomer?.name || 'Tomador' }]}
              checked={prodConfirmed} onChange={setProdConfirmed} versaoAoGravar
              modo={emitMode} quando={schedulePreview?.displayFormatted} />
          )}

          <div className="pf-actions-row">
            <Button variant="outline" onClick={() => setStep(1)}>
              ← Voltar e Editar
            </Button>
            <div className="pf-actions-end">
              <Button variant="secondary" isLoading={isSubmitting} onClick={handleSaveDraft}>
                Salvar como Rascunho
              </Button>
              <Button variant="primary" isLoading={isSubmitting}
                disabled={!reviewConfirmed || (currentCompany?.environment === 'production' && !prodConfirmed) || !!conflitoEmissao}
                onClick={handleEmit}
                title={conflitoEmissao ? 'Ajuste a data: a prestação é posterior à emissão'
                  : !reviewConfirmed ? 'Marque “Conferi os dados acima” para continuar'
                  : currentCompany?.environment === 'production' && !prodConfirmed ? 'Confirme a emissão em produção' : undefined}>
                {emitMode === 'NOW' ? 'Emitir agora' : 'Agendar emissão (01 às 00:01)'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
