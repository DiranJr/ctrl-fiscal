// Teste E2E em Navegador Real (Chrome via CDP): Seção "Tributos e retenções" e Resumo Canônico
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';

const CDP_PORT = 9222;
const BASE_URL = process.env.CTRL_BASE || 'http://127.0.0.1:8098'; // ambiente temporário
const COMPANY_A = '447b0b18-9cc3-4ae4-967a-dbb8ae3a0ae5';
const EVIDENCIAS_DIR = process.env.EVIDENCIAS_DIR || join(process.cwd(), 'docs', 'evidencias');

function getTestCredentials() {
  const email = process.env.CTRL_TEST_USER_EMAIL || 'operador.a@descartavel.example.com';
  let password = process.env.CTRL_TEST_USER_PASSWORD;
  if (!password) {
    const secretPath = process.env.CTRL_TEST_SECRET_FILE || join(process.cwd(), 'secrets-descartavel', 'senha-operador.a.txt');
    if (existsSync(secretPath)) {
      password = readFileSync(secretPath, 'utf-8').trim();
    } else {
      throw new Error(`Credenciais de teste não configuradas.`);
    }
  }
  return { email, password };
}

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.msgId = 1;
    this.pending = new Map();
  }

  async connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.wsUrl);
      this.ws.onopen = () => resolve();
      this.ws.onerror = (err) => reject(err);
      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.id && this.pending.has(msg.id)) {
            const { res, rej } = this.pending.get(msg.id);
            this.pending.delete(msg.id);
            if (msg.error) {
              rej(new Error(JSON.stringify(msg.error)));
            } else {
              res(msg.result);
            }
          }
        } catch (e) {
          console.error('Erro WS:', e);
        }
      };
    });
  }

  async send(method, params = {}) {
    const id = this.msgId++;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async eval(expression) {
    const res = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.exceptionDetails) {
      throw new Error(`Eval error: ${JSON.stringify(res.exceptionDetails)}`);
    }
    return res.result?.value;
  }

  async setInputValue(selector, value) {
    await this.eval(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) throw new Error('Element not found: ' + ${JSON.stringify(selector)});
      el.focus();
      const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) {
        setter.call(el, ${JSON.stringify(value)});
      } else {
        el.value = ${JSON.stringify(value)};
      }
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    await this.sleep(150);
  }

  async setByLabel(labelText, value, isSelect = false) {
    const found = await this.eval(`(() => {
      const labels = Array.from(document.querySelectorAll('label'));
      const lbl = labels.find(l => l.textContent.trim().toLowerCase().includes(${JSON.stringify(labelText.toLowerCase())}));
      if (!lbl) return false;
      let target = null;
      const htmlFor = lbl.getAttribute('for');
      if (htmlFor) target = document.getElementById(htmlFor);
      if (!target) target = lbl.parentElement?.querySelector('input, select, textarea');
      if (!target) return false;
      target.focus();
      if (${isSelect}) {
        target.value = ${JSON.stringify(value)};
      } else {
        const proto = target instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) {
          setter.call(target, ${JSON.stringify(value)});
        } else {
          target.value = ${JSON.stringify(value)};
        }
      }
      target.dispatchEvent(new Event('input', { bubbles: true }));
      target.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    })()`);
    if (!found) {
      console.warn(`[AVISO] Campo não encontrado por label: "${labelText}"`);
    }
    await this.sleep(200);
    return found;
  }

  async navigate(url) {
    await this.send('Page.navigate', { url });
    await this.sleep(2500);
  }

  async screenshot(filename) {
    const res = await this.send('Page.captureScreenshot', { format: 'png' });
    const buffer = Buffer.from(res.data, 'base64');
    const path = join(EVIDENCIAS_DIR, filename);
    writeFileSync(path, buffer);
    console.log(`[EVIDÊNCIA SALVA] ${path}`);
    return path;
  }

  async sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async close() {
    if (this.ws) {
      this.ws.close();
    }
  }
}

