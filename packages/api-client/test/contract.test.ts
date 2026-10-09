import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MockFiscalApiClient,
  MockCoreApiClient,
  parseCurrencyPtBrToDecimal,
  formatCurrencyPtBr,
  calculateNextMonthFirstMinute,
  formatScheduledDisplay,
  parseLocalScheduleToUtcIso,
  getEnvironmentBadgeConfig,
  getDocumentEnvironmentBadgeConfig,
  getFiscalAuthorizationBadgeConfig,
  getReviewStatusBadgeConfig,
  getBatchStatusBadgeConfig,
  getBatchItemStatusBadgeConfig,
  validateServiceCodes,
  formatFieldErrors,
  ProblemDetailsException,
} from '../src/index.js';

test('1. Fluxo Usar Última Nota: Produz nova identidade e DRAFT sem copiar IDs fiscais', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  // Obter última nota do cliente Tech Solutions
  const lastDoc = await fiscalApi.getLastIssuedDocument(companyId, 'cust-tech-001');
  assert.equal(lastDoc.id, 'doc-emit-001');
  assert.equal(lastDoc.number, '202600000000101');
  assert.ok(lastDoc.access_key);

  // Criar rascunho por referência
  const newDraft = await fiscalApi.createDraftFromDocument(companyId, {
    source_document_id: lastDoc.id,
  });

  // Validações do contrato estrito:
  assert.notEqual(newDraft.id, lastDoc.id, 'Novo rascunho deve ter nova identidade UUID');
  assert.equal(newDraft.status, 'DRAFT', 'Novo documento deve ter status DRAFT');
  assert.equal(newDraft.version, 1, 'Versão inicial deve ser 1');
  assert.equal(newDraft.source_document_id, lastDoc.id, 'Deve registrar source_document_id');

  // NUNCA pode ter herdado número de nota, chave ou protocolo do documento original
  assert.equal((newDraft as any).number, undefined, 'Rascunho não pode conter número de nota');
  assert.equal((newDraft as any).access_key, undefined, 'Rascunho não pode conter chave de acesso');
  assert.equal((newDraft as any).protocol, undefined, 'Rascunho não pode conter protocolo');

  // Valores e serviços foram copiados para referência editável
  assert.equal(newDraft.amount, '12500.00');
  assert.equal(newDraft.service.description, lastDoc.description);
});

test('2. Fluxo Usar Última Nota: Retorna 404 canônico para cliente sem histórico anterior', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  await assert.rejects(
    async () => {
      await fiscalApi.getLastIssuedDocument(companyId, 'cust-prisma-002');
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 404);
      assert.equal(err.problem.type, 'urn:problem:not-found');
      return true;
    }
  );
});

test('3. Conversão de Moeda pt-BR: Não perde centavos e produz string decimal exata', () => {
  // Testes de conversão de input pt-BR -> string decimal da API
  assert.equal(parseCurrencyPtBrToDecimal('1.000,50'), '1000.50');
  assert.equal(parseCurrencyPtBrToDecimal('12.500,00'), '12500.00');
  assert.equal(parseCurrencyPtBrToDecimal('0,01'), '0.01');
  assert.equal(parseCurrencyPtBrToDecimal('999,99'), '999.99');
  assert.equal(parseCurrencyPtBrToDecimal('1500'), '1500.00');
  assert.equal(parseCurrencyPtBrToDecimal('R$ 4.800,25'), '4800.25');

  // Testes de formatação da API -> pt-BR
  assert.equal(formatCurrencyPtBr('12500.00', false), '12.500,00');
  assert.equal(formatCurrencyPtBr('0.01', true), 'R$ 0,01');
  assert.equal(formatCurrencyPtBr(null, true), 'R$ 0,00');
});

