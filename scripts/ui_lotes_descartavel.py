"""Validação em NAVEGADOR (Playwright) do DETALHE DOS LOTES no ambiente DESCARTÁVEL (8098, governo simulado).

Somente dados fictícios; nada é transmitido a ambiente oficial. Conta multi.ab (operador + administrador da empresa A).
Reprodução do defeito: lote com 1 item REJEITADO mostrava "0 itens" (a tela usava o resumo da listagem, sem itens).
Os desfechos são gravados no banco DESCARTÁVEL com os mesmos campos que o worker grava (o governo simulado só
rejeita/fica incerto em testes em processo); o lote "Emitir agora" passa pelo worker simulado de verdade.
Cenários: lote rejeitado (1/1), lote misto (rejeição oficial, validação local, incerto, retirado, pendente),
autorizado, falha da API (não vira lista vazia), acesso entre workspaces e celular.
Evidência: docs/evidencias/ui-lotes-<data>.json (+ capturas, não versionadas).

Uso: %LOCALAPPDATA%\\ctrl-fiscal-tools\\pwv\\Scripts\\python -I scripts\\ui_lotes_descartavel.py
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

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ui_validacao_descartavel as base  # noqa: E402  (fixtures, login e contas do descartável)

B, A, EV, SEC = base.B, base.A, base.EV, base.SEC
results, check = base.results, base.check
F = f"/api/fiscal/v1/companies/{A}"
PROJ = "ctrlfiscal-desc"


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
        except Exception:  # noqa: BLE001 - limite de tentativas de login após logins pela API
            if tentativa == 3:
                raise
            time.sleep(20)


def sql(q: str) -> str:
    r = subprocess.run(["docker", "exec", "-i", f"{PROJ}-mysql-1", "sh", "-c",
                        'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysql -uroot -N -B '
                        '--default-character-set=utf8mb4 ctrl_fiscal'], input=q, capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def rascunho(c: httpx.Client, cust: str, valor: str) -> dict:
    r = c.post(f"{F}/drafts", json={"customer_id": cust, "competence": datetime.now().strftime("%Y-%m"), "amount": valor,
                                    "service": {"national_code": "010101", "description": f"Serviço fictício {valor}"},
                                    "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}})
    r.raise_for_status()
    return r.json()


def lote(c: httpx.Client, ds: list[dict], modo: str = "SCHEDULED") -> str:
    body = {"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in ds], "timezone": "America/Sao_Paulo"}
    if modo == "NOW":
        body["mode"] = "NOW"
    else:
        body["scheduled_at"] = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    r = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json=body)
    r.raise_for_status()
    return r.json()["batch_id"]


def marcar(draft_id: str, status: str, *, erros: list | None = None, codigo: str | None = None, msg: str | None = None,
           fase: str = "NONE", dps: int | None = None) -> None:
    """Grava no banco DESCARTÁVEL o desfecho como o worker grava (campos do item e do rascunho)."""
    if dps:  # número único por execução (a série/número da DPS é única por empresa e ambiente)
        dps = (int(time.time()) % 1_000_000) * 10 + dps % 10
    res = "NULL" if erros is None else "'" + json.dumps({"errors": erros, "simulated": True}, ensure_ascii=False).replace("'", "''") + "'"
    fim = "UTC_TIMESTAMP(6)" if status in ("REJECTED", "WITHDRAWN") else "NULL"
    prox = "DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 10 DAY)"
    dps_sql = (f", dps_series='00900', dps_number={dps}, dps_id=CONCAT('DPS15055362', LPAD({dps}, 34, '0'))"
               if dps else "")
    sql(f"""UPDATE batch_items SET status='{status}', submit_phase='{fase}', result={res}, finished_at={fim},
        started_at=UTC_TIMESTAMP(6), attempts=1, next_attempt_at={prox},
        last_error_code={'NULL' if not codigo else "'" + codigo + "'"},
        last_error_message={'NULL' if not msg else "'" + msg.replace("'", "''") + "'"}, updated_at=UTC_TIMESTAMP(6){dps_sql}
        WHERE draft_id='{draft_id}';
        UPDATE drafts SET status='{'DRAFT' if status == 'WITHDRAWN' else status}'{", current_item_id=NULL" if status == 'WITHDRAWN' else ''}
        WHERE id='{draft_id}';""")


def abrir_lote(page, prefixo: str):  # noqa: ANN001, ANN201
    page.goto(f"{B}/fiscal/?view=agendamentos&company_id={A}")
    page.get_by_role("heading", name=re.compile("Lotes")).first.wait_for(timeout=30000)
    page.wait_for_timeout(1500)
    page.locator("tr", has_text=prefixo).get_by_role("button", name=re.compile("Ver Itens")).click()
    dlg = page.get_by_role("dialog", name=re.compile(f"Detalhes do Lote #{prefixo[:8]}"))
    dlg.wait_for(timeout=15000)
    return dlg


def roteiro() -> None:  # noqa: PLR0915 - roteiro linear de validação
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    c = cliente(base.MULTI, base.PW_MULTI)
    n = int(time.time()) % 100000
    cust = c.post(f"/api/core/v1/companies/{A}/customers", json={
        "name": f"Tomador Fictício Lotes {n}", "document_type": "CNPJ", "document": base._cnpj(f"4433{n:08d}")}).json()
    # Lote rejeitado (reprodução do caso: total 1, 1/1, "Concluído com Rejeições").
    r1 = rascunho(c, cust["id"], "111.01")
    b1 = lote(c, [r1])
    marcar(r1["id"], "REJECTED", fase="SUBMITTING", dps=901, codigo="REJECTED", msg="Rejeição confirmada; corrija o rascunho.",
           erros=[{"code": "E0310", "message": "Código de tributação nacional inexistente (simulação)."},
                  {"code": "E0316", "message": "Código NBS inexistente (simulação)."}])
    # Lote misto.
    ds = [rascunho(c, cust["id"], f"2{k}2.02") for k in range(5)]
    b2 = lote(c, ds)
    marcar(ds[0]["id"], "REJECTED", fase="SUBMITTING", dps=902, codigo="REJECTED", msg="Rejeição confirmada; corrija o rascunho.",
           erros=[{"code": "E0617", "message": "Alíquota informada com convênio ativo (simulação)."}])
    marcar(ds[1]["id"], "REJECTED", dps=903, codigo="REJECTED", msg="Rejeição confirmada; corrija o rascunho.",
           erros=[{"code": "LOCAL_XSD", "message": "Elemento cNBS fora do padrão do XSD (simulação)."}])
    marcar(ds[2]["id"], "AWAITING_CONFIRMATION", fase="SUBMITTING", dps=904, codigo="TIMEOUT",
           msg="Sem confirmação do processamento; será reconciliado por consulta à DPS.")
    marcar(ds[3]["id"], "WITHDRAWN")
    # ds[4] fica pendente (SCHEDULED, no futuro).
    # Autorizado de verdade (worker simulado, "Emitir agora").
    r3 = rascunho(c, cust["id"], "333.03")
    b3 = lote(c, [r3], "NOW")
    for _ in range(40):
        if c.get(f"{F}/batches/{b3}").json()["items"][0]["status"] == "AUTHORIZED":
            break
        time.sleep(1.5)
    check("preparação: lotes fictícios (rejeitado, misto e autorizado)", True)

    with sync_playwright() as pwr:
        msgs: list[str] = []
        b = pwr.chromium.launch()
        ctx = b.new_context(viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page, base.MULTI, base.PW_MULTI)

        dlg = abrir_lote(page, b1[:12])
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        t = dlg.inner_text()
        page.screenshot(path=str(EV / "ui-lotes-rejeitado.png"))
        check("lote rejeitado (1/1): mostra o item rejeitado (não '0 itens')",
              dlg.locator(".pf-lote-item").count() == 1 and "Nenhum registro" not in t and "1 de 1 item(ns)" in t)
        check("item: tomador, competência, valor, etapa e códigos oficiais com mensagens",
              cust["name"] in t and "R$ 111,01" in t and "Rejeição oficial da Sefin" in t and "E0310" in t and "E0316" in t
              and "Transmitido: Sim" in t)
        dlg.get_by_text("Datas, referências e rascunho de origem").click()
        t = dlg.inner_text()
        check("item: datas, referências da DPS/submissão e rascunho de origem",
              "Série / número da DPS" in t and "00900 / " in t and "Submissão" in t and r1["id"][:8] in t
              and "Rascunho de origem" in t and "O que fazer" in t)
        page.keyboard.press("Escape")

        dlg = abrir_lote(page, b2[:12])
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        t = dlg.inner_text()
        page.screenshot(path=str(EV / "ui-lotes-misto.png"), full_page=True)
        etapas = [dlg.locator(".pf-lote-item").nth(k).get_attribute("data-stage") for k in range(dlg.locator(".pf-lote-item").count())]
        check("lote misto: todos os 5 itens com a etapa de cada um",
              sorted(etapas) == sorted(["OFFICIAL_REJECTION", "LOCAL_VALIDATION", "UNCERTAIN", "WITHDRAWN", "QUEUED"]), str(etapas))
        check("validação local diferenciada da rejeição oficial e 'Transmitido: Não'",
              "Validação local antes do envio" in t and "LOCAL_XSD" in t and "Nenhuma DPS foi transmitida" in t)
        check("resultado incerto: aviso para não reenviar e reconciliação oficial",
              "Resultado incerto" in t and "Não reenvie" in t and "A confirmar pela reconciliação oficial" in t)
        check("retirado e pendente listados", "Retirado da fila antes do envio" in t and "Na fila" in t)
        page.keyboard.press("Escape")

        dlg = abrir_lote(page, b3[:12])
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        check("lote autorizado: etapa autorizada e número da NFS-e", "Autorizada" in dlg.inner_text())
        page.keyboard.press("Escape")

        # Falha da API: não vira lista vazia.
        page.route(re.compile(r".*/batches/[0-9a-f-]{36}$"), lambda r: r.fulfill(
            status=503, content_type="application/problem+json",
            body=json.dumps({"type": "about:blank", "title": "Indisponível", "status": 503, "detail": "Falha simulada."})))
        dlg = abrir_lote(page, b1[:12])
        dlg.get_by_text("Não foi possível carregar os itens do lote").wait_for(timeout=15000)
        t = dlg.inner_text()
        page.screenshot(path=str(EV / "ui-lotes-falha-api.png"))
        check("falha da API: erro claro, sem 'lista vazia', com 'Tentar novamente'",
              "não significa que o lote esteja vazio" in t and "Nenhum registro" not in t
              and dlg.get_by_role("button", name="Tentar novamente").count() == 1)
        page.unroute(re.compile(r".*/batches/[0-9a-f-]{36}$"))
        dlg.get_by_role("button", name="Tentar novamente").click()
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        check("tentar novamente carrega os itens", dlg.locator(".pf-lote-item").count() == 1)
        ctx.close()

        # Celular.
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = ctx.new_page()
        page.on("console", lambda m: msgs.append(m.text))
        entrar(page, base.MULTI, base.PW_MULTI)
        dlg = abrir_lote(page, b2[:12])
        dlg.locator(".pf-lote-item").first.wait_for(timeout=15000)
        excesso = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.screenshot(path=str(EV / "ui-lotes-celular.png"))
        check("celular: itens legíveis sem rolagem horizontal", excesso <= 1, f"excesso={excesso}px")
        ctx.close()
        b.close()
        v = base.csp_violations(msgs)
        check("sem violação de CSP", not v, "; ".join(v)[:160])

    # Acesso entre workspaces (API) e somente leitura.
    outro = cliente("operador.b@descartavel.example.com",
                    (SEC / "senha-operador.b.txt").read_text(encoding="utf-8").strip())
    rb = outro.get(f"/api/fiscal/v1/companies/{base.B_ID}/batches/{b1}")
    ra = outro.get(f"{F}/batches/{b1}")
    lb = outro.get(f"/api/fiscal/v1/companies/{base.B_ID}/batches").json()["items"]
    check("outro workspace: não lê o lote de A (404/403) nem o vê na lista",
          rb.status_code == 404 and ra.status_code in (403, 404) and all(x["id"] != b1 for x in lb),
          f"B={rb.status_code} B→A={ra.status_code}")
    estado = sql(f"SELECT status, attempts FROM batch_items WHERE batch_id='{b1}'")
    check("somente leitura: o item rejeitado continua igual após abrir o detalhe", estado.split() == ["REJECTED", "1"], estado)


def main() -> None:
    try:
        roteiro()
    except Exception as exc:  # noqa: BLE001 - registra a falha com a causa e grava a evidência
        import traceback  # noqa: PLC0415

        print(traceback.format_exc()[-1500:])
        check("roteiro concluído sem erro", False, f"{type(exc).__name__}: {exc}"[:400])
    out = EV / f"ui-lotes-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"ambiente": B, "governo": "simulado", "dados": "fictícios", "resultados": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    falhas = [r for r in results if r["resultado"] != "PASS"]
    print(f"{len(results) - len(falhas)}/{len(results)} PASS — evidência: {out.relative_to(ROOT)}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
