"""Validação em NAVEGADOR (Playwright) de “Emitir agora” no ambiente DESCARTÁVEL (8098, governo simulado).

Fluxos (conta fictícia multi.ab: operador + administrador da empresa A):
1. Nota usada como REFERÊNCIA pela Nova NFS-e (“Usar Última Nota”): revisão → registrar e aprovar a configuração da
   operação → “Emitir agora” (clique duplo) → um único lote NOW, autorizado sem esperar o dia 01.
2. Nota criada DO ZERO (rascunho manual) pelo lote de Rascunhos: configuração registrada e aprovada no editor →
   “Emitir ou agendar” → confirmação DESATUALIZADA (rascunho alterado com o diálogo aberto) recusada pelo servidor →
   nova revisão → “Emitir agora” autorizado. Agendar o mesmo rascunho em seguida é recusado (409).
Nada é transmitido a ambiente oficial. Evidência: docs/evidencias/ui-emitir-agora-<data>.json (+ capturas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_emitir_agora_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)

B, A, EV = base.B, base.A, base.EV
NS = "http://www.sped.fazenda.gov.br/nfse"
MUN = "1505536"
results = base.results
check = base.check


def api() -> httpx.Client:
    c = httpx.Client(base_url=B, timeout=60, headers={"Origin": B})
    for _ in range(20):
        r = c.post("/api/core/v1/auth/login", json={"email": base.MULTI, "password": base.PW_MULTI})
        if r.status_code != 429:
            break
        time.sleep(7)
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def nota_xml(prest: str, toma: str, numero: int) -> bytes:
    """NFS-e fictícia emitida pela empresa A (tpEmit=1), códigos válidos e grupo IBS/CBS (estrutura do XSD v1.01)."""
    chave = MUN[:6] + prest + f"{numero:030d}"
    dps_id = "DPS" + MUN + "2" + prest + "00900" + str(numero).zfill(15)
    dh = "2026-10-08T13:46:54-03:00"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><NFSe xmlns="{NS}" versao="1.01"><infNFSe Id="NFS{chave}">'
        f"<xLocEmi>Parauapebas</xLocEmi><xLocPrestacao>Parauapebas</xLocPrestacao><nNFSe>{numero}</nNFSe>"
        f"<cLocIncid>{MUN}</cLocIncid><xLocIncid>Parauapebas</xLocIncid><xTribNac>Serviço (fictício)</xTribNac>"
        f"<verAplic>EmissorWeb_1.6.0.0</verAplic><ambGer>2</ambGer><tpEmis>1</tpEmis><procEmi>2</procEmi>"
        f"<cStat>100</cStat><dhProc>{dh}</dhProc><nDFSe>1234567</nDFSe>"
        f"<emit><CNPJ>{prest}</CNPJ><xNome>EMPRESA FICTICIA</xNome><enderNac><xLgr>Rua Ficticia</xLgr><nro>100</nro>"
        f"<xBairro>Centro</xBairro><cMun>{MUN}</cMun><UF>PA</UF><CEP>68515000</CEP></enderNac></emit>"
        f"<valores><vLiq>321.00</vLiq></valores>"
        f'<DPS versao="1.01"><infDPS Id="{dps_id}"><tpAmb>1</tpAmb><dhEmi>{dh}</dhEmi>'
        f"<verAplic>EmissorWeb_1.6.0.0</verAplic><serie>00900</serie><nDPS>{numero}</nDPS><dCompet>2026-10-08</dCompet>"
        f"<tpEmit>1</tpEmit><cLocEmi>{MUN}</cLocEmi>"
        f"<prest><CNPJ>{prest}</CNPJ><regTrib><opSimpNac>1</opSimpNac><regEspTrib>0</regEspTrib></regTrib></prest>"
        f"<toma><CNPJ>{toma}</CNPJ><xNome>TOMADOR EMITIR AGORA</xNome></toma>"
        f"<serv><locPrest><cLocPrestacao>{MUN}</cLocPrestacao></locPrest><cServ><cTribNac>071601</cTribNac>"
        f"<xDescServ>Servico de referencia para emitir agora (fixture)</xDescServ><cNBS>101032000</cNBS></cServ></serv>"
        f"<valores><vServPrest><vServ>321.00</vServ></vServPrest><trib><tribMun><tribISSQN>1</tribISSQN>"
        f"<tpRetISSQN>1</tpRetISSQN></tribMun><tribFed><piscofins><CST>01</CST><tpRetPisCofins>0</tpRetPisCofins>"
        f"</piscofins></tribFed><totTrib><indTotTrib>0</indTotTrib></totTrib></trib></valores>"
        f"<IBSCBS><finNFSe>0</finNFSe><indFinal>0</indFinal><cIndOp>100301</cIndOp><indDest>0</indDest><valores><trib>"
        f"<gIBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib></gIBSCBS></trib></valores></IBSCBS>"
        f"</infDPS></DPS></infNFSe></NFSe>"
    ).encode("utf-8")


def aguardar_item(c: httpx.Client, bid: str, alvo: str = "AUTHORIZED", tentativas: int = 40) -> dict:
    it: dict = {}
    for _ in range(tentativas):
        it = c.get(f"/api/fiscal/v1/companies/{A}/batches/{bid}").json()["items"][0]
        if it["status"] == alvo:
            return it
        time.sleep(1.5)
    return it


def itens_do_rascunho(c: httpx.Client, draft_id: str) -> list[tuple[str, dict]]:
    out = []
    for b in c.get(f"/api/fiscal/v1/companies/{A}/batches", params={"page_size": 100}).json()["items"]:
        det = c.get(f"/api/fiscal/v1/companies/{A}/batches/{b['id']}").json()
        out += [(b["mode"], i) for i in det["items"] if i["draft_id"] == draft_id]
    return out


def aprovar_no_painel(page, escopo) -> None:  # noqa: ANN001
    escopo.get_by_role("button", name=re.compile("Registrar (novamente a )?configuração desta operação")).click()
    aprovada = escopo.get_by_text(re.compile("Aprovada$"))
    conferi = escopo.get_by_text(re.compile("Conferi os códigos desta operação"))
    aprovada.or_(conferi).first.wait_for(timeout=30000)
    if conferi.count():  # configuração pendente: aprovação explícita (conta administradora)
        conferi.click()
        escopo.get_by_role("button", name=re.compile("^Aprovar configuração")).click()
    aprovada.wait_for(timeout=30000)  # pendente aprovada agora, ou aprovada equivalente reutilizada
    page.wait_for_timeout(800)


def referencia_nova_nfse(pwr, c: httpx.Client, prest: str) -> None:  # noqa: ANN001
    numero = 77000 + int(time.time()) % 1000
    toma = base._cnpj(f"7788{numero:08d}")
    cust = c.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Emitir Agora {numero}", "document_type": "CNPJ", "document": toma}).json()
    r = c.post(f"/api/fiscal/v1/companies/{A}/documents/import",
               files=[("files", (f"ea{numero}.xml", nota_xml(prest, toma, numero), "application/xml"))])
    r.raise_for_status()
    doc = None
    for _ in range(40):
        items = c.get(f"/api/fiscal/v1/companies/{A}/documents",
                      params={"direction": "ISSUED", "environment": "mock", "page_size": 100}).json()["items"]
        doc = next((d for d in items if d["number"] == str(numero)), None)
        if doc:
            break
        time.sleep(2)
    check("referência: nota emitida pela empresa A importada (XML oficial fictício)", doc is not None)
    b = pwr.chromium.launch()
    page = b.new_page(viewport={"width": 1366, "height": 900})
    base.login(page, base.MULTI, base.PW_MULTI)
    page.goto(f"{B}/fiscal/?view=nova-nfse&company_id={A}")
    page.get_by_label(re.compile(r"Cliente \(Tomador\)")).select_option(cust["id"])
    page.get_by_role("button", name=re.compile("Usar Última Nota")).click()
    page.get_by_text(re.compile(f"com base na Nota nº {numero}")).wait_for(timeout=30000)
    hoje = datetime.now().strftime("%Y-%m-%d")
    page.get_by_label(re.compile("Data Fiscal / Fato Gerador")).fill(hoje)
    page.get_by_role("button", name=re.compile("Revisar Dados da NFS-e")).click()
    painel = page.get_by_role("region", name="Configuração tributária da operação")
    painel.wait_for(timeout=30000)
    check("referência: painel mostra a origem (NFS-e do XML oficial)",
          painel.get_by_text(re.compile(f"NFS-e nº {numero}")).count() > 0)
    aprovar_no_painel(page, painel)
    check("referência: “Emitir agora” é a ação escolhida", page.get_by_label(re.compile("Emitir agora")).first.is_checked()
          if page.get_by_label(re.compile("Emitir agora")).count() else
          page.locator("input[name=emit-mode]").first.is_checked())
    page.get_by_text("Conferi os dados acima").click()
    botao = page.get_by_role("button", name=re.compile("^Emitir agora$"))
    page.screenshot(path=str(EV / "ui-emitir-agora-referencia-revisao.png"), full_page=True)
    botao.dblclick()  # clique duplicado
    page.wait_for_url(re.compile("view=agendamentos"), timeout=60000)
    rascunho = next(d for d in c.get(f"/api/fiscal/v1/companies/{A}/drafts", params={"page_size": 100}).json()["items"]
                    if d.get("source_document_id") == doc["id"])
    itens = itens_do_rascunho(c, rascunho["id"])
    check("referência: clique duplo gerou UM item, modo NOW", len(itens) == 1 and itens[0][0] == "NOW",
          f"{[(m, i['status']) for m, i in itens]}")
    fim = aguardar_item(c, next(b["id"] for b in c.get(f"/api/fiscal/v1/companies/{A}/batches").json()["items"]
                                if any(i["draft_id"] == rascunho["id"] for i in
                                       c.get(f"/api/fiscal/v1/companies/{A}/batches/{b['id']}").json()["items"])))
    check("referência: autorizada (simulada) sem esperar o dia 01", fim.get("status") == "AUTHORIZED", fim.get("status", ""))
    page.goto(f"{B}/fiscal/?view=agendamentos")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(EV / "ui-emitir-agora-referencia-resultado.png"), full_page=True)
    b.close()


def zero_pelo_lote(pwr, c: httpx.Client) -> None:  # noqa: ANN001
    cust = c.get(f"/api/core/v1/companies/{A}/customers").json()["items"][0]["id"]
    valor = f"{100 + int(time.time()) % 800}.37"
    d = c.post(f"/api/fiscal/v1/companies/{A}/drafts", json={
        "customer_id": cust, "competence": datetime.now().strftime("%Y-%m"),
        "service_date": datetime.now().strftime("%Y-%m-%d"), "amount": valor,
        "service": {"national_code": "071601", "nbs_code": "101032000", "location_code": MUN,
                    "description": "Serviço preenchido do zero (validação de emitir agora)"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01",
                         "approx_tax": {"mode": "NOT_INFORMED"},
                         "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}}}).json()
    check("do zero: rascunho manual criado (origem manual)", (d.get("origin") or {}).get("kind") == "manual")
    b = pwr.chromium.launch()
    page = b.new_page(viewport={"width": 1366, "height": 900})
    base.login(page, base.MULTI, base.PW_MULTI)
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}&draft={d['id']}")
    editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
    editor.wait_for(timeout=30000)
    editor.get_by_role("button", name=re.compile("Configuração da operação")).click()
    painel = editor.get_by_role("region", name="Configuração tributária da operação")
    check("do zero: painel indica preenchimento manual", painel.get_by_text(re.compile("preenchimento manual")).count() > 0)
    aprovar_no_painel(page, painel)
    editor.get_by_role("button", name=re.compile("Cancelar")).first.click()
    page.wait_for_timeout(1500)
    valor_br = valor.replace(".", ",")
    linha = page.locator("tr", has_text=valor_br)
    linha.locator("input[type=checkbox]").check()
    page.get_by_role("button", name=re.compile("Emitir ou agendar")).click()
    modal = page.get_by_role("dialog", name=re.compile("Emitir ou agendar"))
    modal.wait_for(timeout=30000)
    modal.get_by_text("Conferi os dados acima").click()
    # Confirmação DESATUALIZADA: o rascunho muda (outra aba/pessoa) com o diálogo aberto.
    atual = c.get(f"/api/fiscal/v1/companies/{A}/drafts/{d['id']}").json()
    c.patch(f"/api/fiscal/v1/companies/{A}/drafts/{d['id']}", json={
        "expected_version": atual["version"], "service": {"description": "Alterado com a confirmação aberta"}})
    modal.get_by_role("button", name=re.compile("^Emitir agora$")).click()
    page.wait_for_timeout(3000)
    check("do zero: confirmação desatualizada recusada (nada entrou na fila)", itens_do_rascunho(c, d["id"]) == [])
    page.screenshot(path=str(EV / "ui-emitir-agora-zero-desatualizada.png"), full_page=True)
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
    page.wait_for_timeout(2000)
    page.locator("tr", has_text=valor_br).locator("input[type=checkbox]").check()
    page.get_by_role("button", name=re.compile("Emitir ou agendar")).click()
    modal = page.get_by_role("dialog", name=re.compile("Emitir ou agendar"))
    modal.get_by_text("Conferi os dados acima").click()
    modal.get_by_role("button", name=re.compile("^Emitir agora$")).click()
    page.wait_for_url(re.compile("view=agendamentos"), timeout=60000)
    itens = itens_do_rascunho(c, d["id"])
    check("do zero: um item, modo NOW, após nova revisão", len(itens) == 1 and itens[0][0] == "NOW")
    bid = next(b["id"] for b in c.get(f"/api/fiscal/v1/companies/{A}/batches").json()["items"]
               if any(i["draft_id"] == d["id"] for i in c.get(f"/api/fiscal/v1/companies/{A}/batches/{b['id']}").json()["items"]))
    fim = aguardar_item(c, bid)
    check("do zero: autorizada (simulada) imediatamente", fim.get("status") == "AUTHORIZED", fim.get("status", ""))
    atual = c.get(f"/api/fiscal/v1/companies/{A}/drafts/{d['id']}").json()
    r = c.post(f"/api/fiscal/v1/companies/{A}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": atual["version"]}], "mode": "SCHEDULED",
        "scheduled_at": "2026-11-01T00:01:00-03:00", "timezone": "America/Sao_Paulo"})
    check("do zero: agendar o mesmo rascunho depois de emitido é recusado", r.status_code == 409, str(r.status_code))
    page.goto(f"{B}/fiscal/?view=agendamentos")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(EV / "ui-emitir-agora-zero-resultado.png"), full_page=True)
    b.close()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = api()
    prest = next(x for x in c.get("/api/core/v1/companies").json()["items"] if x["id"] == A)["cnpj"]
    with sync_playwright() as pwr:
        for nome, fn in (("referência", lambda: referencia_nova_nfse(pwr, c, prest)),
                         ("do zero", lambda: zero_pelo_lote(pwr, c))):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - registra e segue para o próximo fluxo
                check(f"{nome}: fluxo concluído sem erro de interface", False, f"{type(exc).__name__}: {exc}"[:200])
    out = EV / f"ui-emitir-agora-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "resultados": results}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
