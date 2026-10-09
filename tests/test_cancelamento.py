"""Cancelamento de NFS-e (eventos do emitente 101101/101103): permissão própria, elegibilidade no servidor,
confirmação explícita, idempotência, um pedido ativo por nota, situação alterada SÓ pelo evento oficial,
reconciliação sem reenvio cego, recusa municipal, revogação de autorização, ZIP/XLSX desatualizados e o
provider oficial contra uma Sefin simulada (pedRegEvento assinado e válido no XSD, POST /nfse/{chave}/eventos)."""
from __future__ import annotations

import base64
import gzip
import json
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from lxml import etree

from conftest import SP
from test_official_integration import (  # noqa: F401 - fixtures reaproveitadas
    FakeGov,
    _gz_b64,
    batch_items,
    official_draft,
    ostack,
    real,
    schedule_now,
)

F = "/api/fiscal/v1/companies"
NSU = "http://www.sped.fazenda.gov.br/nfse"
TAX = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}
MOTIVO = "Erro na emissao: valor do servico informado incorretamente"


# ---------------------------------------------------------------- utilidades (ambiente simulado)
def emitir_simulada(stack, world) -> dict:
    r = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "1000.00",
        "service": {"national_code": "010101", "description": "Serviço mensal"}, "tax_settings": TAX})
    assert r.status_code == 201, r.text
    d = r.json()
    at = stack.clock.now() + timedelta(minutes=1)
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202, r.text
    stack.clock.advance(minutes=2)
    w = stack.worker()
    while w.run_emissions():
        pass
    docs = stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"],
                            params={"direction": "ISSUED", "environment": "mock"}).json()["items"]
    assert len(docs) >= 1 and docs[0]["status"] == "AUTHORIZED"
    return docs[0]


def info(stack, world, doc_id, h=None) -> dict:
    r = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc_id}/cancellation", headers=h or world["ha"])
    assert r.status_code == 200, r.text
    return r.json()


def body_for(i: dict, kind="CANCEL", reason_code="1", text=MOTIVO, **conf) -> dict:
    d = i["document"]
    confirm = {"access_key": d["access_key"], "environment": d["environment"], "company_cnpj": i["company"]["cnpj"],
               "document_number": d["number"] or "", **conf}
    return {"kind": kind, "reason_code": reason_code, "reason_text": text, "confirm": confirm}


def pedir(stack, world, doc_id, body, key=None, h=None):  # noqa: ANN001, ANN201
    return stack.fiscal.post(f"{F}/{world['a']}/documents/{doc_id}/cancellation",
                             headers={**(h or world["ha"]), "Idempotency-Key": key or uuid.uuid4().hex}, json=body)


def drain(stack, w=None) -> None:  # noqa: ANN001
    w = w or stack.worker()
    for _ in range(5):
        if not w.run_cancellations():
            break


