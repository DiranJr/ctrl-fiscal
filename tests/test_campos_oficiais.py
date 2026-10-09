"""Campos oficiais da DPS v1.01 acrescentados nesta etapa: serv/obra (inscImobFisc + cObra|cCIB|end), serv/infoCompl,
IBSCBS/dest (indDest=1), IBSCBS/imovel (≠ obra), gTribRegular, gDif e cCredPres — esquema, regras oficiais
(E0928/E0931/E0932, E0964/E0965/E0969/E0970, E0971/E0972, E0911/E0913), ordem do XSD (PRODREST e PRODUÇÃO),
leitura para reutilização e persistência no rascunho. Nenhuma transmissão."""
from __future__ import annotations

from datetime import datetime

from lxml import etree

from conftest import SP, cnpj
from test_official_integration import (  # noqa: F401 - fixtures
    APPROX, MUN, NS, _snapshot, gov, official_draft, ostack, real,
)

F = "/api/fiscal/v1/companies"
ENDERECO = {"postal_code": "68515000", "street": "Rua da Obra", "number": "10", "district": "Centro"}
IBS = {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}


def _montar(snap: dict, env: str = "restricted") -> etree._Element:
    from nfse_api import dps

    return etree.fromstring(dps.montar(snap, environment=env, serie="1", numero=7,
                                       agora=datetime(2026, 10, 20, 10, tzinfo=SP)).xml).find(f"{NS}infDPS")


def _tags(el) -> list[str]:  # noqa: ANN001
    return [c.tag.replace(NS, "") for c in el]


def test_obra_cib_inscricao_e_info_complementar_na_ordem_do_xsd():
    serv = {"national_code": "070201", "municipal_code": "001", "description": "Execução de obra", "location_code": MUN,
            "obra_cib": "AB12345X", "obra_property_registration": "IPTU-778899",
            "additional_info": {"technical_doc": "ART 1234567", "document_reference": "Contrato 15/2026",
                                "order_number": "PO-998", "order_items": ["10", "20"], "notes": "Medição 3 de 5."}}
    for env in ("restricted", "production"):  # produção: série tratada como documentado em dps.validar_xsd
        inf = _montar(_snapshot(service=serv), env)
        s = inf.find(f"{NS}serv")
        assert _tags(s) == ["locPrest", "cServ", "obra", "infoCompl"]
        assert _tags(s.find(f"{NS}obra")) == ["inscImobFisc", "cCIB"]
        ic = s.find(f"{NS}infoCompl")
        assert _tags(ic) == ["idDocTec", "docRef", "xPed", "gItemPed", "xInfComp"]
        assert [i.text for i in ic.find(f"{NS}gItemPed")] == ["10", "20"]
    # endereço da obra com inscrição imobiliária
    inf = _montar(_snapshot(service={**serv, "obra_cib": None, "obra_address": ENDERECO, "additional_info": None}))
    assert _tags(inf.find(f"{NS}serv/{NS}obra")) == ["inscImobFisc", "end"]


def test_ibscbs_destinatario_imovel_regular_e_diferimento_na_ordem_do_xsd():
    ibs = {"operation_indicator": "020101", "cst": "550", "class_code": "550016", "presumed_credit": None,
           "regular": {"cst": "000", "class_code": "000001"}, "deferral": {"uf": "10.00", "mun": "0", "cbs": "100.00"},
           "real_estate": {"registration": "123", "address": ENDERECO},
           "recipient": {"document_type": "CNPJ", "document": cnpj("556677880001"), "name": "Destinatário Fictício",
                         "address": {"municipality_code": MUN, "postal_code": "68515000", "street": "Rua B", "number": "2",
                                     "district": "Centro"}, "phone": "94999990000", "email": "dest@ex.com"}}
    serv = {"national_code": "010101", "municipal_code": "001", "description": "Serviço", "location_code": MUN,
            "nbs_code": "114052200"}
    for env in ("restricted", "production"):
        g = _montar(_snapshot(service=serv, tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                                          "approx_tax": APPROX, "ibs_cbs": ibs}), env).find(f"{NS}IBSCBS")
        assert _tags(g) == ["finNFSe", "cIndOp", "indDest", "dest", "imovel", "valores"]
        assert g.findtext(f"{NS}indDest") == "1"
        assert _tags(g.find(f"{NS}dest")) == ["CNPJ", "xNome", "end", "fone", "email"]
        assert _tags(g.find(f"{NS}imovel")) == ["inscImobFisc", "end"]
        gi = g.find(f"{NS}valores/{NS}trib/{NS}gIBSCBS")
        assert _tags(gi) == ["CST", "cClassTrib", "gTribRegular", "gDif"]
    # Sem destinatário: indDest=0 e nenhum grupo dest; NO_NIF vira cNaoNIF.
    g = _montar(_snapshot(service=serv, tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                                      "approx_tax": APPROX, "ibs_cbs": {
        **ibs, "recipient": {"document_type": "NO_NIF", "no_nif_reason": "1", "name": "Destinatário Exterior"}}})) \
        .find(f"{NS}IBSCBS")
    assert _tags(g.find(f"{NS}dest")) == ["cNaoNIF", "xNome"]


