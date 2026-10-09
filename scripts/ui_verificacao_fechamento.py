"""Verificação de fechamento em navegador (Playwright) contra o AMBIENTE TEMPORÁRIO (8098, scripts/ambiente_descartavel.py).

A) Telas padronizadas (painel, configurações, clientes, sincronização, importação) em desktop/tablet/celular:
   título h1, ação/estrutura, sem rolagem horizontal, campos com rótulo, console sem dados sensíveis.
B) PDF do DANFSe aberto DENTRO do sistema num navegador COM visualizador de PDF (Microsoft Edge instalado),
   com captura de tela do visualizador renderizado.
C) Administração → Usuários e /ativar com FIXTURES interceptadas no navegador (page.route): nenhuma escrita em
   dados operacionais, nenhuma credencial real.

Conta fictícia operador.a do ambiente temporário (nunca impressa). Capturas: docs/evidencias/ui-s02n-*.png.
Uso: <python com playwright> -I scripts/ui_verificacao_fechamento.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
B = "http://127.0.0.1:8098"
EV = ROOT / "docs" / "evidencias"
results: list[tuple[str, bool, str]] = []
VIEWPORTS = (({"width": 1366, "height": 800}, "desktop"), ({"width": 820, "height": 1180}, "tablet"),
             ({"width": 390, "height": 844}, "celular"))
SCREENS = {"dashboard": "Painel", "configuracoes": "Configurações", "clientes": "Clientes",
           "sincronizacao": "Sincroniza", "importar-exportar": "Importa"}


def check(label: str, cond: bool, info: str = "") -> None:
    results.append((label, bool(cond), info))
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {info}".rstrip(), flush=True)


def login(page, pw: str) -> None:  # noqa: ANN001
    page.goto(f"{B}/login", wait_until="networkidle")  # espera a hidratação do Next.js antes de preencher
    page.wait_for_timeout(500)
    page.fill("#inputEmail", "operador.a@descartavel.example.com")
    page.fill("#inputPassword", pw)
    page.click("#btnSubmitLogin")
    page.wait_for_url(re.compile(r"/(select-company|fiscal/)"), timeout=30000)
    page.wait_for_load_state("networkidle")
    # A página de seleção renova a sessão ao abrir (rotação do refresh token). Navegar no meio dessa renovação faria o
    # painel reapresentar o token já rotacionado, e a detecção de reuso encerraria a sessão (proteção esperada).
    page.wait_for_timeout(4000)


UNLABELED_JS = """() => Array.from(document.querySelectorAll('main input, main select, main textarea'))
  .filter(e => e.type !== 'hidden' && e.offsetParent !== null)
  .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby'))
  .map(e => e.outerHTML.slice(0, 80))"""


def screens(pwr, pw: str) -> None:  # noqa: ANN001
    for vp, tag in VIEWPORTS:
        b = pwr.chromium.launch()
        page = b.new_page(viewport=vp)
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(m.text))
        login(page, pw)
        for view, title in SCREENS.items():
            page.goto(f"{B}/fiscal/?view={view}")
            h1 = page.locator("main h1").first
            h1.wait_for(timeout=30000)
            page.wait_for_timeout(1200)
            ok_title = title.lower()[:6] in h1.inner_text().lower()
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            unlabeled = page.evaluate(UNLABELED_JS)
            check(f"{tag} {view}: título, sem rolagem horizontal, campos rotulados",
                  ok_title and overflow <= 1 and not unlabeled, f"h1='{h1.inner_text()[:40]}' excesso={overflow}px sem_rótulo={unlabeled[:2]}")
            page.screenshot(path=str(EV / f"ui-s02n-{tag}-{view}.png"))
        leaked = [m for m in msgs if re.search(r"\d{14}|Bearer|password|senha|<NFSe", m)]
        check(f"{tag}: console sem dados sensíveis", not leaked, f"{len(msgs)} mensagens")
        b.close()


def pdf_viewer(pwr, pw: str) -> None:  # noqa: ANN001
    b = pwr.chromium.launch(channel="msedge", headless=False, args=["--window-position=0,0"])
    page = b.new_page(viewport={"width": 1366, "height": 860})
    login(page, pw)
    page.goto(f"{B}/fiscal/?view=recebidas")
    try:
        page.wait_for_selector("table.ui-table tbody tr[data-clickable]", timeout=30000)
    except Exception:
        page.screenshot(path=str(EV / "ui-s02n-pdf-falha.png"))
        raise
    page.wait_for_function("() => !document.querySelector('table.ui-table[aria-busy=\"true\"]')", timeout=30000)
    rows = page.locator("table.ui-table tbody tr[data-clickable]")
    opened = False
    for i in range(min(rows.count(), 10)):
        rows.nth(i).click()
        page.wait_for_selector("[role=dialog]", timeout=10000)
        btn = page.get_by_role("button", name=re.compile("Visualizar PDF"))
        if btn.is_enabled():
            btn.click()
            frame = page.locator("iframe[title^='DANFSe']")
            frame.wait_for(timeout=30000)
            page.wait_for_timeout(4000)  # visualizador de PDF do navegador renderiza o documento
            box = frame.bounding_box()
            shot = EV / "ui-s02n-pdf-visualizador-edge.png"
            page.screenshot(path=str(shot), clip=box)
            from PIL import Image  # disponível no ambiente do Playwright? se não, só a captura

            try:
                im = Image.open(shot).convert("L")
                hist = im.histogram()
                dark = sum(hist[:128]) / max(1, sum(hist))
            except Exception:  # noqa: BLE001
                dark = -1
            src = frame.get_attribute("src") or ""
            check("PDF aberto no visualizador do navegador (Edge) dentro do sistema",
                  src.startswith("blob:") and (dark == -1 or dark > 0.01), f"src=blob: pixels_escuros={dark:.3f}")
            opened = True
            break
        page.keyboard.press("Escape")
    check("havia nota com DANFSe para abrir", opened)
    b.close()


ME = {"id": "u-super", "name": "Super Admin Fixture", "email": "super@fixture.test", "companies": [],
      "is_super_admin": True, "admin_company_ids": []}
USERS = [
    {"id": "u1", "name": "Ana Fixture", "email": "ana@fixture.test", "status": "ACTIVE", "is_active": True, "is_super_admin": True,
     "version": 3, "memberships": [], "other_companies": 0, "can_manage_identity": True, "active_sessions": 1,
     "password_set_at": "2026-10-08T10:00:00Z", "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-08T10:00:00Z"},
    {"id": "u2", "name": "Bruno Fixture", "email": "bruno@fixture.test", "status": "PENDING", "is_active": True, "is_super_admin": False,
     "version": 1, "memberships": [{"company_id": "c1", "company_name": "Empresa Fixture", "company_cnpj": "00000000000191",
                                    "company_active": True, "permissions": ["view", "review"]}],
     "other_companies": 1, "can_manage_identity": False, "active_sessions": 0, "password_set_at": None,
     "created_at": "2026-10-02T10:00:00Z", "updated_at": "2026-10-02T10:00:00Z"},
]


def admin_ui(pwr) -> None:  # noqa: ANN001
    for vp, tag in (VIEWPORTS[0], VIEWPORTS[2]):
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport=vp)
        page = ctx.new_page()
        calls: list[str] = []

        def handle(route, request):  # noqa: ANN001, ANN202
            url, m = request.url, request.method
            calls.append(f"{m} {url.split('8098')[1].split('?')[0]}")
            j = lambda body, status=200: route.fulfill(status=status, content_type="application/json", body=json.dumps(body))  # noqa: E731
            if "/auth/csrf" in url:
                return j({"csrf_token": "fixture"})
            if "/auth/refresh" in url:
                return j({"access_token": "fixture.jwt.token", "token_type": "Bearer", "expires_in": 900})
            if "/auth/me" in url:
                return j(ME)
            if "/admin/meta" in url:
                return j({"is_super_admin": True, "admin_company_ids": [], "permissions": [
                    {"id": p, "label": p} for p in ("view", "import", "review", "prepare", "schedule", "admin")]})
            if "/admin/companies" in url:
                return j([{"id": "c1", "name": "Empresa Fixture", "cnpj": "00000000000191"}])
            if re.search(r"/admin/users\?|/admin/users$", url) and m == "GET":
                return j({"items": USERS, "page": 1, "page_size": 25, "total": len(USERS)})
            if url.endswith("/admin/users") and m == "POST":
                return j({"user": {**USERS[1], "id": "u3", "name": "Nova Fixture", "email": "nova@fixture.test"},
                          "credential_link": {"purpose": "ACTIVATE", "expires_at": "2026-10-09T13:00:00Z",
                                              "path": "/ativar#token=FIXTURE-TOKEN-0000000000000000"}}, 201)
            if re.search(r"/admin/users/u\d$", url):
                return j(next(u for u in USERS if url.endswith(u["id"])))
            return j({"code": "NOT_MOCKED"}, 404)

        page.route("**/api/**", handle)
        page.goto(f"{B}/fiscal/?view=admin-usuarios")
        page.get_by_role("heading", level=1).first.wait_for(timeout=30000)
        page.wait_for_selector("table.ui-table tbody tr", timeout=20000)
        txt = page.locator("main").inner_text()
        full = tag == "desktop"  # no celular a coluna de empresas/escopo é ocultada de propósito (hideOnMobile)
        check(f"admin {tag}: lista com status, SUPER_ADMIN{' e escopo' if full else ''}", "Ana Fixture" in txt and "Bruno Fixture" in txt
              and "SUPER" in txt.upper() and (not full or "fora do seu escopo" in txt))
        if tag == "desktop":
            menu = page.get_by_role("navigation").first.inner_text()
            check("admin: menu Administração visível para SUPER_ADMIN sem empresas", "Usuários" in menu)
            page.get_by_role("button", name=re.compile("Novo usuário", re.I)).first.click()
            dlg = page.get_by_role("dialog").first
            dlg.wait_for(timeout=5000)
            dlg.get_by_label(re.compile("^Nome", re.I)).first.fill("Nova Fixture")
            dlg.get_by_label(re.compile("e-mail", re.I)).first.fill("nova@fixture.test")
            page.screenshot(path=str(EV / "ui-s02n-admin-novo-usuario.png"))
            dlg.get_by_role("button", name=re.compile("Criar|Cadastrar|Salvar", re.I)).last.click()
            page.wait_for_timeout(800)
            dlg2 = page.get_by_role("dialog").last
            link_txt = dlg2.inner_text()
            link_val = " ".join(i.input_value() for i in dlg2.locator("input").all())
            check("admin: link de ativação exibido uma vez, com alerta de uso único",
                  "FIXTURE-TOKEN" in link_val and re.search(r"uso único|somente agora", link_txt, re.I) is not None)
            check("admin: sem empresa selecionada não exibe selo de ambiente (nem 'Simulação')",
                  "Simulação" not in page.locator("header").first.inner_text())
            page.screenshot(path=str(EV / "ui-s02n-admin-link.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            page.locator("table.ui-table tbody tr").nth(1).click()
            drawer = page.get_by_role("dialog").last
            drawer.wait_for(timeout=5000)
            page.wait_for_timeout(600)
            check("admin: usuário com vínculo fora do escopo tem edição limitada e explicada",
                  re.search(r"fora do seu escopo", drawer.inner_text(), re.I) is not None)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            page.locator("table.ui-table tbody tr").nth(0).click()
            drawer = page.get_by_role("dialog").last
            drawer.wait_for(timeout=5000)
            page.wait_for_timeout(600)
            check("admin: seção SUPER_ADMIN explica que não dá acesso a documentos fiscais",
                  re.search(r"não\s+dá acesso aos documentos", " ".join(drawer.inner_text().split()), re.I) is not None)
            page.screenshot(path=str(EV / "ui-s02n-admin-detalhe.png"))
        else:
            overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check("admin celular: sem rolagem horizontal", overflow <= 1, f"excesso={overflow}px")
            page.screenshot(path=str(EV / "ui-s02n-admin-celular.png"))
        b.close()
    # /ativar: token só no fragmento, removido da barra; POST com fixture
    b = pwr.chromium.launch()
    page = b.new_page(viewport={"width": 390, "height": 844})
    posted: list[dict] = []

    def act(route, request):  # noqa: ANN001, ANN202
        if request.url.endswith("/auth/activate"):
            posted.append(json.loads(request.post_data or "{}"))
            return route.fulfill(status=204, body="")
        return route.continue_()

    page.route("**/api/core/v1/auth/**", act)
    page.goto(f"{B}/ativar#token=FIXTURE-TOKEN-AAAAAAAAAAAAAAAAAAAA")
    page.wait_for_timeout(1500)
    check("ativar: token removido da barra de endereço", "#token" not in page.url and "FIXTURE" not in page.url, page.url[-30:])
    pwds = page.locator("input[type=password]")
    pwds.nth(0).fill("Senha-Fixture-Forte-2026")
    pwds.nth(1).fill("Senha-Fixture-Forte-2026")
    page.get_by_role("button", name=re.compile("Definir|Salvar|Ativar", re.I)).first.click()
    page.wait_for_timeout(1000)
    check("ativar: envia token no corpo (não na URL) e confirma", bool(posted) and posted[0].get("token", "").startswith("FIXTURE")
          and re.search(r"senha definida", page.locator("body").inner_text(), re.I) is not None)
    page.screenshot(path=str(EV / "ui-s02n-ativar-sucesso.png"))
    b.close()


def main() -> int:
    pw = (ROOT / "secrets-descartavel" / "senha-operador.a.txt").read_text(encoding="utf-8").strip()
    with sync_playwright() as pwr:
        for fn, args in ((screens, (pwr, pw)), (pdf_viewer, (pwr, pw)), (admin_ui, (pwr,))):
            if fn is pdf_viewer:
                import time
                time.sleep(65)  # respeita o limite de login do gateway (10/min) após as 3 sessões da etapa A
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001
                check(f"{fn.__name__}: execução completa", False, f"{type(exc).__name__}: {str(exc)[:220]}")
    bad = [r for r in results if not r[1]]
    print(f"RESUMO: {len(results) - len(bad)} PASS / {len(bad)} FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
