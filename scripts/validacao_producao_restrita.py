"""Validação OFICIAL na PRODUÇÃO RESTRITA, no ambiente TEMPORÁRIO (scripts/ambiente_producao_restrita.py, porta 8097).

Autorização do Junior (09/10/2026): última NFS-e emitida pela própria AEL como referência, valor do serviço R$ 1,00,
transmissão EXCLUSIVAMENTE em Produção Restrita, consulta e cancelamento da nota de teste. Nada em produção (as chaves
de produção estão desligadas no temporário) e nada no operacional (cópia restaurada de backup verificado).

Fases (uma por execução, para conferência entre elas):
  (variáveis: PR_EMPRESA = id do workspace; PR_SAIDA = pasta de trabalho fora do repositório)
  rascunho <document_id> <cenario.json>  série exclusiva do teste NA CÓPIA, rascunho pela referência, R$ 1,00 com recálculo dos
                          valores dependentes, configuração da operação registrada e aprovada NA CÓPIA, validação
  conferir-id             DPS Id que será usado e consulta oficial (GET /dps/{id}) — precisa NÃO existir
  emitir                  “Emitir agora” (Produção Restrita) + clique repetido + pedido duplicado; acompanha o resultado
  nota                    NFS-e oficial: valores, tributos, XML e PDF
  cancelar                cancelamento 101101 da nota de teste e consulta oficial do evento
  auditoria               trilha de auditoria e isolamento (produção desligada, operacional intocado)
Estado e retornos BRUTOS ficam em <scratchpad>/pr (fora do repositório); o resumo sem dados sensíveis vai para
docs/evidencias/ no fim.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "secrets-pr"
B = "http://127.0.0.1:8097"
OUT = Path(os.environ["PR_SAIDA"])  # pasta de trabalho fora do repositório (scratchpad)
STATE = OUT / "estado.json"
AEL = os.environ["PR_EMPRESA"]  # id do workspace (não versionado)
SERIE_TESTE = "49001"
F = f"/api/fiscal/v1/companies/{AEL}"
C = f"/api/core/v1/companies/{AEL}"


def estado() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def salvar(**kw) -> None:  # noqa: ANN003
    st = estado()
    st.update(kw)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def bruto(nome: str, dado) -> None:  # noqa: ANN001
    (OUT / f"{nome}.json").write_text(json.dumps(dado, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def api() -> httpx.Client:
    c = httpx.Client(base_url=B, timeout=90, headers={"Origin": B})
    pw = (SEC / "senha-validacao.txt").read_text(encoding="utf-8").strip()
    r = c.post("/api/core/v1/auth/login", json={"email": "validacao.pr@temporario.invalid", "password": pw})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    return c


def compose_exec(service: str, code: str) -> str:
    env = {**os.environ, "SECRETS_DIR": "./secrets-pr", "IMAGE_TAG": "rc"}
    r = subprocess.run(["docker", "compose", "-p", "ctrlfiscal-pr", "--env-file", ".env.pr", "exec", "-T", service,
                        "python", "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{service}: {r.stderr.strip()[-500:]}")
    return r.stdout.strip()


PROVEDOR = (
    "import json\nfrom sqlalchemy import select\n"
    "from ctrl_common.db import make_engine, make_sessionmaker\nfrom ctrl_common.clock import SystemClock\n"
    "from nfse_api.settings import NfseSettings\nfrom nfse_api.vault import Vault\n"
    "from nfse_api.providers.official import OfficialProvider\nfrom nfse_api.providers.base import CompanyContext\n"
    "from nfse_api.models import Certificate\n"
    "st = NfseSettings.from_env(); ses = make_sessionmaker(make_engine(st.database_url))\n"
    "p = OfficialProvider('restricted', ses, Vault(st.vault_key_b64), st, SystemClock(), None, st.icp_validator())\n"
    "with ses() as s:\n"
    "    cnpj = s.scalar(select(Certificate.subject_cnpj).where(Certificate.environment == 'restricted', "
    "Certificate.is_active.is_(True)))\n"
    f"co = CompanyContext('{AEL}', cnpj, 'restricted')\n"
    "def get(svc, path):\n"
    "    r = p._transport(co, svc).request('GET', path, idempotent=True)\n"
    "    try:\n        b = json.loads(r.body.decode('utf-8-sig') or '{}')\n"
    "    except Exception:\n        b = {'_bruto': r.body[:400].decode(errors='replace')}\n"
    "    return {'caminho': path, 'http': r.status, 'corpo': b}\n"
)


def _dec(v: str | None) -> Decimal:
    return Decimal(v or "0")


def fase_rascunho(c: httpx.Client, document_id: str, cenario_arq: str) -> None:
    """Rascunho pela referência + CENÁRIO DE TESTE declarado (arquivo JSON, mostrado antes de qualquer transmissão).
    O cenário é técnico: valida o fluxo na Produção Restrita; NÃO é a aprovação fiscal da operação real da empresa."""
    cen = json.loads(Path(cenario_arq).read_text(encoding="utf-8"))
    print(compose_exec("core-api", (
        "from sqlalchemy.orm import Session\nfrom core_api.settings import CoreSettings\n"
        "from core_api.models import Company\nfrom ctrl_common.db import make_engine\n"
        "with Session(make_engine(CoreSettings.from_env().database_url)) as s:\n"
        f"    co = s.get(Company, '{AEL}'); co.dps_series = '{SERIE_TESTE}'; s.commit()\n"
        "    print('cópia: série da DPS do teste =', co.dps_series, '| ambiente', co.nfse_environment)\n")))
    doc = c.get(f"{F}/documents/{document_id}").json()
    print("referência:", doc["number"], doc["issued_at"], doc["environment"], doc["status"], doc["direction"])
    rec = c.get(f"{F}/documents/{document_id}/recipient").json()
    if not rec.get("registered_customer_id"):
        # Cadastro ASSISTIDO (mesmo fluxo da tela): dados lidos do XML oficial, gravados SÓ NA CÓPIA.
        if not rec.get("document") or not rec.get("name") or rec.get("foreign"):
            raise SystemExit(f"tomador sem dados suficientes no XML: faltam {rec.get('missing')}")
        a = rec["address"]
        cr = c.post(f"{C}/customers", json={
            "name": rec["name"], "document_type": rec["document_type"], "document": rec["document"],
            "email": rec.get("email"), "phone": rec.get("phone"), "municipal_registration": rec.get("municipal_registration"),
            "municipality_code": rec.get("municipality_code"), "postal_code": rec.get("postal_code"),
            "address": {k: a.get(k) for k in ("street", "number", "complement", "district")} if any(a.values()) else None})
        if cr.status_code != 201:
            raise SystemExit(f"cadastro do tomador na cópia: {cr.status_code} {cr.text[:300]}")
        print("tomador cadastrado NA CÓPIA a partir do XML oficial")
    r = c.post(f"{F}/drafts/from-document", headers={"Idempotency-Key": uuid.uuid4().hex},
               json={"source_document_id": document_id})
    if r.status_code != 201:
        raise SystemExit(f"from-document: {r.status_code} {r.text[:400]}")
    d = r.json()
    bruto("01-rascunho-referencia", d)
    print("pendências do reaproveitamento:", [(p["field"], p["code"], p["severity"]) for p in d["origin"]["pending"]])
    # Cenário de TESTE aplicado explicitamente (cada valor está no arquivo do cenário; nada vem "corrigido" da origem).
    r = c.patch(f"{F}/drafts/{d['id']}", json={"expected_version": d["version"], **cen["rascunho"]})
    if r.status_code != 200:
        raise SystemExit(f"cenário: {r.status_code} {r.text[:500]}")
    d = r.json()
    d = c.post(f"{F}/drafts/{d['id']}/operation-config", json={"expected_version": d["version"]}).json()
    op_ = d["origin"]["operation"]
    perfis = {p["id"]: p for p in c.get(f"{C}/service-profiles").json()}
    p = perfis.get(op_.get("profile_id") or "")
    print("configuração do cenário:", op_.get("resolution"), p and p["status"],
          p and [(i["code"], i["severity"]) for i in p["pending"] if i["severity"] != "info"])
    if p and p["status"] == "PENDING_REVIEW":
        if not p["can_approve"]:
            raise SystemExit("cenário com pendência bloqueante: " + json.dumps(p["pending"], ensure_ascii=False))
        a = c.post(f"{C}/service-profiles/{p['id']}/approve", json={
            "expected_version": p["version"], "valid_from": "2026-10-01",
            "note": "CENÁRIO DE TESTE da Produção Restrita, aprovado SOMENTE no ambiente temporário. "
                    "Não é a aprovação fiscal da operação real."})
        a.raise_for_status()
        d = c.post(f"{F}/drafts/{d['id']}/operation-config", json={"expected_version": d["version"]}).json()
    v = c.post(f"{F}/drafts/{d['id']}/validate").json()
    bruto("02-validacao", v)
    d = c.get(f"{F}/drafts/{d['id']}").json()
    bruto("03-rascunho-final", d)
    print("validação:", "VÁLIDO" if v["valid"] else "COM ERROS", [(i["code"], i["severity"]) for i in v["issues"]])
    salvar(draft_id=d["id"], draft_version=d["version"], valido=v["valid"],
           referencia={"id": document_id, "numero": doc["number"], "emitida_em": doc["issued_at"]})


def fase_conferir_id() -> None:
    st = estado()
    out = compose_exec("nfse-api", PROVEDOR + (
        "from sqlalchemy import text\n"
        "with ses() as s:\n"
        "    n = s.execute(text(\"SELECT 1\")).scalar()\n"
        "print(json.dumps({'cnpj_len': len(cnpj)}))\n"))
    print("conector pronto:", out)
    seq = compose_exec("fiscal-api", (
        "from sqlalchemy import select\nfrom sqlalchemy.orm import Session\nfrom ctrl_fiscal.settings import FiscalSettings\n"
        "from ctrl_fiscal.models import DpsSequence\nfrom ctrl_common.db import make_engine\n"
        "with Session(make_engine(FiscalSettings.from_env('fiscal-api').database_url)) as s:\n"
        f"    q = s.scalar(select(DpsSequence).where(DpsSequence.company_id == '{AEL}', DpsSequence.environment == 'restricted',"
        f" DpsSequence.series == '{SERIE_TESTE}'))\n"
        "    print(q.next_number if q else 1)\n"))
    numero = int(seq)
    cnpj_out = compose_exec("nfse-api", PROVEDOR + "print(cnpj)\n")
    dps_id = "DPS" + "1505536" + "2" + cnpj_out.zfill(14) + SERIE_TESTE.zfill(5) + str(numero).zfill(15)
    r = json.loads(compose_exec("nfse-api", PROVEDOR + f"print(json.dumps(get('sefin', '/dps/{dps_id}')))\n"))
    bruto("04-conferencia-dps-id", r)
    print(f"DPS Id do teste: série {SERIE_TESTE}, nDPS {numero} → GET /dps/<id> = HTTP {r['http']}",
          json.dumps(r["corpo"], ensure_ascii=False)[:300])
    salvar(dps_id=dps_id, dps_numero=numero, dps_id_livre=r["http"] == 404)


def _item(c: httpx.Client, bid: str) -> dict:
    return c.get(f"{F}/batches/{bid}").json()["items"][0]


def fase_emitir(c: httpx.Client) -> None:
    st = estado()
    if not st.get("dps_id_livre"):
        raise SystemExit("DPS Id não conferido como livre na Produção Restrita: não transmitir")
    if not st.get("valido"):
        raise SystemExit("rascunho do cenário não está válido: não transmitir")
    d = c.get(f"{F}/drafts/{st['draft_id']}").json()
    body = {"drafts": [{"id": d["id"], "expected_version": d["version"]}], "mode": "NOW", "timezone": "America/Sao_Paulo"}
    key = uuid.uuid4().hex
    r1 = c.post(f"{F}/batches", headers={"Idempotency-Key": key}, json=body)
    r2 = c.post(f"{F}/batches", headers={"Idempotency-Key": key}, json=body)  # clique repetido
    r3 = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json=body)  # outro pedido
    bruto("05-emitir-pedidos", {"primeiro": [r1.status_code, r1.json()], "repetido": [r2.status_code, r2.json()],
                                "duplicado": [r3.status_code, r3.json()]})
    print("emitir agora:", r1.status_code, r1.json().get("mode"), "| repetido:", r2.status_code,
          r2.json().get("batch_id") == r1.json().get("batch_id"), "| outro pedido:", r3.status_code, r3.json().get("code"))
    if r1.status_code != 202:
        raise SystemExit(r1.text[:500])
    bid = r1.json()["batch_id"]
    it = {}
    for _ in range(80):
        it = _item(c, bid)
        if it["status"] in ("AUTHORIZED", "REJECTED", "WITHDRAWN"):
            break
        time.sleep(3)
    bruto("06-resultado-emissao", it)
    print("resultado:", it["status"], it.get("last_error"), json.dumps(it.get("result"), ensure_ascii=False)[:600])
    salvar(batch_id=bid, item=it)


def fase_nota(c: httpx.Client) -> None:
    st = estado()
    it = st["item"]
    doc = c.get(f"{F}/documents/{it['document_id']}").json()
    bruto("07-nfse-oficial", doc)
    x = c.get(f"{F}/documents/{it['document_id']}/xml")
    (OUT / "08-nfse-oficial.xml").write_bytes(x.content)
    pdf = c.get(f"{F}/documents/{it['document_id']}/danfse")
    (OUT / "09-danfse.pdf").write_bytes(pdf.content)
    sys.path[:0] = [str(ROOT / "services" / "fiscal-api"), str(ROOT / "services" / "shared")]
    from ctrl_fiscal import nfse_xml as nx  # noqa: PLC0415

    root = nx.parse_xml_root(x.content, nx.Limits())
    inf = nx._child(root, "infNFSe")
    dps = nx._path(inf, "DPS", "infDPS")
    conf = {
        "nNFSe": nx._text(inf, "nNFSe"), "cStat": nx._text(inf, "cStat"), "ambGer": nx._text(inf, "ambGer"),
        "tpAmb": nx._text(dps, "tpAmb"), "serie": nx._text(dps, "serie"), "nDPS": nx._text(dps, "nDPS"),
        "dCompet": nx._text(dps, "dCompet"), "cTribNac": nx._text(dps, "serv", "cServ", "cTribNac"),
        "cNBS": nx._text(dps, "serv", "cServ", "cNBS"), "cObra": nx._text(dps, "serv", "obra", "cObra"),
        "vServ": nx._text(dps, "valores", "vServPrest", "vServ"),
        "tribISSQN": nx._text(dps, "valores", "trib", "tribMun", "tribISSQN"),
        "tpRetISSQN": nx._text(dps, "valores", "trib", "tribMun", "tpRetISSQN"),
        "pAliq_dps": nx._text(dps, "valores", "trib", "tribMun", "pAliq"),
        "vRetCP": nx._text(dps, "valores", "trib", "tribFed", "vRetCP"),
        "vBC": nx._text(inf, "valores", "vBC"), "pAliqAplic": nx._text(inf, "valores", "pAliqAplic"),
        "vISSQN": nx._text(inf, "valores", "vISSQN"), "vTotalRet": nx._text(inf, "valores", "vTotalRet"),
        "vLiq": nx._text(inf, "valores", "vLiq"), "IBSCBS_dps": nx._child(dps, "IBSCBS") is not None,
        "assinatura_dps": any(nx._local(ch.tag) == "Signature" for ch in nx._child(inf, "DPS")),
    }
    conf["pdf"] = {"http": pdf.status_code, "tipo": pdf.headers.get("content-type"), "bytes": len(pdf.content),
                   "assinatura_pdf": pdf.content[:5] == b"%PDF-"}
    conf["documento"] = {k: doc.get(k) for k in ("number", "status", "environment", "simulated", "amount", "net_amount",
                                                 "iss_amount", "total_retained", "competence", "source")}
    bruto("10-conferencia-nota", conf)
    print(json.dumps(conf, ensure_ascii=False, indent=1))
    salvar(document_id=it["document_id"], access_key=doc.get("access_key"), numero=doc.get("number"))


def fase_cancelar(c: httpx.Client) -> None:
    st = estado()
    info = c.get(f"{F}/documents/{st['document_id']}/cancellation").json()
    bruto("11-cancelamento-elegibilidade", info)
    print("elegível:", info["eligible"], "bloqueios:", {k: [b["code"] for b in v] for k, v in info["blockers"].items()})
    comp = c.get("/api/core/v1/companies").json()["items"]
    cnpj = next(x for x in comp if x["id"] == AEL)["cnpj"]
    r = c.post(f"{F}/documents/{st['document_id']}/cancellation", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "kind": "CANCEL", "reason_code": "1",
        "reason_text": "Nota de teste emitida na Producao Restrita para validacao da plataforma",
        "confirm": {"access_key": st["access_key"], "environment": "restricted", "company_cnpj": cnpj,
                    "document_number": st["numero"]}})
    bruto("12-cancelamento-pedido", [r.status_code, r.json()])
    print("pedido:", r.status_code, r.json().get("status"), r.json().get("code"))
    if r.status_code != 202:
        raise SystemExit(r.text[:500])
    rid = r.json()["id"]
    req = {}
    for _ in range(80):
        req = c.get(f"{F}/cancellations/{rid}").json()
        if req["status"] in ("APPLIED", "REJECTED", "BLOCKED", "CANCELLED", "REGISTERED", "FAILED"):
            break
        time.sleep(3)
    bruto("13-cancelamento-resultado", req)
    print("resultado:", req.get("status"), json.dumps({k: req.get(k) for k in ("last_error", "protocol", "result")},
                                                      ensure_ascii=False)[:600])
    ev = json.loads(compose_exec("nfse-api", PROVEDOR + f"print(json.dumps(get('sefin', '/nfse/{st['access_key']}/eventos/101101/1')))\n"))
    bruto("14-evento-oficial", ev)
    print("consulta oficial do evento:", ev["http"], json.dumps(ev["corpo"], ensure_ascii=False)[:300])
    doc = c.get(f"{F}/documents/{st['document_id']}").json()
    print("situação da nota na plataforma:", doc["status"])
    salvar(cancel_request=rid, cancel_status=req.get("status"), doc_status=doc["status"])


def fase_auditoria(c: httpx.Client) -> None:
    st = estado()
    out = compose_exec("fiscal-api", (
        "import json\nfrom sqlalchemy import select\nfrom sqlalchemy.orm import Session\n"
        "from ctrl_fiscal.settings import FiscalSettings\nfrom ctrl_fiscal.models import AuditEvent\n"
        "from ctrl_common.db import make_engine\n"
        "with Session(make_engine(FiscalSettings.from_env('fiscal-api').database_url)) as s:\n"
        "    rows = s.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(40)).all()\n"
        "    print(json.dumps([[r.action, r.target_type, str(r.occurred_at)] for r in rows]))\n"))
    acoes = json.loads(out)
    bruto("15-auditoria", acoes)
    print("auditoria (mais recentes):", [a[0] for a in acoes[:20]])
    r = c.post(f"{F}/batches", headers={"Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": st["draft_id"], "expected_version": st["draft_version"]}], "mode": "NOW",
        "timezone": "America/Sao_Paulo"})
    print("reenvio do mesmo rascunho após emissão:", r.status_code, r.json().get("code"))
    salvar(reenvio=r.status_code)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    OUT.mkdir(parents=True, exist_ok=True)
    fase, *args = sys.argv[1:] or [""]
    if fase == "conferir-id":
        fase_conferir_id()
    else:
        fn = {"rascunho": fase_rascunho, "emitir": fase_emitir, "nota": fase_nota, "cancelar": fase_cancelar,
              "auditoria": fase_auditoria}.get(fase)
        if fn is None:
            sys.exit(__doc__)
        fn(api(), *args)
