"""Validação em NAVEGADOR (Playwright) da prestação futura (dCompet) x data de emissão — regra E0015 — no ambiente
DESCARTÁVEL (8098, governo simulado). Somente dados fictícios; nada é transmitido a governo real.
Cobre: salvar e reabrir rascunho com prestação futura (só aviso); "Emitir agora" bloqueado com explicação e sugestão;
agendamento anterior à prestação recusado; agendamento no dia da prestação aceito (horário no fuso da empresa); o
servidor recusa o mesmo pedido feito por fora do painel; Nova NFS-e com o mesmo conflito; celular.
Execução atrasada, dhEmi na execução e duplicidade: tests/test_prestacao_futura.py (relógio controlado).
Evidência: docs/evidencias/ui-prestacao-futura-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_prestacao_futura_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_emitir_agora_descartavel as EA  # noqa: E402  (aprovar_no_painel, nota_xml, MUN)
import ui_lotes_descartavel as L  # noqa: E402
import ui_validacao_descartavel as base  # noqa: E402

B, A, EV = base.B, base.A, base.EV
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
SP = ZoneInfo("America/Sao_Paulo")
HOJE = datetime.now(SP).date()
br = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731


def rascunho(c, cust: str, dia, valor: str) -> dict:  # noqa: ANN001
    r = c.post(f"{F}/drafts", json={
        "customer_id": cust, "competence": dia.strftime("%Y-%m"), "service_date": dia.isoformat(), "amount": valor,
        "service": {"national_code": "071601", "nbs_code": "101032000", "location_code": EA.MUN,
                    "description": "Serviço com prestação futura (validação fictícia)"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01",
                         "approx_tax": {"mode": "NOT_INFORMED"},
                         "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}}})
    r.raise_for_status()
    return r.json()


def lote(c, d: dict, **body):  # noqa: ANN001, ANN003, ANN201
    atual = c.get(f"{F}/drafts/{d['id']}").json()
    return c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": atual["version"]}], "timezone": "America/Sao_Paulo", **body})


def modal_lote(page, valor_br: str):  # noqa: ANN001, ANN201
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
    page.get_by_role("heading", name="Rascunhos de NFS-e").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.locator("tr", has_text=valor_br).locator("input[type=checkbox]").check()
    page.get_by_role("button", name=re.compile("Emitir ou agendar")).click()
    m = page.get_by_role("dialog", name=re.compile("Emitir ou agendar"))
    m.wait_for(timeout=30000)
    return m


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = L.cliente(base.MULTI, base.PW_MULTI)
    cust = c.get(f"/api/core/v1/companies/{A}/customers").json()["items"][0]["id"]
    n = int(time.time()) % 700
    futuro = HOJE + timedelta(days=3)
    valor = f"{100 + n}.41"
    valor_br = valor.replace(".", ",")
    d = rascunho(c, cust, HOJE + timedelta(days=2), valor)
    issues = c.post(f"{F}/drafts/{d['id']}/validate").json()["issues"]
    datas = [(i["code"], i["severity"]) for i in issues if i["field"] == "service_date"]
    check("rascunho com prestação futura: salvo; validação só AVISA (sem bloqueio E0015)",
          datas == [("SERVICE_DATE_FUTURE", "warning")], str(datas))

    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)

        # Editor: altera a prestação futura, salva e reabre; aprova a operação.
        page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}&draft={d['id']}")
        ed = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        ed.wait_for(timeout=30000)
        campo = ed.get_by_label("Data Fiscal do Fato Gerador (dCompet)")
        campo.fill(futuro.isoformat())
        if futuro.month != (HOJE + timedelta(days=2)).month:
            ed.get_by_label(re.compile("^Competência")).fill(futuro.strftime("%Y-%m"))
        ed.get_by_role("button", name="Salvar Rascunho").click()
        ed.wait_for(state="detached", timeout=20000)
        page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}&draft={d['id']}")
        ed = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        ed.wait_for(timeout=30000)
        check("editor: prestação futura salva e reaberta", ed.get_by_label("Data Fiscal do Fato Gerador (dCompet)").input_value()
              == futuro.isoformat())
        ed.get_by_role("button", name=re.compile("Configuração da operação")).click()
        EA.aprovar_no_painel(page, ed.get_by_role("region", name="Configuração tributária da operação"))
        ed.get_by_role("button", name=re.compile("Cancelar")).first.click()
        page.wait_for_timeout(1000)

        # Emitir agora: bloqueado, com explicação e sugestão.
        m = modal_lote(page, valor_br)
        alerta = m.get_by_role("alert").filter(has_text="Prestação posterior à emissão")
        m.get_by_text("Conferi os dados acima").click()
        emitir = m.get_by_role("button", name=re.compile("^Emitir agora$"))
        page.screenshot(path=str(EV / "ui-prestacao-futura-emitir-agora.png"), full_page=True)
        check("“Emitir agora”: conflito explicado (E0015) e botão desabilitado",
              alerta.count() == 1 and "não é possível emitir agora" in alerta.inner_text()
              and br(futuro) in alerta.inner_text() and emitir.is_disabled(), alerta.inner_text()[:150] if alerta.count() else "")
        check("lembrete junto ao botão explica o bloqueio", m.get_by_text("“Emitir agora” está bloqueado").count() == 1)
        m.get_by_role("button", name=f"Agendar para {br(futuro)} às 00:05").first.click()
        dt = m.get_by_label("Data e Hora do Início da Transmissão")
        check("sugestão: agenda no dia da prestação às 00:05 e o conflito some",
              dt.input_value() == f"{futuro.isoformat()}T00:05" and m.get_by_role("alert").filter(has_text="Prestação posterior à emissão").count() == 0, dt.input_value())
        dt.fill(f"{(futuro - timedelta(days=1)).isoformat()}T23:59")
        page.wait_for_timeout(300)
        confirmar = m.get_by_role("button", name="Confirmar Agendamento do Lote")
        t = m.get_by_role("alert").filter(has_text="Prestação posterior à emissão").inner_text() if m.get_by_role("alert").filter(has_text="Prestação posterior à emissão").count() else ""
        page.screenshot(path=str(EV / "ui-prestacao-futura-agendamento-anterior.png"), full_page=True)
        check("agendamento anterior à prestação: explica e pede ajuste (botão desabilitado)",
              "anterior à prestação" in t and f"{br(futuro)} ou depois" in t and confirmar.is_disabled(), t[:150])
        dt.fill(f"{futuro.isoformat()}T00:05")
        page.wait_for_timeout(300)
        confirmar.click()
        page.wait_for_url(re.compile("view=agendamentos"), timeout=60000)
        lotes = c.get(f"{F}/batches", params={"view": "in_progress"}).json()["items"]
        meu = next((x for x in lotes for i in c.get(f"{F}/batches/{x['id']}").json()["items"] if i["draft_id"] == d["id"]), None)
        quando = datetime.fromisoformat(meu["scheduled_at"].replace("Z", "+00:00")).astimezone(SP) if meu else None
        check("agendado no dia da prestação (00:05 no fuso da empresa)",
              quando is not None and quando.date() == futuro and quando.strftime("%H:%M") == "00:05", str(quando))

        # O servidor confere por conta própria (pedido feito fora do painel).
        d2 = rascunho(c, cust, futuro, f"{100 + n}.42")
        r_now = lote(c, d2, mode="NOW")
        r_ant = lote(c, d2, mode="SCHEDULED",
                     scheduled_at=datetime.combine(futuro - timedelta(days=1), datetime.min.time().replace(hour=23, minute=59),
                                                   tzinfo=SP).isoformat())
        e_now = [e for e in r_now.json().get("errors") or [] if e["code"] == "SERVICE_DATE_AFTER_EMISSION"]
        e_ant = [e for e in r_ant.json().get("errors") or [] if e["code"] == "SERVICE_DATE_AFTER_EMISSION"]
        check("servidor: “emitir agora” e agendamento anterior recusados (422, E0015, com as datas)",
              r_now.status_code == 422 and r_ant.status_code == 422 and e_now and e_ant
              and "E0015" in e_now[0]["message"] and br(futuro) in e_ant[0]["message"],
              f"{r_now.status_code}/{r_ant.status_code}")
        check("servidor: rascunho recusado continua editável (fora da fila)",
              c.get(f"{F}/drafts/{d2['id']}").json()["status"] == "DRAFT")

        # Nova NFS-e (a partir de nota de referência fictícia).
        prest = next(x for x in c.get("/api/core/v1/companies").json()["items"] if x["id"] == A)["cnpj"]
        numero = 79000 + n
        toma = base._cnpj(f"7799{numero:08d}")
        cust2 = c.post(f"/api/core/v1/companies/{A}/customers", json={
            "name": f"Tomador Prestação Futura {numero}", "document_type": "CNPJ", "document": toma}).json()
        c.post(f"{F}/documents/import", files=[("files", (f"pf{numero}.xml", EA.nota_xml(prest, toma, numero), "application/xml"))])
        for _ in range(40):
            if any(x["number"] == str(numero) for x in c.get(f"{F}/documents", params={"direction": "ISSUED", "page_size": 100}).json()["items"]):
                break
            time.sleep(2)
        page.goto(f"{B}/fiscal/?view=nova-nfse&company_id={A}")
        page.get_by_label(re.compile(r"Cliente \(Tomador\)")).select_option(cust2["id"])
        page.get_by_role("button", name=re.compile("Usar Última Nota")).click()
        page.get_by_text(re.compile(f"com base na Nota nº {numero}")).wait_for(timeout=30000)
        nf_dia = HOJE + timedelta(days=2)
        page.get_by_label(re.compile("Data Fiscal / Fato Gerador")).fill(nf_dia.isoformat())
        page.get_by_label(re.compile("^Competência")).first.fill(nf_dia.strftime("%Y-%m"))
        page.get_by_role("button", name=re.compile("Revisar Dados da NFS-e")).click()
        painel = page.get_by_role("region", name="Configuração tributária da operação")
        painel.wait_for(timeout=30000)
        EA.aprovar_no_painel(page, painel)
        page.get_by_text("Conferi os dados acima").click()
        alerta = page.get_by_role("alert").filter(has_text="Prestação posterior à emissão")
        botao = page.get_by_role("button", name=re.compile("^Emitir agora$"))
        page.screenshot(path=str(EV / "ui-prestacao-futura-nova-nfse.png"), full_page=True)
        check("Nova NFS-e: “Emitir agora” bloqueado com prestação futura, com explicação",
              alerta.count() == 1 and botao.is_disabled() and br(nf_dia) in alerta.inner_text())
        sugestao = alerta.get_by_role("button", name=re.compile("^Agendar para"))
        if nf_dia <= (HOJE.replace(day=1) + timedelta(days=32)).replace(day=1):
            sugestao.click()
            page.wait_for_timeout(300)
            check("Nova NFS-e: agendamento do dia 01 serve para a prestação; conflito some",
                  page.get_by_role("alert").filter(has_text="Prestação posterior à emissão").count() == 0
                  and page.get_by_role("button", name=re.compile("Agendar emissão")).is_enabled())
        ctx.close()

        # Celular: diálogo com o conflito sem rolagem horizontal.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)
        d3 = rascunho(c, cust, futuro, f"{100 + n}.43")
        m = modal_lote(page, f"{100 + n},43")
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-prestacao-futura-celular.png"), full_page=True)
        check("celular: conflito visível no diálogo, sem rolagem horizontal",
              m.get_by_role("alert").filter(has_text="Prestação posterior à emissão").count() == 1 and excesso <= 1, f"excesso={excesso}px")
        c.delete(f"{F}/drafts/{d3['id']}", params={"expected_version": d3["version"]})
        ctx.close()
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        import traceback  # noqa: PLC0415

        print(traceback.format_exc()[-1500:])
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:400])
    out = EV / f"ui-prestacao-futura-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
