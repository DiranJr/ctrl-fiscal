"""IBS/CBS 2026: alíquotas-padrão com vigência (CBS 0,9%; IBS 0,1% ESTADUAL e 0% municipal — sem duplicar), prévia
em Decimal com a base de 2026 do Anexo I (E1530), pendências para redução/regime/classificação indefinida, nada
aplicado a 2027, Simples fora (art. 348 III-c), IBS/CBS fora do valor líquido, DPS sem alíquota nem valor (a Sefin
calcula) e recálculo pela vigência da nova operação na reutilização de notas. Nenhuma nota é transmitida."""
from __future__ import annotations

import uuid
from datetime import datetime

from lxml import etree

from conftest import SP, cnpj

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
NS = "{http://www.sped.fazenda.gov.br/nfse}"
MUN = "1505536"


def perfil(stack, world, simples: str = "1") -> None:  # noqa: ANN001
    r = stack.core.patch(f"{C}/{world['a']}/fiscal-profile", headers=world["ha"], json={
        "simples_option": simples, "special_regime": "0", "municipal_registration": "123456", "source": "teste"})
    assert r.status_code == 200, r.text


def rascunho(stack, world, *, data: str | None = "2026-10-09", ibs: dict | None = None, iss_rate: str | None = "2.00",
             competencia: str = "2026-10") -> dict:
    tax = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01",
           "pis_amount": "6.50", "cofins_amount": "30.00", "approx_tax": {"mode": "VALUES", "federal": "10.00", "state": "0.00", "municipal": "20.00"}}
    if iss_rate:
        tax["iss_rate"] = iss_rate
    if ibs:
        tax["ibs_cbs"] = ibs
    body = {"customer_id": world["customer"], "competence": competencia, "amount": "1000.00",
            "service": {"national_code": "010101", "description": "Serviço de teste IBS/CBS", "location_code": MUN,
                        "nbs_code": "101011100"}, "tax_settings": tax}
    if data:
        body["service_date"] = data
    r = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json=body)
    assert r.status_code == 201, r.text
    return r.json()


def previa(stack, world, d) -> dict:  # noqa: ANN001
    r = stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}/ibs-cbs-preview", headers=world["ha"])
    assert r.status_code == 200, r.text
    return r.json()


def issues(stack, world, d) -> dict:  # noqa: ANN001
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()
    return {i["code"]: i for i in v["issues"]}


INTEGRAL = {"operation_indicator": "100301", "cst": "000", "class_code": "000001", "personal_use": "0"}


def test_parametros_com_vigencia_fonte_e_tabela_oficial(stack, world):
    ps = stack.fiscal.get(f"{F}/{world['a']}/tax-parameters", headers=world["ha"]).json()
    por = {(p["tributo"], p["esfera"]): p for p in ps}
    assert set(por) == {("IBS", "UF"), ("IBS", "MUN"), ("CBS", "FED")}
    assert por[("IBS", "UF")]["aliquota"] == "0.1000" and por[("IBS", "MUN")]["aliquota"] == "0.0000"
    assert por[("CBS", "FED")]["aliquota"] == "0.9000"
    assert all(p["vigencia_inicio"] == "2026-01-01" and p["vigencia_fim"] == "2026-12-31" for p in ps)
    assert "LC 214/2025" in por[("CBS", "FED")]["fonte"] and "titular" in por[("CBS", "FED")]["autorizacao"]
    t = stack.fiscal.get(f"{F}/{world['a']}/tax-classifications", headers=world["ha"], params={"on": "2026-10-09"}).json()
    cods = {c["codigo"]: c for c in t["items"]}
    assert cods["000001"]["tributacao_integral"] is True and cods["200046"]["reducao_ibs"] == "50.0"
    assert t["fonte"].startswith("https://dfe-portal.svrs.rs.gov.br")