# ---------------------------------------------------------------- fluxo completo no ambiente simulado
def test_cancelamento_simulado_fluxo_completo(stack, world):
    doc = emitir_simulada(stack, world)
    i = info(stack, world, doc["id"])
    assert i["eligible"] == {"CANCEL": True, "ANALYSIS": True} and i["can_request"] is True
    assert {r["code"] for r in i["reasons"]} == {"1", "2", "9"} and "105102" in i["substitution_note"]
    # Somente leitura: vê a elegibilidade, mas não pede (permissão própria).
    assert info(stack, world, doc["id"], h=world["hr"])["can_request"] is False
    r = pedir(stack, world, doc["id"], body_for(i), h=world["hr"])
    assert r.status_code == 403 and r.json()["code"] == "PERMISSION_DENIED"
    # Outra empresa não enxerga a nota (isolamento por empresa autenticada).
    r = stack.fiscal.get(f"{F}/{world['b']}/documents/{doc['id']}/cancellation", headers=world["hb"])
    assert r.status_code == 404
    # Validação no servidor: justificativa fora do leiaute e confirmação divergente.
    r = pedir(stack, world, doc["id"], body_for(i, text="curto"))
    assert r.status_code == 422
    r = pedir(stack, world, doc["id"], body_for(i, text="Linha um do motivo\nlinha dois do motivo"))
    assert r.status_code == 422 and r.json()["code"] == "REASON_TEXT_INVALID"
    r = pedir(stack, world, doc["id"], body_for(i, environment="production"))
    assert r.status_code == 409 and r.json()["code"] == "CONFIRMATION_MISMATCH"
    r = pedir(stack, world, doc["id"], {**body_for(i), "status": "CANCELLED"})  # propriedade extra
    assert r.status_code == 422
    # Pedido válido; repetição com a MESMA chave devolve o mesmo pedido; outra chave enquanto ativo => 409.
    key = uuid.uuid4().hex
    r1 = pedir(stack, world, doc["id"], body_for(i), key=key)
    assert r1.status_code == 202 and r1.json()["status"] == "QUEUED", r1.text
    r2 = pedir(stack, world, doc["id"], body_for(i), key=key)
    assert r2.status_code == 202 and r2.json()["id"] == r1.json()["id"]
    r3 = pedir(stack, world, doc["id"], body_for(i))
    assert r3.status_code == 409 and r3.json()["code"] == "CANCELLATION_NOT_ALLOWED"
    # A nota NÃO muda antes do evento.
    d = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    assert d["status"] == "AUTHORIZED"
    drain(stack)
    req = stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r1.json()['id']}", headers=world["ha"]).json()
    assert req["status"] == "REGISTERED", (req["last_error_code"], req["last_error_message"])
    assert req["simulated"] is True and req["result"]["event_code"] == "e101101"
    d = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    assert d["status"] == "CANCELLED" and any(e["event_type"] == "e101101" for e in d["events"])
    assert len(stack.mock.transmissions) == 1 + 1  # 1 DPS + 1 pedido de evento
    i2 = info(stack, world, doc["id"])
    assert i2["eligible"] == {"CANCEL": False, "ANALYSIS": False}
    assert "ALREADY_CANCELLED" in {b["code"] for b in i2["blockers"]["CANCEL"]}
    assert i2["requests"][0]["status"] == "REGISTERED"


def test_timeout_reconcilia_sem_reenvio_e_recusa_municipal_sugere_analise(stack, world):
    doc = emitir_simulada(stack, world)
    i = info(stack, world, doc["id"])
    # Recusa municipal simulada (E0822): REJECTED, nota continua autorizada, sugere a análise fiscal.
    r = pedir(stack, world, doc["id"], body_for(i, text="Prazo vencido no municipio [SIMULAR-E0822]"))
    assert r.status_code == 202
    drain(stack)
    req = stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r.json()['id']}", headers=world["ha"]).json()
    assert req["status"] == "REJECTED" and req["last_error_code"] == "E0822" and req["suggest_analysis"] is True
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()["status"] == "AUTHORIZED"
    # Análise fiscal (101103) registrada: a nota continua AUTORIZADA e fica pendente no município.
    r = pedir(stack, world, doc["id"], body_for(info(stack, world, doc["id"]), kind="ANALYSIS", reason_code="9",
                                                text="Solicito analise fiscal do cancelamento desta nota"))
    assert r.status_code == 202, r.text
    drain(stack)
    assert stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r.json()['id']}",
                            headers=world["ha"]).json()["status"] == "REGISTERED"
    d = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    assert d["status"] == "AUTHORIZED" and any(e["event_type"] == "e101103" for e in d["events"])
    blocked = info(stack, world, doc["id"])
    assert "ANALYSIS_PENDING" in {b["code"] for b in blocked["blockers"]["CANCEL"]}

    # Timeout DEPOIS do aceite: aguarda; a próxima tentativa CONSULTA e encontra o evento — sem segundo envio.
    doc2 = emitir_simulada(stack, world)
    i = info(stack, world, doc2["id"])
    sent_before = len(stack.mock.transmissions)
    r = pedir(stack, world, doc2["id"], body_for(i, text="Servico nao prestado [SIMULAR-TIMEOUT]", reason_code="2"))
    w = stack.worker()
    w.run_cancellations()
    req = stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r.json()['id']}", headers=world["ha"]).json()
    assert req["status"] == "AWAITING_CONFIRMATION"
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc2['id']}", headers=world["ha"]).json()["status"] == "AUTHORIZED"
    stack.clock.advance(minutes=10)
    w.run_cancellations()
    req = stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r.json()['id']}", headers=world["ha"]).json()
    assert req["status"] == "REGISTERED" and req["result"]["reconciled"] is True
    assert len(stack.mock.transmissions) == sent_before + 1  # um único envio
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc2['id']}", headers=world["ha"]).json()["status"] == "CANCELLED"


