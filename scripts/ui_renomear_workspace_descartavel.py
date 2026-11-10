"""Validação em NAVEGADOR (Playwright) de "Renomear workspace" (só o nome de exibição) no ambiente DESCARTÁVEL.

Somente empresas e usuários fictícios (8098, governo simulado); nada é transmitido. Cenários:
1. multi.ab (admin em A, só leitura em B): Administração → Workspaces lista só A; "Renomear" carrega o nome atual;
   nome inválido mostra erro e não salva; salvar atualiza a lista e o seletor de workspaces sem recarregar a página.
2. Reabrir (recarregar a página): o nome salvo continua na lista, no formulário e no seletor; razão social, CNPJ,
   ambiente, situação e permissões de A e de B não mudaram.
3. Isolamento: B continua com o próprio nome para multi.ab e para operador.b; operador.b não renomeia A (404).
4. SUPER_ADMIN renomeia B: o seletor de multi.ab mostra o nome novo de B e A não muda.
5. Sem permissão: operador.a (A sem "admin") não tem o menu nem o botão; o servidor recusa (403).
Evidência: docs/evidencias/ui-renomear-workspace-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_renomear_workspace_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)
import ui_workspaces_descartavel as ws  # noqa: E402  (clientes, login com novas tentativas)

B, A, B_ID, EV = base.B, base.A, base.B_ID, base.EV
results, check = base.results, base.check
MULTI, OPER_A, OPER_B = base.MULTI, ws.OPER, "operador.b@descartavel.example.com"
ADM = "/api/core/v1/admin/workspaces"


def foto(c, wid: str) -> dict:  # noqa: ANN001
    """O que renomear não pode mudar (visto pela API de administração do SUPER_ADMIN)."""
    w = next(x for x in c.get(ADM).json() if x["id"] == wid)
    return {"id": w["id"], "status": w["status"], "environment": w["environment"], "company": w["company"]}


def seletor(page) -> dict[str, str]:  # noqa: ANN001
    sel = page.locator("#pf-company-select")
    sel.wait_for(timeout=30000)
    return {o.get_attribute("value"): o.inner_text().strip() for o in sel.locator("option").all()}


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sa = ws.cliente(ws.ADMIN, ws.PW_ADMIN)
    antes_a, antes_b = foto(sa, A), foto(sa, B_ID)
    multi = ws.cliente(MULTI, base.PW_MULTI)
    perms_antes = {c["id"]: c["permissions"] for c in multi.get("/api/core/v1/auth/me").json()["companies"]}
    nome_b = next(w["name"] for w in sa.get(ADM).json() if w["id"] == B_ID)
    novo_a = f"Empresa A — Renomeada {datetime.now():%H%M%S} (fictícia)"

    with sync_playwright() as pwr:
        msgs: list[str] = []
        nativos: list[str] = []
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        page.on("dialog", lambda dlg: (nativos.append(dlg.message), dlg.dismiss()))
        ws.entrar(page, MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=admin-workspaces&company_id={A}")
        page.get_by_role("heading", name="Workspaces").first.wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        itens = page.locator(".pf-ws-item")
        check("admin de A: a lista mostra só o workspace que administra (A)",
              itens.count() == 1 and antes_a["company"]["cnpj"][-2:] in itens.first.inner_text(), str(itens.count()))
        nome_atual = itens.first.locator("strong").inner_text().strip()
        itens.first.get_by_role("button", name=re.compile("^Renomear")).click()
        dlg = page.get_by_role("dialog", name="Renomear workspace")
        dlg.wait_for(timeout=15000)
        campo = dlg.get_by_label("Nome do workspace *")
        check("Renomear carrega o nome atual e informa que razão social/CNPJ não mudam",
              campo.input_value() == nome_atual and "não mudam" in dlg.inner_text(), campo.input_value())
        campo.fill("  ")
        dlg.get_by_role("button", name="Salvar nome").click()
        page.wait_for_timeout(600)
        check("nome inválido: erro junto ao campo e nada salvo",
              "mínimo de 2 caracteres" in dlg.inner_text()
              and next(w["name"] for w in sa.get(ADM).json() if w["id"] == A) == nome_atual)
        campo.fill(novo_a)
        page.screenshot(path=str(EV / "ui-renomear-formulario.png"))
        dlg.get_by_role("button", name="Salvar nome").click()
        dlg.wait_for(state="detached", timeout=15000)
        page.get_by_text(f"Workspace renomeado para “{novo_a}”").wait_for(timeout=10000)
        page.wait_for_timeout(1200)
        opcoes = seletor(page)
        check("após salvar: lista e seletor mostram o nome novo, sem recarregar; B mantém o nome",
              itens.first.locator("strong").inner_text().strip() == novo_a and opcoes.get(A) == novo_a
              and opcoes.get(B_ID) == nome_b, json.dumps(opcoes, ensure_ascii=False)[:200])
        page.screenshot(path=str(EV / "ui-renomear-salvo.png"))

        # 2) Reabrir.
        page.reload()
        page.get_by_role("heading", name="Workspaces").first.wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        page.locator(".pf-ws-item").first.get_by_role("button", name=re.compile("^Renomear")).click()
        dlg = page.get_by_role("dialog", name="Renomear workspace")
        dlg.wait_for(timeout=15000)
        reaberto = dlg.get_by_label("Nome do workspace *").input_value()
        dlg.get_by_role("button", name="Cancelar").click()
        opcoes = seletor(page)
        check("reabrir: formulário e seletor com o nome salvo", reaberto == novo_a and opcoes.get(A) == novo_a, reaberto)
        depois_a, depois_b = foto(sa, A), foto(sa, B_ID)
        perms_depois = {c["id"]: c["permissions"] for c in multi.get("/api/core/v1/auth/me").json()["companies"]}
        check("preservados: id, razão social, CNPJ, ambiente e situação de A e B; permissões do usuário",
              depois_a == antes_a and depois_b == antes_b and perms_depois == perms_antes,
              json.dumps(depois_a["company"], ensure_ascii=False)[:160])
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        page.wait_for_timeout(2500)
        check("título do painel usa o nome de exibição", novo_a in page.locator("main").inner_text())
        check("nenhum diálogo nativo do navegador foi aberto", not nativos, "; ".join(nativos))
        b.close()

        # 3) Isolamento: operador.b não vê nem renomeia A; continua com B com o nome de B.
        ob = ws.cliente(OPER_B, ws.senha(OPER_B))
        me_b = ob.get("/api/core/v1/auth/me").json()
        r = ob.patch(f"{ADM}/{A}", json={"name": "Tentativa de outro workspace"})
        check("isolamento: operador.b só vê B (nome inalterado) e não renomeia A (404)",
              [c["display_name"] for c in me_b["companies"]] == [nome_b] and r.status_code == 404, str(r.status_code))

        # 4) SUPER_ADMIN renomeia B; multi.ab vê o nome novo de B no seletor e A não muda.
        novo_b = f"Empresa B — Renomeada {datetime.now():%H%M%S} (fictícia)"
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        ws.entrar(page, ws.ADMIN, ws.PW_ADMIN)
        ws.tela_workspaces(page)
        item_b = page.locator(".pf-ws-item", has_text=nome_b)
        item_b.get_by_role("button", name=re.compile("^Renomear")).click()
        dlg = page.get_by_role("dialog", name="Renomear workspace")
        dlg.get_by_label("Nome do workspace *").fill(novo_b)
        dlg.get_by_role("button", name="Salvar nome").click()
        dlg.wait_for(state="detached", timeout=15000)
        page.wait_for_timeout(1000)
        check("SUPER_ADMIN renomeia B pela tela", page.locator(".pf-ws-item", has_text=novo_b).count() == 1)
        b.close()
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        ws.entrar(page, MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        opcoes = seletor(page)
        page.screenshot(path=str(EV / "ui-renomear-seletor.png"))
        check("seletor de multi.ab: A e B com os respectivos nomes novos, sem mistura",
              opcoes.get(A) == novo_a and opcoes.get(B_ID) == novo_b, json.dumps(opcoes, ensure_ascii=False)[:200])
        b.close()

        # 5) Sem permissão (A sem "admin").
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        ws.entrar(page, OPER_A, ws.senha(OPER_A))
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        page.wait_for_timeout(2500)
        menu = page.get_by_role("button", name="Workspaces").count() + page.get_by_role("link", name="Workspaces").count()
        oa = ws.cliente(OPER_A, ws.senha(OPER_A))
        r = oa.patch(f"{ADM}/{A}", json={"name": "Sem permissão"})
        check("sem permissão: sem menu Workspaces; o servidor recusa (403) e o nome não muda",
              menu == 0 and r.status_code == 403 and r.json().get("code") == "WORKSPACE_ADMIN_REQUIRED"
              and next(w["name"] for w in sa.get(ADM).json() if w["id"] == A) == novo_a, f"{menu} | {r.status_code}")
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:300])
    out = EV / f"ui-renomear-workspace-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
