"""Validação em NAVEGADOR (Playwright) do botão "Excluir" por situação do rascunho, no ambiente DESCARTÁVEL (8098,
governo simulado). Somente dados fictícios; integrações oficiais desligadas; nada é transmitido.

Casos (operador.a: "prepare"; leitor.a: só "view"):
1. Rascunho novo: Excluir habilitado na lista; confirmar exclui (410 depois).
2. Retirado da fila: Excluir habilitado no editor; confirmar exclui.
3. Rejeição só da validação local (LOCAL_*, nada transmitido) e corrigido: volta a "Rascunho" com o vínculo antigo ao
   item encerrado (o caso que escondia o botão). Excluir habilitado; o item e o vínculo continuam registrados.
4. Agendado: Excluir visível e desabilitado com o motivo ("Retire-o da fila"); a API recusa (409).
5. Histórico de transmissão: rejeição da Sefin (simulada) corrigida e NFS-e emitida (simulada): Excluir desabilitado
   com o motivo na lista e no editor; a API recusa (409).
6. Usuário sem "prepare": Excluir desabilitado com o motivo da permissão; a API recusa (403).
Os resultados de rejeição são gravados pelo MESMO caminho do worker (emission._finish), dentro do contêiner do worker
descartável, com relógio adiantado só para alcançar os itens agendados fictícios.
Evidência: docs/evidencias/ui-excluir-vinculo-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_excluir_vinculo_descartavel.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_excluir_rascunho_descartavel as ex  # noqa: E402  (login, clientes e rascunhos fictícios)
import ui_validacao_descartavel as base  # noqa: E402

B, A, EV = base.B, base.A, base.EV
results, check = base.results, base.check
F = ex.F
WORKER = "ctrlfiscal-desc-fiscal-worker-1"

# Executado DENTRO do worker descartável: grava o resultado da tentativa pelo caminho do worker (emission._finish).
REGISTRAR = r'''
import sys
from datetime import timedelta
from ctrl_common.clock import SystemClock
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_fiscal.domain import emission
from ctrl_fiscal.settings import FiscalSettings

class Adiantado:
    def now(self):
        return SystemClock().now() + timedelta(days=30)

alvos = dict(a.split("=") for a in sys.argv[1:])
st = FiscalSettings.from_env("fiscal-worker")
sessions = make_sessionmaker(make_engine(st.database_url))
itens = emission.acquire_items(sessions, Adiantado(), "validacao-ui", limit=10, lease_seconds=120, max_in_flight=50,
                               series="1")
for it in itens:
    codigo = alvos.get(it.draft_id)
    if codigo == "LOCAL":
        resp = {"state": "REJECTED", "errors": [{"code": "LOCAL_E0617",
                "message": "pAliq não pode ir na DPS (fictício). Nada foi transmitido."}]}
    elif codigo == "SEFIN":
        resp = {"state": "REJECTED", "errors": [{"code": "MOCK_REJECTION", "message": "Rejeição SIMULADA para teste."}]}
    else:
        resp = {"state": "DEFER", "error": {"code": "VALIDACAO_UI", "message": "Item fora do roteiro (fictício)."}}
    print(it.draft_id, emission._finish(sessions, Adiantado(), "validacao-ui", it, resp))
'''


def registrar(alvos: dict[str, str]) -> str:
    r = subprocess.run(["docker", "exec", "-i", WORKER, "python", "-", *[f"{k}={v}" for k, v in alvos.items()]],
                       input=REGISTRAR, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    return r.stdout


def agendar(c, d: dict, em: datetime) -> dict:  # noqa: ANN001
    r = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": d["version"]}], "scheduled_at": em.isoformat(),
        "timezone": "America/Sao_Paulo"})
    r.raise_for_status()
    return r.json()


def corrigir(c, d: dict, valor: str) -> dict:  # noqa: ANN001
    cur = c.get(f"{F}/drafts/{d['id']}").json()
    r = c.patch(f"{F}/drafts/{d['id']}", json={"expected_version": cur["version"], "amount": valor})
    r.raise_for_status()
    return r.json()


def botao(page, valor_br: str):  # noqa: ANN001, ANN201
    return ex.linha(page, valor_br).get_by_role("button", name=re.compile("Excluir"))


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    op = ex.cliente(base.EMAIL, base.PW)
    n = int(time.time()) % 100000
    cust = op.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Vínculo {n}", "document_type": "CNPJ", "document": base._cnpj(f"5566{n:08d}")})
    cust.raise_for_status()
    cid = cust.json()["id"]
    futuro = datetime.now(timezone.utc) + timedelta(days=20)

    novo = ex.rascunho(op, cid, "101.01", "Serviço fictício novo")
    retirado = ex.rascunho(op, cid, "202.02", "Serviço fictício retirado da fila")
    ag = op.get(f"{F}/drafts/{retirado['id']}").json()
    agendar(op, ag, futuro)
    ag = op.get(f"{F}/drafts/{retirado['id']}").json()
    w = op.post(f"{F}/drafts/{retirado['id']}/withdraw", json={"expected_version": ag["version"]})
    local = ex.rascunho(op, cid, "303.03", "Serviço fictício rejeitado só na validação local")
    sefin = ex.rascunho(op, cid, "404.04", "Serviço fictício rejeitado pela Sefin (simulada)")
    lote_local = agendar(op, local, futuro)
    agendar(op, sefin, futuro)
    saida = registrar({local["id"]: "LOCAL", sefin["id"]: "SEFIN"})
    local_c = corrigir(op, local, "303.33")
    sefin_c = corrigir(op, sefin, "404.44")
    check("preparação: retirado da fila volta a DRAFT", w.status_code == 200 and w.json()["status"] == "DRAFT")
    check("preparação: rejeição local corrigida volta a DRAFT com o vínculo antigo e exclusão liberada pela API",
          local_c["status"] == "DRAFT" and bool(local_c["current_item_id"]) and local_c["deletion"]["allowed"] is True,
          saida.strip().replace("\n", " | ")[:200])
    check("preparação: rejeição da Sefin corrigida volta a DRAFT com exclusão recusada (histórico de transmissão)",
          sefin_c["status"] == "DRAFT" and sefin_c["deletion"]["reason_code"] == "DRAFT_TRANSMITTED")
    emitida = ex.rascunho(op, cid, "505.05", "Serviço fictício emitido (simulado)")
    b = agendar(op, emitida, datetime.now(timezone.utc) + timedelta(seconds=5))
    for _ in range(60):
        if op.get(f"{F}/batches/{b['batch_id']}").json()["items"][0]["status"] == "AUTHORIZED":
            break
        time.sleep(3)
    agendado = ex.rascunho(op, cid, "606.06", "Serviço fictício agendado")
    agendar(op, agendado, futuro)
    leitor_d = ex.rascunho(op, cid, "707.07", "Serviço fictício visto pelo leitor")
    check("preparação: NFS-e simulada emitida", op.get(f"{F}/drafts/{emitida['id']}").json()["status"] == "AUTHORIZED")

    with sync_playwright() as pwr:
        msgs: list[str] = []
        nativos: list[str] = []
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        page.on("dialog", lambda dlg: (nativos.append(dlg.message), dlg.dismiss()))
        ex.entrar(page, base.EMAIL, base.PW)
        ex.abrir_lista(page)
        page.screenshot(path=str(EV / "ui-excluir-vinculo-lista.png"), full_page=True)

        # 3) Caso que escondia o botão: rejeição local corrigida ("Rascunho", vínculo antigo).
        bt = botao(page, "303,33")
        selo = ex.linha(page, "303,33").locator("td").nth(-2).inner_text().strip()
        check("rejeição local corrigida (DRAFT com vínculo antigo): Excluir habilitado na lista",
              bt.count() == 1 and bt.get_attribute("data-excluir") == "disponivel"
              and bt.get_attribute("aria-disabled") is None, f"selo exibido: {selo}")
        bt.click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho"))
        dlg.wait_for(timeout=15000)
        dlg.get_by_role("button", name="Excluir rascunho").click()
        ex.linha(page, "303,33").wait_for(state="detached", timeout=15000)
        itens = [i for i in op.get(f"{F}/batches/{lote_local['batch_id']}").json()["items"]
                 if i["draft_id"] == local["id"]]
        check("rejeição local corrigida: excluída (410); item LOCAL_VALIDATION e vínculo preservados",
              op.get(f"{F}/drafts/{local['id']}").status_code == 410 and len(itens) == 1
              and itens[0]["status"] == "REJECTED" and itens[0]["stage"] == "LOCAL_VALIDATION"
              and itens[0]["draft"]["deleted"] is True and itens[0]["draft"]["linked"] is True,
              json.dumps([{k: i.get(k) for k in ("status", "stage", "draft")} for i in itens], ensure_ascii=False)[:200])

        # 1) Novo, pela lista.
        bt = botao(page, "101,01")
        check("novo: Excluir habilitado", bt.get_attribute("data-excluir") == "disponivel")
        bt.click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho"))
        dlg.get_by_role("button", name="Excluir rascunho").click()
        ex.linha(page, "101,01").wait_for(state="detached", timeout=15000)
        check("novo: excluído pela lista (410)", op.get(f"{F}/drafts/{novo['id']}").status_code == 410)

        # 2) Retirado da fila, pelo editor.
        ex.linha(page, "202,02").get_by_role("button", name=re.compile("Editar")).click()
        editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        editor.wait_for(timeout=15000)
        be = editor.get_by_role("button", name=re.compile("Excluir rascunho"))
        check("retirado da fila: Excluir habilitado no editor, sem aviso de impedimento",
              be.get_attribute("data-excluir") == "disponivel" and editor.get_by_test_id("motivo-excluir").count() == 0)
        be.click()
        dlg = page.get_by_role("dialog", name=re.compile("Excluir rascunho\\?"))
        dlg.get_by_role("button", name="Excluir rascunho").click()
        editor.wait_for(state="detached", timeout=15000)
        check("retirado da fila: excluído pelo editor (410)", op.get(f"{F}/drafts/{retirado['id']}").status_code == 410)

        # 4) Agendado: desabilitado com motivo; clicar só explica.
        bt = botao(page, "606,06")
        titulo = bt.get_attribute("title") or ""
        bt.click(force=True)  # aria-disabled: o navegador entrega o clique, que só mostra o motivo
        aviso = page.get_by_text(re.compile("Não é possível excluir: Rascunho agendado"))
        aviso.wait_for(timeout=10000)
        page.screenshot(path=str(EV / "ui-excluir-vinculo-agendado.png"))
        rd = op.delete(f"{F}/drafts/{agendado['id']}",
                       params={"expected_version": op.get(f"{F}/drafts/{agendado['id']}").json()["version"]})
        check("agendado: Excluir visível e desabilitado com o motivo; clique só explica; API 409",
              bt.get_attribute("aria-disabled") == "true" and "Retire-o da fila" in titulo
              and page.get_by_role("dialog", name=re.compile("Excluir rascunho")).count() == 0
              and rd.status_code == 409, f"{titulo[:90]} | {rd.status_code}")

        # 5a) Rejeição da Sefin corrigida: lista e editor mostram o impedimento.
        bt = botao(page, "404,44")
        check("histórico de transmissão (rejeição da Sefin): Excluir desabilitado com o motivo na lista",
              bt.get_attribute("aria-disabled") == "true" and "histórico de transmissão" in (bt.get_attribute("title") or ""))
        ex.linha(page, "404,44").get_by_role("button", name=re.compile("Editar")).click()
        editor = page.get_by_role("dialog", name=re.compile("Editar Rascunho"))
        editor.wait_for(timeout=15000)
        motivo = editor.get_by_test_id("motivo-excluir")
        be = editor.get_by_role("button", name=re.compile("Excluir rascunho"))
        page.screenshot(path=str(EV / "ui-excluir-vinculo-editor-transmitido.png"))
        be.click(force=True)
        page.wait_for_timeout(800)
        rs = op.delete(f"{F}/drafts/{sefin['id']}", params={"expected_version": sefin_c["version"]})
        check("histórico de transmissão: editor mostra o motivo, botão desabilitado, sem confirmação; API 409",
              "histórico de transmissão" in motivo.inner_text() and be.get_attribute("aria-disabled") == "true"
              and page.get_by_role("dialog", name=re.compile("Excluir rascunho\\?")).count() == 0
              and rs.status_code == 409, motivo.inner_text()[:120])
        editor.get_by_role("button", name="Cancelar").click()
        page.wait_for_timeout(800)

        # 5b) NFS-e emitida (simulada), na visão Emitidos.
        page.get_by_role("button", name=re.compile("^Emitidos")).click()
        page.wait_for_timeout(2000)
        bt = botao(page, "505,05")
        check("NFS-e emitida: Excluir desabilitado com o motivo",
              bt.get_attribute("aria-disabled") == "true" and "NFS-e emitida" in (bt.get_attribute("title") or ""),
              (bt.get_attribute("title") or "")[:120])
        page.screenshot(path=str(EV / "ui-excluir-vinculo-emitida.png"))
        check("nenhum diálogo nativo do navegador foi aberto", not nativos, "; ".join(nativos))
        b.close()

        # 6) Leitor: desabilitado com o motivo da permissão; API 403.
        b = pwr.chromium.launch()
        page = b.new_page(viewport={"width": 1366, "height": 900})
        page.on("console", lambda m: msgs.append(m.text))
        ex.entrar(page, ex.LEITOR, ex.senha(ex.LEITOR))
        ex.abrir_lista(page)
        bt = botao(page, "707,07")
        leitor = ex.cliente(ex.LEITOR, ex.senha(ex.LEITOR))
        rl = leitor.delete(f"{F}/drafts/{leitor_d['id']}", params={"expected_version": leitor_d["version"]})
        page.screenshot(path=str(EV / "ui-excluir-vinculo-leitor.png"))
        check("sem permissão: Excluir desabilitado com o motivo da permissão; API 403",
              bt.get_attribute("aria-disabled") == "true" and "Preparar rascunhos" in (bt.get_attribute("title") or "")
              and rl.status_code == 403, f"{(bt.get_attribute('title') or '')[:90]} | {rl.status_code}")
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:300])
    out = EV / f"ui-excluir-vinculo-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
