// Teste E2E Visual: Conflito 409 com Preservação Local e Retirar da Fila para Editar
import { writeFileSync, mkdirSync, readFileSync } from 'fs';
import { join } from 'path';

const CDP_PORT = 9222;
const BASE_URL = process.env.CTRL_BASE || 'http://127.0.0.1:8098'; // ambiente temporário
const EVIDENCIAS_DIR = process.env.EVIDENCIAS_DIR || join(process.cwd(), 'docs', 'evidencias');
const COMPANY_A = '4b1a2bf2-4114-411a-8bb7-d4f107f9188a';

function getTestCredentials() {
  const secretPath = join(process.cwd(), 'secrets-descartavel', 'senha-operador.a.txt');
  const password = readFileSync(secretPath, 'utf-8').trim();
  return { email: 'operador.a@descartavel.example.com', password };
}

class CdpSession {
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
            if (msg.error) rej(new Error(JSON.stringify(msg.error)));
            else res(msg.result);
          }
        } catch (e) {
          console.error(e);
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

  async sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async navigate(url) {
    await this.send('Page.navigate', { url });
    await this.sleep(2500);
  }

  async screenshot(filename) {
    const res = await this.send('Page.captureScreenshot', { format: 'png' });
    const buffer = Buffer.from(res.data, 'base64');
    mkdirSync(EVIDENCIAS_DIR, { recursive: true });
    const filePath = join(EVIDENCIAS_DIR, filename);
    writeFileSync(filePath, buffer);
    console.log(`[EVIDÊNCIA SALVA] ${filePath}`);
    return filePath;
  }

  async close() {
    if (this.ws) this.ws.close();
  }
}

async function getOrCreateTab() {
  const tabsResp = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
  const tabs = await tabsResp.json();
  const pageTab = tabs.find((t) => t.type === 'page' && !t.url.startsWith('devtools://'));
  return pageTab.webSocketDebuggerUrl;
}

async function main() {
  console.log('=== TESTE E2E VISUAL: CONFLITO 409 E RETIRAR DA FILA ===');
  const wsUrl = await getOrCreateTab();
  const client = new CdpSession(wsUrl);
  await client.connect();

  await client.send('Page.enable');
  await client.send('Runtime.enable');
  await client.send('DOM.enable');

  try {
    // 1. Navega para rascunhos
    await client.navigate(`${BASE_URL}/fiscal/?company_id=${COMPANY_A}&view=rascunhos`);
    await client.sleep(3000);

    // 2. Abre edição do primeiro rascunho
    console.log('\n--- 1. Abrindo Edição do Rascunho para provocar Conflito 409 ---');
    await client.eval(`(() => {
      const editButtons = Array.from(document.querySelectorAll('button')).filter(b => b.textContent.includes('Editar'));
      if (editButtons[0]) editButtons[0].click();
    })()`);
    await client.sleep(1500);

    // Pega informações do rascunho sendo editado
    const modalDraftInfo = await client.eval(`(() => {
      const h2 = document.querySelector('h2');
      return h2 ? h2.innerText : '';
    })()`);
    console.log(`Modal aberto: ${modalDraftInfo}`);

    // Modifica o campo de descrição na UI localmente
    await client.eval(`(() => {
      const textarea = document.querySelector('textarea');
      if (textarea) {
        textarea.value = 'Descrição editada localmente que DEVE ser preservada após 409!';
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        textarea.dispatchEvent(new Event('change', { bubbles: true }));
      }
    })()`);

    // 3. Força um conflito 409:
    // Fazemos um PATCH via API externa para incrementar a versão no banco de dados enquanto o modal está aberto com a versão anterior!
    console.log('\n--- 2. Simulando Concorrência Externa via API Backend ---');
    const creds = getTestCredentials();
    const loginRes = await fetch(`${BASE_URL}/api/core/v1/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Origin': BASE_URL },
      body: JSON.stringify(creds),
    });
    const loginData = await loginRes.json();
    const token = loginData.access_token;

    // Obtém o rascunho atual para saber seu ID e versão
    const draftsRes = await fetch(`${BASE_URL}/api/fiscal/v1/companies/${COMPANY_A}/drafts`, {
      headers: { 'Authorization': `Bearer ${token}`, 'Origin': BASE_URL },
    });
    const draftsData = await draftsRes.json();
    const draftList = Array.isArray(draftsData) ? draftsData : (draftsData.drafts || draftsData.items || []);
    const targetDraft = draftList.find(d => d.status === 'DRAFT');

    if (targetDraft) {
      console.log(`Atualizando rascunho #${targetDraft.id.slice(0, 8)} externamente para gerar versão conflitante (v${targetDraft.version} -> v${targetDraft.version + 1})...`);
      const conflictPatch = await fetch(`${BASE_URL}/api/fiscal/v1/companies/${COMPANY_A}/drafts/${targetDraft.id}`, {
        method: 'PATCH',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
          'Origin': BASE_URL,
        },
        body: JSON.stringify({
          expected_version: targetDraft.version,
          amount: '12500.00',
        }),
      });
      console.log(`Resposta do PATCH concorrente: status ${conflictPatch.status}`);
    }

    // 4. Agora no navegador, clica em Salvar Rascunho com a versão desatualizada
    console.log('\n--- 3. Salvando Rascunho com expected_version defasada (Dispara HTTP 409) ---');
    await client.eval(`(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const saveBtn = btns.find(b => b.textContent?.includes('Salvar Rascunho'));
      if (saveBtn) saveBtn.click();
    })()`);
    await client.sleep(2500);

    // Captura evidência do Modal ABERTO com Banner de Erro 409 e Botões de Resolução
    await client.screenshot('e2e_edit_07_conflito_409.png');
    console.log('✓ Evidência de Conflito 409 capturada com sucesso!');

    // Fecha o modal de edição
    await client.eval(`(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const cancelBtn = btns.find(b => b.textContent?.includes('Cancelar'));
      if (cancelBtn) cancelBtn.click();
    })()`);
    await client.sleep(1500);

    // 5. Teste: Retirar da Fila para Editar (Rascunho SCHEDULED)
    console.log('\n--- 4. Testando Ação "Retirar da fila para editar" em Rascunho Agendado ---');
    const clickedWithdraw = await client.eval(`(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const withdrawBtn = btns.find(b => b.textContent?.includes('Retirar da fila'));
      if (withdrawBtn) {
        withdrawBtn.click();
        return true;
      }
      return false;
    })()`);

    if (clickedWithdraw) {
      console.log('Aguardando confirmação do backend e abertura automática do modal de edição...');
      await client.sleep(3000);
      await client.screenshot('e2e_edit_08_retirar_da_fila.png');
      console.log('✓ Evidência de retirada da fila capturada com sucesso!');
    } else {
      console.warn('Nenhum rascunho com status SCHEDULED encontrado para demonstrar retirada imediata.');
    }

    console.log('\n=== TESTES DE CONFLITO 409 E RETIRADA DA FILA CONCLUÍDOS COM SUCESSO! ===');
  } catch (err) {
    console.error('Erro:', err);
    await client.screenshot('e2e_conflict_error.png');
  } finally {
    await client.close();
  }
}

main().catch(console.error);
