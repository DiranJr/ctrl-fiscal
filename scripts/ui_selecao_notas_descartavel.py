"""Validação em NAVEGADOR (Playwright) da seleção ACUMULADA de notas para o download em lote, no ambiente DESCARTÁVEL
(8098, governo simulado). Somente XMLs fictícios importados; nada é transmitido.
Cobre: seleção entre pesquisas e páginas, retorno aos resultados, cabeçalho só da página, "Ver selecionadas" com remoção
individual, "Limpar filtros" x "Limpar seleção", abrir detalhe, navegação interna, falhas de consulta e de download,
notas indisponíveis com motivo, troca de recebidas/emitidas, ambiente e workspace, e o ZIP contendo EXATAMENTE as
notas escolhidas (conferido no manifesto.json). Desktop e celular.
Evidência: docs/evidencias/ui-selecao-notas-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_selecao_notas_descartavel.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "services" / "nfse-api"))
import ui_lotes_descartavel as L  # noqa: E402
import ui_validacao_descartavel as base  # noqa: E402
from fixtures_nfse import nota_como_187  # noqa: E402 - XML fictício com estrutura de nota real

B, A, B_ID, EV = base.B, base.A, base.B_ID, base.EV
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
N_RECEBIDAS = 30  # > 25 por página: duas páginas


def preparar(c) -> tuple[int, dict[str, str]]:  # noqa: ANN001
    """Importa 30 recebidas e 3 emitidas fictícias; devolve o número base e {numero: id}."""
    a_cnpj = next(x for x in c.get("/api/core/v1/companies").json()["items"] if x["id"] == A)["cnpj"]
    nb = 60000 + int(time.time()) % 30000
    arquivos = [("files", (f"r{nb + i}.xml", nota_como_187(base._cnpj(f"7311{nb + i:08d}"), a_cnpj, nb + i), "application/xml"))
                for i in range(N_RECEBIDAS)]
    arquivos += [("files", (f"e{nb + 100 + i}.xml", nota_como_187(a_cnpj, base._cnpj(f"7322{nb + i:08d}"), nb + 100 + i),
                            "application/xml")) for i in range(3)]
    job = c.post(f"{F}/documents/import", files=arquivos).json()
    for _ in range(90):
        st = c.get(f"{F}/jobs/{job['job_id']}").json()
        if st.get("status") in ("SUCCEEDED", "PARTIAL", "FAILED"):
            break
        time.sleep(2)
    ids: dict[str, str] = {}
    for direction in ("RECEIVED", "ISSUED"):
        pg = 1
        while True:
            r = c.get(f"{F}/documents", params={"direction": direction, "page_size": 100, "page": pg}).json()
            ids.update({d["number"]: d["id"] for d in r["items"]})
            if pg * 100 >= r["total"]:
                break
            pg += 1
    check("preparo: 30 recebidas e 3 emitidas fictícias importadas",
          all(str(nb + i) in ids for i in range(N_RECEBIDAS)) and all(str(nb + 100 + i) in ids for i in range(3)),
          f"job={st.get('status')}")
    return nb, ids


def contagem(page) -> int:  # noqa: ANN001
    reg = page.get_by_role("region", name="Seleção")
    if reg.count() == 0 or not reg.is_visible():
        return 0
    m = re.search(r"(\d+)\s+notas?\s+selecionadas?", reg.inner_text())
    return int(m.group(1)) if m else -1


def caixa(page, numero: int | str):  # noqa: ANN001, ANN201
    return page.get_by_label(f"Selecionar NFS-e {numero}", exact=True)


def buscar(page, termo: str) -> None:  # noqa: ANN001
    page.get_by_label("Buscar").fill(termo)
    page.wait_for_timeout(900)  # debounce (400 ms) + resposta
    page.wait_for_load_state("networkidle")


def ids_da_pagina(page, ids: dict[str, str]) -> list[str]:  # noqa: ANN001
    """Notas EXIBIDAS na página (lidas da tela: as fictícias têm a mesma data, e o desempate pode variar entre consultas)."""
    rot = page.locator("tbody input[type=checkbox]").evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
    return [ids[r.rsplit(" ", 1)[-1]] for r in rot]


def menu(page, nome: str) -> None:  # noqa: ANN001
    page.locator("nav.pf-sidebar").get_by_text(nome, exact=True).first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)


def baixar_e_conferir(page, esperado: set[str], rotulo: str) -> None:  # noqa: ANN001
    page.get_by_role("region", name="Seleção").get_by_role("button", name=re.compile("^Baixar")).click()
    dlg = page.get_by_role("dialog", name="Baixar notas em lote")
    dlg.get_by_label(re.compile("^XML original")).check()
    dlg.get_by_role("button", name=re.compile("Gerar arquivo ZIP")).click()
    dlg.get_by_role("button", name="Baixar ZIP").wait_for(timeout=120000)
    with page.expect_download(timeout=60000) as dl:
        dlg.get_by_role("button", name="Baixar ZIP").click()
    dados = Path(dl.value.path()).read_bytes()
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        man = json.loads(z.read("manifesto.json"))
    entradas = man if isinstance(man, list) else man.get("documents") or man.get("itens") or man.get("items") or []
    obtidos = {e["document_id"] for e in entradas}
    check(f"{rotulo}: ZIP com EXATAMENTE as notas escolhidas", obtidos == esperado,
          f"esperado={len(esperado)} obtido={len(obtidos)} diferença={len(obtidos ^ esperado)}")
    dlg.get_by_role("button", name="Fechar", exact=True).last.click()


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = L.cliente(base.MULTI, base.PW_MULTI)
    nb, ids = preparar(c)
    n0, n1 = nb, nb + 1
    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=recebidas&company_id={A}")
        page.get_by_role("heading", name="Notas recebidas").wait_for(timeout=30000)
        page.wait_for_load_state("networkidle")

        # Seleção entre pesquisas.
        buscar(page, str(n0)); caixa(page, n0).click()
        buscar(page, str(n1)); caixa(page, n1).click()
        reg = page.get_by_role("region", name="Seleção").inner_text()
        check("duas pesquisas: '2 notas selecionadas', contando a que não está visível",
              contagem(page) == 2 and "1 nesta página" in reg, reg.replace("\n", " ")[:120])
        buscar(page, str(n0))
        check("retorno à pesquisa anterior: a nota continua marcada", caixa(page, n0).is_checked())

        page.get_by_role("button", name="Limpar filtros").click()
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(800)
        check("'Limpar filtros' limpa a busca e mantém a seleção",
              page.get_by_label("Buscar").input_value() == "" and contagem(page) == 2)

        # Cabeçalho: só a página atual; outra página preservada.
        p1 = ids_da_pagina(page, ids)
        sel = {ids[str(n0)], ids[str(n1)]}
        page.get_by_label("Selecionar todas as linhas desta página").check()
        page.wait_for_timeout(300)
        sel |= set(p1)
        check("cabeçalho marca a página atual e soma às escolhas anteriores", contagem(page) == len(sel), f"{contagem(page)} x {len(sel)}")
        page.get_by_role("button", name="Próxima página").click()
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(800)
        p2 = ids_da_pagina(page, ids)
        x_id = next(i for i in p2 if i not in sel)
        x_num = next(k for k, v in ids.items() if v == x_id)
        caixa(page, x_num).click()
        sel.add(x_id)
        page.get_by_role("button", name="Página anterior").click()
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(800)
        p1 = ids_da_pagina(page, ids)
        page.get_by_label("Selecionar todas as linhas desta página").uncheck()
        page.wait_for_timeout(300)
        sel -= set(p1)
        page.get_by_role("button", name="Próxima página").click()
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(800)
        check("desmarcar o cabeçalho tira só a página atual; a escolha da outra página continua",
              contagem(page) == len(sel) and caixa(page, x_num).is_checked(), f"{contagem(page)} x {len(sel)}")
        page.get_by_role("button", name="Página anterior").click()
        page.wait_for_load_state("networkidle")

        # Ver selecionadas + remoção individual.
        page.get_by_role("button", name="Ver selecionadas").click()
        gaveta = page.get_by_role("dialog", name=re.compile("Notas selecionadas"))
        gaveta.wait_for(timeout=10000)
        lista = gaveta.get_by_role("list", name="Notas selecionadas").locator("li")
        txt = gaveta.inner_text()
        page.screenshot(path=str(EV / "ui-selecao-ver-selecionadas.png"))
        check("'Ver selecionadas' lista todas com número, chave, prestador e valor",
              lista.count() == len(sel) and f"nº {x_num}" in txt and "Prestador:" in txt, f"{lista.count()} itens")
        gaveta.get_by_role("button", name=f"Remover NFS-e nº {x_num} da seleção").click()
        sel.discard(x_id)
        page.wait_for_timeout(300)
        check("remoção individual pela lista", lista.count() == len(sel) and contagem(page) == len(sel))
        gaveta.get_by_role("button", name="Fechar", exact=True).click()

        # Detalhe e navegação interna.
        buscar(page, str(n1))
        page.get_by_role("button", name=re.compile(f"Abrir NFS-e {n1}")).click()
        page.get_by_role("dialog", name=re.compile(f"NFS-e nº {n1}")).wait_for(timeout=15000)
        page.keyboard.press("Escape"); page.wait_for_timeout(500)
        check("abrir e fechar o detalhe preserva a seleção", contagem(page) == len(sel))
        menu(page, "Painel")
        menu(page, "Notas recebidas")
        caixa(page, n1).wait_for(timeout=15000)
        estado = (contagem(page), len(sel), page.get_by_label("Buscar").input_value(), caixa(page, n1).is_checked())
        check("navegação interna (Painel → Recebidas): seleção mantida e consulta retomada",
              estado[0] == estado[1] and estado[2] == str(n1) and estado[3], str(estado))

        # Falha de consulta.
        page.route("**/api/fiscal/v1/companies/*/documents?*", lambda r: r.abort())
        buscar(page, str(n0 + 2))
        falhou = page.get_by_role("button", name=re.compile("Tentar")).count() > 0
        page.unroute("**/api/fiscal/v1/companies/*/documents?*")
        check("falha de consulta (simulada): erro exibido e seleção preservada", falhou and contagem(page) == len(sel))
        buscar(page, "")

        # Falha ao iniciar o download.
        page.route("**/documents/bundles", lambda r: r.fulfill(status=500, content_type="application/problem+json",
                                                                 body=json.dumps({"title": "Falha simulada", "status": 500})))
        page.get_by_role("region", name="Seleção").get_by_role("button", name=re.compile("^Baixar")).click()
        dlg = page.get_by_role("dialog", name="Baixar notas em lote")
        dlg.get_by_role("button", name=re.compile("Gerar arquivo ZIP")).click()
        page.wait_for_timeout(1000)
        check("falha de download (simulada): mensagem e seleção mantida",
              "A seleção foi mantida" in dlg.inner_text() and contagem(page) == len(sel))
        page.unroute("**/documents/bundles")
        dlg.get_by_role("button", name="Cancelar").click()

        # Nota indisponível: o servidor recusa informando só a quantidade; o painel descobre qual e o motivo.
        alvo = sorted(sel)[0]
        alvo_num = next(k for k, v in ids.items() if v == alvo)
        page.route("**/documents/bundles", lambda r: r.fulfill(status=422, content_type="application/problem+json", body=json.dumps(
            {"title": "x", "status": 422, "code": "BUNDLE_DOCUMENTS_NOT_FOUND", "detail": "1 documento(s) não encontrado(s) nesta empresa/ambiente."})))
        page.route(f"**/documents/{alvo}", lambda r: r.fulfill(status=404, content_type="application/problem+json",
                                                               body=json.dumps({"title": "Não encontrado", "status": 404})))
        page.get_by_role("region", name="Seleção").get_by_role("button", name=re.compile("^Baixar")).click()
        dlg.get_by_role("button", name=re.compile("Gerar arquivo ZIP")).click()
        regiao = dlg.get_by_role("region", name="Notas indisponíveis")
        regiao.wait_for(timeout=20000)
        t = regiao.inner_text()
        page.screenshot(path=str(EV / "ui-selecao-indisponivel.png"))
        check("indisponível (resposta simulada): nota identificada com o motivo, sem omissão silenciosa",
              f"nº {alvo_num}" in t and "Não encontrada" in t and t.count("—") == 1, t.replace("\n", " ")[:160])
        regiao.get_by_role("button", name="Remover 1 da seleção").click()
        sel.discard(alvo)
        page.unroute("**/documents/bundles"); page.unroute(f"**/documents/{alvo}")
        page.wait_for_timeout(300)
        check("'Remover da seleção' tira só a indisponível", contagem(page) == len(sel))

        # Download real: exatamente as escolhidas (somam-se mais 3 notas, cada uma de uma pesquisa).
        for n in (nb + 6, nb + 7, nb + 8):
            buscar(page, str(n))
            if not caixa(page, n).is_checked():
                caixa(page, n).click()
            sel.add(ids[str(n)])
        buscar(page, "")
        baixar_e_conferir(page, sel, "desktop")
        check("após o download a seleção continua", contagem(page) == len(sel))

        page.get_by_role("region", name="Seleção").get_by_role("button", name="Limpar seleção").click()
        check("'Limpar seleção' zera a seleção", contagem(page) == 0)

        # Troca de contexto.
        buscar(page, str(n1)); caixa(page, n1).click()
        menu(page, "Notas emitidas")
        check("Recebidas → Emitidas: seleção vazia", contagem(page) == 0)
        buscar(page, str(nb + 100)); caixa(page, nb + 100).click()
        menu(page, "Notas recebidas")
        check("Emitidas → Recebidas: a seleção de emitidas não aparece e a anterior não volta", contagem(page) == 0)
        buscar(page, str(n1))
        if not caixa(page, n1).is_checked():
            caixa(page, n1).click()
        page.get_by_role("combobox", name="Ambiente", exact=True).select_option("mock")
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(800)
        check("trocar o ambiente limpa a seleção", contagem(page) == 0)
        buscar(page, str(n1)); caixa(page, n1).click()
        ws = page.locator("#pf-company-select")
        ws.select_option(B_ID)
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
        noB = contagem(page) == 0
        ws = page.locator("#pf-company-select")
        ws.select_option(A)
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
        check("trocar de workspace limpa a seleção (e voltar não a recupera)", noB and contagem(page) == 0)
        ctx.close()

        # Celular.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, accept_downloads=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        L.entrar(page, base.MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=recebidas&company_id={A}")
        page.get_by_role("heading", name="Notas recebidas").wait_for(timeout=30000)
        page.wait_for_load_state("networkidle")
        escolhidas = [nb + 3, nb + 4, nb + 5]
        for n in escolhidas:
            buscar(page, str(n)); caixa(page, n).tap()
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        check("celular: 3 notas de 3 pesquisas acumuladas, sem rolagem horizontal",
              contagem(page) == 3 and excesso <= 1, f"excesso={excesso}px")
        page.get_by_role("button", name="Ver selecionadas").tap()
        gaveta = page.get_by_role("dialog", name=re.compile("Notas selecionadas"))
        gaveta.wait_for(timeout=10000)
        exc2 = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-selecao-celular.png"))
        check("celular: 'Ver selecionadas' legível e sem rolagem horizontal",
              gaveta.locator("li").count() == 3 and exc2 <= 1, f"excesso={exc2}px")
        gaveta.get_by_role("button", name="Fechar", exact=True).tap()
        baixar_e_conferir(page, {ids[str(n)] for n in escolhidas}, "celular")
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
    out = EV / f"ui-selecao-notas-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