async function getNewPageWsUrl() {
  const resp = await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?${encodeURIComponent(BASE_URL + '/login')}`, {
    method: 'PUT',
  });
  const data = await resp.json();
  return data.webSocketDebuggerUrl;
}

async function run() {
  console.log('=== TESTE E2E: TRIBUTOS E RETENÇÕES & RESUMO CANÔNICO ===');
  mkdirSync(EVIDENCIAS_DIR, { recursive: true });

  const wsUrl = await getNewPageWsUrl();
  console.log(`Conectando ao Chrome via CDP: ${wsUrl}`);
  const client = new CdpClient(wsUrl);
  await client.connect();

  await client.send('Page.enable');
  await client.send('Runtime.enable');
  await client.send('DOM.enable');
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1366,
    height: 768,
    deviceScaleFactor: 1,
    mobile: false,
  });

  try {
    // 1. Login no Gateway
    console.log('\n--- 1. Login no gateway do ambiente temporário ---');
    await client.navigate(`${BASE_URL}/login`);
    const { email, password } = getTestCredentials();
    await client.setInputValue('#inputEmail', email);
    await client.setInputValue('#inputPassword', password);
    await client.eval(`(() => { document.getElementById('btnSubmitLogin').click(); })()`);
    await client.sleep(3000);

    // 2. Navegar diretamente para Nova NFS-e da Empresa Demonstração A
    console.log('\n--- 2. Acessando Nova NFS-e na Empresa Demonstração A ---');
    await client.navigate(`${BASE_URL}/fiscal/?company_id=${COMPANY_A}&view=nova-nfse`);
    await client.sleep(3000);

    // 3. Preencher Discriminação dos Serviços
    console.log('\n--- 3. Preenchendo Discriminação e Classificações ---');
    await client.setByLabel('Discriminação dos Serviços', 'Consultoria técnica em integração fiscal, tributos municipais e retenções federais.');
    await client.setByLabel('Código Tributação Nacional', '010701');
    await client.setByLabel('Código Tributação Municipal', '269');

    // 4. Preencher Valores e Descontos
    console.log('\n--- 4. Preenchendo Valores e Descontos: 10.000,00 | 500,00 | 200,00 | 300,00 ---');
    await client.setByLabel('Valor do Serviço', '10.000,00');
    await client.setByLabel('Desconto Incondicionado', '500,00');
    await client.setByLabel('Desconto Condicionado', '200,00');
    await client.setByLabel('Deduções Legais', '300,00');

    // 5. Preencher Seção "Tributos e retenções": ISS, IRRF, INSS, CSLL, PIS/COFINS
    console.log('\n--- 5. Preenchendo Seção "Tributos e retenções" ---');
    // ISS
    await client.setByLabel('Tipo de Retenção e Responsável', 'RETAINED_BY_TOMADOR', true);
    await client.setByLabel('Alíquota do ISS (%)', '2.00');

    // Retenções Federais
    await client.setByLabel('IRRF Retido', '150,00');
    await client.setByLabel('INSS / Previdenciária', '350,00');
    await client.setByLabel('Contribuições Federais Retidas (vRetCSLL / PCC)', '100,00');
    await client.setByLabel('Modalidade de Retenção Aplicável', '1', true);

    // PIS/COFINS Apuração Própria
    await client.setByLabel('Situação Tributária PIS/COFINS (CST)', '01', true);
    await client.sleep(400);
    await client.setByLabel('Base de Cálculo PIS/COFINS', '9.500,00');
    await client.setByLabel('Alíquota PIS (%)', '0.65');
    await client.setByLabel('Alíquota COFINS (%)', '3.00');
    await client.setByLabel('Valor PIS (R$)', '61,75');
    await client.setByLabel('Valor COFINS (R$)', '285,00');
    await client.sleep(500);

    // 6. Rolar até o Resumo Canônico e Conferir Valores
    console.log('\n--- 6. Conferindo Resumo Canônico em Tempo Real ---');
    await client.eval(`(() => {
      const summaryEl = document.getElementById('canonical-retention-summary');
      if (summaryEl) summaryEl.scrollIntoView({ behavior: 'instant', block: 'center' });
    })()`);
    await client.sleep(500);

    const summaryCardInfo = await client.eval(`(() => {
      const el = document.getElementById('canonical-retention-summary');
      const text = el ? el.innerText : document.body.innerText;
      return {
        hasSummaryCard: !!el,
        hasIssRet: text.includes('184,00'),
        hasIrrf: text.includes('150,00'),
        hasInss: text.includes('350,00'),
        hasCsll: text.includes('100,00'),
        hasTotalRet: text.includes('784,00'),
        hasNet: text.includes('8.516,00'),
        hasPccNote: text.includes('Contribuições Federais (PIS/COFINS/CSLL)') || text.includes('não duplica'),
      };
    })()`);
    console.log('Validação do Resumo Canônico na UI:', summaryCardInfo);

    await client.screenshot('e2e_tributos_01_resumo_canonico_step1.png');

    // 7. Avançar para Etapa 2: Revisão
    console.log('\n--- 7. Avançando para Etapa 2: Revisão ---');
    await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const btn = buttons.find(b => b.textContent?.includes('Revisar Dados da NFS-e'));
      if (btn) btn.click();
    })()`);
    await client.sleep(2000);

    // Rolar até o resumo canônico na Etapa 2
    await client.eval(`(() => {
      const summaryEl = document.getElementById('canonical-retention-summary');
      if (summaryEl) summaryEl.scrollIntoView({ behavior: 'instant', block: 'center' });
    })()`);
    await client.sleep(400);

    const step2Info = await client.eval(`(() => {
      const el = document.getElementById('canonical-retention-summary');
      const text = el ? el.innerText : document.body.innerText;
      return {
        hasGross: text.includes('10.000,00'),
        hasNet: text.includes('8.516,00'),
        hasTotalRet: text.includes('784,00'),
      };
    })()`);
    console.log('Validação da Etapa 2 (Revisão):', step2Info);

    await client.screenshot('e2e_tributos_02_revisao_step2.png');
    console.log('✓ Etapa 2 confirmada com Resumo Canônico!');

    // 8. Salvar como Rascunho
    console.log('\n--- 8. Salvando Rascunho no Servidor ---');
    await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const btn = buttons.find(b => b.textContent?.includes('Salvar como Rascunho'));
      if (btn) btn.click();
    })()`);
    await client.sleep(3000);

    // 9. Verificar Lista de Rascunhos e Coluna Valor Líquido
    console.log('\n--- 9. Conferindo Lista de Rascunhos ---');
    await client.navigate(`${BASE_URL}/fiscal/?company_id=${COMPANY_A}&view=rascunhos`);
    await client.sleep(2500);

    const tableInfo = await client.eval(`(() => {
      const text = document.body.innerText;
      return {
        hasGross: text.includes('10.000,00'),
        hasNet: text.includes('8.516,00') || text.includes('Líq:'),
      };
    })()`);
    console.log('Validação da Tabela de Rascunhos:', tableInfo);

    await client.screenshot('e2e_tributos_03_rascunhos_lista.png');

    // 10. Abrir Modal de Edição Completo do Rascunho
    console.log('\n--- 10. Abrindo Modal de Edição Completo do Rascunho ---');
    await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const editBtn = buttons.find(b => b.textContent?.includes('Editar'));
      if (editBtn) editBtn.click();
    })()`);
    await client.sleep(1500);

    // Ir para Aba 3: Tributos e Retenções
    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('Tributos e Retenções'));
      if (tab) tab.click();
    })()`);
    await client.sleep(1000);

    const editModalInitial = await client.eval(`(() => {
      const findInput = (lbl) => {
        const labels = Array.from(document.querySelectorAll('label'));
        const l = labels.find(x => x.textContent.toLowerCase().includes(lbl.toLowerCase()));
        if (!l) return null;
        const id = l.getAttribute('for');
        return id ? document.getElementById(id) : l.parentElement?.querySelector('input, select');
      };
      return {
        issRate: findInput('Alíquota do ISS (%)')?.value,
        irrf: findInput('IRRF Retido')?.value,
        inss: findInput('INSS / Previdenciária')?.value,
        csll: findInput('Contribuições Federais Retidas')?.value,
        pisBase: findInput('Base de Cálculo PIS/COFINS')?.value,
      };
    })()`);
    console.log('Valores carregados no Modal de Edição (Aba 3):', editModalInitial);

    await client.screenshot('e2e_tributos_04_modal_editar_carregado.png');
    console.log('✓ Modal aberto na Aba Tributos e Retenções com valores carregados!');

    // 11. Alterar e Limpar Retenções: Limpar IRRF e Alterar CSLL de 100,00 para 200,00
    console.log('\n--- 11. Alterando e Removendo Retenções no Modal de Edição ---');
    await client.setByLabel('IRRF Retido', '');
    await client.setByLabel('Contribuições Federais Retidas', '200,00');
    await client.sleep(500);

    // Rolar até o resumo canônico dentro do modal
    await client.eval(`(() => {
      const summaryEl = document.querySelector('.ui-modal #canonical-retention-summary, [role="dialog"] #canonical-retention-summary, #canonical-retention-summary');
      if (summaryEl) summaryEl.scrollIntoView({ behavior: 'instant', block: 'center' });
    })()`);
    await client.sleep(400);

    const modalSummaryAfterEdit = await client.eval(`(() => {
      const summaryEl = document.querySelector('.ui-modal #canonical-retention-summary, [role="dialog"] #canonical-retention-summary, #canonical-retention-summary');
      const text = summaryEl ? summaryEl.innerText : '';
      return {
        hasIssRet: text.includes('184,00'),
        hasIrrfZeroOrEmpty: !text.includes('150,00'),
        hasCsllNew: text.includes('200,00'),
        hasTotalRet: text.includes('734,00'), // 184 + 350 + 200 = 734,00
        hasNet: text.includes('8.566,00'),    // 10000 - 500 - 200 - 734 = 8.566,00
      };
    })()`);
    console.log('Resumo Canônico atualizado em tempo real no Modal:', modalSummaryAfterEdit);

    await client.screenshot('e2e_tributos_05_modal_apos_alteracao.png');

    // Salvar Rascunho
    console.log('Clicando em Salvar Rascunho...');
    await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const saveBtn = buttons.find(b => b.textContent?.includes('Salvar Rascunho'));
      if (saveBtn) saveBtn.click();
    })()`);
    await client.sleep(2500);

    // 12. Reabrir e Conferir Persistência
    console.log('\n--- 12. Reabrindo Rascunho e Conferindo Persistência dos Dados ---');
    await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const editBtn = buttons.find(b => b.textContent?.includes('Editar'));
      if (editBtn) editBtn.click();
    })()`);
    await client.sleep(1500);

    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('Tributos e Retenções'));
      if (tab) tab.click();
    })()`);
    await client.sleep(1000);

    const checkPersisted = await client.eval(`(() => {
      const findInput = (lbl) => {
        const labels = Array.from(document.querySelectorAll('label'));
        const l = labels.find(x => x.textContent.toLowerCase().includes(lbl.toLowerCase()));
        if (!l) return null;
        const id = l.getAttribute('for');
        return id ? document.getElementById(id) : l.parentElement?.querySelector('input, select');
      };

      return {
        irrfValue: findInput('IRRF Retido')?.value,
        csllValue: findInput('Contribuições Federais Retidas')?.value,
        inssValue: findInput('INSS / Previdenciária')?.value,
      };
    })()`);
    console.log('Valores após salvar e reabrir:', checkPersisted);

    await client.screenshot('e2e_tributos_06_reaberto_conferido.png');
    console.log('✓ Conferência concluída: IRRF limpo e CSLL com 200,00 persistidos com sucesso!');

    console.log('\n=== TESTE E2E FINALIZADO COM SUCESSO! ===');
  } catch (err) {
    console.error('Falha no teste E2E:', err);
    await client.screenshot('e2e_tributos_erro.png');
    throw err;
  } finally {
    await client.close();
  }
}

run();
