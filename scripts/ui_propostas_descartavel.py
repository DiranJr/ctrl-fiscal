"""Validação em NAVEGADOR (Playwright) das PROPOSTAS DE REVISÃO de perfis no ambiente DESCARTÁVEL (8098).

Somente dados fictícios. Fluxo:
1. Notas fictícias emitidas pela empresa A em dois serviços → perfis PENDENTES; uma terceira nota, importada depois,
   fica num grupo SEM perfil (mostrado como pendência, sem criar configuração).
2. Propostas registradas pela rota interna (como na AEL): não alteram campos e bloqueiam a aprovação.
3. Rascunho com dados próprios (descrição, endereço da obra, valor) e o perfil aplicado.
4. Permissões: leitor vê a proposta sem ações; operador sem "admin" e outro workspace não resolvem (API).
5. Administrador: visualiza, aplica a alternativa (confirmação obrigatória; perfil continua pendente), descarta a outra
   proposta com justificativa e só então aprova o perfil, em passo separado.
6. Auditoria (banco descartável) e preservação dos dados do rascunho.
Evidência: docs/evidencias/ui-propostas-<data>.json (+ capturas). Nada é transmitido a ambiente oficial.

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_propostas_descartavel.py
"""
from __future__ import annotations

import json
import re
import subprocess
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
PROJ = "ctrlfiscal-desc"
CORE = f"/api/core/v1/companies/{A}/service-profiles"
X, Y, Z = "070201:101021100", "071601:101032000", "070202:101032000"


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
    """Login no navegador com nova tentativa (o limite de tentativas de login pode estar ativo após logins pela API)."""
    for tentativa in range(4):
        try:
            base.login(page, email, pw)
            return
        except Exception:  # noqa: BLE001
            if tentativa == 3:
                raise
            time.sleep(20)


def nota(prest: str, toma: str, numero: int, ctn: str, nbs: str) -> bytes:
    x = base._xml_como_187(prest, toma, numero).decode()
    return (x.replace("<cTribNac>072101</cTribNac>", f"<cTribNac>{ctn}</cTribNac>")
             .replace("<cNBS>101010100</cNBS>", f"<cNBS>{nbs}</cNBS>").encode())


def importar(c: httpx.Client, xml: bytes, numero: int) -> dict:
    c.post(f"/api/fiscal/v1/companies/{A}/documents/import",
           files=[("files", (f"p{numero}.xml", xml, "application/xml"))]).raise_for_status()
    for _ in range(40):
        items = c.get(f"/api/fiscal/v1/companies/{A}/documents",
                      params={"direction": "ISSUED", "environment": "mock", "page_size": 100}).json()["items"]
        doc = next((d for d in items if d["number"] == str(numero)), None)
        if doc:
            return doc
        time.sleep(2)
    raise RuntimeError(f"nota {numero} não importada")


def perfis(c: httpx.Client) -> dict:
    return {p["origin_key"]: p for p in c.get(CORE).json()}