test('4. Agendamento 01 às 00:01: Trata corretamente fusos IANA (SP e Manaus), virada de ano e Fevereiro', () => {
  // Cenário 1: Data em 28 de Fevereiro de 2026 -> Próximo mês é Março
  const febDate = new Date(2026, 1, 28, 17, 30); // 28 de Fevereiro de 2026
  
  // America/Sao_Paulo (UTC-3): 00:01 local vira 03:01 UTC
  const febResultSP = calculateNextMonthFirstMinute(febDate, 'America/Sao_Paulo');
  assert.equal(febResultSP.utcIsoString, '2026-03-01T03:01:00.000Z');
  assert.equal(febResultSP.displayFormatted, '01/03/2026 às 00:01');
  assert.equal(formatScheduledDisplay(febResultSP.utcIsoString, 'America/Sao_Paulo'), '01/03/2026, 00:01');

  // America/Manaus (UTC-4): 00:01 local vira 04:01 UTC
  const febResultManaus = calculateNextMonthFirstMinute(febDate, 'America/Manaus');
  assert.equal(febResultManaus.utcIsoString, '2026-03-01T04:01:00.000Z');
  assert.equal(febResultManaus.displayFormatted, '01/03/2026 às 00:01');
  assert.equal(formatScheduledDisplay(febResultManaus.utcIsoString, 'America/Manaus'), '01/03/2026, 00:01');

  // Cenário 2: Data em 31 de Dezembro de 2026 -> Virada de ano para 01 de Janeiro de 2027
  const decDate = new Date(2026, 11, 31, 23, 45); // 31 de Dezembro de 2026
  const decResultSP = calculateNextMonthFirstMinute(decDate, 'America/Sao_Paulo');
  assert.equal(decResultSP.utcIsoString, '2027-01-01T03:01:00.000Z');
  assert.equal(decResultSP.displayFormatted, '01/01/2027 às 00:01');

  const decResultManaus = calculateNextMonthFirstMinute(decDate, 'America/Manaus');
  assert.equal(decResultManaus.utcIsoString, '2027-01-01T04:01:00.000Z');
  assert.equal(decResultManaus.displayFormatted, '01/01/2027 às 00:01');

  // Cenário 3: Mês de 30 dias (Abril -> Maio) com parse de input datetime-local
  const aprInput = '2026-05-01T00:01';
  const aprUtcSP = parseLocalScheduleToUtcIso(aprInput, 'America/Sao_Paulo');
  const aprUtcManaus = parseLocalScheduleToUtcIso(aprInput, 'America/Manaus');
  assert.equal(aprUtcSP, '2026-05-01T03:01:00.000Z');
  assert.equal(aprUtcManaus, '2026-05-01T04:01:00.000Z');
});

test('5. Concorrência e Conflito de Versão (expected_version): Retorna 409', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  // Teste no Rascunho: rascunho tem version=1, tentativa de atualizar com expected_version=99
  await assert.rejects(
    async () => {
      await fiscalApi.updateDraft(companyId, 'draft-001-uuid', {
        amount: '13000.00',
        expected_version: 99, // Desatualizado
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 409);
      assert.equal(err.problem.type, 'urn:problem:version-conflict');
      return true;
    }
  );

  // Teste na Conferência de Documento Recebido: doc-rec-101 tem version=1, enviar expected_version=5
  await assert.rejects(
    async () => {
      await fiscalApi.reviewDocument(companyId, 'doc-rec-101', {
        review_status: 'REVIEWED',
        note: 'Nota conferida.',
        expected_version: 5,
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 409);
      return true;
    }
  );
});

test('6. Retenções de Notas Recebidas: Trata nulos e PCC consolidado sem duplicidade', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  const doc = await fiscalApi.getDocument(companyId, 'doc-rec-101');
  assert.equal(doc.direction, 'RECEIVED');

  // Retenção ausente deve ser null explícito
  assert.equal(doc.retentions.iss, null);
  assert.equal(doc.retentions.inss, null);

  // Retenções presentes
  assert.equal(doc.retentions.irrf, '48.00');
  assert.equal(doc.retentions.pis, '20.80');
  assert.equal(doc.retentions.cofins, '96.00');
  assert.equal(doc.retentions.csll, '32.00');
  assert.equal(doc.retentions.pcc_total, '148.80'); // 20.80 + 96.00 + 32.00 = 148.80
});

test('7. Agendamento de Lote e Retirada de Pendentes (cancel-pending)', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  const batch = await fiscalApi.getBatch(companyId, 'batch-001-uuid');
  assert.equal(batch.status, 'SCHEDULED');
  assert.equal(batch.items[0].status, 'SCHEDULED');

  // Retirar pendente
  const cancelResult = await fiscalApi.cancelBatchPending(companyId, batch.id);
  assert.equal(cancelResult.cancelled_count, 1);

  // Verificar status atualizado para WITHDRAWN
  const updatedBatch = await fiscalApi.getBatch(companyId, batch.id);
  assert.equal(updatedBatch.items[0].status, 'WITHDRAWN');
  assert.equal(updatedBatch.status, 'WITHDRAWN');
});

