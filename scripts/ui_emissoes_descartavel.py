"""Validação em NAVEGADOR (Playwright) de Rascunhos (Pendentes/Emitidos/Todos) e "Emissões e agendamentos" no
ambiente DESCARTÁVEL (8098, governo simulado). Somente dados fictícios; nada é transmitido a ambiente oficial.

Desfechos de rejeição/incerteza/cancelamento/substituição e o "registro anterior" são gravados no banco DESCARTÁVEL
com os mesmos campos que o fluxo grava; as emissões autorizadas passam pelo worker simulado de verdade.
Evidência: docs/evidencias/ui-emissoes-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_emissoes_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_lotes_descartavel as L  # noqa: E402  (cliente, entrar, sql, rascunho, lote, marcar)
import ui_validacao_descartavel as base  # noqa: E402

B, A, EV, SEC = base.B, base.A, base.EV, base.SEC
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"


def emitir_agora(c, d: dict) -> str:  # noqa: ANN001
    bid = L.lote(c, [d], "NOW")
    for _ in range(40):
        if c.get(f"{F}/batches/{bid}").json()["items"][0]["status"] == "AUTHORIZED":
            break
        time.sleep(1.5)
    return bid


def doc_do(c, draft_id: str) -> str:  # noqa: ANN001
    return c.get(f"{F}/drafts/{draft_id}").json()["issued"]["document_id"]


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = L.cliente(base.MULTI, base.PW_MULTI)
    n = int(time.time()) % 100000
    cust = c.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Emissões {n}", "document_type": "CNPJ", "document": base._cnpj(f"6633{n:08d}")}).json()
    v = lambda k: f"{100 + (n + k) % 800}.{k:02d}"  # noqa: E731  valores únicos (abaixo de mil)
    br = lambda s: s.replace(".", ",")  # noqa: E731
    d_auth, d_canc, d_sub, d_ant = (L.rascunho(c, cust["id"], v(k)) for k in (1, 2, 3, 4))
    d_pend, d_rej = L.rascunho(c, cust["id"], v(5)), L.rascunho(c, cust["id"], v(6))
    b_auth = emitir_agora(c, d_auth)
    emitir_agora(c, d_canc)
    emitir_agora(c, d_sub)
    L.sql(f"UPDATE documents SET status='CANCELLED' WHERE id='{doc_do(c, d_canc['id'])}'")
    L.sql(f"UPDATE documents SET substituted_by_access_key=REPEAT('9',50) WHERE id='{doc_do(c, d_sub['id'])}'")
    L.sql(f"UPDATE drafts SET status='AUTHORIZED' WHERE id='{d_ant['id']}'")  # registro anterior, sem vínculos novos
    b_rej = L.lote(c, [d_rej])
    L.marcar(d_rej["id"], "REJECTED", fase="SUBMITTING", dps=1, codigo="REJECTED", msg="Rejeição confirmada; corrija o rascunho.",
             erros=[{"code": "E0310", "message": "Código de tributação nacional inexistente (simulação)."}])
    ms = [L.rascunho(c, cust["id"], v(10 + k)) for k in range(3)]
    b_mix = L.lote(c, ms)
    L.marcar(ms[0]["id"], "AWAITING_CONFIRMATION", fase="SUBMITTING", dps=2, codigo="TIMEOUT",
             msg="Sem confirmação do processamento; será reconciliado por consulta à DPS.")
    L.marcar(ms[1]["id"], "WITHDRAWN")
    check("preparação: emitidos (imediata), cancelada, substituída, registro anterior, rejeitado, misto e pendente", True)

    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
        page.get_by_role("heading", name="Rascunhos de NFS-e").wait_for(timeout=30000)
        page.wait_for_timeout(1800)
        corpo = page.inner_text("main") if page.locator("main").count() else page.inner_text("body")
        api = c.get(f"{F}/drafts", params={"view": "pending"}).json()
        check("Pendentes é o padrão: sem emitidos; rejeitado, incerto e pendente visíveis; contagens do servidor",
              br(v(1)) not in corpo and br(v(5)) in corpo and br(v(6)) in corpo and br(v(10)) in corpo
              and f"Pendentes ({api['counts']['pending']})" in corpo and f"Emitidos ({api['counts']['issued']})" in corpo
              and "emita agora ou agende" in corpo)
        linha_inc = page.locator("tr", has_text=br(v(10)))
        check("incerto: 'Aguardando confirmação', sem editar nem selecionar",
              "Aguardando confirmação" in linha_inc.inner_text() and linha_inc.locator("input[type=checkbox]").count() == 0
              and linha_inc.get_by_role("button", name=re.compile("Editar")).count() == 0)
        page.screenshot(path=str(EV / "ui-emissoes-pendentes.png"))
        page.get_by_role("button", name=re.compile(r"^Emitidos")).click()
        page.wait_for_timeout(1500)
        t = page.inner_text("body")
        la = page.locator("tr", has_text=br(v(1)))
        check("Emitidos: 'Emitida' com 'Ver nota', sem seleção/edição/exclusão; cancelada, substituída e registro anterior",
              "Emitida" in la.inner_text() and la.get_by_role("button", name="Ver nota").count() == 1
              and la.locator("input[type=checkbox]").count() == 0
              and la.get_by_role("button", name=re.compile("Editar|Excluir|Validar")).count() == 0
              and "NFS-e cancelada" in page.locator("tr", has_text=br(v(2))).inner_text()
              and "NFS-e substituída" in page.locator("tr", has_text=br(v(3))).inner_text()
              and page.locator("tr", has_text=br(v(4))).count() == 1)
        page.screenshot(path=str(EV / "ui-emissoes-emitidos.png"))
        la.get_by_role("button", name="Ver nota").click()
        page.wait_for_url(re.compile(r"view=emitidas.*doc="), timeout=30000)
        page.wait_for_load_state("networkidle")  # deixa a renovação da sessão (recarga) terminar antes de navegar
        page.wait_for_timeout(2500)
        check("Ver nota abre a NFS-e em Notas emitidas", "doc=" + doc_do(c, d_auth["id"]) in page.url)
        dialogos: list[str] = []
        page.on("dialog", lambda d: (dialogos.append(d.message), d.accept()))

        # Emissões e agendamentos.
        page.goto(f"{B}/fiscal/?view=agendamentos&company_id={A}")
        page.get_by_role("heading", name="Emissões e agendamentos").wait_for(timeout=30000)
        page.wait_for_timeout(1800)
        t = page.inner_text("body")
        cont = c.get(f"{F}/batches", params={"view": "all"}).json()["counts"]
        check("título, menu, botão e aviso atualizados; sem horário fixo 00:01",
              "00:01" not in t and "Emitir ou agendar rascunhos" in t and "não cancela uma NFS-e já autorizada" in t
              and page.get_by_role("link", name=re.compile("Emissões e agendamentos")).count() + page.get_by_role("button", name=re.compile("Emissões e agendamentos")).count() >= 1)
        check("abas com as contagens do servidor",
              all(f"{rot} ({cont[k]})" in t for k, rot in (("in_progress", "Em andamento"), ("attention", "Exigem atenção"),
                                                         ("history", "Histórico"), ("all", "Todos"))), json.dumps(cont))
        em_and = page.locator("tbody tr")
        check("Em andamento (padrão): agendados e incertos; modo 'Agendada' e 'Programada para'",
              page.locator("tr", has_text=b_mix[:8]).count() == 1 and "Agendada" in t and "Programada para" in t)
        page.get_by_role("button", name=re.compile(r"^Exigem atenção")).click()
        page.wait_for_timeout(1500)
        t = page.inner_text("body")
        check("Exigem atenção: rejeição não resolvida, com a marca", page.locator("tr", has_text=b_rej[:8]).count() == 1
              and "Exige atenção" in t)
        page.get_by_role("button", name=re.compile(r"^Histórico")).click()
        page.wait_for_timeout(1500)
        lh = page.locator("tr", has_text=b_auth[:8])
        check("Histórico: emissão imediata com 'Solicitada em'; rejeitado encerrado também no histórico",
              lh.count() == 1 and "Emissão imediata" in lh.inner_text() and "Solicitada em" in lh.inner_text()
              and page.locator("tr", has_text=b_rej[:8]).count() == 1)
        page.screenshot(path=str(EV / "ui-emissoes-historico.png"))
        lh.get_by_role("button", name="Ver itens").click()
        dlg = page.get_by_role("dialog", name=re.compile("Emissão imediata"))
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        check("detalhe do autorizado com 'Ver nota'", dlg.get_by_role("link", name="Ver nota").count() == 1)
        page.keyboard.press("Escape")
        page.locator("tr", has_text=b_rej[:8]).get_by_role("button", name="Ver itens").click()
        dlg = page.get_by_role("dialog", name=re.compile("Emissão agendada"))
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        dlg.get_by_role("link", name="Abrir rascunho e pendências").click()
        page.get_by_role("dialog", name=re.compile("Editar Rascunho")).wait_for(timeout=30000)
        check("rejeitado: 'Abrir rascunho e pendências' abre o editor do rascunho", True)

        # Falha da leitura do detalhe.
        page.goto(f"{B}/fiscal/?view=agendamentos&company_id={A}")
        page.get_by_role("heading", name="Emissões e agendamentos").wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        page.route(re.compile(r".*/batches/[0-9a-f-]{36}$"), lambda r: r.fulfill(status=503, content_type="application/problem+json",
                   body=json.dumps({"type": "about:blank", "title": "Indisponível", "status": 503, "detail": "Falha simulada."})))
        page.locator("tr", has_text=b_mix[:8]).get_by_role("button", name="Ver itens").click()
        d2 = page.get_by_role("dialog").first
        d2.get_by_text("Não foi possível carregar os itens do lote").wait_for(timeout=15000)
        page.unroute(re.compile(r".*/batches/[0-9a-f-]{36}$"))
        d2.get_by_role("button", name="Tentar novamente").click()
        d2.locator(".pf-lote-item").first.wait_for(timeout=15000)
        check("falha no detalhe: mensagem e nova tentativa", d2.locator(".pf-lote-item").count() == 3)
        page.keyboard.press("Escape")

        # Atualização durante o processamento (sem recarregar a página).
        antes = c.get(f"{F}/batches", params={"view": "history"}).json()["counts"]["history"]
        novo = L.rascunho(c, cust["id"], v(20))
        bid_now = L.lote(c, [novo], "NOW")
        alvo = f"Histórico ({antes + 1})"
        ok = False
        for _ in range(30):
            if alvo in page.inner_text("body"):
                ok = True
                break
            page.wait_for_timeout(2000)
        check("lista atualiza sozinha durante o processamento (sem recarregar)", ok, bid_now[:8])
        ctx.close()

        # Celular.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)
        excessos = []
        for vw in ("rascunhos", "agendamentos"):
            page.goto(f"{B}/fiscal/?view={vw}&company_id={A}")
            page.wait_for_timeout(3000)
            excessos.append(page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"))
            page.screenshot(path=str(EV / f"ui-emissoes-celular-{vw}.png"))
        check("celular: Rascunhos e Emissões sem rolagem horizontal", max(excessos) <= 1, str(excessos))
        ctx.close()
        b.close()
        vcsp = base.csp_violations(msgs)
        check("sem violação de CSP", not vcsp, "; ".join(vcsp)[:160])

    # Prevenção de edição, exclusão e reenvio; isolamento.
    cur = c.get(f"{F}/drafts/{d_auth['id']}").json()
    rp = c.patch(f"{F}/drafts/{d_auth['id']}", json={"expected_version": cur["version"], "amount": "1.00"})
    rd = c.delete(f"{F}/drafts/{d_auth['id']}", params={"expected_version": cur["version"]})
    rb = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d_auth["id"], "expected_version": cur["version"]}], "mode": "NOW", "timezone": "America/Sao_Paulo"})
    check("emitido: servidor recusa editar, excluir e reenviar", (rp.status_code, rd.status_code, rb.status_code) == (409, 409, 409),
          f"{rp.status_code}/{rd.status_code}/{rb.status_code}")
    outro = L.cliente("operador.b@descartavel.example.com", (SEC / "senha-operador.b.txt").read_text(encoding="utf-8").strip())
    fb = f"/api/fiscal/v1/companies/{base.B_ID}"
    cb = outro.get(f"{fb}/batches", params={"view": "all"}).json()["counts"]
    db = outro.get(f"{fb}/drafts", params={"view": "all"}).json()["counts"]
    check("isolamento: outro workspace não vê lotes nem rascunhos de A", cb["all"] == 0 and db["all"] == 0
          and outro.get(f"{F}/batches/{b_auth}").status_code in (403, 404))


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        import traceback  # noqa: PLC0415

        print(traceback.format_exc()[-1500:])
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:400])
    out = EV / f"ui-emissoes-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
