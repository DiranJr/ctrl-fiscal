"""Validação em NAVEGADOR (Playwright) no ambiente DESCARTÁVEL (8098), com as contas fictícias.

1. Desktop/tablet/celular: logomarca no login e no cabeçalho do painel, telas sem rolagem horizontal e
   NENHUMA violação de Content-Security-Policy no console.
2. Cancelamento pela interface (desktop): painel da nota emitida → diálogo com empresa/ambiente/chave → motivo,
   justificativa, confirmação e número → envio → evento SIMULADO registrado (nota CANCELADA). Celular: diálogo
   aberto e conferido sem rolagem horizontal (sem enviar).
3. PDF do DANFSe dentro do sistema no Microsoft Edge (visualizador nativo) sob a nova CSP (frame-src blob:).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_validacao_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "secrets-descartavel"
FIX = json.loads((SEC / "fixtures.json").read_text(encoding="utf-8"))
B = FIX["base"]
A = FIX["companies"]["A"]
EV = ROOT / "docs" / "evidencias"
EMAIL = "operador.a@descartavel.example.com"
PW = (SEC / "senha-operador.a.txt").read_text(encoding="utf-8").strip()
# Mesmo usuário em dois workspaces: admin/operador em A, só leitura em B (scripts/ambiente_descartavel.py).
MULTI = "multi.ab@descartavel.example.com"
PW_MULTI = (SEC / "senha-multi.ab.txt").read_text(encoding="utf-8").strip()
B_ID = FIX["companies"]["B"]
VIEWPORTS = (({"width": 1366, "height": 800}, "desktop"), ({"width": 820, "height": 1180}, "tablet"),
             ({"width": 390, "height": 844}, "celular"))
results: list[dict] = []


def check(label: str, cond: bool, info: str = "") -> None:
    results.append({"teste": label, "resultado": "PASS" if cond else "FAIL", "obtido": info[:200]})
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {info}".rstrip(), flush=True)


# ------------------------------------------------------------------ preparação pela API
def api() -> httpx.Client:
    c = httpx.Client(base_url=B, timeout=60, headers={"Origin": B})
    for _ in range(20):
        r = c.post("/api/core/v1/auth/login", json={"email": EMAIL, "password": PW})
        if r.status_code != 429:
            break
        time.sleep(7)
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def emitir(c: httpx.Client, amount: str) -> dict:
    cust = c.get(f"/api/core/v1/companies/{A}/customers").json()["items"][0]["id"]
    d = c.post(f"/api/fiscal/v1/companies/{A}/drafts", json={
        "customer_id": cust, "competence": datetime.now().strftime("%Y-%m"), "amount": amount,
        "service": {"national_code": "010101", "description": "Serviço para validação da interface (descartável)"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}}).json()
    b = c.post(f"/api/fiscal/v1/companies/{A}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": d["version"]}],
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat(), "timezone": "America/Sao_Paulo"}).json()
    for _ in range(60):
        it = c.get(f"/api/fiscal/v1/companies/{A}/batches/{b['batch_id']}").json()["items"][0]
        if it["status"] == "AUTHORIZED":
            return c.get(f"/api/fiscal/v1/companies/{A}/documents/{it['document_id']}").json()
        time.sleep(3)
    raise RuntimeError("nota simulada não autorizada a tempo")


def sincronizar(c: httpx.Client) -> int:
    c.post(f"/api/fiscal/v1/companies/{A}/sync/run", headers={"Idempotency-Key": uuid.uuid4().hex}, params={"environment": "mock"})
    for _ in range(40):
        n = c.get(f"/api/fiscal/v1/companies/{A}/documents", params={"direction": "RECEIVED", "environment": "mock"}).json()["total"]
        if n:
            return n
        time.sleep(3)
    return 0


# ------------------------------------------------------------------ navegador
def login(page, email: str = EMAIL, pw: str = PW) -> None:  # noqa: ANN001
    page.goto(f"{B}/login", wait_until="networkidle")
    page.wait_for_timeout(500)
    page.fill("#inputEmail", email)
    page.fill("#inputPassword", pw)
    page.click("#btnSubmitLogin")
    page.wait_for_url(re.compile(r"/(select-company|fiscal/)"), timeout=60000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(4000)  # rotação do refresh na seleção de empresa (ver ui_verificacao_fechamento.py)


def csp_violations(msgs: list[str]) -> list[str]:
    return [m for m in msgs if "Content Security Policy" in m or "Refused to" in m]


def telas(pwr) -> None:  # noqa: ANN001
    for vp, tag in VIEWPORTS:
        b = pwr.chromium.launch()
        page = b.new_page(viewport=vp)
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(m.text))
        page.goto(f"{B}/login", wait_until="networkidle")
        logo = page.locator("img[alt='CTRL Fiscal']").first
        ok = logo.is_visible() and page.evaluate("(i) => i.naturalWidth > 0", logo.element_handle())
        page.screenshot(path=str(EV / f"ui-desc-{tag}-login-logo.png"))
        check(f"{tag}: logomarca no login", ok)
        login(page)
        for view in ("dashboard", "emitidas", "recebidas", "configuracoes"):
            page.goto(f"{B}/fiscal/?view={view}")
            page.locator("main h1").first.wait_for(timeout=30000)
            page.wait_for_timeout(1500)
            sel, other = (".pf-brand-logo", ".pf-brand-mark") if tag != "celular" else (".pf-brand-mark", ".pf-brand-logo")
            brand = page.locator(sel)
            ok_logo = (brand.is_visible() and page.evaluate("(i) => i.naturalWidth > 0", brand.element_handle())
                       and not page.locator(other).is_visible())  # só UMA versão da marca por tamanho de tela
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check(f"{tag} {view}: logomarca no cabeçalho e sem rolagem horizontal", ok_logo and overflow <= 1,
                  f"excesso={overflow}px")
            page.screenshot(path=str(EV / f"ui-desc-{tag}-{view}.png"))
        v = csp_violations(msgs)
        check(f"{tag}: nenhuma violação de CSP no console", not v, "; ".join(v)[:200])
        b.close()


def cancelamento(pwr, doc_desktop: dict, doc_mobile: dict) -> None:  # noqa: ANN001
    for (vp, tag), doc, enviar in ((VIEWPORTS[0], doc_desktop, True), (VIEWPORTS[2], doc_mobile, False)):
        b = pwr.chromium.launch()
        page = b.new_page(viewport=vp)
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(m.text))
        login(page)
        page.goto(f"{B}/fiscal/?view=emitidas&doc={doc['id']}")
        page.get_by_role("button", name=re.compile("Cancelar NFS-e")).wait_for(timeout=30000)
        page.screenshot(path=str(EV / f"ui-desc-{tag}-cancelamento-painel.png"))
        page.get_by_role("button", name=re.compile("Cancelar NFS-e")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Cancelar NFS-e"))
        dlg.wait_for(timeout=15000)
        text = dlg.inner_text()
        check(f"{tag}: diálogo identifica empresa, ambiente, número e chave",
              doc["access_key"] in text and (doc.get("number") or "") in text and "Simulação" in text,
              f"chave={'ok' if doc['access_key'] in text else 'ausente'}")
        send = dlg.get_by_role("button", name=re.compile("Enviar pedido"))
        check(f"{tag}: envio bloqueado antes de preencher e confirmar", send.is_disabled())
        dlg.get_by_label(re.compile("Motivo")).select_option("2")
        dlg.get_by_label(re.compile("Justificativa")).fill("Servico nao prestado: validacao da interface no descartavel")
        dlg.get_by_role("checkbox").check()
        dlg.get_by_label(re.compile("Digite o número")).fill(doc.get("number") or "")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / f"ui-desc-{tag}-cancelamento-dialogo.png"), full_page=False)
        check(f"{tag}: diálogo sem rolagem horizontal e envio liberado após confirmar", overflow <= 1 and send.is_enabled(),
              f"excesso={overflow}px")
        if enviar:
            send.click()
            page.get_by_text(re.compile("Pedido em andamento|Evento registrado")).first.wait_for(timeout=30000)
            reg = False
            for _ in range(30):
                page.wait_for_timeout(4000)
                if page.get_by_text("Evento registrado").count():
                    reg = True
                    break
            page.screenshot(path=str(EV / f"ui-desc-{tag}-cancelamento-registrado.png"))
            situacao = page.locator(".pf-dl").first.inner_text()
            check(f"{tag}: pedido enviado pela interface e evento SIMULADO registrado (nota cancelada)",
                  reg and ("Cancel" in situacao or "CANCEL" in situacao), situacao[:120].replace("\n", " "))
        else:
            dlg.get_by_role("button", name=re.compile("Voltar sem enviar")).click()
        v = csp_violations(msgs)
        check(f"{tag}: cancelamento sem violação de CSP", not v, "; ".join(v)[:200])
        b.close()


def pdf_edge(pwr) -> None:  # noqa: ANN001
    b = pwr.chromium.launch(channel="msedge", headless=False, args=["--window-position=0,0"])
    page = b.new_page(viewport={"width": 1366, "height": 860})
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(m.text))
    login(page)
    page.goto(f"{B}/fiscal/?view=emitidas&env=mock")
    page.wait_for_selector("table.ui-table tbody tr[data-clickable]", timeout=30000)
    rows = page.locator("table.ui-table tbody tr[data-clickable]")
    opened = False
    for i in range(rows.count()):
        rows.nth(i).click()
        page.wait_for_selector("[role=dialog]", timeout=10000)
        btn = page.get_by_role("button", name=re.compile("Visualizar PDF"))
        if btn.is_enabled():
            btn.click()
            frame = page.locator("iframe[title^='DANFSe']")
            frame.wait_for(timeout=30000)
            page.wait_for_timeout(4000)
            page.screenshot(path=str(EV / "ui-desc-pdf-edge-csp.png"), clip=frame.bounding_box())
            opened = (frame.get_attribute("src") or "").startswith("blob:")
            break
        page.keyboard.press("Escape")
    v = csp_violations(msgs)
    check("PDF do DANFSe no visualizador do Edge sob a CSP (frame-src blob:)", opened and not v,
          ("sem nota com DANFSe" if not opened else "") + "; ".join(v)[:160])
    b.close()


# ------------------------------------------------------------------ "Usar como referência" (correção da NFS-e 187)
def _cnpj(base12: str) -> str:
    def dv(s: str, pesos: list[int]) -> str:
        r = sum(int(a) * b for a, b in zip(s, pesos)) % 11
        return "0" if r < 2 else str(11 - r)
    d1 = dv(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + d1 + dv(base12 + d1, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])


def _xml_como_187(prest: str, toma: str, numero: int) -> bytes:
    """Mesma estrutura da NFS-e 187 (Emissor Web, tomador com endereço nacional), valores fictícios."""
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT / "services" / "nfse-api"))
    from fixtures_nfse import nota_como_187  # noqa: PLC0415

    return nota_como_187(prest, toma, numero)


def referencia(pwr, c: httpx.Client) -> None:  # noqa: ANN001
    prest = next(x for x in c.get("/api/core/v1/companies").json()["items"] if x["id"] == A)["cnpj"]
    numero = 18700 + int(time.time()) % 1000
    toma = _cnpj(f"5566{numero:08d}")
    r = c.post(f"/api/fiscal/v1/companies/{A}/documents/import", files=[("files", (f"n{numero}.xml", _xml_como_187(prest, toma, numero), "application/xml"))])
    r.raise_for_status()
    doc = None
    for _ in range(40):
        items = c.get(f"/api/fiscal/v1/companies/{A}/documents", params={"direction": "ISSUED", "environment": "mock", "page_size": 100}).json()["items"]
        doc = next((d for d in items if d["number"] == str(numero)), None)
        if doc:
            break
        time.sleep(2)
    check("preparo: nota com a estrutura da 187 importada (tomador sem cadastro)", doc is not None)
    b = pwr.chromium.launch()
    page = b.new_page(viewport=VIEWPORTS[0][0])
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(m.text))
    login(page)
    page.goto(f"{B}/fiscal/?view=emitidas&doc={doc['id']}")
    btn = page.get_by_role("button", name=re.compile("Usar como referência"))
    btn.wait_for(timeout=30000)
    btn.click()
    dlg = page.get_by_role("dialog", name=re.compile("Tomador ainda não cadastrado"))
    dlg.wait_for(timeout=20000)
    page.screenshot(path=str(EV / "ui-ref-1-tomador-do-xml.png"))
    check("referência: tomador lido do XML exibido para conferência", toma in dlg.inner_text())
    dlg.get_by_role("button", name=re.compile("Cadastrar tomador e criar rascunho")).click()
    editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
    editor.wait_for(timeout=30000)
    page.screenshot(path=str(EV / "ui-ref-2-rascunho-aberto.png"))
    check("referência: rascunho novo criado e aberto direto no editor", editor.is_visible())
    editor.get_by_label(re.compile("Data Fiscal do Fato Gerador")).fill("2026-10-09")
    editor.get_by_role("button", name=re.compile("Salvar Rascunho")).click()
    page.wait_for_timeout(3000)
    page.screenshot(path=str(EV / "ui-ref-3-salvo.png"))
    drafts = [d for d in c.get(f"/api/fiscal/v1/companies/{A}/drafts", params={"page_size": 100}).json()["items"]
              if d.get("source_document_id") == doc["id"]]
    check("referência: edição salva (versão 2, data informada) e um único rascunho para a nota",
          len(drafts) == 1 and drafts[0]["version"] >= 2 and drafts[0].get("service_date") == "2026-10-09",
          f"rascunhos={len(drafts)} versão={drafts[0]['version'] if drafts else '-'}")
    # Reabre o rascunho salvo e confere o IBS/CBS no editor (sem salvar).
    page.on("dialog", lambda dlg_: dlg_.accept())
    page.goto(f"{B}/fiscal/?view=rascunhos&draft={drafts[0]['id']}")
    editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
    editor.wait_for(timeout=30000)
    # IBS/CBS no editor: sem valor padrão; cClassTrib da tabela oficial pela data; CST derivado; prévia com vigência.
    editor.get_by_role("button", name=re.compile("Reforma IBS")).first.click()
    habilitar = editor.get_by_label(re.compile("Habilitar Grupo IBS"))
    ibs_ok = False
    if habilitar.count():
        habilitar.check()
        cls = editor.get_by_label(re.compile(r"Classificação tributária \(cClassTrib\)"))
        cls.wait_for(timeout=15000)
        page.wait_for_timeout(1500)
        opcoes = cls.locator("option").count()
        cst_vazio = editor.get_by_label(re.compile("CST")).input_value() == ""
        cls.select_option("000001")
        cst = editor.get_by_label(re.compile("CST")).input_value()
        card = page.get_by_role("heading", name=re.compile("CBS — prévia"))
        ibs_ok = opcoes > 10 and cst_vazio and cst == "000" and card.count() > 0
        page.screenshot(path=str(EV / "ui-ref-4-ibscbs-editor.png"))
    check("IBS/CBS no editor: sem padrão inventado, tabela oficial pela data, CST derivado e prévia visível", ibs_ok)
    editor.get_by_role("button", name=re.compile("Cancelar")).first.click()
    # Clique duplo rápido numa nota cujo tomador já está cadastrado: um único rascunho.
    page.goto(f"{B}/fiscal/?view=emitidas&doc={doc['id']}")
    btn = page.get_by_role("button", name=re.compile("Usar como referência"))
    btn.wait_for(timeout=30000)
    btn.dblclick()
    page.get_by_role("dialog", name=re.compile("Editar Rascunho")).wait_for(timeout=30000)
    drafts = [d for d in c.get(f"/api/fiscal/v1/companies/{A}/drafts", params={"page_size": 100}).json()["items"]
              if d.get("source_document_id") == doc["id"]]
    check("referência: clique duplo cria um único rascunho novo", len(drafts) == 2, f"rascunhos={len(drafts)}")
    nota = c.get(f"/api/fiscal/v1/companies/{A}/documents/{doc['id']}").json()
    check("referência: nota original inalterada", nota["number"] == doc["number"] and nota["status"] == doc["status"]
          and nota["access_key"] == doc["access_key"])
    v = csp_violations(msgs)
    check("referência: sem violação de CSP", not v, "; ".join(v)[:160])
    b.close()


# ------------------------------------------------------------------ workspaces
def workspaces(pwr) -> None:  # noqa: ANN001
    """Um workspace: entra direto e só identifica (sem seletor). Dois: seletor; troca com formulário aberto pede
    confirmação, descarta o formulário e respostas atrasadas do workspace anterior são ignoradas."""
    b = pwr.chromium.launch()
    page = b.new_page(viewport=VIEWPORTS[0][0])
    msgs: list[str] = []
    page.on("console", lambda m: msgs.append(m.text))
    login(page)
    page.goto(f"{B}/fiscal/?view=dashboard")
    page.locator(".pf-workspace").first.wait_for(timeout=30000)
    try:  # a situação do certificado chega depois da identificação
        page.wait_for_function("() => /ertificado/.test(document.querySelector('.pf-workspace').innerText)", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    ident = page.locator(".pf-workspace").first.inner_text()
    page.screenshot(path=str(EV / "ui-ws-1-um-workspace.png"))
    check("workspace único: entra direto, identifica empresa/CNPJ/certificado e não mostra seletor",
          "/fiscal/" in page.url and page.locator("#pf-company-select").count() == 0 and "CNPJ" in ident
          and "certificado" in ident.lower(), ident.replace("\n", " ")[:120])
    b.close()

    b = pwr.chromium.launch()
    page = b.new_page(viewport=VIEWPORTS[0][0])
    page.on("console", lambda m: msgs.append(m.text))
    login(page, MULTI, PW_MULTI)
    if "/select-company" in page.url:
        page.get_by_text(re.compile("A \\(LOCAL\\)")).first.click()
        page.wait_for_url(re.compile("/fiscal/"), timeout=30000)
    page.goto(f"{B}/fiscal/?view=nova-nfse&company_id={A}")
    sel = page.locator("#pf-company-select")
    sel.wait_for(timeout=30000)
    check("dois workspaces: seletor visível", sel.is_visible())
    campo = page.locator("main textarea").first
    campo.wait_for(timeout=30000)
    marca = "Texto digitado no workspace A " + uuid.uuid4().hex[:6]
    campo.fill(marca)
    respostas: list[str] = []
    page.once("dialog", lambda d: (respostas.append(d.message), d.dismiss()))
    sel.select_option(B_ID)
    page.wait_for_timeout(1500)
    ficou = sel.input_value() == A and page.locator("main textarea").first.input_value() == marca
    check("troca com formulário aberto pede confirmação; ao recusar, permanece no workspace e mantém o texto",
          bool(respostas) and ficou, (respostas[0] if respostas else "sem confirmação")[:80])
    page.once("dialog", lambda d: d.accept())
    sel.select_option(B_ID)
    page.wait_for_timeout(3000)
    limpo = marca not in page.locator("main").inner_text() and all(
        page.locator("main textarea").nth(i).input_value() != marca for i in range(page.locator("main textarea").count()))
    page.screenshot(path=str(EV / "ui-ws-2-troca-workspace.png"))
    check("ao confirmar: workspace B ativo, URL atualizada e formulário de A descartado",
          sel.input_value() == B_ID and f"company_id={B_ID}" in page.url and limpo, page.url[-60:])
    # Resposta atrasada: o painel de A demora; o usuário volta para B antes; nada de A aparece depois.
    page.goto(f"{B}/fiscal/?view=dashboard&company_id={B_ID}")
    page.locator("#pf-company-select").wait_for(timeout=30000)

    def lento(route):  # noqa: ANN001, ANN202
        time.sleep(4)
        route.continue_()

    page.route(f"**/api/fiscal/v1/companies/{A}/**", lento)
    page.locator("#pf-company-select").select_option(A)
    page.wait_for_timeout(300)
    page.locator("#pf-company-select").select_option(B_ID)
    page.wait_for_timeout(9000)
    toasts = page.locator(".pf-toast").all_inner_texts()
    nome_a = "A (LOCAL)"
    check("resposta atrasada do workspace anterior é ignorada (fica em B, sem aviso de A)",
          page.locator("#pf-company-select").input_value() == B_ID and not any(nome_a in x for x in toasts),
          "; ".join(toasts)[:120])
    page.unroute(f"**/api/fiscal/v1/companies/{A}/**")
    # Só leitura em B: Configurações não oferece cadastro de perfis; em A (admin) oferece.
    page.goto(f"{B}/fiscal/?view=configuracoes&company_id={B_ID}")
    page.get_by_role("heading", name=re.compile("Perfis de serviço")).wait_for(timeout=30000)
    sem = page.get_by_role("button", name=re.compile("Novo perfil")).count() == 0
    page.goto(f"{B}/fiscal/?view=configuracoes&company_id={A}")
    page.get_by_role("heading", name=re.compile("Perfis de serviço")).wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    com = page.get_by_role("button", name=re.compile("Novo perfil")).count() == 1
    check("permissões por workspace na interface: admin em A cadastra perfis; só leitura em B não", sem and com)
    v = csp_violations(msgs)
    check("workspaces: sem violação de CSP", not v, "; ".join(v)[:160])
    b.close()


def perfis(pwr) -> None:  # noqa: ANN001
    """Tela de revisão dos perfis de serviço (preparados pelo validar_descartavel.py a partir de notas fictícias)
    e aplicação de perfil pendente na Nova NFS-e."""
    for vp, tag in VIEWPORTS:
        b = pwr.chromium.launch()
        page = b.new_page(viewport=vp)
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(m.text))
        login(page, MULTI, PW_MULTI)
        page.goto(f"{B}/fiscal/?view=configuracoes&company_id={A}")
        sec = page.locator("section", has=page.get_by_role("heading", name=re.compile("^Perfis de serviço")))
        sec.first.wait_for(timeout=30000)
        page.locator(".pf-perfil").first.wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        cards = page.locator(".pf-perfil").count()
        texto = sec.first.inner_text()
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        sec.first.screenshot(path=str(EV / f"ui-perfis-{tag}-lista.png"))
        check(f"{tag}: revisão de perfis mostra situação, origem dos dados e pendências, sem rolagem horizontal",
              cards >= 2 and "Pendente de revisão" in texto and "XML oficial" in texto and "Falta para aprovar" in texto
              and overflow <= 1, f"perfis={cards} excesso={overflow}px")
        if tag == "desktop":
            card = page.locator(".pf-perfil[data-status='PENDING_REVIEW']").first
            card.locator("summary", has_text="Notas de referência").click()
            page.wait_for_timeout(500)
            card.screenshot(path=str(EV / "ui-perfis-desktop-evidencias.png"))
            card.get_by_role("button", name=re.compile("Revisar e editar")).click()
            dlg = page.get_by_role("dialog", name=re.compile("Revisar perfil"))
            dlg.wait_for(timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(EV / "ui-perfis-desktop-edicao.png"))
            check("edição do perfil abre com códigos e classificação IBS/CBS da tabela oficial",
                  dlg.get_by_label(re.compile(r"Classificação \(cClassTrib\)")).locator("option").count() > 10)
            dlg.get_by_role("button", name=re.compile("Cancelar")).click()
            aprovavel = page.locator(".pf-perfil[data-status='PENDING_REVIEW']").filter(
                has=page.get_by_role("button", name=re.compile("^Aprovar$"))).filter(
                has=page.locator("button:not([disabled])", has_text=re.compile("^Aprovar$")))
            if aprovavel.count():
                aprovavel.first.get_by_role("button", name=re.compile("^Aprovar$")).click()
                dlg = page.get_by_role("dialog", name=re.compile("Aprovar perfil"))
                dlg.wait_for(timeout=15000)
                botao = dlg.get_by_role("button", name=re.compile("Aprovar perfil"))
                bloqueado = botao.is_disabled()
                page.screenshot(path=str(EV / "ui-perfis-desktop-aprovacao.png"))
                check("aprovação exige a confirmação explícita (botão desabilitado antes de marcar)", bloqueado)
                dlg.get_by_role("button", name=re.compile("Cancelar")).click()
            page.locator(".pf-perfil").first.get_by_role("button", name=re.compile("Histórico")).click()
            dlg = page.get_by_role("dialog", name=re.compile("Histórico"))
            dlg.wait_for(timeout=15000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(EV / "ui-perfis-desktop-historico.png"))
            check("histórico do perfil com versões, ação e responsável", "v1" in dlg.inner_text())
            page.keyboard.press("Escape")
            # Nova NFS-e: perfil pendente pode ser aplicado (prepara), com aviso de que o agendamento depende da aprovação.
            page.goto(f"{B}/fiscal/?view=nova-nfse&company_id={A}")
            sel = page.get_by_label(re.compile("Aplicar perfil de serviço"))
            sel.wait_for(timeout=30000)
            opcoes = [o for o in sel.locator("option").all_inner_texts() if "pendente de revisão" in o]
            if opcoes:
                sel.select_option(label=opcoes[0])
                page.wait_for_timeout(800)
            aviso = page.get_by_text(re.compile("agendamento só é liberado depois da aprovação"))
            page.screenshot(path=str(EV / "ui-perfis-desktop-nova-nfse.png"))
            check("Nova NFS-e aplica perfil pendente e avisa que o agendamento depende da aprovação",
                  bool(opcoes) and aviso.count() > 0, f"opções pendentes={len(opcoes)}")
        v = csp_violations(msgs)
        check(f"{tag}: perfis sem violação de CSP", not v, "; ".join(v)[:160])
        b.close()


def formularios(pwr) -> None:  # noqa: ANN001
    """Capturas dos formulários padronizados (Nova NFS-e, Configurações com prontidão e perfis) nos 3 tamanhos,
    sem rolagem horizontal."""
    for vp, tag in VIEWPORTS:
        b = pwr.chromium.launch()
        page = b.new_page(viewport=vp)
        login(page, MULTI, PW_MULTI)
        for view, nome in (("nova-nfse", "nova-nfse"), ("configuracoes", "configuracoes")):
            page.goto(f"{B}/fiscal/?view={view}&company_id={A}")
            page.locator("main h1").first.wait_for(timeout=30000)
            page.wait_for_timeout(2500)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            page.screenshot(path=str(EV / f"ui-form-{tag}-{nome}.png"), full_page=True)
            check(f"{tag} {nome}: formulário sem rolagem horizontal", overflow <= 1, f"excesso={overflow}px")
        if tag == "desktop":
            page.get_by_role("button", name=re.compile("Novo perfil")).click()
            dlg = page.get_by_role("dialog", name=re.compile("Novo perfil de serviço"))
            dlg.wait_for(timeout=15000)
            page.wait_for_timeout(1500)
            page.screenshot(path=str(EV / "ui-form-desktop-perfil-servico.png"))
            dlg.get_by_role("button", name=re.compile("Salvar perfil")).click()
            page.wait_for_timeout(800)
            check("perfil de serviço: validação local sem salvar dados incompletos",
                  dlg.is_visible() and "Informe um nome" in dlg.inner_text())
            dlg.get_by_role("button", name=re.compile("Cancelar")).click()
        b.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # textos da interface com símbolos (console cp1252)
    c = api()
    recebidas = sincronizar(c)
    check("preparo: sincronização simulada trouxe notas recebidas (com XML)", recebidas > 0, f"{recebidas}")
    d1, d2 = emitir(c, "210.00"), emitir(c, "220.00")
    with sync_playwright() as pwr:
        telas(pwr)
        cancelamento(pwr, d1, d2)
        referencia(pwr, c)
        workspaces(pwr)
        perfis(pwr)
        formularios(pwr)
        pdf_edge(pwr)
    out = EV / f"ui-validacao-descartavel-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    fails = [r for r in results if r["resultado"] != "PASS"]
    out.write_text(json.dumps({"ambiente": B, "total": len(results), "falhas": len(fails), "resultados": results},
                              indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(results) - len(fails)}/{len(results)} PASS — {out.relative_to(ROOT)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
