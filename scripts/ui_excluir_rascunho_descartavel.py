"""Validação em NAVEGADOR (Playwright) de "Excluir rascunho" no ambiente DESCARTÁVEL (8098, governo simulado).

Somente dados fictícios; integrações oficiais desligadas; nada é transmitido. Fluxos (conta operador.a: "prepare"):
1. Lista: "Excluir" abre confirmação com tomador, serviço e valor; "Cancelar" mantém; confirmar remove da lista e o
   rascunho fica indisponível (410).
2. Editor: "Excluir rascunho" no rodapé; após confirmar, o editor fecha e a lista é atualizada.
3. Falha da API: rascunho alterado em outra sessão com a confirmação aberta → erro claro no diálogo e rascunho mantido.
4. Agendado: sem botão de exclusão; a API recusa (409) — cancelar o agendamento é o fluxo próprio.
5. Leitor (sem "prepare"): sem botão; a API recusa (403).
Evidência: docs/evidencias/ui-excluir-rascunho-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_excluir_rascunho_descartavel.py
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
sys.path.insert(0, str(ROOT / "scripts"))
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)

B, A, EV, SEC = base.B, base.A, base.EV, base.SEC
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
TAX = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}
LEITOR = "leitor.a@descartavel.example.com"


def cliente(email: str, pw: str) -> httpx.Client:
    c = httpx.Client(base_url=B, timeout=60, headers={"Origin": B})
    for _ in range(20):
        r = c.post("/api/core/v1/auth/login", json={"email": email, "password": pw})
        if r.status_code != 429:
            break
        time.sleep(7)
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def senha(email: str) -> str:
    return (SEC / f"senha-{email.split('@')[0]}.txt").read_text(encoding="utf-8").strip()


def entrar(page, email: str, pw: str) -> None:  # noqa: ANN001
    for tentativa in range(4):
        try:
            base.login(page, email, pw)
            return
        except Exception:  # noqa: BLE001 - limite de tentativas de login após logins pela API
            if tentativa == 3:
                raise
            time.sleep(20)


def rascunho(c: httpx.Client, cust: str, valor: str, desc: str) -> dict:
    r = c.post(f"{F}/drafts", json={"customer_id": cust, "competence": datetime.now().strftime("%Y-%m"), "amount": valor,
                                    "service": {"national_code": "010101", "description": desc}, "tax_settings": TAX})
    r.raise_for_status()
    return r.json()


def linha(page, valor_br: str):  # noqa: ANN001, ANN201
    return page.locator("tr", has_text=valor_br)


def abrir_lista(page) -> None:  # noqa: ANN001
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
    page.get_by_role("heading", name="Rascunhos de NFS-e").wait_for(timeout=30000)
    page.wait_for_timeout(1500)


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    op = cliente(base.EMAIL, base.PW)
    n = int(time.time()) % 100000
    cust = op.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Exclusão {n}", "document_type": "CNPJ", "document": base._cnpj(f"5544{n:08d}")})
    cust.raise_for_status()
    cust = cust.json()
    d1 = rascunho(op, cust["id"], "111.11", "Serviço fictício para excluir pela lista")
    d2 = rascunho(op, cust["id"], "222.22", "Serviço fictício para excluir pelo editor")
    d3 = rascunho(op, cust["id"], "333.33", "Serviço fictício agendado")
    d4 = rascunho(op, cust["id"], "444.44", "Serviço fictício alterado em outra sessão")
    em = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    r = op.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d3["id"], "expected_version": d3["version"]}], "scheduled_at": em, "timezone": "America/Sao_Paulo"})
    check("preparação: 4 rascunhos fictícios; um deles agendado", r.status_code == 202, r.text[:160])

    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        dialogos_nativos: list[str] = []
        page.on("dialog", lambda dlg: (dialogos_nativos.append(dlg.message), dlg.dismiss()))
        entrar(page, base.EMAIL, base.PW)
        abrir_lista(page)

        # 1) Pela lista: cancelar mantém; confirmar remove.
        linha(page, "111,11").get_by_role("button", name=re.compile("Excluir")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho"))
        dlg.wait_for(timeout=15000)
        txt = dlg.inner_text()
        page.screenshot(path=str(EV / "ui-excluir-confirmacao.png"))
        check("confirmação identifica tomador, serviço e valor",
              cust["name"] in txt and "excluir pela lista" in txt and "R$ 111,11" in txt, txt[:200].replace("\n", " | "))
        dlg.get_by_role("button", name="Cancelar").click()
        page.wait_for_timeout(800)
        check("cancelar mantém o rascunho na lista", linha(page, "111,11").count() == 1
              and op.get(f"{F}/drafts/{d1['id']}").status_code == 200)
        linha(page, "111,11").get_by_role("button", name=re.compile("Excluir")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho"))
        dlg.get_by_role("button", name="Excluir rascunho").click()
        linha(page, "111,11").wait_for(state="detached", timeout=15000)
        check("lista: após confirmar, o rascunho sai da lista e fica indisponível (410)",
              linha(page, "111,11").count() == 0 and op.get(f"{F}/drafts/{d1['id']}").status_code == 410)
        page.screenshot(path=str(EV / "ui-excluir-lista-atualizada.png"))

        # 2) Pelo editor: volta para a lista.
        linha(page, "222,22").get_by_role("button", name=re.compile("Editar")).click()
        editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        editor.wait_for(timeout=15000)
        editor.get_by_role("button", name=re.compile("Excluir rascunho")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho\\?"))
        dlg.wait_for(timeout=15000)
        page.screenshot(path=str(EV / "ui-excluir-pelo-editor.png"))
        check("editor: confirmação aparece sobre o editor com os dados do rascunho", "R$ 222,22" in dlg.inner_text())
        dlg.get_by_role("button", name="Excluir rascunho").click()
        editor.wait_for(state="detached", timeout=15000)
        page.wait_for_timeout(1000)
        check("editor: após excluir, o editor fecha e a lista não mostra o rascunho",
              page.get_by_role("dialog").count() == 0 and linha(page, "222,22").count() == 0
              and op.get(f"{F}/drafts/{d2['id']}").status_code == 410)

        # 3) Falha da API: versão mudou com a confirmação aberta → erro claro, rascunho mantido.
        linha(page, "444,44").get_by_role("button", name=re.compile("Excluir")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho"))
        dlg.wait_for(timeout=15000)
        op.patch(f"{F}/drafts/{d4['id']}", json={"expected_version": d4["version"],
                                                  "service": {"description": "Alterado em outra sessão (fictício)"}})
        dlg.get_by_role("button", name="Excluir rascunho").click()
        erro = dlg.get_by_text("O rascunho não foi excluído")
        erro.wait_for(timeout=15000)
        page.screenshot(path=str(EV / "ui-excluir-falha-api.png"))
        check("falha da API: erro claro no diálogo e rascunho mantido",
              "alterado" in dlg.inner_text().lower() and op.get(f"{F}/drafts/{d4['id']}").status_code == 200)
        dlg.get_by_role("button", name="Cancelar").click()
        page.wait_for_timeout(800)
        check("falha da API: rascunho continua na lista", linha(page, "444,44").count() == 1)

        # 4) Agendado: sem botão; a API recusa.
        ag = linha(page, "333,33")
        rd = op.delete(f"{F}/drafts/{d3['id']}", params={"expected_version": op.get(f"{F}/drafts/{d3['id']}").json()["version"]})
        check("agendado: sem botão de exclusão; a API recusa (409) e pede cancelar o agendamento",
              ag.count() == 1 and ag.get_by_role("button", name=re.compile("Excluir")).count() == 0
              and rd.status_code == 409 and rd.json().get("code") == "DRAFT_NOT_DELETABLE", str(rd.status_code))
        check("nenhum diálogo nativo do navegador foi aberto", not dialogos_nativos, "; ".join(dialogos_nativos))
        b.close()

        # 5) Leitor: sem botão; API 403.
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page, LEITOR, senha(LEITOR))
        abrir_lista(page)
        leitor = cliente(LEITOR, senha(LEITOR))
        cur = op.get(f"{F}/drafts/{d4['id']}").json()
        rl = leitor.delete(f"{F}/drafts/{d4['id']}", params={"expected_version": cur["version"]})
        check("leitor: sem botão de exclusão e a API recusa (403)",
              linha(page, "444,44").count() == 1
              and page.get_by_role("button", name=re.compile("Excluir")).count() == 0 and rl.status_code == 403,
              str(rl.status_code))
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:300])
    out = EV / f"ui-excluir-rascunho-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
