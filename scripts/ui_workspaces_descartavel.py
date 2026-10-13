"""Validação em NAVEGADOR (Playwright) de "Criar workspace" e da permissão create_workspace no ambiente DESCARTÁVEL.

Somente empresas e usuários fictícios (8098, governo simulado); nada é transmitido. Cenários:
1. SUPER_ADMIN: Administração → Workspaces; erros junto aos campos (vazio, CNPJ inválido, CNPJ duplicado); criação com
   novo administrador (link de ativação, sem senha automática); clique repetido não duplica.
2. SUPER_ADMIN concede "Permitir criar workspace" a operador.a em Administração → Usuários.
3. operador.a cria um workspace, vira admin SÓ dele, vai para a configuração (pendências de prontidão) e o seletor
   mostra os dois workspaces com empresa/CNPJ; trocar com alteração não salva pede confirmação.
4. Revogação com a sessão aberta: a criação seguinte é recusada.
5. leitor.a (sem permissão): sem o menu e com 403 no servidor; isolamento do workspace criado.
6. Celular (390 px): lista e formulário sem rolagem horizontal.
Evidência: docs/evidencias/ui-workspaces-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_workspaces_descartavel.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)

B, A, EV, SEC = base.B, base.A, base.EV, base.SEC
results, check = base.results, base.check
ADMIN = "admin@descartavel.example.com"
PW_ADMIN = (SEC / "admin_password.txt").read_text(encoding="utf-8").strip()
OPER = "operador.a@descartavel.example.com"
LEITOR = "leitor.a@descartavel.example.com"


def senha(email: str) -> str:
    return (SEC / f"senha-{email.split('@')[0]}.txt").read_text(encoding="utf-8").strip()


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


def entrar(page, email: str, pw: str) -> None:  # noqa: ANN001
    for tentativa in range(4):
        try:
            base.login(page, email, pw)
            return
        except Exception:  # noqa: BLE001 - limite de tentativas de login
            if tentativa == 3:
                raise
            time.sleep(20)


def cnpj_ficticio(seed: int) -> str:
    return base._cnpj(f"7{seed:011d}")


def tela_workspaces(page) -> None:  # noqa: ANN001
    page.goto(f"{B}/fiscal/?view=admin-workspaces")
    page.get_by_role("heading", name="Workspaces").first.wait_for(timeout=30000)
    page.wait_for_timeout(1200)


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sa = cliente(ADMIN, PW_ADMIN)
    n = int(time.time()) % 10_000_000
    a_cnpj = next(x for x in sa.get("/api/core/v1/admin/workspaces").json() if x["id"] == A)["company"]["cnpj"]
    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page, ADMIN, PW_ADMIN)
        tela_workspaces(page)
        check("SUPER_ADMIN: lista de workspaces com situação e botão “Criar workspace”",
              page.locator(".pf-ws-item").count() >= 2 and page.get_by_role("button", name="Criar workspace").count() == 1)
        page.get_by_role("button", name="Criar workspace").click()
        dlg = page.get_by_role("dialog", name="Criar workspace")
        dlg.get_by_role("button", name="Criar workspace").click()
        page.wait_for_timeout(400)
        t = dlg.inner_text()
        check("erros junto aos campos (obrigatórios)", "Informe o nome do workspace" in t and "Informe a razão social" in t
              and "CNPJ inválido" in t)
        dlg.get_by_label("Nome do workspace *").fill("Workspace Fictício SA")
        dlg.get_by_label("Razão social *").fill("Empresa Fictícia SA Ltda")
        dlg.get_by_label("CNPJ *").fill("11.222.333/0001-00")
        dlg.get_by_role("button", name="Criar workspace").click()
        page.wait_for_timeout(400)
        check("CNPJ inválido recusado no campo", "CNPJ inválido" in dlg.inner_text())
        dlg.get_by_label("CNPJ *").fill(a_cnpj)
        dlg.get_by_role("button", name="Criar workspace").click()
        dlg.get_by_text("CNPJ já cadastrado em outro workspace").wait_for(timeout=15000)
        page.screenshot(path=str(EV / "ui-workspaces-erros.png"))
        check("CNPJ de outro workspace recusado pelo servidor, com erro no campo", True)
        doc1 = cnpj_ficticio(n)
        dlg.get_by_label("CNPJ *").fill(doc1)
        dlg.get_by_label("Administrador do workspace").select_option("novo")
        dlg.get_by_label("Nome do administrador *").fill("Administradora Fictícia")
        dlg.get_by_label("E-mail do administrador *").fill(f"adm.ficticia.{n}@descartavel.example.com")
        dlg.get_by_role("button", name="Criar workspace").dblclick()  # clique repetido
        page.get_by_role("dialog", name=re.compile("link", re.I)).first.wait_for(timeout=20000)
        page.screenshot(path=str(EV / "ui-workspaces-link-ativacao.png"))
        lista = sa.get("/api/core/v1/admin/workspaces").json()
        criados = [x for x in lista if x["company"]["cnpj"] == doc1]
        check("criado uma vez (clique repetido), em preparação, com link de ativação do novo administrador",
              len(criados) == 1 and criados[0]["status"] == "PREPARING", str(len(criados)))
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        page.keyboard.press("Escape")

        # 2) Concessão pelo SUPER_ADMIN na tela de usuários (parte de "sem permissão", inclusive em reexecução).
        oper_id = next(u for u in sa.get("/api/core/v1/admin/users", params={"query": OPER}).json()["items"])["id"]
        sa.put(f"/api/core/v1/admin/users/{oper_id}/create-workspace", json={"enabled": False}).raise_for_status()
        page.goto(f"{B}/fiscal/?view=admin-usuarios")
        page.get_by_text(OPER).first.wait_for(timeout=30000)
        page.get_by_text(OPER).first.click()
        gaveta = page.get_by_role("dialog").first
        gaveta.get_by_label("Permitir criar workspace").click()  # controlada: muda após confirmar
        page.get_by_role("button", name="Permitir criar workspace").click()
        page.wait_for_timeout(1500)
        oper_id = next(u for u in sa.get("/api/core/v1/admin/users", params={"query": OPER}).json()["items"])["id"]
        u = sa.get(f"/api/core/v1/admin/users/{oper_id}").json()
        page.screenshot(path=str(EV / "ui-workspaces-permissao.png"))
        check("SUPER_ADMIN concede “Permitir criar workspace” (sem virar SUPER_ADMIN)",
              u["can_create_workspace"] is True and u["is_super_admin"] is False)
        ctx.close()

        # 3) Usuário autorizado cria e vira admin só do novo workspace.
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        dialogos: list[str] = []
        aceitar = [False]
        page.on("dialog", lambda d: (dialogos.append(d.message), d.accept() if aceitar[0] else d.dismiss()))
        entrar(page, OPER, senha(OPER))
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        page.wait_for_timeout(2500)
        menu = page.get_by_role("navigation").first.inner_text() if page.get_by_role("navigation").count() else page.inner_text("body")
        me_oper = cliente(OPER, senha(OPER)).get("/api/core/v1/auth/me").json()
        check("autorizado: menu mostra Workspaces; Usuários só se já administra algum workspace",
              "Workspaces" in menu and (("Usuários" in menu) == bool(me_oper["admin_company_ids"])))
        tela_workspaces(page)
        page.get_by_role("button", name="Criar workspace").click()
        dlg = page.get_by_role("dialog", name="Criar workspace")
        dlg.get_by_label("Nome do workspace *").fill("Workspace Fictício do Operador")
        dlg.get_by_label("Razão social *").fill("Empresa Fictícia do Operador Ltda")
        doc2 = cnpj_ficticio(n + 1)
        dlg.get_by_label("CNPJ *").fill(doc2)
        check("autorizado não escolhe outro administrador", dlg.get_by_label("Administrador do workspace").count() == 0)
        dlg.get_by_role("button", name="Criar workspace").click()
        page.get_by_text("Você é o administrador deste workspace").wait_for(timeout=20000)
        page.screenshot(path=str(EV / "ui-workspaces-criado.png"))
        page.get_by_role("button", name="Configurar a empresa").click()
        page.wait_for_url(re.compile("view=configuracoes"), timeout=30000)
        page.wait_for_load_state("networkidle")
        cab = page.locator(".pf-workspace:visible").first
        cab.wait_for(timeout=60000)
        page.wait_for_timeout(1500)
        faixa = cab.inner_text()
        novo = next(x for x in sa.get("/api/core/v1/admin/workspaces").json() if x["company"]["cnpj"] == doc2)
        corpo = page.inner_text("body")
        check("encaminhado à configuração do novo workspace: empresa, CNPJ e situação no cabeçalho; pendências visíveis",
              f"company_id={novo['id']}" in page.url and doc2[:2] in faixa.replace(".", "") and "preparação" in faixa.lower()
              and ("Série" in corpo or "série" in corpo), faixa[:120])
        page.screenshot(path=str(EV / "ui-workspaces-configuracao.png"), full_page=True)
        sel = page.locator("select[aria-label='Workspace selecionado']:visible").first
        opcoes = sel.locator("option").all_inner_texts()
        n_ws = len(cliente(OPER, senha(OPER)).get("/api/core/v1/auth/me").json()["companies"])
        check("seletor mostra todos os workspaces do usuário (inclusive o novo)",
              len(opcoes) == n_ws >= 2 and "Empresa Fictícia do Operador Ltda" in opcoes, str(opcoes))
        # Troca com alteração não salva: confirmação (recusada) mantém o workspace.
        oper = cliente(OPER, senha(OPER))
        cust = oper.post(f"/api/core/v1/companies/{A}/customers", json={
            "name": f"Tomador Fictício WS {n}", "document_type": "CNPJ", "document": base._cnpj(f"5{n:011d}")}).json()
        oper.post(f"/api/fiscal/v1/companies/{A}/drafts", json={
            "customer_id": cust["id"], "competence": datetime.now().strftime("%Y-%m"), "amount": "123.45",
            "service": {"national_code": "010101", "description": "Serviço fictício"},
            "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}}).raise_for_status()
        page.goto(f"{B}/fiscal/?view=rascunhos&company_id={A}")
        page.locator("tr", has_text="123,45").get_by_role("button", name=re.compile("Editar")).first.click()
        ed = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        ed.wait_for(timeout=15000)
        ed.get_by_label(re.compile("Valor Total do Serviço")).fill("999,99")
        page.locator("select[aria-label='Workspace selecionado']:visible").first.select_option(novo["id"])
        page.wait_for_timeout(800)
        check("trocar com alteração não salva pede confirmação e, recusada, mantém o workspace",
              any("não salvas" in d for d in dialogos) and f"company_id={A}" in page.url, "; ".join(dialogos)[:120])

        aceitar[0] = True  # agora descarta a edição para seguir

        # 4) Revogação com a sessão aberta.
        sa.put(f"/api/core/v1/admin/users/{oper_id}/create-workspace", json={"enabled": False}).raise_for_status()
        page.goto(f"{B}/fiscal/?view=admin-workspaces")
        page.get_by_role("heading", name="Workspaces").first.wait_for(timeout=30000)
        page.wait_for_timeout(1000)
        r = oper.post("/api/core/v1/admin/workspaces", json={"name": "Negado", "company": {"cnpj": cnpj_ficticio(n + 2),
                                                                                          "name": "Negado Ltda"}})
        check("revogação vale na sessão aberta (servidor recusa) e o vínculo com o workspace criado continua",
              r.status_code == 403 and oper.get(f"/api/core/v1/companies/{novo['id']}/readiness").status_code == 200,
              str(r.status_code))
        ctx.close()

        # 5) Sem permissão.
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        entrar(page, LEITOR, senha(LEITOR))
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        page.wait_for_timeout(2500)
        check("sem permissão: menu sem Administração", "Workspaces" not in page.inner_text("body"))
        leitor = cliente(LEITOR, senha(LEITOR))
        rl = leitor.post("/api/core/v1/admin/workspaces", json={"name": "X ws", "company": {"cnpj": cnpj_ficticio(n + 3), "name": "X Ltda"}})
        ri = leitor.get(f"/api/core/v1/companies/{novo['id']}/readiness")
        check("sem permissão: servidor recusa criar (403) e o workspace criado é isolado", rl.status_code == 403
              and ri.status_code in (403, 404), f"{rl.status_code}/{ri.status_code}")
        ctx.close()

        # 6) Celular.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        entrar(page, ADMIN, PW_ADMIN)
        tela_workspaces(page)
        page.get_by_role("button", name="Criar workspace").click()
        page.get_by_role("dialog", name="Criar workspace").wait_for(timeout=15000)
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-workspaces-celular.png"))
        check("celular: lista e formulário sem rolagem horizontal", excesso <= 1, f"excesso={excesso}px")
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
    out = EV / f"ui-workspaces-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
