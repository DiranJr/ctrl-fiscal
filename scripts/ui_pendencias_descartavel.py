"""Validação em NAVEGADOR (Playwright) do detalhamento das PENDÊNCIAS dos rascunhos no ambiente DESCARTÁVEL (8098).

Somente dados fictícios; governo simulado; nada é transmitido. Conta multi.ab (operador + administrador da empresa A).
1. Rascunho com várias pendências (regras oficiais E0316/E0302, aviso de competência futura, descrição longa,
   tomador inativo e perfil de serviço pendente).
2. "Validar" abre painel persistente: bloqueios x avisos, seção/campo, mensagem, motivo oficial, ação, detalhes
   técnicos (código, regra, caminho XML) e versão validada.
3. Cliente e perfil: link abre o cadastro em NOVA aba (cliente já aberto para edição); o editor mantém o que não foi
   salvo. "Ir ao campo" no editor troca a aba e foca o controle.
4. Edição após validação: aviso de nova validação (não salvo e salvo).
5. Falha da API na validação: erro claro no painel, informação anterior mantida.
6. Revisão de "Emitir ou agendar" mostra as mesmas pendências; o servidor continua recusando (bloqueio fiscal).
7. Celular (390 px): painel sem rolagem horizontal.
Evidência: docs/evidencias/ui-pendencias-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_pendencias_descartavel.py
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

B, A, EV = base.B, base.A, base.EV
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
_N = int(time.time()) % 900
VALOR, VALOR_BR = f"{100 + _N % 800}.43", f"{100 + _N % 800},43"  # únicos por execução (abaixo de mil: sem separador)
NOVO_BR = f"{100 + (_N + 400) % 800},77"


def cliente() -> httpx.Client:
    c = httpx.Client(base_url=B, timeout=60, headers={"Origin": B})
    for _ in range(20):
        r = c.post("/api/core/v1/auth/login", json={"email": base.MULTI, "password": base.PW_MULTI})
        if r.status_code != 429:
            break
        time.sleep(7)
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def entrar(page) -> None:  # noqa: ANN001
    for tentativa in range(4):
        try:
            base.login(page, base.MULTI, base.PW_MULTI)
            return
        except Exception:  # noqa: BLE001 - limite de tentativas de login após logins pela API
            if tentativa == 3:
                raise
            time.sleep(20)


def lista(page) -> None:  # noqa: ANN001
    page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
    page.get_by_role("heading", name="Rascunhos de NFS-e").wait_for(timeout=30000)
    page.wait_for_timeout(1500)


def preparar(c: httpx.Client) -> tuple[dict, dict, dict]:
    n = int(time.time()) % 100000
    cust = c.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Pendências {n}", "document_type": "CNPJ", "document": base._cnpj(f"3322{n:08d}")})
    cust.raise_for_status()
    cust = cust.json()
    perfil = c.post(f"/api/core/v1/companies/{A}/service-profiles", json={
        "name": f"Perfil fictício pendente {n}", "national_code": "010101"})
    perfil.raise_for_status()
    perfil = perfil.json()
    d = c.post(f"{F}/drafts", json={
        "customer_id": cust["id"], "competence": "2099-12", "amount": VALOR, "service_profile_id": perfil["id"],
        "service": {"national_code": "010101", "nbs_code": "999999999", "location_code": "9999999",
                    "description": "Serviço fictício com descrição longa para conferir a quebra de linhas. " * 25},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}})
    d.raise_for_status()
    # Cliente inativado depois: a validação aponta o tomador (destino = cadastro do cliente).
    c.patch(f"/api/core/v1/companies/{A}/customers/{cust['id']}", json={"is_active": False}).raise_for_status()
    return cust, perfil, d.json()


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = cliente()
    cust, perfil, d = preparar(c)
    check("preparação: rascunho fictício com várias pendências, tomador e perfil fictícios", bool(d["id"]))
    pwr = sync_playwright().start()  # sem "with": uma falha mostra a causa real (não a do encerramento)
    if True:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page)
        lista(page)

        # 1-2) Validar abre o painel com o detalhamento.
        page.locator("tr", has_text=VALOR_BR).get_by_role("button", name=re.compile("Validar")).click()
        painel = page.get_by_role("dialog", name=re.compile("Pendências do rascunho"))
        painel.wait_for(timeout=30000)
        painel.get_by_text(re.compile(r"^Bloqueios \(\d+\)")).first.wait_for(timeout=15000)
        txt = painel.inner_text()
        servidor = c.get(f"{F}/drafts/{d['id']}").json()
        nb = sum(1 for i in servidor["issues"] if i["severity"] == "error")
        na = sum(1 for i in servidor["issues"] if i["severity"] == "warning")
        itens = painel.locator(".pf-pend-item")
        check("painel lista TODAS as pendências do servidor, separando bloqueios e avisos",
              itens.count() == len(servidor["issues"]) and f"Bloqueios ({nb})" in txt and f"Avisos ({na})" in txt
              and f"validado na versão v{servidor['version']}" in txt, f"itens={itens.count()} servidor={len(servidor['issues'])}")
        e0316 = next(i for i in servidor["issues"] if i["code"] == "NBS_NOT_IN_LIST")
        nbs = painel.locator(".pf-pend-item", has_text=e0316["message"]).first
        nbs.get_by_text("Detalhes técnicos").click()
        tn = nbs.inner_text()
        check("pendência com regra oficial: seção, mensagem, motivo oficial, ação e detalhes técnicos (código, regra, XML)",
              "Serviço" in tn and e0316["message"] in tn and e0316["reason"][:60] in tn and "Como corrigir" in tn
              and "NBS_NOT_IN_LIST" in tn and "E0316" in tn and "DPS/infDPS/serv/cServ/cNBS" in tn)
        aviso = painel.locator(".pf-pend-item[data-severity=warning]", has_text="Competência")
        check("aviso local sem motivo inventado (origem: validação local)",
              aviso.count() == 1 and "Validação local" in aviso.inner_text() and "Motivo" not in aviso.inner_text())
        page.screenshot(path=str(EV / "ui-pendencias-painel.png"), full_page=False)

        # 3) Cliente: nova aba com o cadastro já aberto; o painel continua.
        link = painel.locator(".pf-pend-item", has_text="Tomador (cliente)").get_by_role("link", name=re.compile("cadastro do cliente"))
        href = link.get_attribute("href") or ""
        with ctx.expect_page() as nova:
            link.click()
        aba = nova.value
        aba.wait_for_load_state()
        aba.get_by_role("dialog").first.wait_for(timeout=30000)
        check("cliente: abre em nova aba com o cadastro do tomador em edição; painel original continua aberto",
              "customer_id=" + cust["id"] in href and link.get_attribute("target") == "_blank"
              and cust["name"] in aba.get_by_role("dialog").first.inner_text() and painel.is_visible())
        aba.screenshot(path=str(EV / "ui-pendencias-cliente-nova-aba.png"))
        aba.close()
        painel.get_by_role("button", name="Fechar").last.click()
        page.wait_for_timeout(500)
        page.locator("tr", has_text=VALOR_BR).get_by_role("button", name=re.compile("Ver pendências")).click()
        check("“Ver pendências” reabre o mesmo detalhamento",
              page.get_by_role("dialog", name=re.compile("Pendências do rascunho")).locator(".pf-pend-item").count()
              == len(servidor["issues"]))

        # Editor: ir ao campo; alterações não salvas preservadas ao abrir cadastro em nova aba.
        page.get_by_role("dialog", name=re.compile("Pendências do rascunho")).get_by_role("button", name=re.compile("Abrir no editor")).click()
        editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        editor.wait_for(timeout=15000)
        pend = editor.locator(".pf-pend")
        pend.wait_for(timeout=15000)
        pend.locator(".pf-pend-item", has_text=e0316["message"]).first.get_by_role("button", name="Ir ao campo").click()
        page.wait_for_timeout(900)
        focado = page.evaluate("""() => { const e = document.activeElement; const l = e && e.id ?
            document.querySelector(`label[for="${e.id}"]`) : null; return l ? l.textContent : ''; }""")
        check("editor: “Ir ao campo” troca de aba e foca o controle (cNBS)", "cNBS" in (focado or ""), focado)
        editor.screenshot(path=str(EV / "ui-pendencias-editor-ir-ao-campo.png"))
        valor = editor.get_by_label(re.compile("Valor Total do Serviço"))
        editor.get_by_role("button", name=re.compile("Geral & Valores")).click()
        valor = editor.get_by_label(re.compile("Valor Total do Serviço"))
        valor.fill(NOVO_BR)
        page.wait_for_timeout(400)
        check("editor: alteração não salva pede nova validação (botão Validar bloqueado)",
              "alterações não salvas" in pend.inner_text() and pend.get_by_role("button", name=re.compile("Validar")).is_disabled())
        lp = pend.locator(".pf-pend-item", has_text="Perfil de serviço").get_by_role("link").first
        with ctx.expect_page() as nova:
            lp.click()
        nova.value.wait_for_load_state()
        check("perfil: abre Configurações em nova aba e o editor mantém o valor não salvo",
              "view=configuracoes" in (lp.get_attribute("href") or "") and valor.input_value() == NOVO_BR)
        nova.value.close()
        editor.get_by_role("button", name=re.compile("Salvar Rascunho")).click()
        editor.wait_for(state="detached", timeout=20000)
        page.wait_for_timeout(1200)
        page.locator("tr", has_text=NOVO_BR).get_by_role("button", name=re.compile("Ver pendências")).click()
        painel = page.get_by_role("dialog", name=re.compile("Pendências do rascunho"))
        atual = c.get(f"{F}/drafts/{d['id']}").json()
        check("edição salva após validação: painel mostra versão validada e exige nova validação",
              "Nova validação necessária" in painel.inner_text()
              and f"validado na v{servidor['version']}; atual v{atual['version']}" in painel.inner_text())

        # 5) Falha da API: painel mostra o erro e mantém o que se sabia.
        page.route("**/drafts/*/validate", lambda r: r.fulfill(status=503, content_type="application/problem+json",
                   body=json.dumps({"type": "about:blank", "title": "Serviço indisponível", "status": 503,
                                    "detail": "Serviço temporariamente indisponível (falha simulada)."})))
        painel.get_by_role("button", name=re.compile("Validar")).click()
        painel.get_by_text("Não foi possível validar agora").wait_for(timeout=15000)
        check("falha da API: erro claro no painel e informação anterior mantida",
              "falha simulada" in painel.inner_text() and "Nova validação necessária" in painel.inner_text())
        page.screenshot(path=str(EV / "ui-pendencias-falha-api.png"))
        page.unroute("**/drafts/*/validate")
        painel.get_by_role("button", name=re.compile("Validar")).click()
        painel.get_by_text(re.compile(r"^Bloqueios \(\d+\)")).first.wait_for(timeout=20000)
        painel.get_by_role("button", name="Fechar").last.click()

        # 6) Revisão de emitir/agendar com as mesmas pendências; servidor mantém o bloqueio.
        page.locator("tr", has_text=NOVO_BR).locator("input[type=checkbox]").check()
        page.get_by_role("button", name=re.compile("Emitir ou agendar")).click()
        rev = page.get_by_role("dialog", name=re.compile("Emitir ou agendar"))
        rev.wait_for(timeout=15000)
        check("revisão de emissão/agendamento mostra as pendências do rascunho",
              rev.locator(".pf-pend-item[data-severity=error]").count() == nb)
        rev.screenshot(path=str(EV / "ui-pendencias-revisao-lote.png"))
        page.keyboard.press("Escape")
        cur = c.get(f"{F}/drafts/{d['id']}").json()
        rb = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
            "drafts": [{"id": d["id"], "expected_version": cur["version"]}],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=20)).isoformat(), "timezone": "America/Sao_Paulo"})
        check("bloqueio fiscal preservado: o servidor recusa agendar rascunho com bloqueios",
              rb.status_code in (409, 422), str(rb.status_code))
        ctx.close()

        # 7) Celular.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page)
        lista(page)
        page.locator("tr", has_text=NOVO_BR).get_by_role("button", name=re.compile("Ver pendências")).click()
        painel = page.get_by_role("dialog", name=re.compile("Pendências do rascunho"))
        painel.wait_for(timeout=15000)
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-pendencias-celular.png"), full_page=False)
        check("celular: painel legível sem rolagem horizontal", excesso <= 1 and painel.locator(".pf-pend-item").count() > 0,
              f"excesso={excesso}px")
        ctx.close()
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])
        pwr.stop()


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        import traceback  # noqa: PLC0415

        print(traceback.format_exc()[-1500:])
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:400])
    out = EV / f"ui-pendencias-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