def test_revogacao_antes_do_envio_retira_o_pedido(stack, world):
    from core_api.models import Membership
    from sqlalchemy import select

    doc = emitir_simulada(stack, world)
    r = pedir(stack, world, doc["id"], body_for(info(stack, world, doc["id"])))
    assert r.status_code == 202
    with stack.core_sessions() as s:
        m = s.scalar(select(Membership).where(Membership.user_id == world["ua"], Membership.company_id == world["a"]))
        m.permissions = [p for p in m.permissions if p != "cancel"]
        s.commit()
    sent = len(stack.mock.transmissions)
    drain(stack)
    req = stack.fiscal.get(f"{F}/{world['a']}/cancellations/{r.json()['id']}", headers=world["ha"]).json()
    assert req["status"] == "WITHDRAWN" and req["last_error_code"] == "AUTHORIZATION_REVOKED"
    assert len(stack.mock.transmissions) == sent  # nada enviado
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()["status"] == "AUTHORIZED"


def test_nota_recebida_e_producao_bloqueadas(stack, world):
    from ctrl_fiscal.models import Document
    from sqlalchemy import select

    doc = emitir_simulada(stack, world)
    with stack.fiscal_sessions() as s:
        d = s.get(Document, doc["id"])
        recebida = Document(**{c.name: getattr(d, c.name) for c in Document.__table__.columns
                               if c.name not in ("id",)})
        recebida.access_key = d.access_key[:-1] + ("0" if d.access_key[-1] != "0" else "1")
        recebida.direction, recebida.company_role = "RECEIVED", "TOMADOR"
        prod = Document(**{c.name: getattr(d, c.name) for c in Document.__table__.columns if c.name not in ("id",)})
        prod.access_key = d.access_key[:-2] + "99"
        prod.environment, prod.simulated = "production", False
        s.add_all([recebida, prod])
        s.commit()
        rid, pid = recebida.id, prod.id
    i = info(stack, world, rid)
    assert "NOT_ISSUED_BY_COMPANY" in {b["code"] for b in i["blockers"]["CANCEL"]}
    r = pedir(stack, world, rid, body_for(i))
    assert r.status_code == 409 and r.json()["code"] == "CANCELLATION_NOT_ALLOWED"
    i = info(stack, world, pid)
    assert "PRODUCTION_CANCELLATION_BLOCKED" in {b["code"] for b in i["blockers"]["CANCEL"]}
    assert pedir(stack, world, pid, body_for(i)).status_code == 409
    assert len(select.__name__) > 0


# ---------------------------------------------------------------- provider oficial (Sefin simulada)
class FakeGovEventos(FakeGov):
    """Acrescenta POST /nfse/{chave}/eventos e GET /nfse/{chave}/eventos/{tipo}/{seq} conforme a Swagger v1."""

    def __init__(self) -> None:
        super().__init__()
        self.event_plan: list[str] = []  # "201" | "400_E0822" | "400_E0840" | "timeout_after_accept" | "500"
        self.events: dict[tuple[str, str], bytes] = {}
        self.posted_events: list[bytes] = []

    def handle(self, service: str, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if service == "sefin" and "/eventos" in path:
            self.calls.append((service, req.method, req.url.host, path, req.url.query.decode()))
            key = path.split("/")[3]
            if req.method == "POST":
                return self._post_evento(key, req)
            tipo = path.split("/")[5]
            hit = self.events.get((key, tipo))
            if hit is None:
                return httpx.Response(404, json={"erro": {"codigo": "E404", "descricao": "Nenhum evento encontrado"}})
            return httpx.Response(200, json={"tipoAmbiente": 2, "versaoAplicativo": "x",
                                             "dataHoraProcessamento": "2026-10-20T10:10:00-03:00",
                                             "eventoXmlGZipB64": _gz_b64(hit)})
        return super().handle(service, req)

    def _post_evento(self, key: str, req: httpx.Request) -> httpx.Response:
        ped = gzip.decompress(base64.b64decode(json.loads(req.content)["pedidoRegistroEventoXmlGZipB64"]))
        self.posted_events.append(ped)
        mode = self.event_plan.pop(0) if self.event_plan else "201"
        root = etree.fromstring(ped)
        inf = root.find(f"{{{NSU}}}infPedReg")
        code = [c for c in inf if etree.QName(c).localname.startswith("e")][0].tag.split("}")[1][1:]
        if mode == "400_E0822":
            return httpx.Response(400, json={"tipoAmbiente": 2, "erro": {
                "codigo": "E0822", "descricao": "O prazo para o cancelamento da NFS-e expirou.", "complemento": None}})
        if (key, code) in self.events:
            return httpx.Response(400, json={"erro": {"codigo": "E0840", "descricao": "Evento já vinculado."}})
        evento = (f'<evento xmlns="{NSU}" versao="1.01"><infEvento Id="EVT{inf.get("Id")[3:]}001">'
                  f"<verAplic>SefinNacional_1.0</verAplic><ambGer>2</ambGer><nSeqEvento>1</nSeqEvento>"
                  f"<dhProc>2026-10-20T10:10:00-03:00</dhProc><nDFSe>77</nDFSe>").encode()
        evento += etree.tostring(root) + b"</infEvento></evento>"
        self.events[(key, code)] = evento
        if mode == "timeout_after_accept":
            raise httpx.ReadTimeout("simulado")
        if mode == "500":
            return httpx.Response(500, json={})
        return httpx.Response(201, json={"tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.0",
                                         "dataHoraProcessamento": "2026-10-20T10:10:00-03:00",
                                         "eventoXmlGZipB64": _gz_b64(evento)})


@pytest.fixture
def gov():
    return FakeGovEventos()


def _emitir_oficial(ostack, real) -> dict:
    d = official_draft(ostack, real)
    b = schedule_now(ostack, real, [d])
    ostack.worker().run_emissions()
    assert batch_items(ostack, real, b["batch_id"])[0]["status"] == "AUTHORIZED"
    docs = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"],
                             params={"direction": "ISSUED", "environment": "restricted"}).json()["items"]
    return docs[0]


