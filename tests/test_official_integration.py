"""Sprint 02 — integração oficial em teste automatizado. O "governo" é um httpx.MockTransport que segue os
contratos confirmados (Sefin /SefinNacional, ADN /contribuintes, envelopes 201/400/403/500 e E2220);
nenhuma chamada sai da máquina. Cobre: certificado anexado pela plataforma (core → nfse), perfil fiscal,
DPS v1.01 (XSD + assinatura), emissão/rejeição/reconciliação sem reenvio, ADN por ambiente (cursor,
dedupe, eventos, CNPJ exato) e bloqueios de produção/mock."""
from __future__ import annotations

import base64
import gzip
import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

from conftest import KEYS, SP, AuthHeaders, Stack, _truncate, cnpj
from test_nfse_connector import TEST_TRUST, make_cert

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
MUN = "1505536"
NS = "{http://www.sped.fazenda.gov.br/nfse}"
PFX_PASSWORD = "senha-pfx-de-teste"
# totTrib por valores: não optante (opSimpNac=1) nunca usa indTotTrib/pTotTribSN (Anexo I, E0713).
APPROX = {"mode": "VALUES", "federal": "10.00", "state": "0.00", "municipal": "75.00"}


def _gz_b64(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data)).decode()


class FakeGov:
    """Sefin/ADN simulados por host. Registra cada chamada (serviço, método, host, caminho, query)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str, str]] = []
        self.sefin_plan: list[str] = []  # "201" (padrão) | "400" | "403" | "500_after_accept"
        self.accepted: dict[str, tuple[str, bytes]] = {}  # idDps -> (chave, xml NFS-e)
        self.posted_dps: list[bytes] = []
        self.adn: dict[str, list[dict]] = {"adn.producaorestrita.nfse.gov.br": [], "adn.nfse.gov.br": []}
        self.convenio_inativo: set[str] = set()
        # Quando ligado, a NFS-e devolvida embute a DPS REALMENTE transmitida e o grupo valores da NFS-e é calculado
        # aqui, de forma independente do backend, pela fórmula do XSD/Anexo I (vBC, vISSQN, vTotalRet, vLiq).
        self.embed_posted_dps = False
        self.aliquota_parametrizada = "2.00"  # usada pela "Sefin" quando a DPS não traz pAliq
        self.param_indisponivel = False  # Parametrização fora do ar (HTTP 503)
        # Relógio da "Sefin" (callable → datetime). Quando definido, toda resposta traz o cabeçalho Date e a DPS com
        # dhEmi posterior ao processamento é rejeitada (E0008), como no ambiente oficial.
        self.server_now = None

    def factory(self, service: str) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(lambda req: self.handle(service, req)))

    def handle(self, service: str, req: httpx.Request) -> httpx.Response:
        resp = self._handle(service, req)
        if self.server_now is not None:
            from email.utils import format_datetime

            resp.headers["date"] = format_datetime(self.server_now().astimezone(timezone.utc), usegmt=True)
        return resp

    def _handle(self, service: str, req: httpx.Request) -> httpx.Response:
        self.calls.append((service, req.method, req.url.host, req.url.path, req.url.query.decode()))
        if req.method == "HEAD" and req.url.path in ("/SefinNacional", "/SefinNacional/"):
            return httpx.Response(301, headers={"location": "/SefinNacional/"})  # como o oficial: redireciona
        path = req.url.path
        if service == "sefin" and req.method == "POST" and path == "/SefinNacional/nfse":
            return self._post_nfse(req)
        if service == "sefin" and req.method == "GET" and path.startswith("/SefinNacional/dps/"):
            hit = self.accepted.get(path.rsplit("/", 1)[1])
            if hit is None:
                return httpx.Response(404, json={"erros": [{"codigo": "E404", "descricao": "DPS não localizada"}]})
            return httpx.Response(200, json={"idDps": path.rsplit("/", 1)[1], "chaveAcesso": hit[0]})
        if service == "sefin" and req.method == "GET" and path.startswith("/SefinNacional/nfse/"):
            key = path.rsplit("/", 1)[1]
            for k, xml in self.accepted.values():
                if k == key:
                    return httpx.Response(200, json={"chaveAcesso": k, "nfseXmlGZipB64": _gz_b64(xml)})
            return httpx.Response(404, json={})
        if service == "adn" and req.method == "GET" and path.startswith("/contribuintes/DFe/"):
            nsu = int(path.rsplit("/", 1)[1])
            docs = [d for d in self.adn[req.url.host] if d["NSU"] > nsu][:50]
            if not docs:
                return httpx.Response(404, json={"StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO", "LoteDFe": [],
                                                 "Erros": [{"Codigo": "E2220", "Descricao": "Nenhum documento localizado."}]})
            return httpx.Response(200, json={"StatusProcessamento": "DOCUMENTOS_LOCALIZADOS", "LoteDFe": docs,
                                             "Alertas": [], "Erros": [], "TipoAmbiente": 2})
        if service == "param" and req.method == "GET":
            mun = path.split("/")[2] if path.count("/") >= 2 else ""
            if self.param_indisponivel:
                return httpx.Response(503, json={})
            if path.endswith("/aliquota"):  # /parametrizacao/{mun}/{99.99.99.999}/{AAAA-MM-01T00:00:00}/aliquota
                serv = path.split("/")[3]
                if len(serv) != 12:
                    return httpx.Response(400, json={"aliquotas": None, "mensagem": "Chamada mal formada. O código do "
                                                     "serviço deve ser composto por nove dígitos."})
                return httpx.Response(200, json={"aliquotas": {serv: [{"Incidencia": "SIM",
                                                                       "Aliq": float(self.aliquota_parametrizada),
                                                                       "DtIni": "2025-11-01T00:00:00", "DtFim": None}]},
                                                 "mensagem": "Alíquotas recuperadas com sucesso."})
            if path.endswith("/convenio") and mun in self.convenio_inativo:
                return httpx.Response(404, json={"parametrosConvenio": None,
                                                 "mensagem": "O convênio do município ainda não está ativo"})
            return httpx.Response(200, json={"parametrosConvenio": {"aderenteAmbienteNacional": 1,
                                                                    "aderenteEmissorNacional": 1}})
        return httpx.Response(404, json={})

    def _post_nfse(self, req: httpx.Request) -> httpx.Response:
        from nfse_api.providers.mock_data import mock_access_key, nfse_xml

        dps = gzip.decompress(base64.b64decode(json.loads(req.content)["dpsXmlGZipB64"]))
        self.posted_dps.append(dps)
        mode = self.sefin_plan.pop(0) if self.sefin_plan else "201"
        if mode == "400":
            return httpx.Response(400, json={"erros": [{"codigo": "E0312", "descricao": "Código de tributação inválido.",
                                                        "complemento": "cTribNac"}]})
        if mode == "403":
            return httpx.Response(403, json={"erros": [{"codigo": "E1235", "descricao": "Certificado não autorizado."}]})
        root = etree.fromstring(dps)
        inf = root.find(f"{NS}infDPS")
        dps_id = inf.get("Id")
        if self.server_now is not None and datetime.fromisoformat(inf.findtext(f"{NS}dhEmi")) > self.server_now():
            return httpx.Response(400, json={"erros": [{"codigo": "E0008", "descricao": "A data de emissão da DPS não "
                                                                            "pode ser posterior à data do seu processamento."}]})
        prest = inf.findtext(f"{NS}prest/{NS}CNPJ")
        number = int(inf.findtext(f"{NS}nDPS"))
        toma = inf.findtext(f"{NS}toma/{NS}CNPJ")
        now = datetime(2026, 10, 20, 10, 5, tzinfo=SP)
        key = mock_access_key(prest, number, now, dps_id)
        xml = nfse_xml(key=key, number=number, prest=(prest, "EMPRESA OFICIAL TESTE"), toma=(toma, "TOMADOR TESTE"),
                       interm=None, description=inf.findtext(f"{NS}serv/{NS}cServ/{NS}xDescServ"),
                       v_serv=inf.findtext(f"{NS}valores/{NS}vServPrest/{NS}vServ"), issued_at=now,
                       competence_day=inf.findtext(f"{NS}dCompet"))
        if self.embed_posted_dps:
            xml = self._nfse_with_posted_dps(xml, root)
        self.accepted[dps_id] = (key, xml)
        if mode == "500_after_accept":
            return httpx.Response(500, json={"erros": [{"codigo": "E9999", "descricao": "Erro interno."}]})
        return httpx.Response(201, json={"tipoAmbiente": 2, "versaoAplicativo": "SefinNacional_1.0",
                                         "dataHoraProcessamento": now.isoformat(), "idDps": dps_id, "chaveAcesso": key,
                                         "nfseXmlGZipB64": _gz_b64(xml), "alertas": []})

    def _nfse_with_posted_dps(self, xml: bytes, dps_root) -> bytes:  # noqa: ANN001
        from decimal import ROUND_HALF_EVEN, Decimal

        def v(path: str) -> Decimal:
            return Decimal(dps_root.findtext(f"{NS}infDPS/" + "/".join(f"{NS}{p}" for p in path.split("/"))) or "0")

        nfse = etree.fromstring(xml)
        inf_nfse = nfse.find(f"{NS}infNFSe")
        inf_nfse.remove(inf_nfse.find(f"{NS}DPS"))
        inf_nfse.append(dps_root)
        v_serv, d_inc, d_con, v_dr = (v("valores/vServPrest/vServ"), v("valores/vDescCondIncond/vDescIncond"),
                                      v("valores/vDescCondIncond/vDescCond"), v("valores/vDedRed/vDR"))
        aliq = dps_root.findtext(f"{NS}infDPS/{NS}valores/{NS}trib/{NS}tribMun/{NS}pAliq") or self.aliquota_parametrizada
        v_bc = v_serv - d_inc - v_dr
        v_iss = (v_bc * Decimal(aliq) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        retido = dps_root.findtext(f"{NS}infDPS/{NS}valores/{NS}trib/{NS}tribMun/{NS}tpRetISSQN") in ("2", "3")
        tot = v("valores/trib/tribFed/vRetCP") + v("valores/trib/tribFed/vRetIRRF") + v("valores/trib/tribFed/vRetCSLL")
        tot += v_iss if retido else Decimal("0")
        valores = inf_nfse.find(f"{NS}valores")
        for c in list(valores):
            valores.remove(c)
        for tag, val in (("vBC", v_bc), ("pAliqAplic", Decimal(aliq)), ("vISSQN", v_iss), ("vTotalRet", tot),
                         ("vLiq", v_serv - d_inc - d_con - tot)):
            etree.SubElement(valores, f"{NS}{tag}").text = str(val.quantize(Decimal("0.01")))
        return etree.tostring(nfse, xml_declaration=True, encoding="UTF-8")

    def posts(self) -> list[tuple]:
        return [c for c in self.calls if c[1] == "POST"]


@pytest.fixture
def gov():
    return FakeGov()


@pytest.fixture
def ostack(db_urls, jwt_keys, clock, tmp_path, gov):
    for url in db_urls.values():
        _truncate(url)
    return Stack(db_urls, jwt_keys, clock, tmp_path, official={
        "vault_key_b64": base64.b64encode(os.urandom(32)).decode(), "restricted_enabled": True,
        "production_distribution_enabled": True, "http_factory": gov.factory, **TEST_TRUST})


def _pfx(cnpj_value: str, password: str = PFX_PASSWORD) -> bytes:
    key, cert = make_cert(cnpj_value=cnpj_value)
    return pkcs12.serialize_key_and_certificates(b"teste", key, cert, None,
                                                 serialization.BestAvailableEncryption(password.encode()))


@pytest.fixture
def real(ostack):
    """Empresa em Produção Restrita: perfil confirmado + certificado anexado pela plataforma."""
    c_cnpj = cnpj("123456780001")
    cid = ostack.add_company("Empresa Oficial Teste", c_cnpj, environment="restricted", municipality=MUN)
    uid = ostack.add_user("dono@ex.com")
    ostack.grant(uid, cid)
    h = AuthHeaders(ostack, "dono@ex.com")
    r = ostack.core.patch(f"{C}/{cid}/fiscal-profile", headers=h, json={
        "simples_option": "1", "special_regime": "0", "municipal_registration": "123456",
        "adn_environments": ["restricted", "production"], "source": "teste automatizado"})
    assert r.status_code == 200 and r.json()["complete_for_emission"] is True, r.text
    r = ostack.core.post(f"{C}/{cid}/certificate", headers=h, files={"file": ("e.pfx", _pfx(c_cnpj), "application/x-pkcs12")},
                         data={"password": PFX_PASSWORD})
    assert r.status_code == 201, r.text
    cust = ostack.core.post(f"{C}/{cid}/customers", headers=h, json={
        "name": "Tomador Teste", "document_type": "CNPJ", "document": cnpj("445556660001"),
        "municipality_code": MUN, "postal_code": "68500000",
        "address": {"street": "Rua A", "number": "10", "district": "Centro", "city": "Marabá", "state": "PA"}})
    assert cust.status_code == 201, cust.text
    return {"id": cid, "cnpj": c_cnpj, "h": h, "customer": cust.json()["id"], "cert_response": r}


def official_draft(ostack, real, **over) -> dict:
    body = {
        "customer_id": real["customer"], "competence": "2026-10", "service_date": "2026-10-01", "amount": "1500.00",
        "service": {"national_code": "071601", "municipal_code": "001", "description": "Serviço de supressão vegetal (teste)",
                    "location_code": MUN},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                         "approx_tax": APPROX},
    }
    body.update(over)
    r = ostack.fiscal.post(f"{F}/{real['id']}/drafts", headers=real["h"], json=body)
    assert r.status_code == 201, r.text
    return r.json()


def schedule_now(ostack, real, drafts, minutes: int = 1) -> dict:
    at = ostack.clock.now() + timedelta(minutes=minutes)
    r = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": uuid.uuid4().hex},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts],
                                 "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202, r.text
    ostack.clock.advance(minutes=minutes + 1)
    return r.json()


def batch_items(ostack, real, bid) -> list[dict]:
    return ostack.fiscal.get(f"{F}/{real['id']}/batches/{bid}", headers=real["h"]).json()["items"]


# ---------------------------------------------------------------- certificado e perfil
def test_certificate_uploaded_via_platform_never_exposed(ostack, real):
    body = real["cert_response"].json()
    assert body["accepted"] is True and body["subject_cnpj_matches"] is True
    assert sorted(body["environments"]) == ["production", "restricted"]
    text = real["cert_response"].text
    assert PFX_PASSWORD not in text and "BEGIN" not in text and "pfx_b64" not in text
    st = ostack.core.get(f"{C}/{real['id']}/integration-status", headers=real["h"]).json()
    assert st["environment"] == "restricted" and st["status"] == "READY" and st["capabilities"]["emission"] is True
    prod = ostack.core.get(f"{C}/{real['id']}/integration-status", headers=real["h"], params={"environment": "production"}).json()
    assert prod["capabilities"] == {"emission": False, "distribution": True, "cancellation": False, "simulated": False}
    assert "PRODUCTION_EMISSION_BLOCKED" in {b["code"] for b in prod["blockers"]}
    # Senha errada, CNPJ de outra empresa e usuário sem admin são recusados.
    r = ostack.core.post(f"{C}/{real['id']}/certificate", headers=real["h"],
                         files={"file": ("e.pfx", _pfx(real["cnpj"]), "application/x-pkcs12")}, data={"password": "errada"})
    assert r.status_code == 422 and r.json()["code"] == "PFX_INVALID" and "errada" not in r.text
    r = ostack.core.post(f"{C}/{real['id']}/certificate", headers=real["h"],
                         files={"file": ("e.pfx", _pfx(cnpj("998887770001")), "application/x-pkcs12")},
                         data={"password": PFX_PASSWORD})
    assert r.status_code == 422 and r.json()["code"] == "CERT_CNPJ_MISMATCH"
    key, cert = make_cert(cnpj_value=real["cnpj"], self_signed=True)
    forged = pkcs12.serialize_key_and_certificates(b"t", key, cert, None,
                                                   serialization.BestAvailableEncryption(PFX_PASSWORD.encode()))
    r = ostack.core.post(f"{C}/{real['id']}/certificate", headers=real["h"],
                         files={"file": ("e.pfx", forged, "application/x-pkcs12")}, data={"password": PFX_PASSWORD})
    assert r.status_code == 422 and r.json()["code"] == "CERT_SELF_SIGNED"
    uid = ostack.add_user("operador@ex.com")
    ostack.grant(uid, real["id"], perms=("view", "prepare", "schedule", "emit"))
    r = ostack.core.post(f"{C}/{real['id']}/certificate", headers=AuthHeaders(ostack, "operador@ex.com"),
                         files={"file": ("e.pfx", _pfx(real["cnpj"]), "application/x-pkcs12")}, data={"password": PFX_PASSWORD})
    assert r.status_code == 403


def test_fiscal_profile_rules(ostack, real):
    h = real["h"]
    assert ostack.core.patch(f"{C}/{real['id']}/fiscal-profile", headers=h, json={"environment": "production"}).status_code == 422
    r = ostack.core.patch(f"{C}/{real['id']}/fiscal-profile", headers=h, json={"simples_regime": "1"})
    assert r.status_code == 422 and r.json()["code"] == "SIMPLES_REGIME_REQUIRES_OPTION_3"
    p = ostack.core.get(f"{C}/{real['id']}/fiscal-profile", headers=h).json()
    assert p["simples_option"] == "1" and p["special_regime"] == "0" and p["environment"] == "restricted"


# ---------------------------------------------------------------- DPS v1.01
def _snapshot(**over) -> dict:
    snap = {
        "company": {"cnpj": cnpj("123456780001"), "municipality_code": MUN, "timezone": "America/Sao_Paulo",
                    "fiscal_profile": {"simples_option": "1", "special_regime": "0", "municipal_registration": "123456"}},
        "customer": {"document": cnpj("445556660001"), "name": "Tomador Teste", "municipality_code": MUN,
                     "postal_code": "68500000", "address": {"street": "Rua A", "number": "10", "district": "Centro"}},
        "competence": "2026-10", "service_date": "2026-10-01", "amount": "1500.00", "deductions": "0.00",
        "service": {"national_code": "071601", "municipal_code": "001", "description": "Serviço", "location_code": MUN},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX},
    }
    snap.update(over)
    return snap


def test_dps_builder_fields_xsd_and_signature():
    from nfse_api import dps
    from nfse_api.signer import sign_enveloped, verify_enveloped

    m = dps.montar(_snapshot(), environment="restricted", serie="1", numero=7, agora=datetime(2026, 10, 20, 13, 0, tzinfo=SP))
    assert len(m.dps_id) == 45 and m.dps_id == "DPS" + MUN + "2" + cnpj("123456780001") + "00001" + "7".zfill(15)
    root = etree.fromstring(m.xml)
    inf = root.find(f"{NS}infDPS")
    assert root.get("versao") == "1.01" and inf.findtext(f"{NS}tpAmb") == "2"
    assert inf.findtext(f"{NS}dCompet") == "2026-10-01"  # data completa, separada do agendamento
    assert inf.findtext(f"{NS}dhEmi").endswith("-03:00")  # fuso da empresa
    assert inf.findtext(f"{NS}serv/{NS}cServ/{NS}cTribNac") == "071601"
    assert inf.findtext(f"{NS}serv/{NS}cServ/{NS}cTribMun") == "001"
    assert inf.findtext(f"{NS}prest/{NS}regTrib/{NS}opSimpNac") == "1"
    assert inf.find(f"{NS}prest/{NS}regTrib/{NS}regApTribSN") is None
    assert inf.findtext(f"{NS}toma/{NS}end/{NS}endNac/{NS}cMun") == MUN
    key, cert = make_cert(cnpj_value=cnpj("123456780001"))
    signed = sign_enveloped(m.xml, key, cert.public_bytes(serialization.Encoding.DER))
    dps.validar_xsd(signed, "restricted")
    assert verify_enveloped(signed)

    with pytest.raises(dps.DpsIncompleta) as e:
        dps.montar(_snapshot(service_date=None, service={"description": "x"}, tax_settings={}),
                   environment="restricted", serie="1", numero=1, agora=datetime(2026, 10, 20, tzinfo=SP))
    campos = {f["campo"] for f in e.value.faltas}
    assert {"service_date", "service.location_code", "service.national_code", "tax_settings.issqn_taxation",
            "tax_settings.approx_tax.mode"} <= campos
    with pytest.raises(dps.DpsIncompleta) as e:
        dps.montar(_snapshot(service_date="2026-09-30"), environment="restricted", serie="1", numero=1,
                   agora=datetime(2026, 10, 20, tzinfo=SP))
    assert {f["codigo"] for f in e.value.faltas} == {"COMPETENCIA_DIVERGENTE"}
    # Endereço incompleto do tomador não é enviado pela metade.
    partial = dps.montar(_snapshot(customer={"document": cnpj("445556660001"), "name": "T", "municipality_code": MUN}),
                         environment="restricted", serie="1", numero=2, agora=datetime(2026, 10, 20, tzinfo=SP))
    assert etree.fromstring(partial.xml).find(f"{NS}infDPS/{NS}toma/{NS}end") is None


def test_draft_requires_official_fields_before_scheduling(ostack, real):
    d = official_draft(ostack, real, service_date=None,
                       service={"national_code": "070201", "description": "Sem local"}, tax_settings={
                           "issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"})
    v = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()
    fields = {e["field"] for e in v["issues"] if e["severity"] == "error"}
    assert {"service_date", "service.location_code", "tax_settings.approx_tax"} <= fields, v
    r = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "x1"},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                 "scheduled_at": (ostack.clock.now() + timedelta(hours=1)).isoformat(),
                                 "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422


# ---------------------------------------------------------------- emissão (Produção Restrita simulada)
def test_official_emission_stores_returned_xml(ostack, real, gov):
    d = official_draft(ostack, real)
    b = schedule_now(ostack, real, [d])
    w = ostack.worker()
    assert w.run_emissions() == 1
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "AUTHORIZED" and item["result"]["simulated"] is False, item
    posts = gov.posts()
    assert posts == [("sefin", "POST", "sefin.producaorestrita.nfse.gov.br", "/SefinNacional/nfse", "")]
    from nfse_api import dps
    from nfse_api.signer import verify_enveloped

    sent = gov.posted_dps[0]
    assert verify_enveloped(sent)
    dps.validar_xsd(sent, "restricted")
    inf = etree.fromstring(sent).find(f"{NS}infDPS")
    assert inf.get("Id") == item["dps_id"] and inf.findtext(f"{NS}serie") == "1" and inf.findtext(f"{NS}tpAmb") == "2"
    docs = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"], params={"direction": "ISSUED", "environment": "restricted"}).json()
    assert docs["total"] == 1 and docs["items"][0]["simulated"] is False
    doc = docs["items"][0]
    xml = ostack.fiscal.get(f"{F}/{real['id']}/documents/{doc['id']}/xml", headers=real["h"])
    key, original = next(iter(gov.accepted.values()))
    assert xml.status_code == 200 and xml.content == original and doc["access_key"] == key
    assert ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()["status"] == "AUTHORIZED"


def test_sefin_rejection_is_final_without_resend(ostack, real, gov):
    gov.sefin_plan = ["400"]
    d = official_draft(ostack, real)
    b = schedule_now(ostack, real, [d])
    w = ostack.worker()
    w.run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "REJECTED" and item["result"]["errors"][0]["code"] == "E0312"
    ostack.clock.advance(hours=2)
    assert w.run_emissions() == 0 and len(gov.posts()) == 1


def test_sefin_500_after_accept_reconciles_by_query_without_resend(ostack, real, gov):
    gov.sefin_plan = ["500_after_accept"]
    d = official_draft(ostack, real)
    b = schedule_now(ostack, real, [d])
    w = ostack.worker()
    w.run_emissions()
    assert batch_items(ostack, real, b["batch_id"])[0]["status"] == "AWAITING_CONFIRMATION"
    ostack.clock.advance(minutes=2)
    w.run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "AUTHORIZED" and len(gov.posts()) == 1
    gets = [c[3] for c in gov.calls if c[1] == "GET" and c[0] == "sefin"]
    assert gets[0] == f"/SefinNacional/dps/{item['dps_id']}" and gets[1].startswith("/SefinNacional/nfse/")
    docs = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"], params={"direction": "ISSUED", "environment": "restricted"}).json()
    assert docs["total"] == 1


def test_certificate_refused_by_sefin_is_not_final(ostack, real, gov):
    gov.sefin_plan = ["403"]
    d = official_draft(ostack, real)
    b = schedule_now(ostack, real, [d])
    ostack.worker().run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "SCHEDULED" and item["last_error"]["code"] == "CERT_REJECTED_BY_SEFIN", item


def test_small_batch_in_company_timezone(ostack, real, gov):
    drafts = [official_draft(ostack, real, amount=f"{100 + i}.00") for i in range(3)]
    at = datetime(2026, 10, 21, 8, 0, tzinfo=SP)
    r = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "lote-pequeno"},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts],
                                 "scheduled_at": "2026-10-21T11:00:00Z", "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202, r.text
    w = ostack.worker()
    ostack.clock.set(at - timedelta(seconds=1))
    assert w.run_emissions() == 0 and gov.posts() == []
    ostack.clock.set(at)
    while w.run_emissions():
        pass
    items = batch_items(ostack, real, r.json()["batch_id"])
    assert [i["status"] for i in items] == ["AUTHORIZED"] * 3
    numbers = sorted(int(i["dps_id"][-15:]) for i in items)
    assert numbers == list(range(numbers[0], numbers[0] + 3)) and len(gov.posts()) == 3


# ---------------------------------------------------------------- produção bloqueada / mock desligado
def test_production_emission_blocked_everywhere(ostack, real, gov):
    from core_api.models import Company

    d = official_draft(ostack, real)
    with ostack.core_sessions() as s, s.begin():
        s.get(Company, real["id"]).nfse_environment = "production"
    r = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "prod"},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                 "scheduled_at": (ostack.clock.now() + timedelta(hours=1)).isoformat(),
                                 "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "PRODUCTION_EMISSION_BLOCKED"
    from ctrl_common.hashing import canonical_hash

    h = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + KEYS["worker_nfse"]}
    payload = {"x": 1}
    res = ostack.nfse.post("/internal/v1/submissions", headers=h, json={
        "submission_id": str(uuid.uuid4()), "company_id": real["id"], "environment": "production",
        "company_cnpj": real["cnpj"], "dps": {"id": "DPS" + MUN + "2" + real["cnpj"] + "00001" + "1".zfill(15),
                                              "series": "1", "number": 1},
        "payload": payload, "payload_hash": canonical_hash(payload)})
    assert res.status_code == 200 and res.json()["state"] == "BLOCKED"
    assert not [c for c in gov.calls if c[0] == "sefin"]


def test_mock_disabled_at_runtime_has_no_fallback(db_urls, clock):
    from fastapi.testclient import TestClient
    from nfse_api.app import create_app
    from nfse_api.settings import NfseSettings

    settings = NfseSettings(database_url=db_urls["nfse"], vault_key_b64=None, app_env="test",
                            internal_clients={"core-api": ("k" * 40, {"integration:read"})}, mock_enabled=False)
    client = TestClient(create_app(settings, clock))
    r = client.get(f"/internal/v1/companies/{uuid.uuid4()}/integration-status",
                   params={"environment": "mock", "cnpj": cnpj("112223330001")},
                   headers={"X-Service-Name": "core-api", "Authorization": "Bearer " + "k" * 40})
    assert r.status_code == 200 and r.json()["status"] == "BLOCKED"
    assert r.json()["blockers"][0]["code"] == "MOCK_DISABLED" and r.json()["capabilities"]["emission"] is False


# ---------------------------------------------------------------- ADN por ambiente
def _adn_doc(nsu: int, kind: str, xml: bytes, key: str | None = None) -> dict:
    return {"NSU": nsu, "ChaveAcesso": key, "TipoDocumento": kind, "ArquivoXml": _gz_b64(xml),
            "DataHoraGeracao": "2026-10-19T10:00:00-03:00"}


def _seed_adn(gov: FakeGov, company_cnpj: str) -> None:
    from nfse_api.providers.mock_data import cancel_event_xml, mock_access_key, nfse_xml

    base = datetime(2026, 10, 10, 9, 0, tzinfo=SP)
    sup = cnpj("100000000001")
    k1 = mock_access_key(sup, 11, base, "r1")
    k2 = mock_access_key(company_cnpj, 12, base, "r2")
    other_root = company_cnpj[:8] + "9999"
    k3 = mock_access_key(sup, 13, base, "r3")
    restricted = [
        _adn_doc(1, "NFSE", nfse_xml(key=k1, number=11, prest=(sup, "Fornecedor"), toma=(company_cnpj, "Empresa"),
                                     interm=None, description="Recebida", v_serv="10.00", issued_at=base,
                                     competence_day="2026-10-01"), k1),
        _adn_doc(2, "NFSE", nfse_xml(key=k2, number=12, prest=(company_cnpj, "Empresa"), toma=(sup, "Fornecedor"),
                                     interm=None, description="Emitida", v_serv="20.00", issued_at=base,
                                     competence_day="2026-10-01"), k2),
        # Filial (mesma raiz, CNPJ diferente) como tomadora: fora do escopo do CNPJ exato.
        _adn_doc(3, "NFSE", nfse_xml(key=k3, number=13, prest=(sup, "Fornecedor"), toma=(cnpj(other_root), "Filial"),
                                     interm=None, description="Filial", v_serv="30.00", issued_at=base,
                                     competence_day="2026-10-01"), k3),
        _adn_doc(4, "EVENTO", cancel_event_xml(k1, sup, base + timedelta(days=1)), k1),
    ]
    kp = mock_access_key(sup, 91, base, "p1")
    production = [
        _adn_doc(1, "NFSE", nfse_xml(key=kp, number=91, prest=(sup, "Fornecedor"), toma=(company_cnpj, "Empresa"),
                                     interm=None, description="Produção", v_serv="99.00", issued_at=base,
                                     competence_day="2026-10-01"), kp),
    ]
    gov.adn["adn.producaorestrita.nfse.gov.br"] = restricted
    gov.adn["adn.nfse.gov.br"] = production


def _drain(w) -> None:  # noqa: ANN001
    for _ in range(10):
        if not w.run_sync():
            return


def test_adn_sync_per_environment_cursor_dedupe_events_exact_cnpj(ostack, real, gov):
    from ctrl_fiscal.models import DfeItem, SyncSchedule
    from sqlalchemy import select

    _seed_adn(gov, real["cnpj"])
    w = ostack.worker()
    w.refresh_companies(force=True)
    _drain(w)
    adn_calls = [c for c in gov.calls if c[0] == "adn"]
    assert {c[2] for c in adn_calls} == {"adn.producaorestrita.nfse.gov.br", "adn.nfse.gov.br"}
    assert all(f"cnpjConsulta={real['cnpj']}" in c[4] and "lote=true" in c[4] for c in adn_calls)
    assert not [c for c in gov.calls if c[1] != "GET"]  # leitura apenas
    with ostack.fiscal_sessions() as s:
        scheds = {x.environment: x for x in s.scalars(select(SyncSchedule).where(SyncSchedule.company_id == real["id"]))}
        assert set(scheds) == {"restricted", "production"}
        assert scheds["restricted"].cursor_nsu == 4 and scheds["production"].cursor_nsu == 1
        assert scheds["restricted"].status == "OK" and scheds["restricted"].interval_hours == 12
        items = s.scalars(select(DfeItem).where(DfeItem.company_id == real["id"])).all()
        by_env = {(i.environment, i.nsu): i for i in items}
        assert len(items) == 5
        assert by_env[("restricted", 3)].parse_status == "OUT_OF_SCOPE"
        assert by_env[("restricted", 4)].doc_kind in ("EVENT", "EVENTO") and by_env[("restricted", 4)].parse_status == "PARSED"
    # Consulta operacional padrão = produção (leitura ADN); Produção Restrita (homologação) só quando pedida.
    default = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"]).json()
    restricted = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"], params={"environment": "restricted"}).json()
    assert default["total"] == 1 and restricted["total"] == 2
    assert {d["environment"] for d in default["items"]} == {"production"}
    assert {d["environment"] for d in restricted["items"]} == {"restricted"}
    cancelled = [d for d in restricted["items"] if d["status"] == "CANCELLED"]
    assert len(cancelled) == 1 and cancelled[0]["direction"] == "RECEIVED"
    st = ostack.fiscal.get(f"{F}/{real['id']}/sync/status", headers=real["h"], params={"environment": "production"}).json()
    assert st["environment"] == "production" and st["simulated"] is False and st["status"] == "OK"
    # Replay após 12 h: nada duplica; o fim da fila (E2220) respeita o intervalo.
    before = len(gov.calls)
    assert w.run_sync() == 0  # dentro do intervalo, nenhuma consulta
    assert len(gov.calls) == before
    ostack.clock.advance(hours=12, minutes=1)
    _drain(w)
    with ostack.fiscal_sessions() as s:
        assert len(s.scalars(select(DfeItem).where(DfeItem.company_id == real["id"])).all()) == 5
    assert [c[3] for c in gov.calls[before:]] and all(c[3].endswith(("/DFe/4", "/DFe/1")) for c in gov.calls[before:])


def test_environment_query_requires_enabled_environment(ostack, real):
    other = ostack.add_company("Outra", cnpj("556667770001"), environment="restricted", municipality=MUN)
    uid = ostack.add_user("outra@ex.com")
    ostack.grant(uid, other)
    h = AuthHeaders(ostack, "outra@ex.com")
    r = ostack.fiscal.get(f"{F}/{other}/documents", headers=h, params={"environment": "production"})
    assert r.status_code == 403 and r.json()["code"] == "ENVIRONMENT_NOT_ENABLED"
    assert ostack.core.get(f"{C}/{other}/integration-status", headers=h,
                           params={"environment": "production"}).status_code == 403
    assert ostack.fiscal.get(f"{F}/{other}/documents", headers=h).status_code == 200
    # Usuário de outra empresa não lê produção da empresa real.
    assert ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=h, params={"environment": "production"}).status_code == 403


def test_municipal_parameters_read_only(ostack, real, gov):
    h = {"X-Service-Name": "core-api", "Authorization": "Bearer " + KEYS["core_nfse"]}
    r = ostack.nfse.get(f"/internal/v1/companies/{real['id']}/municipal-parameters/{MUN}", headers=h,
                        params={"environment": "restricted", "cnpj": real["cnpj"]})
    assert r.status_code == 200 and r.json()["http_status"] == 200
    assert gov.calls[-1][:4] == ("param", "GET", "adn.producaorestrita.nfse.gov.br", f"/parametrizacao/{MUN}/convenio")


def test_date_type_service_date_round_trip(ostack, real):
    d = official_draft(ostack, real, service_date="2026-10-15")
    assert d["service_date"] == "2026-10-15" and date.fromisoformat(d["service_date"]).month == 10


def test_dps_ibs_cbs_and_obra_address_follow_xsd():
    from nfse_api import dps
    from nfse_api.signer import sign_enveloped

    snap = _snapshot(
        service={"national_code": "070201", "description": "Obra", "location_code": MUN, "nbs_code": "114052200",
                 "obra_address": {"postal_code": "68500000", "street": "Rua da Obra", "number": "1", "district": "Centro"}},
        tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "RETAINED_BY_TOMADOR", "approx_tax": APPROX,
                      "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001", "personal_use": "0"}})
    m = dps.montar(snap, environment="restricted", serie="1", numero=3, agora=datetime(2026, 10, 20, tzinfo=SP))
    inf = etree.fromstring(m.xml).find(f"{NS}infDPS")
    assert [c.tag.replace(NS, "") for c in inf][-3:] == ["serv", "valores", "IBSCBS"]
    g = inf.find(f"{NS}IBSCBS")
    assert [c.tag.replace(NS, "") for c in g] == ["finNFSe", "indFinal", "cIndOp", "indDest", "valores"]
    assert g.findtext(f"{NS}indDest") == "0" and g.findtext(f"{NS}valores/{NS}trib/{NS}gIBSCBS/{NS}cClassTrib") == "000001"
    assert inf.findtext(f"{NS}serv/{NS}obra/{NS}end/{NS}CEP") == "68500000" and inf.find(f"{NS}serv/{NS}obra/{NS}cObra") is None
    key, cert = make_cert(cnpj_value=cnpj("123456780001"))
    dps.validar_xsd(sign_enveloped(m.xml, key, cert.public_bytes(serialization.Encoding.DER)), "restricted")
    with pytest.raises(dps.DpsIncompleta):
        dps.montar(_snapshot(service={**snap["service"], "obra_code": "123"}), environment="restricted", serie="1",
                   numero=4, agora=datetime(2026, 10, 20, tzinfo=SP))


def test_draft_obra_choice_and_ibs_cbs_warning(ostack, real):
    r = ostack.fiscal.post(f"{F}/{real['id']}/drafts", headers=real["h"], json={
        "customer_id": real["customer"], "competence": "2026-10", "service_date": "2026-10-01", "amount": "10.00",
        "service": {"national_code": "070201", "description": "x", "location_code": MUN, "obra_code": "1",
                    "obra_address": {"postal_code": "68500000", "street": "R", "number": "1", "district": "C"}}})
    assert r.status_code == 422
    d = official_draft(ostack, real)
    v = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()
    assert v["valid"] is True and "IBS_CBS_REQUIRED" in {i["code"] for i in v["issues"] if i["severity"] == "warning"}  # Ato Conjunto 4/2026: aviso fora de produção
    ibs = {"operation_indicator": "100301", "cst": "000", "class_code": "000001", "personal_use": "0"}
    d1 = official_draft(ostack, real, tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                                    "approx_tax": APPROX, "ibs_cbs": ibs})
    v1 = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d1['id']}/validate", headers=real["h"]).json()
    assert v1["valid"] is False and "NBS_REQUIRED_WITH_IBS_CBS" in {i["code"] for i in v1["issues"]}
    imovel = {**ibs, "operation_indicator": "020101"}
    d3 = official_draft(ostack, real, service={"national_code": "010101", "description": "x", "location_code": MUN,
                                               "nbs_code": "114052200"},
                        tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                      "approx_tax": APPROX, "ibs_cbs": imovel})
    v3 = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d3['id']}/validate", headers=real["h"]).json()
    assert "REAL_ESTATE_REQUIRED" in {i["code"] for i in v3["issues"]}  # E0932: imóvel exige o grupo imovel
    d2 = official_draft(ostack, real, tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                                    "approx_tax": APPROX, "ibs_cbs": ibs})
    assert d2["tax_settings"]["ibs_cbs"] == ibs


def test_dps_customer_phone_and_pis_cofins_detail_order():
    from nfse_api import dps

    snap = _snapshot(customer={**_snapshot()["customer"], "phone": "(94) 3322-1100", "email": "t@ex.com"},
                     tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX,
                                   "pis_cofins_cst": "01", "pis_cofins_base": "1500.00", "pis_rate": "0.65", "cofins_rate": "3.00",
                                   "pis_amount": "9.75", "cofins_amount": "45.00", "pis_cofins_retention": "0", "inss_withheld": "165.00"})
    inf = etree.fromstring(dps.montar(snap, environment="restricted", serie="1", numero=5,
                                      agora=datetime(2026, 10, 20, tzinfo=SP)).xml).find(f"{NS}infDPS")
    assert [c.tag.replace(NS, "") for c in inf.find(f"{NS}toma")][-3:] == ["end", "fone", "email"]
    assert inf.findtext(f"{NS}toma/{NS}fone") == "9433221100"
    pc = inf.find(f"{NS}valores/{NS}trib/{NS}tribFed/{NS}piscofins")
    assert [c.tag.replace(NS, "") for c in pc] == ["CST", "vBCPisCofins", "pAliqPis", "pAliqCofins", "vPis", "vCofins", "tpRetPisCofins"]
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribFed/{NS}vRetCP") == "165.00"


def test_paliq_rules_e0617_and_e0595(ostack, real):
    # Convênio ATIVO (Parametrização consultada na validação): pAliq informado é bloqueio E0617 com orientação própria.
    base = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX}
    d = official_draft(ostack, real, tax_settings={**base, "iss_rate": "5.00"})
    v = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()
    err = next(i for i in v["issues"] if i["code"] == "PALIQ_NOT_ALLOWED_BY_CONVENIO")
    assert v["valid"] is False and err["severity"] == "error" and err["rule"] == "E0617"
    assert "ATIVO" in err["message"] and "Mover para a referência" in err["action"]
    assert err["reason_source"].startswith("Anexo I") and err["details"]["convenio"] == "ACTIVE"
    assert "PALIQ_DEPENDS_ON_INCIDENCE_CONVENIO" not in {i["code"] for i in v["issues"]}
    d6 = official_draft(ostack, real, tax_settings={**base, "iss_rate": "6.00"})
    v6 = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d6['id']}/validate", headers=real["h"]).json()
    assert "PALIQ_ABOVE_5" in {i["code"] for i in v6["issues"] if i["severity"] == "error"}


def test_paliq_e0617_e0619_by_incidence_convenio_at_transmission(ostack, real, gov):
    """A regra é conferida de novo na transmissão: se o convênio mudar entre o agendamento e o envio, nada sai."""
    from core_api import parametrizacao_iss

    pa = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "iss_rate": "5.00", "approx_tax": APPROX}
    # Agendado com convênio INATIVO (pAliq obrigatório e informado); ATIVO na hora do envio => LOCAL_E0617, sem POST.
    gov.convenio_inativo.add(MUN)
    d = official_draft(ostack, real, tax_settings=pa)
    b = schedule_now(ostack, real, [d])
    gov.convenio_inativo.discard(MUN)
    ostack.worker().run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "REJECTED" and item["result"]["errors"][0]["code"] == "LOCAL_E0617" and gov.posts() == []
    assert "Nada foi transmitido" in item["result"]["errors"][0]["message"]
    # Evidência de não envio gravada pelo worker: UMA chamada ao conector, barrada antes do POST => pode excluir.
    assert item["result"]["connector_transmissions"] == 1
    rd = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()
    assert rd["deletion"]["allowed"] is True, rd["deletion"]
    assert any(c[0] == "param" and c[3] == f"/parametrizacao/{MUN}/convenio" for c in gov.calls)
    # Agendado com convênio ATIVO sem pAliq; INATIVO na hora do envio => LOCAL_E0619, sem POST.
    parametrizacao_iss._cache.clear()
    d2 = official_draft(ostack, real)
    b2 = schedule_now(ostack, real, [d2])
    gov.convenio_inativo.add(MUN)
    ostack.worker().run_emissions()
    item2 = batch_items(ostack, real, b2["batch_id"])[0]
    assert item2["status"] == "REJECTED" and item2["result"]["errors"][0]["code"] == "LOCAL_E0619" and gov.posts() == []
    # Convênio INATIVO + alíquota informada => transmite.
    parametrizacao_iss._cache.clear()
    d3 = official_draft(ostack, real, tax_settings=pa)
    b3 = schedule_now(ostack, real, [d3])
    ostack.worker().run_emissions()
    assert batch_items(ostack, real, b3["batch_id"])[0]["status"] == "AUTHORIZED" and len(gov.posts()) == 1


def test_dps_series_range_e0010():
    from nfse_api import dps

    for serie in ("70000", "50000", "90000"):
        with pytest.raises(dps.DpsIncompleta) as e:
            dps.montar(_snapshot(), environment="restricted", serie=serie, numero=1, agora=datetime(2026, 10, 20, tzinfo=SP))
        assert {f["codigo"] for f in e.value.faltas} == {"SERIE_FORA_DA_FAIXA"}
    assert dps.montar(_snapshot(), environment="restricted", serie="49999", numero=1,
                      agora=datetime(2026, 10, 20, tzinfo=SP)).dps_id[25:30] == "49999"
