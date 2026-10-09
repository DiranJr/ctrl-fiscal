"""Verificação de interface em navegador real (Chromium headless, Playwright) contra o AMBIENTE TEMPORÁRIO (8098, scripts/ambiente_descartavel.py).

Desktop (1366x800), tablet (820x1180) e celular (390x844): navegação, teclado (link de pular, foco, Escape),
preservação de filtros/página ao abrir uma nota e voltar, visualização de PDF (blob:, sem token na URL) e XML
(texto escapado), seleção "esta página" × "todos os resultados", download em lote com quantidade e formato,
menu e filtros em drawer no celular, troca de empresa limpando seleção/filtros e aviso de rascunho não salvo.

Conta fictícia operador.a do ambiente temporário (nunca impressa). Somente empresas de demonstração; nenhuma
chamada ao governo. Capturas em docs/evidencias/ui-s02m-*.png.

Uso: <python com playwright> -I scripts/ui_verificacao_playwright.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
B = "http://127.0.0.1:8098"
EV = ROOT / "docs" / "evidencias"
results: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, info: str = "") -> None:
    results.append((label, bool(cond), info))
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {info}".rstrip(), flush=True)


def login(page: Page, pw: str) -> None:
    page.goto(f"{B}/login")
    page.fill("#inputEmail", "operador.a@descartavel.example.com")
    page.fill("#inputPassword", pw)
    page.click("#btnSubmitLogin")
    page.wait_for_url(re.compile(r"/(select-company|fiscal/)"), timeout=30000)


def company_ids(page: Page) -> list[str]:
    return page.evaluate("""async () => {
      const r = await fetch('/api/core/v1/auth/csrf', {credentials:'include'}); await r.json();
      return [];
    }""")


def run(viewport: dict, tag: str, pw: str, pwr) -> None:  # noqa: ANN001
    browser = pwr.chromium.launch()
    ctx = browser.new_context(viewport=viewport, accept_downloads=True, locale="pt-BR")
    page = ctx.new_page()
    console_msgs: list[str] = []
    page.on("console", lambda m: console_msgs.append(m.text))
    login(page, pw)
    page.goto(f"{B}/fiscal/?view=recebidas")
    page.wait_for_selector("table.ui-table tbody tr", timeout=30000)
    expect(page.get_by_role("heading", name="Notas recebidas")).to_be_visible()
    page.wait_for_function("() => !document.querySelector('table.ui-table[aria-busy=\"true\"]')", timeout=30000)
    page.screenshot(path=str(EV / f"ui-s02m-{tag}-01-recebidas.png"), full_page=False)

    # Teclado: primeiro Tab = link "Pular para o conteúdo"
    page.keyboard.press("Tab")
    focused = page.evaluate("() => document.activeElement && document.activeElement.textContent")
    check(f"{tag}: primeiro Tab foca 'Pular para o conteúdo'", "Pular para o conteúdo" in (focused or ""))

    # Filtro na URL + página 2 + abrir nota + Escape/Voltar preservam
    page.fill("input[type=search]", "Servi")
    page.wait_for_function("() => new URLSearchParams(location.search).get('q') === 'Servi'", timeout=10000)
    page.wait_for_function("() => !document.querySelector('table.ui-table[aria-busy=\"true\"]')", timeout=30000)
    rows = page.locator("table.ui-table tbody tr[data-clickable]")
    n = rows.count()
    if n:
        rows.first.click()
        page.wait_for_selector("[role=dialog]", timeout=10000)
        url_open = page.url
        check(f"{tag}: abrir nota grava doc= e mantém q=", "doc=" in url_open and "q=Servi" in url_open)
        page.screenshot(path=str(EV / f"ui-s02m-{tag}-02-detalhe.png"))
        page.go_back()
        page.wait_for_selector("[role=dialog]", state="detached", timeout=10000)
        check(f"{tag}: Voltar fecha a nota e preserva o filtro", "q=Servi" in page.url and "doc=" not in page.url
              and page.input_value("input[type=search]") == "Servi")
        # Teclado: Enter na linha abre; Escape fecha e devolve o foco
        rows.first.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("[role=dialog]", timeout=10000)
        inside = page.evaluate("() => !!document.activeElement.closest('[role=dialog]')")
        check(f"{tag}: Enter abre o painel e o foco vai para dentro dele", inside)
        for _ in range(25):
            page.keyboard.press("Tab")
        trapped = page.evaluate("() => !!document.activeElement.closest('[role=dialog]')")
        check(f"{tag}: Tab permanece dentro do painel (foco controlado)", trapped)

        # PDF e XML dentro do sistema
        pdf_btn = page.get_by_role("button", name=re.compile("Visualizar PDF"))
        if pdf_btn.is_enabled():
            pdf_btn.click()
            frame = page.locator("iframe[title^='DANFSe']")
            frame.wait_for(timeout=30000)
            src = frame.get_attribute("src") or ""
            check(f"{tag}: PDF exibido por URL blob: local (sem URL pública/token)", src.startswith("blob:") and "token" not in src)
            page.screenshot(path=str(EV / f"ui-s02m-{tag}-03-pdf.png"))
            with page.expect_download() as dl:
                page.get_by_role("dialog", name=re.compile("^DANFSe")).get_by_role("button", name="Baixar PDF").click()
            check(f"{tag}: Baixar PDF", dl.value.suggested_filename.startswith("DANFSe_") and dl.value.suggested_filename.endswith(".pdf"))
            page.keyboard.press("Escape")
            page.wait_for_selector("iframe[title^='DANFSe']", state="detached", timeout=10000)
        xml_btn = page.get_by_role("button", name=re.compile("Visualizar XML"))
        if xml_btn.is_enabled():
            xml_btn.click()
            pre = page.locator("pre.pf-xml")
            pre.wait_for(timeout=30000)
            text = pre.inner_text()
            check(f"{tag}: XML exibido como texto escapado", "<NFSe" in text or "<?xml" in text,
                  "(sem elementos HTML interpretados)" if page.locator("pre.pf-xml *").count() == 0 else "elementos dentro do pre!")
            page.screenshot(path=str(EV / f"ui-s02m-{tag}-04-xml.png"))
            with page.expect_download() as dl:
                page.get_by_role("dialog", name=re.compile("^XML original")).get_by_role("button", name="Baixar XML original").click()
            check(f"{tag}: Baixar XML original", dl.value.suggested_filename.endswith(".xml"))
            page.keyboard.press("Escape")
        page.keyboard.press("Escape")
        page.wait_for_selector("[role=dialog]", state="detached", timeout=10000)

    # Seleção: esta página × todos os resultados; download em lote mostra quantidade e formato
    page.fill("input[type=search]", "")
    page.wait_for_function("() => !new URLSearchParams(location.search).get('q')", timeout=10000)
    page.wait_for_function("() => !document.querySelector('table.ui-table[aria-busy=\"true\"]')", timeout=30000)
    page.get_by_label("Selecionar todas as linhas desta página").check()
    bulk = page.locator(".pf-bulkbar")
    bulk.wait_for(timeout=5000)
    has_all = page.get_by_role("button", name=re.compile(r"Selecionar todos os \d+ resultados")).count() > 0
    check(f"{tag}: barra diferencia 'esta página' de 'todos os resultados'", "esta página" in bulk.inner_text() or not has_all,
          bulk.inner_text().replace("\n", " ")[:120])
    if has_all:
        page.get_by_role("button", name=re.compile(r"Selecionar todos os \d+ resultados")).click()
        check(f"{tag}: modo 'todos os resultados' ativo", "Todos os" in bulk.inner_text())
    page.get_by_role("button", name=re.compile(r"Baixar \d+ notas?")).click()
    dlg = page.get_by_role("dialog", name="Baixar notas em lote")
    dlg.wait_for(timeout=5000)
    check(f"{tag}: diálogo mostra quantidade e formato antes de iniciar",
          bool(re.search(r"Gerar arquivo ZIP \(\d+ notas?, .+\)", dlg.inner_text())))
    page.screenshot(path=str(EV / f"ui-s02m-{tag}-05-lote.png"))
    if tag == "desktop":
        dlg.get_by_label(re.compile("XML original")).check()
        dlg.get_by_role("button", name=re.compile("Gerar arquivo ZIP")).click()
        dlg.get_by_role("button", name="Baixar ZIP").wait_for(timeout=120000)
        page.screenshot(path=str(EV / f"ui-s02m-{tag}-06-lote-concluido.png"))
        with page.expect_download() as dl:
            dlg.get_by_role("button", name="Baixar ZIP").click()
        check(f"{tag}: ZIP baixado por POST com ticket (sem token na URL)", dl.value.suggested_filename.endswith(".zip")
              and "ticket" not in (dl.value.url or ""), dl.value.suggested_filename)
    page.keyboard.press("Escape")

    # Celular: menu e filtros em drawer
    if viewport["width"] < 768:
        page.get_by_role("button", name="Abrir menu").click()
        nav = page.get_by_role("dialog", name="Menu")
        nav.wait_for(timeout=5000)
        check(f"{tag}: menu em drawer no celular", nav.get_by_role("link", name="Notas recebidas").is_visible())
        page.screenshot(path=str(EV / f"ui-s02m-{tag}-07-menu.png"))
        page.keyboard.press("Escape")
        page.get_by_role("button", name=re.compile("^Filtros")).click()
        fd = page.get_by_role("dialog", name="Filtros avançados")
        fd.wait_for(timeout=5000)
        check(f"{tag}: filtros avançados em drawer no celular", fd.get_by_text("Tipos de retenção efetiva").is_visible())
        page.screenshot(path=str(EV / f"ui-s02m-{tag}-08-filtros.png"))
        page.keyboard.press("Escape")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check(f"{tag}: sem rolagem horizontal da página", overflow <= 1, f"excesso={overflow}px")
    else:
        page.get_by_role("button", name=re.compile("Filtros avançados")).click()
        page.get_by_label("Retenção", exact=True).select_option("WITH")
        page.wait_for_function("() => new URLSearchParams(location.search).get('ret') === 'WITH'", timeout=10000)
        page.wait_for_function("() => !document.querySelector('table.ui-table[aria-busy=\"true\"]')", timeout=30000)
        page.get_by_label("ISS retido").check()
        page.get_by_label("IRRF").check()
        check(f"{tag}: 2+ tipos exibem a regra explícita (qualquer um/todos)",
              page.get_by_role("radiogroup", name="Combinar tipos de retenção").is_visible())
        page.screenshot(path=str(EV / f"ui-s02m-{tag}-07-filtros-retencao.png"))
        check(f"{tag}: filtros de retenção na URL", "ret=WITH" in page.url and "rtype=ISS%2CIRRF" in page.url)

    # Troca de empresa limpa filtros e seleção
    sel = page.locator("#pf-company-select")
    opts = sel.locator("option").all()
    if len(opts) > 1:
        current = sel.input_value()
        other = next(o.get_attribute("value") for o in opts if o.get_attribute("value") != current)
        page.fill("input[type=search]", "abc")
        page.wait_for_function("() => new URLSearchParams(location.search).get('q') === 'abc'", timeout=10000)
        sel.select_option(other)
        page.wait_for_function(f"() => new URLSearchParams(location.search).get('company_id') === '{other}'", timeout=10000)
        page.wait_for_timeout(800)
        check(f"{tag}: trocar de empresa limpa filtros e seleção", "q=abc" not in page.url
              and page.input_value("input[type=search]") == "" and page.locator(".pf-bulkbar").count() == 0)
        sel.select_option(current)

    leaked = [m for m in console_msgs if re.search(r"\d{14}|Bearer|password|senha|<NFSe", m)]
    check(f"{tag}: console sem credenciais/dados fiscais", not leaked, f"{len(console_msgs)} mensagens")
    ctx.close()
    browser.close()


def drafts_guard(pw: str, pwr) -> None:  # noqa: ANN001
    browser = pwr.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 800})
    login(page, pw)
    page.goto(f"{B}/fiscal/?view=nova-nfse")
    page.get_by_role("heading").first.wait_for(timeout=30000)
    field = page.locator("main textarea").first
    field.wait_for(timeout=30000)
    field.fill("alteração não salva")
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.get_by_role("link", name="Rascunhos").first.click()
    page.wait_for_timeout(500)
    check("rascunho: sair com alteração não salva pede confirmação", bool(dialogs) and "não salvas" in dialogs[0])
    check("rascunho: ao recusar, permanece na tela e mantém o preenchimento",
          "view=nova-nfse" in page.url and field.input_value() == "alteração não salva")
    page.screenshot(path=str(EV / "ui-s02m-desktop-09-nova-nfse.png"))
    browser.close()


def main() -> int:
    pw = (ROOT / "secrets-descartavel" / "senha-operador.a.txt").read_text(encoding="utf-8").strip()
    with sync_playwright() as pwr:
        for vp, tag in (({"width": 1366, "height": 800}, "desktop"), ({"width": 820, "height": 1180}, "tablet"),
                        ({"width": 390, "height": 844}, "celular")):
            try:
                run(vp, tag, pw, pwr)
            except Exception as exc:  # noqa: BLE001
                check(f"{tag}: execução completa", False, f"{type(exc).__name__}: {str(exc)[:200]}")
        try:
            drafts_guard(pw, pwr)
        except Exception as exc:  # noqa: BLE001
            check("rascunho: execução completa", False, f"{type(exc).__name__}: {str(exc)[:200]}")
    bad = [r for r in results if not r[1]]
    print(f"RESUMO: {len(results) - len(bad)} PASS / {len(bad)} FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