def _pedir_oficial(ostack, real, doc, **kw):  # noqa: ANN001, ANN201
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/cancellation", headers=real["h"]).json()
    assert i["eligible"]["CANCEL"] is True, i["blockers"]
    r = ostack.fiscal.post(f"{F}/{real['id']}/documents/{doc['id']}/cancellation",
                           headers={**real["h"], "Idempotency-Key": uuid.uuid4().hex}, json=body_for(i, **kw))
    assert r.status_code == 202, r.text
    return r.json()


def test_oficial_pedido_assinado_valido_no_xsd_e_aplicado(ostack, real, gov):
    from nfse_api import eventos
    from nfse_api.signer import verify_enveloped

    doc = _emitir_oficial(ostack, real)
    req = _pedir_oficial(ostack, real, doc)
    w = ostack.worker()
    w.run_cancellations()
    posts = [c for c in gov.calls if c[1] == "POST" and "/eventos" in c[3]]
    assert [c[3] for c in posts] == [f"/SefinNacional/nfse/{doc['access_key']}/eventos"]
    assert posts[0][2] == "sefin.producaorestrita.nfse.gov.br"
    ped = gov.posted_events[0]
    eventos.validar_xsd(ped, "restricted")
    assert verify_enveloped(ped)
    inf = etree.fromstring(ped).find(f"{{{NSU}}}infPedReg")
    assert inf.get("Id") == "PRE" + doc["access_key"] + "101101"
    assert inf.findtext(f"{{{NSU}}}tpAmb") == "2" and inf.findtext(f"{{{NSU}}}CNPJAutor") == real["cnpj"]
    assert inf.findtext(f"{{{NSU}}}dhEvento").endswith("-03:00")
    assert inf.findtext(f"{{{NSU}}}e101101/{{{NSU}}}xMotivo") == MOTIVO
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req['id']}", headers=real["h"]).json()
    assert out["status"] == "REGISTERED" and out["simulated"] is False
    d = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}", headers=real["h"]).json()
    assert d["status"] == "CANCELLED" and d["danfse_watermark"] == "CANCELADA"
    pdf = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/danfse", headers=real["h"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_oficial_recusa_e0822_final_e_timeout_reconcilia(ostack, real, gov):
    doc = _emitir_oficial(ostack, real)
    gov.event_plan = ["400_E0822"]
    req = _pedir_oficial(ostack, real, doc)
    w = ostack.worker()
    w.run_cancellations()
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req['id']}", headers=real["h"]).json()
    # 400 só vira recusa depois da consulta confirmar que o evento NÃO existe.
    gets = [c[3] for c in gov.calls if c[1] == "GET" and "/eventos/" in c[3]]
    assert gets == [f"/SefinNacional/nfse/{doc['access_key']}/eventos/101101/1"]
    assert out["status"] == "REJECTED" and out["last_error_code"] == "E0822" and out["suggest_analysis"] is True
    # Novo pedido depois da recusa é permitido; timeout após aceite => reconciliação por GET, sem 2º POST.
    gov.event_plan = ["timeout_after_accept"]
    req2 = _pedir_oficial(ostack, real, doc, reason_code="9", text="Outro motivo descrito com detalhes suficientes")
    w.run_cancellations()
    assert ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req2['id']}",
                             headers=real["h"]).json()["status"] == "AWAITING_CONFIRMATION"
    ostack.clock.advance(minutes=10)
    w.run_cancellations()
    out2 = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req2['id']}", headers=real["h"]).json()
    assert out2["status"] == "REGISTERED" and out2["result"]["reconciled"] is True
    assert len(gov.posted_events) == 2  # 1 recusado + 1 aceito (sem reenvio do incerto)