test('8. Falha de API Real: Nunca faz fallback automático para mocks em erro HTTP', async () => {
  // Simula cliente HTTP real apontando para endpoint indisponível
  const { HttpFiscalApiClient } = await import('../src/client.js');
  
  // Custom fetch mock que simula indisponibilidade de gateway (503 Service Unavailable)
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    return new Response(
      JSON.stringify({
        type: 'https://ctrl-fiscal.local/problems/service-unavailable',
        title: 'Serviço Indisponível',
        status: 503,
        detail: 'Backend fiscal temporariamente fora do ar.',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/problem+json' },
      }
    );
  };

  try {
    const realHttpApi = new HttpFiscalApiClient('/api/fiscal/v1');
    await assert.rejects(
      async () => {
        await realHttpApi.getDashboard('cmp-alpha-1111-uuid');
      },
      (err: unknown) => {
        assert.ok(err instanceof ProblemDetailsException, 'Erro deve ser ProblemDetailsException, não dados demo');
        assert.equal(err.problem.status, 503);
        assert.equal(err.problem.title, 'Serviço Indisponível');
        return true;
      }
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('9. Troca de Empresa e Isolamento: Empresa Beta não enxerga dados da Alpha', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const alphaId = 'cmp-alpha-1111-uuid';
  const betaId = 'cmp-beta-2222-uuid';

  const alphaDocs = await fiscalApi.getDocuments(alphaId);
  const betaDocs = await fiscalApi.getDocuments(betaId);

  assert.ok(alphaDocs.total > 0, 'Alpha deve possuir documentos semeados');
  assert.equal(betaDocs.total, 0, 'Beta não pode enxergar documentos da Alpha');

  const alphaDashboard = await fiscalApi.getDashboard(alphaId);
  const betaDashboard = await fiscalApi.getDashboard(betaId);

  assert.ok(alphaDashboard.issued_count > 0);
  assert.equal(betaDashboard.issued_count, 0);
});

test('10. Independência entre Competência da Nota e Data/Hora do Agendamento', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  // Nota com competência de Setembro (2026-09)
  const draft = await fiscalApi.createDraft(companyId, {
    customer_id: 'cust-tech-001',
    competence: '2026-09',
    service: {
      national_code: '01.07.01',
      municipal_code: '269',
      description: 'Consultoria de fechamento contábil',
    },
    amount: '5000.00',
  });

  assert.equal(draft.competence, '2026-09', 'Competência da nota preservada');

  // Lote agendado para início de Novembro (2026-11-01 às 00:01)
  const scheduledTime = '2026-11-01T00:01:00-03:00';
  const batchCreated = await fiscalApi.createBatch(
    companyId,
    {
      drafts: [{ id: draft.id, expected_version: draft.version }],
      scheduled_at: scheduledTime,
      timezone: 'America/Sao_Paulo',
    },
    'idemp-key-test-10'
  );

  assert.equal(batchCreated.scheduled_at, scheduledTime);
  const fullBatch = await fiscalApi.getBatch(companyId, batchCreated.batch_id);
  assert.equal(fullBatch.items[0].competence, '2026-09', 'Competência e agendamento são tratados como campos distintos');
});

test('11. Validação de códigos de serviço: Não trunca e não converte silenciosamente (lança 422)', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  // Código nacional com 4 dígitos (inválido) deve rejeitar com 422
  await assert.rejects(
    async () => {
      await fiscalApi.createDraft(companyId, {
        customer_id: 'cust-tech-001',
        competence: '2026-10',
        service: {
          national_code: '01.07', // Inválido: apenas 4 dígitos
          municipal_code: '269',
          description: 'Teste',
        },
        amount: '100.00',
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 422);
      assert.ok(err.problem.detail?.includes('6 dígitos numéricos'));
      return true;
    }
  );

  // Código nacional com caracteres não numéricos deve rejeitar com 422
  await assert.rejects(
    async () => {
      await fiscalApi.createDraft(companyId, {
        customer_id: 'cust-tech-001',
        competence: '2026-10',
        service: {
          national_code: 'ABCDEF', // Inválido
          municipal_code: '269',
          description: 'Teste',
        },
        amount: '100.00',
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 422);
      return true;
    }
  );

  // Código municipal com letras deve rejeitar com 422
  await assert.rejects(
    async () => {
      await fiscalApi.createDraft(companyId, {
        customer_id: 'cust-tech-001',
        competence: '2026-10',
        service: {
          national_code: '010701',
          municipal_code: 'XYZ', // Inválido
          description: 'Teste',
        },
        amount: '100.00',
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 422);
      assert.ok(err.problem.detail?.includes('Municipal'));
      return true;
    }
  );

  // Código válido com máscara é aceito e normalizado sem truncamento
  const validDraft = await fiscalApi.createDraft(companyId, {
    customer_id: 'cust-tech-001',
    competence: '2026-10',
    service: {
      national_code: '01.07.01',
      municipal_code: '269',
      description: 'Consultoria válida',
    },
    amount: '100.00',
  });
  assert.equal(validDraft.service.national_code, '010701');
});

test('12. D7: Badge de Ambiente distingue simulação, produção restrita e produção com capacidades reais', () => {
  // Produção real
  const prodReal = getEnvironmentBadgeConfig('production', false);
  assert.equal(prodReal.status, 'VALID');
  assert.equal(prodReal.label, 'Produção');

  // Produção simulada
  const prodSim = getEnvironmentBadgeConfig('production', true);
  assert.equal(prodSim.status, 'PENDING');
  assert.equal(prodSim.label, 'Produção (Simulada)');

  // Produção restrita (homologação) real
  const restrReal = getEnvironmentBadgeConfig('restricted', false);
  assert.equal(restrReal.status, 'PENDING');
  assert.equal(restrReal.label, 'Produção Restrita');

  // Produção restrita simulada
  const restrSim = getEnvironmentBadgeConfig('restricted', true);
  assert.equal(restrSim.status, 'PENDING');
  assert.equal(restrSim.label, 'Produção Restrita (Simulada)');

  // Mock / Simulação pura
  const mockEnv = getEnvironmentBadgeConfig('mock', true);
  assert.equal(mockEnv.status, 'NOT_CONFIGURED');
  assert.equal(mockEnv.label, 'Simulação (Mock)');

  // Valor nulo ou indefinido recai em Mock seguro
  const defaultEnv = getEnvironmentBadgeConfig(null, true);
  assert.equal(defaultEnv.status, 'NOT_CONFIGURED');
  assert.equal(defaultEnv.label, 'Simulação (Mock)');
});

test('13. D11: Mapeamento de estados de lote e separação estrita dos estados de item', () => {
  // Estados do LOTE (BatchStatus)
  const schedBatch = getBatchStatusBadgeConfig('SCHEDULED');
  assert.equal(schedBatch.status, 'SCHEDULED');
  assert.equal(schedBatch.label, 'Agendado');

  const procBatch = getBatchStatusBadgeConfig('PROCESSING');
  assert.equal(procBatch.status, 'PROCESSING');
  assert.equal(procBatch.label, 'Processando');
  assert.notEqual(procBatch.status, 'WITHDRAWN', 'PROCESSING não pode aparecer como WITHDRAWN');

  const compBatch = getBatchStatusBadgeConfig('COMPLETED');
  assert.equal(compBatch.status, 'VALID');
  assert.equal(compBatch.label, 'Concluído');
  assert.notEqual(compBatch.status, 'WITHDRAWN', 'COMPLETED não pode aparecer como WITHDRAWN');

  const rejBatch = getBatchStatusBadgeConfig('COMPLETED_WITH_REJECTIONS');
  assert.equal(rejBatch.status, 'DIVERGENT');
  assert.equal(rejBatch.label, 'Concluído com Rejeições');
  assert.notEqual(rejBatch.status, 'WITHDRAWN', 'COMPLETED_WITH_REJECTIONS não pode aparecer como WITHDRAWN');

  const withBatch = getBatchStatusBadgeConfig('WITHDRAWN');
  assert.equal(withBatch.status, 'WITHDRAWN');
  assert.equal(withBatch.label, 'Retirado da Fila');

  // Estados do ITEM do lote (QueueStatus / BatchItemStatus)
  const itemDraft = getBatchItemStatusBadgeConfig('DRAFT');
  assert.equal(itemDraft.status, 'DRAFT');

  const itemSched = getBatchItemStatusBadgeConfig('SCHEDULED');
  assert.equal(itemSched.status, 'SCHEDULED');

  const itemProc = getBatchItemStatusBadgeConfig('PROCESSING');
  assert.equal(itemProc.status, 'PROCESSING');

  const itemAuth = getBatchItemStatusBadgeConfig('AUTHORIZED');
  assert.equal(itemAuth.status, 'AUTHORIZED');
  assert.equal(itemAuth.label, 'Autorizada');

  const itemRej = getBatchItemStatusBadgeConfig('REJECTED');
  assert.equal(itemRej.status, 'REJECTED');
  assert.equal(itemRej.label, 'Rejeitada');

  const itemAwait = getBatchItemStatusBadgeConfig('AWAITING_CONFIRMATION');
  assert.equal(itemAwait.status, 'AWAITING_CONFIRMATION');

  const itemWith = getBatchItemStatusBadgeConfig('WITHDRAWN');
  assert.equal(itemWith.status, 'WITHDRAWN');
});

test('14. D15: Helper formatFieldErrors e métodos de conveniência em ProblemDetailsException', () => {
  // 1. Array canônico ProblemFieldError[]
  const rawErrors = [
    { field: 'name', code: 'REQUIRED', message: 'Nome é obrigatório' },
    { field: 'name', code: 'MIN_LENGTH', message: 'Mínimo 3 caracteres' },
    { field: 'document', code: 'INVALID_CNPJ', message: 'CNPJ inválido' },
    { field: '', code: 'GLOBAL_RULE', message: 'Inconsistência geral cadastral' },
  ];

  const formatted = formatFieldErrors(rawErrors);
  assert.deepEqual(formatted, {
    name: ['Nome é obrigatório', 'Mínimo 3 caracteres'],
    document: ['CNPJ inválido'],
    _general: ['Inconsistência geral cadastral'],
  });

  // 2. Pass-through para Record já formatado
  const preformatted = { name: ['Campo inválido'] };
  assert.deepEqual(formatFieldErrors(preformatted), preformatted);

  // 3. Null / undefined retorna objeto vazio
  assert.deepEqual(formatFieldErrors(null), {});
  assert.deepEqual(formatFieldErrors(undefined), {});

  // 4. Métodos em ProblemDetailsException
  const exception = new ProblemDetailsException({
    type: 'urn:problem:validation-error',
    title: 'Erro de validação',
    status: 422,
    detail: 'Dados incorretos submetidos',
    errors: rawErrors,
  });

  // formatFieldErrors() no objeto de erro
  const fromEx = exception.formatFieldErrors();
  assert.deepEqual(fromEx, formatted);

  // getFieldErrorMessage()
  assert.equal(exception.getFieldErrorMessage('name'), 'Nome é obrigatório, Mínimo 3 caracteres');
  assert.equal(exception.getFieldErrorMessage('document'), 'CNPJ inválido');
  assert.equal(exception.getFieldErrorMessage('email'), null);

  // formatErrorsSummary()
  const summary = exception.formatErrorsSummary();
  assert.ok(summary.includes('name: Nome é obrigatório, Mínimo 3 caracteres'));
  assert.ok(summary.includes('document: CNPJ inválido'));
  assert.ok(summary.includes('Inconsistência geral cadastral'));
});

test('15. Diferenciação visual entre documentos de homologação, produção e simulação', () => {
  // Produção real
  const prodDoc = getDocumentEnvironmentBadgeConfig('production', false);
  assert.equal(prodDoc.status, 'VALID');
  assert.equal(prodDoc.label, 'PRODUÇÃO');

  // Homologação (Restricted) real
  const restrDoc = getDocumentEnvironmentBadgeConfig('restricted', false);
  assert.equal(restrDoc.status, 'PENDING');
  assert.equal(restrDoc.label, 'HOMOLOGAÇÃO');

  // Homologação simulada
  const restrSimDoc = getDocumentEnvironmentBadgeConfig('restricted', true);
  assert.equal(restrSimDoc.status, 'PENDING');
  assert.equal(restrSimDoc.label, 'HOMOLOGAÇÃO');

  // Produção simulada
  const prodSimDoc = getDocumentEnvironmentBadgeConfig('production', true);
  assert.equal(prodSimDoc.status, 'PENDING');
  assert.equal(prodSimDoc.label, 'HOMOLOGAÇÃO');

  // Simulação pura (Mock)
  const mockDoc = getDocumentEnvironmentBadgeConfig('mock', true);
  assert.equal(mockDoc.status, 'NOT_CONFIGURED');
  assert.equal(mockDoc.label, 'SIMULADA');

  // Nulo
  const nullDoc = getDocumentEnvironmentBadgeConfig(null, true);
  assert.equal(nullDoc.status, 'NOT_CONFIGURED');
  assert.equal(nullDoc.label, 'SIMULADA');
});

test('16. Sprint 02: cTribMun exige exatamente 3 dígitos e preserva zeros à esquerda', () => {
  // Códigos válidos com exatamente 3 dígitos
  assert.equal(validateServiceCodes('010701', '269').valid, true);
  assert.equal(validateServiceCodes('010701', '001').valid, true);
  assert.equal(validateServiceCodes('010701', '010').valid, true);
  assert.equal(validateServiceCodes('010701', null).valid, true);
  assert.equal(validateServiceCodes('010701', '').valid, true);

  // Códigos inválidos: menos ou mais de 3 dígitos, ou caracteres
  const twoDigits = validateServiceCodes('010701', '26');
  assert.equal(twoDigits.valid, false);
  assert.match(twoDigits.error || '', /exatamente 3 dígitos/);

  const fourDigits = validateServiceCodes('010701', '2691');
  assert.equal(fourDigits.valid, false);
  assert.match(fourDigits.error || '', /exatamente 3 dígitos/);

  const letters = validateServiceCodes('010701', '26A');
  assert.equal(letters.valid, false);
});

test('17. Sprint 02: Rascunho com dados fiscais aditivos (service_date, location_code, nbs_code, obra, PIS/COFINS, IBS/CBS, ApproxTax)', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  const draft = await fiscalApi.createDraft(companyId, {
    customer_id: 'cust-tech-001',
    competence: '2026-10',
    service_date: '2026-10-05',
    service: {
      national_code: '010701',
      municipal_code: '001', // Preserva zero
      nbs_code: '123456789', // 9 dígitos
      location_code: '3550308', // 7 dígitos IBGE
      obra_code: 'CNO-123456',
      description: 'Desenvolvimento e consultoria fiscal com reforma tributária',
    },
    amount: '10000.00',
    deductions: '0.00',
    tax_settings: {
      pis_cofins_cst: '01',
      pis_cofins_base: '10000.00',
      pis_rate: '0.65',
      cofins_rate: '3.00',
      pis_amount: '65.00',
      cofins_amount: '300.00',
      approx_tax: {
        mode: 'VALUES',
        federal: '1345.00',
        state: '0.00',
        municipal: '200.00',
      },
      ibs_cbs: {
        operation_indicator: '020101',
        cst: '000',
        class_code: '010101',
        personal_use: '0',
      },
    },
  });

  assert.equal(draft.service_date, '2026-10-05');
  assert.equal(draft.service.municipal_code, '001');
  assert.equal(draft.service.nbs_code, '123456789');
  assert.equal(draft.service.location_code, '3550308');
  assert.equal(draft.service.obra_code, 'CNO-123456');
  assert.equal(draft.tax_settings?.pis_cofins_cst, '01');
  assert.equal(draft.tax_settings?.approx_tax?.mode, 'VALUES');
  assert.equal(draft.tax_settings?.ibs_cbs?.operation_indicator, '020101');
});

test('18. Sprint 02: Desacoplamento estrito das 3 dimensões (Ambiente, Autorização Fiscal e Conferência)', () => {
  // Cenário A: Documento autorizado em homologação (produção restrita)
  // Permanece como documento de teste (sem validade fiscal), mas com autorização válida na SEFIN
  const envHomolog = getDocumentEnvironmentBadgeConfig('restricted', false);
  const authValida = getFiscalAuthorizationBadgeConfig('AUTHORIZED');
  const reviewPendente = getReviewStatusBadgeConfig('PENDING');

  assert.equal(envHomolog.label, 'HOMOLOGAÇÃO');
  assert.equal(envHomolog.status, 'PENDING');
  assert.equal(envHomolog.isTestDocument, true, 'Documento em homologação nunca possui validade fiscal');
  assert.equal(authValida.label, 'Autorizada');
  assert.equal(authValida.status, 'AUTHORIZED');
  assert.equal(reviewPendente.label, 'Pendente Revisão');

  // Cenário B: Documento emitido em produção real, porém CANCELADO
  const envProd = getDocumentEnvironmentBadgeConfig('production', false);
  const authCancelada = getFiscalAuthorizationBadgeConfig('CANCELLED');
  const reviewRevisada = getReviewStatusBadgeConfig('REVIEWED');

  assert.equal(envProd.label, 'PRODUÇÃO');
  assert.equal(envProd.status, 'VALID');
  assert.equal(envProd.isTestDocument, false);
  assert.equal(authCancelada.label, 'Cancelada');
  assert.equal(authCancelada.status, 'CANCELLED');
  assert.equal(reviewRevisada.label, 'Revisada');

  // Cenário C: Documento divergente na conferência interna
  const reviewDivergente = getReviewStatusBadgeConfig('DIVERGENT');
  assert.equal(reviewDivergente.label, 'Divergente');
  assert.equal(reviewDivergente.status, 'DIVERGENT');
});

test('19. Sprint 02: Editar Rascunho completo, preservação de zeros, remoção de opcionais e retirada da fila', async () => {
  const fiscalApi = new MockFiscalApiClient();
  const companyId = 'cmp-alpha-1111-uuid';

  // 1. Obter rascunho existente
  const initialDraft = await fiscalApi.getDraft(companyId, 'draft-001-uuid');
  assert.equal(initialDraft.version, 1);

  // 2. Editar rascunho alterando múltiplos grupos com expected_version
  const updated = await fiscalApi.updateDraft(companyId, initialDraft.id, {
    expected_version: 1,
    amount: '15000.50',
    competence: '2026-11',
    service_date: '2026-11-05',
    service: {
      description: 'Consultoria fiscal avançada e perícia contábil',
      national_code: '010701',
      municipal_code: '042', // Deve preservar zero à esquerda
      nbs_code: '123456789',
      location_code: '3550308',
      obra_code: 'CNO-998877',
      obra_address: null,
    },
    tax_settings: {
      issqn_taxation: 'TAXABLE',
      iss_retention: 'RETAINED_BY_TOMADOR',
      iss_rate: '3.50',
      pis_cofins_cst: '01',
      pis_cofins_base: '15000.50',
      pis_rate: '0.65',
      cofins_rate: '3.00',
      pis_amount: '97.50',
      cofins_amount: '450.02',
      approx_tax: {
        mode: 'VALUES',
        federal: '1500.00',
        state: '0.00',
        municipal: '525.00',
      },
      ibs_cbs: {
        operation_indicator: '100101',
        cst: '001',
        class_code: '010101',
        personal_use: '0',
      },
    },
  });

  assert.equal(updated.version, 2);
  assert.equal(updated.amount, '15000.50');
  assert.equal(updated.competence, '2026-11');
  assert.equal(updated.service_date, '2026-11-05');
  assert.equal(updated.service.municipal_code, '042');
  assert.equal(updated.service.obra_code, 'CNO-998877');
  assert.equal(updated.service.obra_address, null);
  assert.equal(updated.tax_settings?.iss_rate, '3.50');
  assert.equal(updated.tax_settings?.ibs_cbs?.operation_indicator, '100101');

  // 3. Teste de conflito 409 ao enviar expected_version desatualizada
  await assert.rejects(
    async () => {
      await fiscalApi.updateDraft(companyId, initialDraft.id, {
        expected_version: 1, // Conflito: versão atual é 2
        amount: '16000.00',
      });
    },
    (err: unknown) => {
      assert.ok(err instanceof ProblemDetailsException);
      assert.equal(err.problem.status, 409);
      assert.equal(err.problem.type, 'urn:problem:version-conflict');
      return true;
    }
  );

  // 4. Limpeza de campos opcionais: enviar null remove conforme contrato
  const cleared = await fiscalApi.updateDraft(companyId, initialDraft.id, {
    expected_version: 2,
    service_date: null,
    service: {
      description: 'Consultoria fiscal resumida',
      national_code: '010701',
      municipal_code: null, // removido
      nbs_code: null, // removido
      location_code: null, // removido
      obra_code: null, // removido
      obra_address: null,
    },
    tax_settings: {
      issqn_taxation: 'TAXABLE',
      iss_retention: 'NOT_RETAINED',
      iss_rate: null, // removido
      pis_cofins_cst: null, // removido
      approx_tax: null, // removido
      ibs_cbs: null, // removido
    },
  });

  assert.equal(cleared.version, 3);
  assert.equal(cleared.service_date, null);
  assert.equal(cleared.service.municipal_code, null);
  assert.equal(cleared.service.obra_code, null);
  assert.equal(cleared.tax_settings?.pis_cofins_cst, null);
  assert.equal(cleared.tax_settings?.approx_tax, null);
  assert.equal(cleared.tax_settings?.ibs_cbs, null);

  // 5. Retirada de rascunho agendado (withdrawDraft)
  // Criar um rascunho e agendá-lo
  const schedDraft = await fiscalApi.createDraft(companyId, {
    customer_id: 'cust-001',
    competence: '2026-10',
    amount: '5000.00',
    service: {
      description: 'Serviço para lote agendado',
      national_code: '010701',
    },
  });
  // Simula estado SCHEDULED
  schedDraft.status = 'SCHEDULED';
  schedDraft.current_item_id = 'item-batch-test';

  const withdrawn = await fiscalApi.withdrawDraft(companyId, schedDraft.id, schedDraft.version);
  assert.equal(withdrawn.status, 'DRAFT', 'Rascunho retirado deve voltar a DRAFT');
  assert.equal(withdrawn.current_item_id, null);
  assert.equal(withdrawn.version, schedDraft.version + 1);
});

test('20. Sprint 02: Cálculo canônico de tributos e retenções, não-duplicação de PCC e diferenciação de zero vs não informado', () => {
  const { calculateRetentionSummary, formatOptionalCurrency, formatOptionalPercent } = require('../src/retentions.js');

  // 1. Diferenciação de campo não informado vs zero
  assert.equal(formatOptionalCurrency(null), 'Não informado');
  assert.equal(formatOptionalCurrency(''), 'Não informado');
  assert.equal(formatOptionalCurrency(undefined), 'Não informado');
  assert.equal(formatOptionalCurrency('0.00'), 'R$ 0,00');
  assert.equal(formatOptionalCurrency('0'), 'R$ 0,00');
  assert.equal(formatOptionalCurrency('150.50'), 'R$ 150,50');

  assert.equal(formatOptionalPercent(null), 'Não informada');
  assert.equal(formatOptionalPercent(''), 'Não informada');
  assert.equal(formatOptionalPercent('0.00'), '0,00%');
  assert.equal(formatOptionalPercent('2.50'), '2,50%');

  // 2. Cálculo canônico com retenções discriminadas e sem duplicação de PCC
  const summary = calculateRetentionSummary({
    amount: '10000.00',
    unconditionalDiscount: '50.00',
    conditionalDiscount: '20.00',
    deductions: '200.00',
    taxSettings: {
      issqn_taxation: 'TAXABLE',
      iss_retention: 'RETAINED_BY_TOMADOR',
      iss_rate: '2.00',
      irrf_withheld: '150.00',
      inss_withheld: '500.00',
      csll_withheld: '100.00', // vRetCSLL: total das contribuições retidas (PCC)
      pis_cofins_retention: '3', // 3 - PIS/COFINS/CSLL retidos (Total PCC)
      pis_cofins_cst: '01',
      pis_cofins_base: '9750.00',
      pis_rate: '0.65',
      cofins_rate: '3.00',
      pis_amount: '63.38', // Débito próprio informativo: NUNCA entra no total retido
      cofins_amount: '292.50', // Débito próprio informativo: NUNCA entra no total retido
    },
  });

  // Base do ISS: 10000 - 50 (desc incond) - 200 (deduções) = 9750.00
  assert.equal(summary.iss.base, '9750.00');
  // ISS retido: 9750.00 * 2% = 195.00
  assert.equal(summary.iss.amount, '195.00');
  assert.equal(summary.iss.retained, true);

  // Total das Retenções: INSS (500) + IRRF (150) + CSLL/PCC (100) + ISS (195) = 945.00
  assert.equal(summary.federal.total, '750.00');
  assert.equal(summary.total_withheld, '945.00');

  // Líquido: 10000 - 50 (desc incond) - 20 (desc cond) - 945 (retenções) = 8985.00
  assert.equal(summary.net_amount, '8985.00');

  // Garante que débitos próprios de PIS e COFINS não foram somados às retenções
  assert.equal(summary.informed_not_withheld.pis, '63.38');
  assert.equal(summary.informed_not_withheld.cofins, '292.50');
  assert.equal(summary.complete, true);
});




test('Retenções: prévia local idêntica ao backend (BigInt, HALF_EVEN, sem limitar base, código 1 pendente)', () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { calculateRetentionSummary } = require('../src/retentions.js');
  const multi = {
    iss_retention: 'RETAINED_BY_TOMADOR', iss_rate: '2.50', inss_withheld: '1100.00', irrf_withheld: '150.00',
    pis_cofins_cst: '01', pis_amount: '64.35', cofins_amount: '297.00', pis_cofins_retention: '3', csll_withheld: '460.35',
  };
  const s = calculateRetentionSummary({ amount: '10000.00', unconditionalDiscount: '100.00', conditionalDiscount: '50.00', taxSettings: multi });
  assert.equal(s.iss.amount, '247.50');
  assert.equal(s.total_withheld, '1957.85'); // mesmos valores de tests/test_retencoes.py
  assert.equal(s.net_amount, '7892.15');
  const half = calculateRetentionSummary({ amount: '2.50', taxSettings: { iss_retention: 'RETAINED_BY_TOMADOR', iss_rate: '5.00' } });
  assert.equal(half.iss.amount, '0.12'); // 0,125 → 0,12 (bancário)
  const neg = calculateRetentionSummary({ amount: '100.00', unconditionalDiscount: '60.00', taxSettings: { inss_withheld: '50.00' } });
  assert.equal(neg.net_amount, '-10.00');
  const c1 = calculateRetentionSummary({ amount: '1000.00', taxSettings: { pis_cofins_retention: '1', csll_withheld: '10.00' } });
  assert.equal(c1.complete, false);
  assert.ok(c1.pending.includes('federal.contributions_code_1'));
});