def registrar(itens: list[dict]) -> list[dict]:
    """Mesma rota interna usada na AEL, chamada de dentro do fiscal-api descartável (credencial não sai do container)."""
    codigo = (
        "import sys, json, httpx\n"
        "from ctrl_fiscal.settings import FiscalSettings\n"
        "from ctrl_common.service_auth import service_headers\n"
        "s = FiscalSettings.from_env('fiscal-api'); body = json.load(sys.stdin)\n"
        f"url = s.core_api_url.rstrip('/') + '/internal/v1/companies/{A}/service-profiles/proposals'\n"
        "out = [httpx.post(url, json={**body, 'items': [it]}, headers=service_headers(s.core_service_name, "
        "s.core_api_key), timeout=60).json() for it in body.pop('items')]\n"
        "print(json.dumps(out))\n")
    r = subprocess.run(["docker", "exec", "-i", f"{PROJ}-fiscal-api-1", "python", "-c", codigo],
                       input=json.dumps({"actor_type": "system", "actor_id": "teste-descartavel", "items": itens}),
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def sql(q: str) -> list[list[str]]:
    r = subprocess.run(["docker", "exec", "-i", f"{PROJ}-mysql-1", "sh", "-c",
                        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -B '
                        '--default-character-set=utf8mb4'], input=q + ";\n", capture_output=True, text=True, check=True)
    return [linha.split("\t") for linha in r.stdout.strip().splitlines()]


ALT = {"id": "A", "rotulo": "Serviço de construção civil sobre bem imóvel (teste)",
       "condicao": "Somente se a contabilidade confirmar o enquadramento (fictício).",
       "campos": {"ibs_cbs": {"operation_indicator": "020201", "cst": "200", "class_code": "200046"}},
       "fundamento": ["LC 214/2025, art. 11, II", "LC 214/2025, arts. 252, V, e 261"]}


def proposta(pid: str, alternativas: list, perguntas: list[str]) -> dict:
    return {"id": pid, "resumo": f"Proposta fictícia {pid} para validação da interface.", "alternativas": alternativas,
            "perguntas": [{"id": q, "texto": f"Pergunta fictícia {q}?", "responsavel": "Contabilidade"} for q in perguntas],
            "proxima_acao": "Responder as perguntas fictícias.",
            "analise": {"servico": ["Serviço fictício"], "por_emissao": ["Endereço da obra", "Valores"]}}


def comparavel(d: dict) -> dict:
    return {k: d[k] for k in ("customer_id", "competence", "service_date", "service", "amount", "deductions",
                              "tax_settings", "service_profile_id", "service_profile_version", "source_document_id")}


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    adm = cliente(base.MULTI, base.PW_MULTI)
    prest = next(x for x in adm.get("/api/core/v1/companies").json()["items"] if x["id"] == A)["cnpj"]
    n0 = 88000 + int(time.time()) % 900
    toma = base._cnpj(f"6655{n0:08d}")
    adm.post(f"/api/core/v1/companies/{A}/customers",
             json={"name": f"Tomador Propostas {n0}", "document_type": "CNPJ", "document": toma})
    doc_x = importar(adm, nota(prest, toma, n0, "070201", "101021100"), n0)
    importar(adm, nota(prest, toma, n0 + 1, "071601", "101032000"), n0 + 1)
    adm.post(f"/api/fiscal/v1/companies/{A}/service-profiles/prepare").raise_for_status()
    importar(adm, nota(prest, toma, n0 + 2, "070202", "101032000"), n0 + 2)  # grupo SEM perfil (não preparado)
    p0 = perfis(adm)
    check("preparação: perfis pendentes dos dois serviços; terceiro grupo sem perfil",
          p0[X]["status"] == p0[Y]["status"] == "PENDING_REVIEW" and Z not in p0)
    reg = registrar([{"origin_key": X, "proposal": proposta("PR-T-1", [ALT], ["Q1", "Q4"])},
                     {"origin_key": X, "proposal": proposta("PR-T-2", [], ["Q7"])},
                     {"origin_key": Y, "proposal": proposta("PR-T-3", [], ["Q6"])}])
    p1 = perfis(adm)
    campos = ("national_code", "nbs_code", "issqn_taxation", "pis_cofins_cst", "ibs_cbs", "field_sources", "status")
    check("registro: 3 propostas, nenhum campo de perfil alterado, aprovação bloqueada",
          all(r.get("registered") for r in reg) and all(p1[k][f] == p0[k][f] for k in (X, Y) for f in campos)
          and not p1[X]["can_approve"] and not p1[Y]["can_approve"]
          and any(i["code"] == "PROPOSTA_EM_ABERTO" for i in p1[X]["pending"]), json.dumps(reg)[:200])

    # Rascunho com dados próprios e o perfil X aplicado (antes de qualquer decisão sobre a proposta).
    d = adm.post(f"/api/fiscal/v1/companies/{A}/drafts/from-document", json={"source_document_id": doc_x["id"]},
                 headers={"Idempotency-Key": f"prop-{n0}"})
    d.raise_for_status()
    d = d.json()
    d = adm.patch(f"/api/fiscal/v1/companies/{A}/drafts/{d['id']}", json={
        "expected_version": d["version"], "service_profile_id": p1[X]["id"], "amount": "4321.09",
        "service": {"description": "Rascunho fictício — dados próprios desta emissão", "national_code": "070201",
                    "nbs_code": "101021100", "obra_address": {"postal_code": "68515000", "street": "Rua Fictícia da Obra",
                                                              "number": "77", "district": "Bairro Fictício"}}})
    check("rascunho: criado com dados próprios e perfil pendente aplicado", d.status_code == 200, d.text[:160])
    antes = comparavel(d.json())

    # Permissões pela API: leitor e operador (sem admin) não resolvem; outro workspace não enxerga.
    url = f"{CORE}/{p1[X]['id']}/proposals/PR-T-1/resolve"
    corpo = {"expected_version": p1[X]["version"], "decision": "DISCARD", "note": "tentativa sem permissão"}
    leitor = cliente("leitor.a@descartavel.example.com", senha("leitor.a@descartavel.example.com"))
    oper = cliente(base.EMAIL, base.PW)
    outro = cliente("operador.b@descartavel.example.com", senha("operador.b@descartavel.example.com"))
    rl, ro = leitor.post(url, json=corpo), oper.post(url, json=corpo)
    rb = outro.post(f"/api/core/v1/companies/{base.B_ID}/service-profiles/{p1[X]['id']}/proposals/PR-T-1/resolve", json=corpo)
    ra = outro.post(url, json=corpo)
    # Outro workspace: a permissão é conferida antes da busca — 403/404 sem revelar se o perfil existe.
    check("permissões: leitor 403, operador sem admin 403, outro workspace recusado — nada resolvido",
          rl.status_code == 403 and ro.status_code == 403 and rb.status_code in (403, 404) and ra.status_code in (403, 404)
          and perfis(adm)[X]["version"] == p1[X]["version"],
          f"leitor={rl.status_code} operador={ro.status_code} B={rb.status_code} B→A={ra.status_code}")

    with sync_playwright() as pwr:
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        msgs: list[str] = []
        page.on("console", lambda m: msgs.append(m.text))
        # Leitor: vê a proposta, sem botões de decisão.
        entrar(page, "leitor.a@descartavel.example.com", senha("leitor.a@descartavel.example.com"))
        page.goto(f"{B}/fiscal/?view=configuracoes&company_id={A}")
        card = page.locator(".pf-perfil", has_text="Proposta de revisão PR-T-1")
        card.wait_for(timeout=30000)
        page.wait_for_timeout(1000)
        card.screenshot(path=str(EV / "ui-propostas-leitor.png"))
        check("leitor: vê proposta, alternativas e perguntas, sem aplicar/descartar",
              "Em aberto" in card.inner_text() and "Pergunta fictícia Q1" in card.inner_text()
              and card.get_by_role("button", name=re.compile("Aplicar alternativa|Descartar proposta")).count() == 0)
        b.close()

        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page, base.MULTI, base.PW_MULTI)
        page.goto(f"{B}/fiscal/?view=configuracoes&company_id={A}")
        card = page.locator(".pf-perfil", has_text="Proposta de revisão PR-T-1")
        card.wait_for(timeout=30000)
        page.wait_for_timeout(1500)
        pend = page.locator(".pf-perfil-pend[role=status]", has_text="sem perfil")
        texto_pend = pend.inner_text() if pend.count() else ""
        pend.first.screenshot(path=str(EV / "ui-propostas-grupo-sem-perfil.png")) if pend.count() else None
        check("grupo sem perfil aparece como pendência explícita, sem configuração criada",
              "07.02.02" in texto_pend and "101032000" in texto_pend and Z not in perfis(adm), texto_pend[:160])
        aprovar = card.get_by_role("button", name=re.compile("^Aprovar$"))
        check("admin: com proposta aberta o perfil não pode ser aprovado",
              aprovar.count() == 0 or aprovar.first.is_disabled())
        card.get_by_text("Análise das notas e tabelas").first.click()
        page.wait_for_timeout(400)
        card.screenshot(path=str(EV / "ui-propostas-admin-aberta.png"))

        # Aplicar alternativa A: confirmação obrigatória; perfil continua pendente.
        card.get_by_role("button", name="Aplicar alternativa A").click()
        dlg = page.get_by_role("dialog", name=re.compile("Aplicar alternativa A"))
        dlg.wait_for(timeout=15000)
        btn = dlg.get_by_role("button", name="Aplicar alternativa")
        bloqueado_vazio = btn.is_disabled()
        dlg.get_by_label(re.compile("Confirmação recebida")).fill("Contabilidade confirmou (teste fictício)")
        bloqueado_sem_check = btn.is_disabled()
        dlg.get_by_text(re.compile("Entendo que o perfil NÃO será aprovado")).click()
        page.screenshot(path=str(EV / "ui-propostas-aplicar-modal.png"))
        btn.click()
        page.wait_for_timeout(2500)
        p2 = perfis(adm)
        check("aplicar: exige justificativa e confirmação; aplica a alternativa e o perfil segue pendente",
              bloqueado_vazio and bloqueado_sem_check and p2[X]["status"] == "PENDING_REVIEW"
              and p2[X]["ibs_cbs"] == ALT["campos"]["ibs_cbs"]
              and p2[X]["field_sources"]["ibs_cbs"].get("proposta") == "PR-T-1/A" and not p2[X]["can_approve"],
              f"status={p2[X]['status']} can_approve={p2[X]['can_approve']}")

        # Descartar a segunda proposta com justificativa.
        card = page.locator(".pf-perfil", has_text="Proposta de revisão PR-T-2")
        card.locator(".pf-perfil-pend", has_text="PR-T-2").get_by_role("button", name="Descartar proposta").click()
        dlg = page.get_by_role("dialog", name=re.compile("Descartar proposta PR-T-2"))
        dlg.wait_for(timeout=15000)
        dlg.get_by_label(re.compile("Motivo do descarte")).fill("Escopo já coberto pela alternativa aplicada (teste)")
        dlg.get_by_text("Esta proposta não se aplica a este serviço.").click()
        dlg.get_by_role("button", name="Descartar proposta").click()
        page.wait_for_timeout(2500)
        p3 = perfis(adm)
        prop2 = next(s for s in p3[X]["suggestions"] if s.get("id") == "PR-T-2")
        check("descartar: registra justificativa, não altera campos e libera a aprovação (passo separado)",
              prop2["status"] == "DESCARTADA" and prop2.get("nota", "").startswith("Escopo")
              and p3[X]["ibs_cbs"] == p2[X]["ibs_cbs"] and p3[X]["status"] == "PENDING_REVIEW" and p3[X]["can_approve"])
        card = page.locator(".pf-perfil", has_text="Proposta de revisão PR-T-1")
        card.screenshot(path=str(EV / "ui-propostas-resolvidas.png"))

        # Aprovar separadamente.
        card.get_by_role("button", name=re.compile("^Aprovar$")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Aprovar perfil"))
        dlg.wait_for(timeout=15000)
        dlg.get_by_text(re.compile("Conferi os códigos")).click()
        dlg.get_by_role("button", name="Aprovar perfil").click()
        page.wait_for_timeout(2500)
        p4 = perfis(adm)
        check("aprovação separada: perfil aprovado pela conta administradora; outro perfil segue bloqueado",
              p4[X]["status"] == "APPROVED" and p4[X]["approved_by"] and p4[Y]["status"] == "PENDING_REVIEW"
              and not p4[Y]["can_approve"])
        card = page.locator(".pf-perfil", has_text="Proposta de revisão PR-T-1")
        card.get_by_role("button", name=re.compile("Histórico")).click()
        dlg = page.get_by_role("dialog", name=re.compile("Histórico"))
        dlg.wait_for(timeout=15000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(EV / "ui-propostas-historico.png"))
        hist = dlg.inner_text()
        check("histórico mostra registro, aplicação, descarte e aprovação",
              all(t in hist for t in ("Proposta de revisão registrada", "Alternativa da proposta aplicada",
                                      "Proposta de revisão descartada", "Aprovado")))
        page.keyboard.press("Escape")
        v = base.csp_violations(msgs)
        check("propostas: sem violação de CSP nem erro de console", not v, "; ".join(v)[:160])
        b.close()

    # Auditoria (banco descartável): ações, autores e justificativas.
    linhas = sql(f"SELECT action, actor_type, COALESCE(actor_id,''), "
                 f"COALESCE(JSON_UNQUOTE(JSON_EXTRACT(details,'$.note')),'') FROM ctrl_core.audit_events "
                 f"WHERE target_id='{p1[X]['id']}' ORDER BY occurred_at, action")
    acoes = [linha[0] for linha in linhas]
    uid = p4[X]["approved_by"]
    pessoa = {linha[0]: linha for linha in linhas if linha[0] != "service_profile.proposal_registered"}
    check("auditoria: registro pelo sistema; aplicação, descarte e aprovação pela pessoa, com justificativa",
          acoes.count("service_profile.proposal_registered") == 2
          and all(linha[1] == "system" for linha in linhas if linha[0] == "service_profile.proposal_registered")
          and all(pessoa[a][2] == uid for a in ("service_profile.proposal_applied", "service_profile.proposal_discarded",
                                                "service_profile.approved"))
          and pessoa["service_profile.proposal_applied"][3].startswith("Contabilidade confirmou")
          and pessoa["service_profile.proposal_discarded"][3].startswith("Escopo"), " | ".join(acoes))

    # Preservação do rascunho: nenhum dado próprio da emissão mudou; na revisão, a mudança do perfil vira aviso.
    did = d.json()["id"]
    depois = adm.get(f"/api/fiscal/v1/companies/{A}/drafts/{did}").json()
    rev = adm.post(f"/api/fiscal/v1/companies/{A}/drafts/{did}/validate").json()
    codigos = [i["code"] for i in rev.get("issues") or []]
    check("rascunho preservado: dados próprios intactos; a revisão aponta que o perfil mudou desde a aplicação",
          comparavel(depois) == antes and "SERVICE_PROFILE_CHANGED" in codigos, ", ".join(codigos)[:200])



def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:300])
    out = EV / f"ui-propostas-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
