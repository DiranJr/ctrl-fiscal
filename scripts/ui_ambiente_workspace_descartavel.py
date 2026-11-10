"""Validação em NAVEGADOR (Playwright) de "Ambiente e leitura ADN" do workspace no ambiente DESCARTÁVEL.

Somente dados fictícios; governo simulado; nenhum certificado instalado => o conector bloqueia antes de qualquer
chamada externa (nada é consultado nem transmitido). Pré-requisito do roteiro: o .env.descartavel com
NFSE_PRODUCTION_DISTRIBUTION_ENABLED=true e NFSE_RESTRICTED_ENABLED=false (Produção "disponível na instalação",
Produção Restrita desligada — a situação relatada). Cenários (admin.a = admin do workspace novo e de A):
1. Workspace fictício novo (Produção Restrita): o formulário separa a pendência GLOBAL (Produção Restrita desligada na
   instalação) da pendência DO WORKSPACE (sem certificado), mostra os dados por ambiente e só libera "Confirmar troca"
   com a confirmação marcada.
2. Troca para Produção (sem leitura ADN, pois não há certificado): lista mostra Produção; reabrir mostra o atual;
   outro workspace (A) intacto; workspace continua em preparação.
3. Empresa de demonstração (A): a troca é recusada pelo servidor com o motivo, nada muda.
4. Sem permissão (operador.a: A sem "admin"): sem o menu; o servidor recusa (403).
Evidência: docs/evidencias/ui-ambiente-workspace-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_ambiente_workspace_descartavel.py
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
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)
import ui_workspaces_descartavel as ws  # noqa: E402  (clientes, login com novas tentativas)

B, A, EV = base.B, base.A, base.EV
results, check = base.results, base.check
ADM_A = "admin.a@descartavel.example.com"
ADM = "/api/core/v1/admin/workspaces"


def situacao(c, wid: str) -> dict:  # noqa: ANN001
    w = next(x for x in c.get(ADM).json() if x["id"] == wid)
    return {"environment": w["environment"], "status": w["status"], "name": w["name"], "company": w["company"]}


def abrir(page, nome: str):  # noqa: ANN001, ANN201
    ws.tela_workspaces(page)
    page.locator(".pf-ws-item", has_text=nome).get_by_role("button", name=re.compile("^Ambiente e leitura ADN")).click()
    dlg = page.get_by_role("dialog", name="Ambiente e leitura ADN")
    dlg.get_by_test_id("ambiente-workspace").wait_for(timeout=30000)
    page.wait_for_timeout(1200)  # impacto (fiscal) carrega em seguida
    return dlg


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sa = ws.cliente(ws.ADMIN, ws.PW_ADMIN)
    adm_id = next(u["id"] for u in sa.get("/api/core/v1/admin/users", params={"page_size": 100}).json()["items"]
                  if u["email"] == ADM_A)
    nome = f"Filial Fictícia Ambiente {int(time.time()) % 100000}"
    r = sa.post(ADM, json={"name": nome, "company": {"cnpj": ws.cnpj_ficticio(int(time.time()) % 10**9), "name": nome},
                           "admin": {"user_id": adm_id}})
    check("preparação: workspace fictício novo em Produção Restrita, admin.a como administrador",
          r.status_code == 201 and r.json()["environment"] == "restricted", r.text[:160])
    novo = r.json()["id"]
    antes_a = situacao(sa, A)

    with sync_playwright() as pwr:
        msgs: list[str] = []
        nativos: list[str] = []
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 1000})
        page.on("console", lambda m: msgs.append(m.text))
        page.on("dialog", lambda dlg: (nativos.append(dlg.message), dlg.dismiss()))
        ws.entrar(page, ADM_A, ws.senha(ADM_A))
        dlg = abrir(page, nome)
        txt = dlg.inner_text()
        rest = dlg.locator("[data-env=restricted]").first
        prod = dlg.locator("[data-env=production]").first
        page.screenshot(path=str(EV / "ui-ambiente-opcoes.png"), full_page=True)
        check("atual: Produção Restrita; Produção Restrita indisponível com pendência GLOBAL (instalação)",
              "Atual: Produção Restrita" in txt and rest.locator("input[type=radio]").is_disabled()
              and "Pendência global (instalação)" in rest.inner_text() and "NFSE_RESTRICTED_ENABLED" in rest.inner_text(),
              rest.inner_text()[:200].replace("\n", " | "))
        check("Produção disponível; sem certificado é pendência DESTE workspace e a leitura ADN fica indisponível",
              not prod.locator("input[type=radio]").is_disabled() and "Pendência deste workspace" in prod.inner_text()
              and "Sem certificado" in prod.inner_text()
              and dlg.get_by_role("checkbox", name=re.compile("Habilitar a leitura ADN")).is_disabled(),
              prod.inner_text()[:200].replace("\n", " | "))
        check("dados por ambiente e o que acontece na troca são mostrados antes de confirmar",
              "Dados por ambiente" in (dlg.get_by_role("region", name="Dados por ambiente").get_attribute("aria-label") or "")
              and "Agendamentos não iniciados" in txt and "Emissão e cancelamento não mudam" in txt)
        confirmar = dlg.get_by_role("button", name="Confirmar troca")
        check("“Confirmar troca” só com a confirmação marcada", confirmar.is_disabled())
        dlg.get_by_role("checkbox", name=re.compile("Confirmo trocar o ambiente")).check()
        check("confirmação nomeia origem, destino e CNPJ",
              re.search(r"Produção Restrita para\s+Produção sem leitura ADN no CNPJ", dlg.inner_text()) is not None
              and not confirmar.is_disabled())
        confirmar.click()
        dlg.wait_for(state="detached", timeout=20000)
        page.get_by_text(re.compile(f"Ambiente de “{re.escape(nome)}”: Produção")).wait_for(timeout=10000)
        page.wait_for_timeout(1000)
        item = page.locator(".pf-ws-item", has_text=nome)
        depois = situacao(sa, novo)
        check("troca salva: lista mostra Produção; workspace continua em preparação; A intacto",
              "· Produção" in item.inner_text() and "Produção Restrita" not in item.inner_text()
              and depois["environment"] == "production"
              and depois["status"] == "PREPARING" and situacao(sa, A) == antes_a, item.inner_text()[:160])
        page.screenshot(path=str(EV / "ui-ambiente-trocado.png"))
        dlg = abrir(page, nome)
        check("reabrir: atual Produção, sem leitura ADN", "Atual: Produção · Leitura ADN: nenhuma" in dlg.inner_text())
        dlg.get_by_role("button", name="Cancelar").click()

        # 3) Empresa de demonstração: recusada pelo servidor.
        dlg = abrir(page, antes_a["name"])
        dlg.get_by_role("checkbox", name=re.compile("Confirmo trocar o ambiente")).check()
        dlg.get_by_role("button", name="Confirmar troca").click()
        dlg.get_by_text("Nada foi alterado").first.wait_for(timeout=15000)
        check("empresa de demonstração: recusada com o motivo e nada muda",
              "demonstração" in dlg.inner_text() and situacao(sa, A) == antes_a, dlg.inner_text()[-200:].replace("\n", " | "))
        page.screenshot(path=str(EV / "ui-ambiente-demo-recusada.png"))
        check("nenhum diálogo nativo do navegador foi aberto", not nativos, "; ".join(nativos))
        b.close()

        # 4) Sem permissão.
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        ws.entrar(page, ws.OPER, ws.senha(ws.OPER))
        page.goto(f"{B}/fiscal/?view=dashboard&company_id={A}")
        page.wait_for_timeout(2500)
        menu = page.get_by_role("button", name="Workspaces").count() + page.get_by_role("link", name="Workspaces").count()
        oa = ws.cliente(ws.OPER, ws.senha(ws.OPER))
        g = oa.get(f"/api/core/v1/companies/{A}/environment")
        p = oa.post(f"/api/core/v1/companies/{A}/environment", json={"environment": "production", "adn_read": False,
                    "confirmation": {"company_id": A, "cnpj": antes_a["company"]["cnpj"], "from_environment": "mock",
                                     "to_environment": "production"}})
        check("sem permissão: sem menu Workspaces; o servidor recusa (403) consulta e troca",
              menu == 0 and g.status_code == 403 and p.status_code == 403 and situacao(sa, A) == antes_a,
              f"{menu} | {g.status_code} | {p.status_code}")
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:300])
    out = EV / f"ui-ambiente-workspace-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