def test_oficial_e0840_com_evento_existente_vira_registrado(ostack, real, gov):
    """Recusa 'evento já vinculado' de um envio anterior cujo retorno se perdeu: a consulta encontra o evento e
    o pedido é REGISTERED — sucesso nunca vira recusa."""
    doc = _emitir_oficial(ostack, real)
    gov.events[(doc["access_key"], "101101")] = (
        f'<evento xmlns="{NSU}" versao="1.01"><infEvento Id="EVT{doc["access_key"]}101101001">'
        f"<verAplic>x</verAplic><ambGer>2</ambGer><nSeqEvento>1</nSeqEvento><dhProc>2026-10-20T10:00:00-03:00</dhProc>"
        f'<nDFSe>1</nDFSe><pedRegEvento versao="1.01"><infPedReg Id="PRE{doc["access_key"]}101101"><tpAmb>2</tpAmb>'
        f"<verAplic>x</verAplic><dhEvento>2026-10-20T09:59:00-03:00</dhEvento><CNPJAutor>{real['cnpj']}</CNPJAutor>"
        f"<chNFSe>{doc['access_key']}</chNFSe><e101101><xDesc>Cancelamento de NFS-e</xDesc><cMotivo>1</cMotivo>"
        f"<xMotivo>Pedido anterior registrado</xMotivo></e101101></infPedReg></pedRegEvento></infEvento></evento>"
    ).encode()
    req = _pedir_oficial(ostack, real, doc)
    ostack.worker().run_cancellations()
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req['id']}", headers=real["h"]).json()
    assert out["status"] == "REGISTERED" and out["result"]["reconciled"] is True
    assert ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}", headers=real["h"]).json()["status"] == "CANCELLED"


def test_zip_e_xlsx_gerados_antes_do_cancelamento_ficam_desatualizados(ostack, real, gov):
    doc = _emitir_oficial(ostack, real)
    h = real["h"]
    r = ostack.fiscal.post(f"{F}/{real['id']}/documents/bundles", headers=h,
                           json={"document_ids": [doc["id"]], "format": "PDF_XML", "environment": "restricted"})
    assert r.status_code == 202, r.text
    bundle = r.json()["job_id"]
    r = ostack.fiscal.post(f"{F}/{real['id']}/exports", headers=h,
                           json={"format": "xlsx", "environment": "restricted", "direction": "ISSUED", "status": "AUTHORIZED"})
    assert r.status_code == 202, r.text
    export = r.json()["job_id"]
    w = ostack.worker()
    while w.run_jobs():
        pass
    for jid in (bundle, export):
        j = ostack.fiscal.get(f"{F}/{real['id']}/jobs/{jid}", headers=h).json()
        assert j["download_available"] is True and j["stale_documents"] == 0, j
    ostack.clock.advance(seconds=5)
    _pedir_oficial(ostack, real, doc)
    w.run_cancellations()
    for jid, code in ((bundle, "BUNDLE_STALE"), (export, "EXPORT_STALE")):
        j = ostack.fiscal.get(f"{F}/{real['id']}/jobs/{jid}", headers=h).json()
        assert j["stale_documents"] == 1 and j["download_available"] is False, j
        r = ostack.fiscal.get(f"{F}/{real['id']}/jobs/{jid}/download", headers=h)
        assert r.status_code == 409 and r.json()["code"] == code