def test_regras_dos_grupos_condicionais(ostack, real):
    def issues(**over) -> set[str]:  # noqa: ANN003
        d = official_draft(ostack, real, **over)
        v = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()
        return {i["code"] for i in v["issues"]}

    servico = {"national_code": "010101", "municipal_code": "001", "description": "x", "location_code": MUN,
               "nbs_code": "114052200"}

    def tax(ibs: dict) -> dict:
        return {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX, "ibs_cbs": ibs}

    regime = {"operation_indicator": "100301", "cst": "550", "class_code": "550016"}
    assert "TRIB_REGULAR_REQUIRED" in issues(service=servico, tax_settings=tax(regime))  # E0965
    assert "CST_REG_MISMATCH" in issues(service=servico, tax_settings=tax(
        {**regime, "regular": {"cst": "200", "class_code": "000001"}}))  # E0970
    assert "CCLASSTRIB_REG_NOT_VALID_FOR_NFSE" in issues(service=servico, tax_settings=tax(
        {**regime, "regular": {"cst": "999", "class_code": "999999"}}))  # E0969
    ok = issues(service=servico, tax_settings=tax({**regime, "regular": {"cst": "000", "class_code": "000001"}}))
    assert not ok & {"TRIB_REGULAR_REQUIRED", "CST_REG_MISMATCH", "CCLASSTRIB_REG_NOT_VALID_FOR_NFSE"}
    assert "TRIB_REGULAR_NOT_ALLOWED" in issues(service=servico, tax_settings=tax(
        {**IBS, "regular": {"cst": "000", "class_code": "000001"}}))  # E0964
    dif = {"operation_indicator": "100301", "cst": "515", "class_code": "515001"}
    assert "DEFERRAL_REQUIRED" in issues(service=servico, tax_settings=tax(dif))  # E0972
    assert "DEFERRAL_NOT_ALLOWED" in issues(service=servico, tax_settings=tax(
        {**IBS, "deferral": {"uf": "10.00", "mun": "0", "cbs": "10.00"}}))  # E0971
    assert "PRESUMED_CREDIT_NOT_ALLOWED" in issues(service=servico, tax_settings=tax({**IBS, "presumed_credit": "01"}))
    imovel = {"address": ENDERECO}
    assert "REAL_ESTATE_NOT_ALLOWED" in issues(service=servico, tax_settings=tax({**IBS, "real_estate": imovel}))  # E0928
    obra = {"national_code": "070201", "municipal_code": "001", "description": "x", "location_code": MUN,
            "nbs_code": "114052200", "obra_code": "CNO-1"}
    assert "REAL_ESTATE_NOT_ALLOWED_FOR_OBRA" in issues(service=obra, tax_settings=tax(
        {**IBS, "operation_indicator": "020101", "real_estate": imovel}))  # E0931
    sem = issues(service=servico, tax_settings=tax({**IBS, "operation_indicator": "020101", "real_estate": imovel}))
    assert not sem & {"REAL_ESTATE_REQUIRED", "REAL_ESTATE_NOT_ALLOWED", "REAL_ESTATE_NOT_ALLOWED_FOR_OBRA"}
    # inscrição imobiliária sem identificação da obra (fora de subitem de obra)
    assert "OBRA_IDENTIFICATION_REQUIRED" in issues(service={**servico, "obra_property_registration": "123"})