def test_previa_2026_decimal_base_e1530_sem_duplicar_ibs(stack, world):
    perfil(stack, world)
    d = rascunho(stack, world, ibs=INTEGRAL)
    p = previa(stack, world, d)
    assert p["status"] == "CALCULADO", p
    # vBC = 1000,00 − 0 − vISSQN 20,00 − vPIS 6,50 − vCOFINS 30,00 = 943,50
    assert p["base"]["vBC"] == "943.50" and p["base"]["vISSQN"] == "20.00"
    v = p["valores"]
    assert v["vIBSUF"] == "0.94" and v["vIBSMun"] == "0.00" and v["vIBSTot"] == "0.94"  # 0,1% só estadual
    assert v["vCBS"] == "8.49"  # 943,50 × 0,9% = 8,4915 → 8,49 (Decimal, 2 casas)
    assert v["vLiq_inalterado"] == "1000.00" and v["vTotNF_2026"] == "1000.00"  # por fora: não altera o líquido
    assert p["aliquotas"]["ibs_uf"] == "0.1000" and p["aliquotas"]["ibs_mun"] == "0.0000"
    assert any("DISPENSADO" in o for o in p["observacoes"])  # dispensa ≠ alíquota zero
    sumario = stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()["retention_summary"]
    assert sumario["net_amount"] == "1000.00" and sumario["total_withheld"] == "0.00"  # IBS/CBS não é retenção


def test_2027_reducao_regime_classificacao_e_simples(stack, world):
    perfil(stack, world)
    p = previa(stack, world, rascunho(stack, world, data="2027-01-10", competencia="2027-01", ibs=INTEGRAL))
    assert p["status"] == "PENDENTE" and "ALIQUOTA_SEM_VIGENCIA" in {x["code"] for x in p["pendencias"]}
    assert p["valores"] is None and p["aliquotas"] is None  # nada de 2026 aplicado a 2027
    red = rascunho(stack, world, ibs={**INTEGRAL, "cst": "200", "class_code": "200052"})
    p = previa(stack, world, red)
    assert p["status"] == "PENDENTE" and "REDUCAO_OU_REGIME_ESPECIFICO" in {x["code"] for x in p["pendencias"]}
    assert p["valores"] is None
    assert issues(stack, world, red)["IBS_CBS_SPECIAL_TREATMENT_REVIEW"]["severity"] == "warning"
    errado = rascunho(stack, world, ibs={**INTEGRAL, "cst": "000", "class_code": "200052"})
    assert "CST_CCLASSTRIB_MISMATCH" in issues(stack, world, errado)  # E0959
    sem = rascunho(stack, world)
    p = previa(stack, world, sem)
    assert "CLASSIFICACAO_INDEFINIDA" in {x["code"] for x in p["pendencias"]} and p["valores"] is None
    # Ato Conjunto 4/2026 (≥ 01/10/2026): erro de prontidão em produção; aviso na simulação/homologação.
    assert issues(stack, world, sem)["IBS_CBS_REQUIRED"]["severity"] == "warning"
    from ctrl_fiscal.domain import ibscbs as mod

    class D:  # noqa: D106
        tax_settings, service_date = {}, "2026-10-09"
    assert mod.validacao_issues(D(), {"simples_option": "1"}, "production")[0]["severity"] == "error"
    antes = rascunho(stack, world, data="2026-09-15", competencia="2026-09")
    assert issues(stack, world, antes)["IBS_CBS_NOT_INFORMED"]["severity"] == "warning"
    sefin = rascunho(stack, world, ibs=INTEGRAL, iss_rate=None)
    assert "ISS_DEFINIDO_PELA_SEFIN" in {x["code"] for x in previa(stack, world, sefin)["pendencias"]}
    perfil(stack, world, simples="3")
    assert previa(stack, world, rascunho(stack, world, ibs=INTEGRAL))["status"] == "NAO_APLICAVEL_SIMPLES"


def test_dps_leva_so_codigos_e_valida_no_xsd():
    from nfse_api import dps

    snap = {
        "company": {"cnpj": cnpj("123456780001"), "municipality_code": MUN, "timezone": "America/Sao_Paulo",
                    "fiscal_profile": {"simples_option": "1", "special_regime": "0", "municipal_registration": "123456"}},
        "customer": {"document": cnpj("445556660001"), "name": "Tomador Teste", "municipality_code": MUN,
                     "postal_code": "68500000", "address": {"street": "Rua A", "number": "10", "district": "Centro"}},
        "competence": "2026-10", "service_date": "2026-10-09", "amount": "1000.00", "deductions": "0.00",
        "service": {"national_code": "010101", "municipal_code": "001", "description": "Serviço", "location_code": MUN,
                    "nbs_code": "101011100"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                         "approx_tax": {"mode": "VALUES", "federal": "10.00", "state": "0.00", "municipal": "20.00"}, "ibs_cbs": INTEGRAL},
    }
    m = dps.montar(snap, environment="restricted", serie="1", numero=1, agora=datetime(2026, 10, 9, 10, 0, tzinfo=SP))
    root = etree.fromstring(m.xml)
    g = root.find(f"{NS}infDPS/{NS}IBSCBS")
    assert g is not None and g.findtext(f"{NS}cIndOp") == "100301"
    assert g.findtext(f"{NS}valores/{NS}trib/{NS}gIBSCBS/{NS}CST") == "000"
    assert g.findtext(f"{NS}valores/{NS}trib/{NS}gIBSCBS/{NS}cClassTrib") == "000001"
    texto = m.xml.decode()
    for proibido in ("pIBSUF", "pIBSMun", "pCBS", "vIBS", "vCBS", "vBC"):
        assert proibido not in texto  # a DPS não leva alíquota nem valor de IBS/CBS (a Sefin calcula)