def test_producao_cancelamento_bloqueado_no_conector(ostack, real, gov):
    from nfse_api.providers.base import CompanyContext, ProviderBlocked

    prov = ostack.nfse_app.state.providers["production"]
    with pytest.raises(ProviderBlocked) as e:
        prov.check_ready(CompanyContext(real["id"], real["cnpj"], "production"), "event")
    assert e.value.code == "PRODUCTION_CANCELLATION_BLOCKED"
    assert not [c for c in gov.calls if "/eventos" in c[3]]
    assert datetime.now(SP).year >= 2026


# ---------------------------------------------------------------- produção habilitada (configuração) e correções
def _clonar_para_producao(ostack, doc: dict) -> dict:  # noqa: ANN001
    """Cópia da nota autorizada (mesmo XML oficial, ambGer=2/tpEmit=1) registrada no ambiente de PRODUÇÃO —
    só no banco de teste; a Sefin é simulada (httpx.MockTransport)."""
    from ctrl_fiscal.models import Document

    with ostack.fiscal_sessions() as s:
        d = s.get(Document, doc["id"])
        p = Document(**{c.name: getattr(d, c.name) for c in Document.__table__.columns if c.name != "id"})
        p.environment, p.access_key = "production", d.access_key[:-2] + "77"
        s.add(p)
        s.commit()
        return {"id": p.id, "access_key": p.access_key}


def _ligar_producao(ostack) -> None:  # noqa: ANN001
    ostack.nfse_settings.production_cancellation_enabled = True
    ostack.fiscal_app.state.ctx.settings.production_cancellation_enabled = True


def test_producao_habilitada_envia_ao_host_de_producao_e_aplica(ostack, real, gov):
    from nfse_api import eventos

    prod = _clonar_para_producao(ostack, _emitir_oficial(ostack, real))
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{prod['id']}/cancellation", headers=real["h"]).json()
    assert i["eligible"]["CANCEL"] is False
    assert "PRODUCTION_CANCELLATION_BLOCKED" in {b["code"] for b in i["blockers"]["CANCEL"]}
    _ligar_producao(ostack)
    req = _pedir_oficial(ostack, real, prod)
    # Cliques simultâneos: segundo pedido para a mesma nota enquanto o primeiro está ativo => 409.
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{prod['id']}/cancellation", headers=real["h"]).json()
    r = ostack.fiscal.post(f"{F}/{real['id']}/documents/{prod['id']}/cancellation",
                           headers={**real["h"], "Idempotency-Key": uuid.uuid4().hex}, json=body_for(i))
    assert r.status_code == 409
    ostack.worker().run_cancellations()
    posts = [c for c in gov.calls if c[1] == "POST" and "/eventos" in c[3]]
    assert [c[2] for c in posts] == ["sefin.nfse.gov.br"]
    ped = gov.posted_events[-1]
    eventos.validar_xsd(ped, "production")
    inf = etree.fromstring(ped).find(f"{{{NSU}}}infPedReg")
    assert inf.findtext(f"{{{NSU}}}tpAmb") == "1" and inf.findtext(f"{{{NSU}}}CNPJAutor") == real["cnpj"]
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req['id']}", headers=real["h"]).json()
    assert out["status"] == "REGISTERED" and out["result"]["certificate_fingerprint"]
    d = ostack.fiscal.get(f"{F}/{real['id']}/documents/{prod['id']}", headers=real["h"]).json()
    assert d["status"] == "CANCELLED" and d["danfse_watermark"] == "CANCELADA" and d["xml_available"]


def test_bloqueio_antes_do_envio_nao_trava_a_nota(ostack, real, gov):
    """G1: pedido BLOCKED (nada transmitido) libera o evento no conector; o próximo pedido é aceito e enviado."""
    from nfse_api.providers.base import ProviderBlocked

    doc = _emitir_oficial(ostack, real)
    prov = ostack.nfse_app.state.providers["restricted"]
    original = prov.check_ready

    def bloqueia(company, op):  # noqa: ANN001, ANN202
        if op == "event":
            raise ProviderBlocked("CERT_REVOCATION_UNAVAILABLE", "LCR indisponível (simulado).", "BLOCKED")
        return original(company, op)

    prov.check_ready = bloqueia
    req = _pedir_oficial(ostack, real, doc)
    w = ostack.worker()
    w.run_cancellations()
    assert ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req['id']}", headers=real["h"]).json()["status"] == "BLOCKED"
    assert not [c for c in gov.calls if c[1] == "POST" and "/eventos" in c[3]]
    prov.check_ready = original
    req2 = _pedir_oficial(ostack, real, doc)
    w.run_cancellations()
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{req2['id']}", headers=real["h"]).json()
    assert out["status"] == "REGISTERED", out


