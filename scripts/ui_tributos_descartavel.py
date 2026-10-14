"""Validação em NAVEGADOR (Playwright) dos tributos aproximados no editor de rascunhos e na Nova NFS-e, no ambiente
DESCARTÁVEL (8098, governo simulado). Somente dados fictícios; nada é transmitido.
Modos: "Valores em moeda (R$)" (três valores) e "Percentuais (%)" (três percentuais). Salvar/reabrir, zero
preservado, vazio não vira zero, troca de modo com aviso (sem reaproveitar números). Só frontend: o contrato
existente (approx_tax.federal/state/municipal) já grava os três percentuais. O XML (pTotTrib/vTotTrib) é conferido nos testes automatizados (tests/test_tributos_aproximados.py).
Evidência: docs/evidencias/ui-tributos-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_tributos_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_lotes_descartavel as L  # noqa: E402  (cliente, entrar, rascunho)
import ui_validacao_descartavel as base  # noqa: E402

B, A, EV = base.B, base.A, base.EV
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
ABA = re.compile("TotTrib")


def aprox(c, did: str) -> dict:  # noqa: ANN001
    return (c.get(f"{F}/drafts/{did}").json()["tax_settings"] or {}).get("approx_tax") or {}


def abrir_editor(page, valor_br: str):  # noqa: ANN001, ANN201
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
    page.get_by_role("heading", name="Rascunhos de NFS-e").wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.locator("tr", has_text=valor_br).get_by_role("button", name=re.compile("Editar")).click()
    ed = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
    ed.wait_for(timeout=15000)
    ed.get_by_role("button", name=ABA).click()
    page.wait_for_timeout(500)
    return ed


def salvar(page, ed) -> None:  # noqa: ANN001
    ed.get_by_role("button", name="Salvar Rascunho").click()
    ed.wait_for(state="detached", timeout=20000)
    page.wait_for_timeout(800)


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = L.cliente(base.MULTI, base.PW_MULTI)
    n = int(time.time()) % 100000
    cust = c.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Tributos {n}", "document_type": "CNPJ", "document": base._cnpj(f"2211{n:08d}")}).json()
    v1, v2 = f"{100 + n % 800}.61", f"{100 + (n + 1) % 800}.62"
    d = L.rascunho(c, cust["id"], v1)
    d2 = L.rascunho(c, cust["id"], v2)
    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        page.on("dialog", lambda dl: dl.accept())  # descartar alterações ao cancelar
        L.entrar(page, base.MULTI, base.PW_MULTI)

        ed = abrir_editor(page, v1.replace(".", ","))
        modo = ed.get_by_label("Modo de Informação")
        opcoes = modo.locator("option").all_inner_texts()
        check("rótulos: 'Valores em moeda (R$)' e 'Percentuais (%)'; sem 'Percentual Federal / Total'",
              "Valores em moeda (R$)" in opcoes and "Percentuais (%)" in opcoes and "Percentual Federal / Total" not in ed.inner_text(),
              str(opcoes))
        modo.select_option("PERCENT")
        page.wait_for_timeout(300)
        check("percentuais: três campos Federal (%), Estadual (%) e Municipal (%)",
              all(ed.get_by_label(x).count() == 1 for x in ("Federal (%)", "Estadual (%)", "Municipal (%)")))
        ed.get_by_label("Federal (%)").fill("5,25")
        ed.get_by_label("Estadual (%)").fill("0")
        page.screenshot(path=str(EV / "ui-tributos-percentuais.png"))
        salvar(page, ed)
        ap = aprox(c, d["id"])
        check("salvo: percentuais normalizados, zero preservado e vazio NÃO vira zero",
              ap.get("mode") == "PERCENT" and ap.get("federal") == "5.25" and ap.get("state") == "0.00"
              and ap.get("municipal") in (None, ""), json.dumps(ap))
        ed = abrir_editor(page, v1.replace(".", ","))
        check("reaberto: valores carregados nos campos certos (zero visível, vazio vazio)",
              ed.get_by_label("Federal (%)").input_value() == "5.25" and ed.get_by_label("Estadual (%)").input_value() == "0.00"
              and ed.get_by_label("Municipal (%)").input_value() == "")
        ed.get_by_label("Municipal (%)").fill("2")
        salvar(page, ed)
        ap = aprox(c, d["id"])
        check("três percentuais salvos (pTotTribFed/Est/Mun)",
              (ap["federal"], ap["state"], ap["municipal"]) == ("5.25", "0.00", "2.00"), json.dumps(ap))
        ed = abrir_editor(page, v1.replace(".", ","))
        check("reaberto: os três percentuais, com o zero explícito",
              [ed.get_by_label(x).input_value() for x in ("Federal (%)", "Estadual (%)", "Municipal (%)")] == ["5.25", "0.00", "2.00"])
        ed.get_by_role("button", name="Cancelar").click()
        page.wait_for_timeout(800)

        # Troca de modo: limpa e avisa; cancelar não grava nada.
        ed = abrir_editor(page, v1.replace(".", ","))
        ed.get_by_label("Modo de Informação").select_option("VALUES")
        page.wait_for_timeout(400)
        t = ed.inner_text()
        page.screenshot(path=str(EV / "ui-tributos-troca-modo.png"))
        check("troca % → R$: aviso e campos em R$ vazios (não reaproveita os números)",
              "foram apagados" in t and ed.get_by_label("Federal (R$)").input_value() == ""
              and ed.get_by_label("Estadual (R$)").input_value() == "" and ed.get_by_label("Municipal (R$)").input_value() == "")
        ed.get_by_role("button", name="Cancelar").click()
        page.wait_for_timeout(800)
        check("cancelar a troca não altera o rascunho salvo", aprox(c, d["id"]).get("mode") == "PERCENT")

        # Moeda: três valores.
        ed = abrir_editor(page, v2.replace(".", ","))
        ed.get_by_label("Modo de Informação").select_option("VALUES")
        ed.get_by_label("Federal (R$)").fill("10,50")
        ed.get_by_label("Estadual (R$)").fill("0")
        ed.get_by_label("Municipal (R$)").fill("3,00")
        salvar(page, ed)
        ap = aprox(c, d2["id"])
        check("moeda: três valores em R$ salvos (zero preservado)",
              ap.get("mode") == "VALUES" and (ap["federal"], ap["state"], ap["municipal"]) == ("10.50", "0.00", "3.00"), json.dumps(ap))
        ed = abrir_editor(page, v2.replace(".", ","))
        check("moeda reaberta no formato brasileiro", ed.get_by_label("Federal (R$)").input_value() == "10,50"
              and ed.get_by_label("Estadual (R$)").input_value() == "0,00")
        ed.get_by_role("button", name="Cancelar").click()

        # Nova NFS-e: mesmos rótulos e mesmo aviso.
        page.goto(f"{B}/fiscal/?view=nova-nfse&company_id={A}")
        sel = page.get_by_label("Modalidade de Apuração")
        sel.wait_for(timeout=30000)
        ops = sel.locator("option").all_inner_texts()
        sel.select_option("VALUES")
        page.get_by_label("Tributos Federais (R$)").fill("1,00")
        sel.select_option("PERCENT")
        page.wait_for_timeout(400)
        check("Nova NFS-e: mesmos rótulos e aviso ao trocar R$ → %",
              "Percentuais (%)" in ops and "Valores em moeda (R$)" in ops and "foram apagados" in page.inner_text("body")
              and page.get_by_label("Tributos Federais (%)").input_value() == "", str(ops))
        ctx.close()

        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.on("dialog", lambda dl: dl.accept())
        L.entrar(page, base.MULTI, base.PW_MULTI)
        ed = abrir_editor(page, v1.replace(".", ","))
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-tributos-celular.png"))
        check("celular: seção de tributos aproximados sem rolagem horizontal", excesso <= 1, f"excesso={excesso}px")
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
    out = EV / f"ui-tributos-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