def test_reutilizacao_recalcula_pela_vigencia_da_nova_operacao(stack, world):
    from test_rascunho_referencia import doc_por_numero, importar, nota_como_187

    perfil(stack, world)
    toma = cnpj("556677880001")
    importar(stack, world, nota_como_187(world["a_cnpj"], toma, 187), "n187.xml")
    stack.core.post(f"{C}/{world['a']}/customers", headers=world["ha"],
                    json={"name": "TOMADOR FICTICIO", "document_type": "CNPJ", "document": toma})
    doc = doc_por_numero(stack, world, "187")
    d = stack.fiscal.post(f"{F}/{world['a']}/drafts/from-document",
                          headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"source_document_id": doc["id"]}).json()
    p = previa(stack, world, d)
    assert p["status"] == "PENDENTE" and "FATO_GERADOR_PENDENTE" in {x["code"] for x in p["pendencias"]}
    d2 = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"], json={
        "expected_version": d["version"], "service_date": "2026-10-20",
        "tax_settings": {**{k: v for k, v in d["tax_settings"].items() if v is not None}, "iss_rate": "2.00",
                         "ibs_cbs": INTEGRAL}}).json()
    p = previa(stack, world, d2)
    assert p["status"] == "CALCULADO" and p["fato_gerador"] == "2026-10-20", p
    original = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    assert original["number"] == "187" and original["status"] == doc["status"]
    assert original["access_key"] == doc["access_key"]


def test_valores_oficiais_da_sefin_preservados_e_separados_da_previa(stack, world, tmp_path):
    """infNFSe/IBSCBS (calculado pela Sefin) é guardado com o texto exato do XML e exibido no detalhe da nota;
    a migração 0008 preenche as notas já armazenadas com o mesmo extrator."""
    from test_rascunho_referencia import doc_por_numero, importar
    from fixtures_nfse import nota_como_187

    toma = cnpj("556677880001")
    importar(stack, world, nota_como_187(world["a_cnpj"], toma, 201, ibscbs=True), "n201.xml")
    importar(stack, world, nota_como_187(world["a_cnpj"], toma, 202), "n202.xml")
    d = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc_por_numero(stack, world, '201')['id']}",
                         headers=world["ha"]).json()
    of = d["ibs_cbs_official"]
    assert of["fonte"] == "infNFSe/IBSCBS (Sefin)"
    v = of["valores"]
    assert v["valores/vBC"] == "14700.00" and v["valores/uf/pIBSUF"] == "0.10" and v["valores/fed/pCBS"] == "0.90"
    assert v["totCIBS/gIBS/vIBSTot"] == "14.70" and v["totCIBS/gCBS/vCBS"] == "132.30"
    assert v["cLocalidadeIncid"] == MUN and of["dps"] == {"finNFSe": "0", "indFinal": "0", "cIndOp": "100301",
                                                           "indDest": "0", "CST": "000", "cClassTrib": "000001"}
    sem = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc_por_numero(stack, world, '202')['id']}",
                           headers=world["ha"]).json()
    assert sem["ibs_cbs_official"] is None  # nota sem o grupo: nada é inventado

    # Backfill: apaga o campo e reaplica a migração 0008 sobre o XML guardado.
    from alembic import command
    from sqlalchemy import create_engine, text

    from conftest import ROOT, SERVICES, Config

    url = stack.urls["fiscal"]
    e = create_engine(url)
    with e.begin() as c:
        c.execute(text("UPDATE documents SET ibs_cbs_official = NULL"))
    cfg = Config(str(ROOT / SERVICES["fiscal"] / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / SERVICES["fiscal"] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(cfg, "0007")
    command.upgrade(cfg, "head")
    again = stack.fiscal.get(f"{F}/{world['a']}/documents/{d['id']}", headers=world["ha"]).json()
    assert again["ibs_cbs_official"] == of
    e.dispose()