def test_autoria_tpemit_e_workspace(ostack, real, gov):
    """G2: nota com tpEmit≠1 (emitida por tomador/intermediário) não é pedida; G3: workspace fora de ACTIVE bloqueia."""
    import gzip as _gz

    from core_api.models import Workspace
    from ctrl_fiscal.models import Document, StoredBlob
    from sqlalchemy import select

    doc = _emitir_oficial(ostack, real)
    with ostack.core_sessions() as s:
        s.scalar(select(Workspace).where(Workspace.company_id == real["id"])).status = "SUSPENDED"
        s.commit()
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/cancellation", headers=real["h"]).json()
    assert "WORKSPACE_NOT_ACTIVE" in {b["code"] for b in i["blockers"]["CANCEL"]}
    with ostack.core_sessions() as s:
        s.scalar(select(Workspace).where(Workspace.company_id == real["id"])).status = "ACTIVE"
        s.commit()
    with ostack.fiscal_sessions() as s:
        d = s.get(Document, doc["id"])
        b = s.get(StoredBlob, d.xml_blob_id)
        b.content_gz = _gz.compress(_gz.decompress(b.content_gz).replace(b"<tpEmit>1</tpEmit>", b"<tpEmit>2</tpEmit>"))
        s.commit()
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/cancellation", headers=real["h"]).json()
    assert "ISSUER_NOT_PROVIDER" in {b["code"] for b in i["blockers"]["CANCEL"]}


def test_analise_fiscal_pendente_e_decisao_do_municipio(ostack, real, gov):
    """101103 registrado NÃO cancela; o worker consulta 105104/105105 e aplica a decisão do município."""
    doc = _emitir_oficial(ostack, real)
    i = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/cancellation", headers=real["h"]).json()
    r = ostack.fiscal.post(f"{F}/{real['id']}/documents/{doc['id']}/cancellation",
                           headers={**real["h"], "Idempotency-Key": uuid.uuid4().hex}, json=body_for(i, kind="ANALYSIS"))
    assert r.status_code == 202, r.text
    rid = r.json()["id"]
    w = ostack.worker()
    w.run_cancellations()
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{rid}", headers=real["h"]).json()
    assert out["status"] == "REGISTERED" and out["analysis"]["situacao"] == "PENDENTE"
    assert "Aguardando decisão do município" in out["analysis"]["label"]
    assert ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}", headers=real["h"]).json()["status"] == "AUTHORIZED"
    # Município defere (105104): a próxima consulta (6 h depois) aplica o evento e a nota fica cancelada.
    key = doc["access_key"]
    gov.events[(key, "105104")] = (
        f'<evento xmlns="{NSU}" versao="1.01"><infEvento Id="EVT{key}105104001"><verAplic>x</verAplic>'
        f"<ambGer>2</ambGer><nSeqEvento>1</nSeqEvento><dhProc>2026-10-21T10:00:00-03:00</dhProc><nDFSe>9</nDFSe>"
        f'<pedRegEvento versao="1.01"><infPedReg Id="PRE{key}105104"><tpAmb>2</tpAmb><verAplic>x</verAplic>'
        f"<dhEvento>2026-10-21T09:59:00-03:00</dhEvento><CNPJAutor>{real['cnpj']}</CNPJAutor><chNFSe>{key}</chNFSe>"
        f"<e105104><xDesc>Cancelamento de NFS-e Deferido por Análise Fiscal</xDesc><CPFAgTrib>52998224725</CPFAgTrib>"
        f"<nProcAdm>123</nProcAdm><cMotivo>1</cMotivo><xMotivo>Deferido pelo municipio</xMotivo></e105104>"
        f"</infPedReg></pedRegEvento></infEvento></evento>").encode()
    ostack.clock.advance(hours=7)
    w.run_cancellations()
    out = ostack.fiscal.get(f"{F}/{real['id']}/cancellations/{rid}", headers=real["h"]).json()
    assert out["analysis"]["situacao"] == "DEFERIDA", out
    assert ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}", headers=real["h"]).json()["status"] == "CANCELLED"
    assert [c for c in gov.calls if c[1] == "GET" and "/eventos/105104/" in c[3]], "consulta de 105104 esperada"
