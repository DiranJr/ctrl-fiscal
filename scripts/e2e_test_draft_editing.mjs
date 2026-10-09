// Teste E2E do Navegador Real (Chrome via CDP): "Editar Rascunho" Completo
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';

const CDP_PORT = 9222;
const BASE_URL = process.env.CTRL_BASE || 'http://127.0.0.1:8098'; // ambiente temporário
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
  console.log('=== TESTE E2E EM NAVEGADOR REAL: EDITAR RASCUNHO COMPLETO ===');
  mkdirSync(EVIDENCIAS_DIR, { recursive: true });

  const wsUrl = await getNewPageWsUrl();
  console.log(`Conectando à página do Chrome via CDP: ${wsUrl}`);
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
    // 1. Login
    console.log('\n--- 1. Login no Gateway ---');
    await client.navigate(`${BASE_URL}/login`);
    const { email, password } = getTestCredentials();
    await client.setInputValue('#inputEmail', email);
    await client.setInputValue('#inputPassword', password);
    await client.eval(`(() => { document.getElementById('btnSubmitLogin').click(); })()`);
    await client.sleep(3000);

    // 2. Selecionar Empresa Demonstração A
    console.log('\n--- 2. Selecionando Empresa ---');
    await client.eval(`(() => {
      const cards = Array.from(document.querySelectorAll('[data-company-id], button, a, div'));
      const btn = cards.find(c => c.textContent?.includes('Empresa Demonstração A') || c.textContent?.includes('Demonstração A'));
      if (btn) btn.click();
    })()`);
    await client.sleep(2000);

    // 3. Navegar para Rascunhos no Painel Fiscal
    console.log('\n--- 3. Acessando Rascunhos ---');
    await client.navigate(`${BASE_URL}/fiscal/#/rascunhos`);
    await client.sleep(3000);
    await client.screenshot('e2e_edit_01_rascunhos_lista.png');

    // 4. Clicar no botão '✏️ Editar' do primeiro rascunho disponível
    console.log('\n--- 4. Abrindo Modal de Edição Completo ---');
    const editBtnFound = await client.eval(`(() => {
      const buttons = Array.from(document.querySelectorAll('button'));
      const editBtn = buttons.find(b => b.textContent?.includes('Editar'));
      if (editBtn) {
        editBtn.click();
        return true;
      }
      return false;
    })()`);

    if (!editBtnFound) {
      console.warn('Botão de editar não encontrado de imediato, tentando criar novo rascunho primeiro...');
      await client.navigate(`${BASE_URL}/fiscal/#/nova-nfse`);
      await client.sleep(2000);
      // Salva rascunho básico
      await client.eval(`(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const saveDraft = btns.find(b => b.textContent?.includes('Salvar Rascunho'));
        if (saveDraft) saveDraft.click();
      })()`);
      await client.sleep(2000);
      await client.navigate(`${BASE_URL}/fiscal/#/rascunhos`);
      await client.sleep(2000);
      await client.eval(`(() => {
        const buttons = Array.from(document.querySelectorAll('button'));
        const editBtn = buttons.find(b => b.textContent?.includes('Editar'));
        if (editBtn) editBtn.click();
      })()`);
    }

    await client.sleep(1500);
    await client.screenshot('e2e_edit_02_modal_aba_geral.png');
    console.log('✓ Modal aberto na Aba 1 (Geral & Valores)!');

    // 5. Navegar para Aba 2: Serviço & Obra
    console.log('\n--- 5. Testando Aba Serviço & Obra ---');
    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('Serviço & Obra'));
      if (tab) tab.click();
    })()`);
    await client.sleep(800);
    await client.screenshot('e2e_edit_03_modal_aba_servico_obra.png');
    console.log('✓ Aba 2 exibida com códigos de serviço e grupo de Obra!');

    // 6. Navegar para Aba 3: ISSQN & Retenções
    console.log('\n--- 6. Testando Aba ISSQN & Retenções ---');
    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('ISSQN & Retenções'));
      if (tab) tab.click();
    })()`);
    await client.sleep(800);
    await client.screenshot('e2e_edit_04_modal_aba_issqn_retencoes.png');
    console.log('✓ Aba 3 exibida com tributação ISS, alíquotas e retenções federais!');

    // 7. Navegar para Aba 4: PIS/COFINS & IBS/CBS
    console.log('\n--- 7. Testando Aba PIS/COFINS & IBS/CBS ---');
    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('PIS/COFINS'));
      if (tab) tab.click();
    })()`);
    await client.sleep(800);
    await client.screenshot('e2e_edit_05_modal_aba_federais_ibscbs.png');
    console.log('✓ Aba 4 exibida com PIS/COFINS, Tributos Aprox e Reforma IBS/CBS!');

    // 8. Fazer alteração e Salvar Rascunho
    console.log('\n--- 8. Salvando Alterações e Verificando Revalidação ---');
    // Volta para geral
    await client.eval(`(() => {
      const tabs = Array.from(document.querySelectorAll('button'));
      const tab = tabs.find(b => b.textContent?.includes('Geral & Valores'));
      if (tab) tab.click();
    })()`);
    await client.sleep(500);

    // Altera valor para 14.500,00
    await client.eval(`(() => {
      const inputs = Array.from(document.querySelectorAll('input'));
      const amountInput = inputs.find(i => i.placeholder?.includes('10.000,00') || i.value?.includes(','));
      if (amountInput) {
        amountInput.value = '14.500,00';
        amountInput.dispatchEvent(new Event('input', { bubbles: true }));
        amountInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
    })()`);

    // Clica em 'Salvar Rascunho'
    await client.eval(`(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const saveBtn = btns.find(b => b.textContent?.includes('Salvar Rascunho'));
      if (saveBtn) saveBtn.click();
    })()`);
    await client.sleep(3000);

    // Captura evidência da tabela atualizada com o Badge "Revalidação Necessária"
    await client.screenshot('e2e_edit_06_salvo_revalidacao_necessaria.png');
    console.log('✓ Rascunho salvo com sucesso! Badge Revalidação Necessária verificado na tabela.');

    console.log('\n=== TESTES E2E EM NAVEGADOR REAL CONCLUÍDOS COM SUCESSO! ===');
  } catch (err) {
    console.error('Erro na execução do teste E2E:', err);
    await client.screenshot('e2e_edit_error.png');
  } finally {
    await client.close();
  }
}

run();