def test_esquema_recusa_combinacoes_invalidas(ostack, real):
    base = {"customer_id": real["customer"], "competence": "2026-10", "amount": "10.00"}

    def post(**over) -> int:  # noqa: ANN003
        return ostack.fiscal.post(f"{F}/{real['id']}/drafts", headers=real["h"], json={**base, **over}).status_code

    svc = {"national_code": "070201", "description": "x", "location_code": MUN}
    assert post(service={**svc, "obra_code": "1", "obra_cib": "AB12345X"}) == 422  # escolha do XSD
    assert post(service={**svc, "obra_cib": "curto"}) == 422  # TSCodCIB: 8 caracteres
    assert post(service={**svc, "additional_info": {"order_items": ["x" * 61]}}) == 422
    t = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}
    for dest in ({"document_type": "CNPJ", "document": "11222333000100", "name": "X"},  # DV inválido (E0911)
                 {"document_type": "CPF", "document": "12345678900", "name": "X"},  # DV inválido (E0913)
                 {"document_type": "NO_NIF", "name": "X"},  # sem motivo
                 {"document_type": "CNPJ", "document": cnpj("556677880001"), "name": "X", "email": "sem-arroba"}):
        assert post(service=svc, tax_settings={**t, "ibs_cbs": {**IBS, "recipient": dest}}) == 422, dest
    assert post(service=svc, tax_settings={**t, "ibs_cbs": {**IBS, "real_estate": {"cib": "AB12345X",
                                                                                   "address": ENDERECO}}}) == 422
    assert post(service=svc, tax_settings={**t, "ibs_cbs": {**IBS, "deferral": {"uf": "1000", "mun": "0",
                                                                               "cbs": "0"}}}) == 422


def test_rascunho_guarda_e_edita_os_grupos(ostack, real):
    info = {"technical_doc": "ART 1", "order_number": "PO-1", "order_items": ["1"], "notes": "Obs."}
    d = official_draft(ostack, real, service={"national_code": "070201", "municipal_code": "001", "description": "Obra",
                                              "location_code": MUN, "obra_cib": "AB12345X",
                                              "obra_property_registration": "IPTU-1", "additional_info": info})
    assert d["service"]["obra_cib"] == "AB12345X" and d["service"]["additional_info"]["order_items"] == ["1"]
    assert d["service"]["obra_property_registration"] == "IPTU-1"
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"], json={
        "expected_version": d["version"], "service": {"obra_cib": None, "obra_code": "CNO-77", "additional_info": None}})
    assert r.status_code == 200, r.text
    s = r.json()["service"]
    assert s["obra_code"] == "CNO-77" and s["obra_cib"] is None and s["additional_info"] is None
    assert s["obra_property_registration"] == "IPTU-1"
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"], json={
        "expected_version": r.json()["version"], "service": {"obra_cib": "AB12345X"}})
    assert r.status_code == 422 and r.json()["code"] == "OBRA_CHOICE"  # cObra já existe: escolha do XSD


def test_leitura_para_reutilizacao_devolve_os_mesmos_grupos():
    from ctrl_fiscal.nfse_xml import _ibs_cbs, servico_extra

    ibs = {"operation_indicator": "020101", "cst": "550", "class_code": "550016",
           "regular": {"cst": "000", "class_code": "000001"}, "deferral": {"uf": "10.00", "mun": "0", "cbs": "100.00"},
           "real_estate": {"registration": "123", "address": ENDERECO},
           "recipient": {"document_type": "CPF", "document": "52998224725", "name": "Pessoa Destinatária"}}
    serv = {"national_code": "010101", "municipal_code": "001", "description": "Serviço", "location_code": MUN,
            "nbs_code": "114052200"}
    inf = _montar(_snapshot(service=serv, tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                                                        "approx_tax": APPROX, "ibs_cbs": ibs}))
    lido = _ibs_cbs(inf.find(f"{NS}IBSCBS"))
    assert lido == {**ibs, "personal_use": None}
    # obra (subitem de obra) e infoCompl
    obra = {**serv, "national_code": "070201", "obra_property_registration": "I-9", "obra_cib": "AB12345X",
            "additional_info": {"order_number": "PO-5", "order_items": ["7"], "notes": "Nota."}}
    inf = _montar(_snapshot(service=obra))
    assert servico_extra(inf.find(f"{NS}serv")) == {
        "obra_cib": "AB12345X", "obra_property_registration": "I-9",
        "additional_info": {"order_number": "PO-5", "order_items": ["7"], "notes": "Nota."}}
