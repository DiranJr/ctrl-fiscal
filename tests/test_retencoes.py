"""Retenções: separação entre valor do tributo e valor retido, decimais exatos e os MESMOS valores em criação,
edição, revisão (retention_summary), snapshot de emissão, XML da DPS e documento ingerido da NFS-e.

Fórmulas (XSD v1.01 TCValoresNFSe / Anexo I): vTotalRet = vRetCP + vRetIRRF + vRetCSLL + ISSQN (só se retido);
vLiq = vServ − vDescIncond − vDescCond − vTotalRet. vPis/vCofins (apuração própria) NUNCA entram.
A "Sefin" simulada (FakeGov.embed_posted_dps) calcula vTotalRet/vLiq de forma independente a partir da DPS enviada."""
from __future__ import annotations

from decimal import Decimal

import pytest
from lxml import etree

from test_official_integration import (  # noqa: F401  (fixtures)
    APPROX, F, MUN, NS, batch_items, gov, official_draft, ostack, real, schedule_now,
)

# PIS 0,65% e COFINS 3% sobre 9.900,00; CSLL 1% = 99,00. tpRetPisCofins=3 (PIS/COFINS/CSLL retidos):
# vRetCSLL = 64,35 + 297,00 + 99,00 = 460,35 (o total das contribuições retidas, não só a CSLL).
MULTI = {
    "issqn_taxation": "TAXABLE", "iss_retention": "RETAINED_BY_TOMADOR", "iss_rate": "2.50", "approx_tax": APPROX,
    "inss_withheld": "1100.00", "irrf_withheld": "150.00",
    "pis_cofins_cst": "01", "pis_cofins_base": "9900.00", "pis_rate": "0.65", "cofins_rate": "3.00",
    "pis_amount": "64.35", "cofins_amount": "297.00", "pis_cofins_retention": "3", "csll_withheld": "460.35",
}


# ---------------------------------------------------------------- cálculo (fonte única)
def test_resumo_multiplas_retencoes_sem_pis_cofins_informado():
    from ctrl_fiscal.domain.retencoes import resumo

    r = resumo("10000.00", "100.00", "50.00", "0.00", MULTI)
    assert r["iss"] == {"retention": "RETAINED_BY_TOMADOR", "retained": True, "base": "9900.00", "rate": "2.50",
                        "rate_source": "DPS_PALIQ", "dps_rate": "2.50", "reference_rate": None,
                        "estimated_amount": "247.50", "amount": "247.50", "amount_source": "ESTIMATED_FROM_RATE"}
    assert r["federal"]["total"] == "1710.35" and r["federal"]["contributions_covered"] == {"pis": True, "cofins": True, "csll": True}
    assert r["informed_not_withheld"] == {"pis": "64.35", "cofins": "297.00"}
    assert r["total_withheld"] == "1957.85"  # 1100 + 150 + 460,35 + 247,50 — sem somar vPis/vCofins de novo
    assert r["net_amount"] == "7892.15"  # 10000 − 100 − 50 − 1957,85
    assert r["complete"] is True and r["pending"] == []


def test_resumo_iss_sem_aliquota_fica_pendente_e_nao_retido_nao_entra():
    from ctrl_fiscal.domain.retencoes import resumo

    pend = resumo("1000.00", None, None, "100.00", {"iss_retention": "RETAINED_BY_TOMADOR", "inss_withheld": "110.00"})
    assert pend["iss"]["amount"] is None and pend["iss"]["amount_source"] == "DETERMINED_BY_SEFIN"
    assert pend["iss"]["base"] == "900.00" and pend["complete"] is False and pend["pending"] == ["iss.amount"]
    assert pend["total_withheld"] == "110.00" and pend["net_amount"] == "890.00"
    nao = resumo("1000.00", None, None, None, {"iss_retention": "NOT_RETAINED", "iss_rate": "5.00",
                                              "pis_amount": "6.50", "cofins_amount": "30.00", "pis_cofins_retention": "2"})
    assert nao["iss"]["amount"] is None and nao["total_withheld"] == "0.00" and nao["net_amount"] == "1000.00"


def test_resumo_arredonda_como_a_sefin_e_nao_aceita_float_nem_casas_extras():
    from ctrl_fiscal.domain.retencoes import dec, resumo

    # 0,125 → 0,12 (HALF_EVEN, comportamento conferido em 186 NFS-e reais); HALF_UP daria 0,13.
    assert resumo("2.50", None, None, None, {"iss_retention": "RETAINED_BY_TOMADOR", "iss_rate": "5.00"})["iss"]["amount"] == "0.12"
    assert resumo("2.70", None, None, None, {"iss_retention": "RETAINED_BY_TOMADOR", "iss_rate": "5.00"})["iss"]["amount"] == "0.14"
    with pytest.raises(TypeError):
        dec(0.1)
    with pytest.raises(ValueError):
        dec("10.005")
    assert dec("0.10") + dec("0.20") == Decimal("0.30")


# ---------------------------------------------------------------- ponta a ponta
def _patch(ostack, real, d, **body):  # noqa: ANN001, ANN003, ANN202
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"],
                            json={"expected_version": d["version"], **body})
    assert r.status_code == 200, r.text
    return r.json()


def _validate(ostack, real, d) -> dict:  # noqa: ANN001
    return ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()


def _emit(ostack, real, gov, d) -> tuple[dict, etree._Element, dict]:  # noqa: ANN001
    """Agenda, roda o worker e devolve (item do lote, infDPS transmitida, documento ingerido)."""
    b = schedule_now(ostack, real, [d])
    ostack.worker().run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "AUTHORIZED", item
    inf = etree.fromstring(gov.posted_dps[-1]).find(f"{NS}infDPS")
    doc = ostack.fiscal.get(f"{F}/{real['id']}/documents/{item['document_id']}", headers=real["h"]).json()
    return item, inf, doc


def _fed(inf, tag):  # noqa: ANN001, ANN202
    return inf.findtext(f"{NS}valores/{NS}trib/{NS}tribFed/" + "/".join(f"{NS}{p}" for p in tag.split("/")))


def test_multiplas_retencoes_mesmos_valores_em_rascunho_snapshot_xml_e_documento(ostack, real, gov):
    gov.embed_posted_dps = True
    gov.convenio_inativo.add(MUN)  # convênio inativo: pAliq informado é exigido (E0619) e a soma fecha localmente
    d = official_draft(ostack, real, amount="10000.00", unconditional_discount="100.00", conditional_discount="50.00",
                       tax_settings=MULTI)
    s = d["retention_summary"]
    assert (s["total_withheld"], s["net_amount"], s["complete"]) == ("1957.85", "7892.15", True)
    assert s["informed_not_withheld"] == {"pis": "64.35", "cofins": "297.00"}
    v = _validate(ostack, real, d)
    assert v["valid"] is True, v["issues"]
    item, inf, doc = _emit(ostack, real, gov, d)
    # snapshot congelado = revisão
    assert (item["total_withheld"], item["net_amount"]) == ("1957.85", "7892.15")
    # XML da DPS: cada retenção uma única vez, valores exatos; vPis/vCofins no grupo piscofins
    assert [_fed(inf, t) for t in ("vRetCP", "vRetIRRF", "vRetCSLL")] == ["1100.00", "150.00", "460.35"]
    assert _fed(inf, "piscofins/vPis") == "64.35" and _fed(inf, "piscofins/vCofins") == "297.00"
    assert _fed(inf, "piscofins/tpRetPisCofins") == "3"
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}tpRetISSQN") == "2"
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}pAliq") == "2.50"
    assert inf.findtext(f"{NS}valores/{NS}vDescCondIncond/{NS}vDescIncond") == "100.00"
    for tag in ("vRetCP", "vRetIRRF", "vRetCSLL"):
        assert len(inf.findall(f".//{NS}{tag}")) == 1
    # documento ingerido da NFS-e (Sefin simulada calculou de forma independente) = rascunho
    assert (doc["total_retained"], doc["net_amount"]) == ("1957.85", "7892.15")
    assert doc["retentions"]["inss"] == "1100.00" and doc["retentions"]["irrf"] == "150.00"
    assert doc["retentions"]["csll"] == "460.35" and doc["retentions"]["iss"] == "247.50"
    assert doc["retentions"]["pis"] is None and doc["retentions"]["cofins"] is None  # não retidos individualmente
    # se vPis/vCofins tivessem sido descontados de novo, o líquido seria 7530,80
    assert Decimal(doc["net_amount"]) != Decimal("7892.15") - Decimal("64.35") - Decimal("297.00")


def test_remocao_das_retencoes_na_edicao_sem_duplicidade(ostack, real, gov):
    gov.embed_posted_dps = True
    gov.convenio_inativo.add(MUN)
    d = official_draft(ostack, real, amount="10000.00", unconditional_discount="100.00", conditional_discount="50.00",
                       tax_settings=MULTI)
    # Reenviar os mesmos valores não acumula
    d = _patch(ostack, real, d, tax_settings={"inss_withheld": "1100.00", "irrf_withheld": "150.00"})
    assert d["retention_summary"]["total_withheld"] == "1957.85"
    # Remove só o IRRF: total cai exatamente 150,00
    d = _patch(ostack, real, d, tax_settings={"irrf_withheld": None})
    assert d["tax_settings"]["irrf_withheld"] is None
    assert (d["retention_summary"]["total_withheld"], d["retention_summary"]["net_amount"]) == ("1807.85", "8042.15")
    # Contribuições: trocar para "não retidos" exige remover vRetCSLL (E0720 com 0); vPis/vCofins continuam informados
    d = _patch(ostack, real, d, tax_settings={"pis_cofins_retention": "0", "csll_withheld": None})
    assert d["retention_summary"]["informed_not_withheld"] == {"pis": "64.35", "cofins": "297.00"}
    assert d["retention_summary"]["federal"]["contributions"] is None
    # Remove CP e ISS retido
    d = _patch(ostack, real, d, tax_settings={"inss_withheld": None, "iss_retention": "NOT_RETAINED"})
    s = d["retention_summary"]
    assert (s["total_withheld"], s["net_amount"]) == ("0.00", "9850.00") and s["iss"]["amount"] is None
    assert d["validation"]["status"] != "VALID"  # edição invalidou a validação anterior
    v = _validate(ostack, real, d)
    assert v["valid"] is True, v["issues"]
    item, inf, doc = _emit(ostack, real, gov, d)
    assert (item["total_withheld"], item["net_amount"]) == ("0.00", "9850.00")
    for tag in ("vRetCP", "vRetIRRF", "vRetCSLL"):
        assert inf.find(f".//{NS}{tag}") is None
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}tpRetISSQN") == "1"
    assert _fed(inf, "piscofins/tpRetPisCofins") == "0" and _fed(inf, "piscofins/vPis") == "64.35"
    assert doc["net_amount"] == "9850.00" and doc["total_retained"] == "0.00"


def test_iss_retido_sem_aliquota_sefin_define_e_resumo_marca_pendente(ostack, real, gov):
    gov.embed_posted_dps = True  # convênio ATIVO: pAliq proibido (E0617); Sefin aplica a alíquota parametrizada (2,00)
    d = official_draft(ostack, real, amount="1000.00", tax_settings={
        "issqn_taxation": "TAXABLE", "iss_retention": "RETAINED_BY_TOMADOR", "approx_tax": APPROX, "inss_withheld": "110.00"})
    s = d["retention_summary"]
    assert (s["complete"], s["pending"], s["total_withheld"], s["net_amount"]) == (False, ["iss.amount"], "110.00", "890.00")
    v = _validate(ostack, real, d)
    assert v["valid"] is True and "ISS_RETAINED_AMOUNT_BY_SEFIN" in {i["code"] for i in v["issues"]}
    _item, _inf, doc = _emit(ostack, real, gov, d)
    # valor oficial: 110,00 + 20,00 de ISS; a plataforma não inventou o ISS antes da resposta
    assert (doc["total_retained"], doc["net_amount"], doc["retentions"]["iss"]) == ("130.00", "870.00", "20.00")


def test_validacao_bloqueia_liquido_negativo_e_combinacoes(ostack, real):
    d = official_draft(ostack, real, amount="100.00", unconditional_discount="60.00",
                       tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX,
                                     "inss_withheld": "50.00"})
    assert d["retention_summary"]["net_amount"] == "-10.00"
    errs = {i["code"] for i in _validate(ostack, real, d)["issues"] if i["severity"] == "error"}
    assert "NET_AMOUNT_NEGATIVE" in errs
    d2 = official_draft(ostack, real, tax_settings={
        "issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX,
        "pis_cofins_cst": "01", "pis_cofins_retention": "1"})
    issues = {i["code"]: i for i in _validate(ostack, real, d2)["issues"]}
    assert issues["CSLL_REQUIRED"]["rule"] == "E0724" and issues["CSLL_REQUIRED"]["severity"] == "error"
    d3 = official_draft(ostack, real, tax_settings={
        "issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX,
        "pis_cofins_cst": "01", "pis_cofins_retention": "0", "csll_withheld": "10.00"})
    assert {i["code"]: i["rule"] for i in _validate(ostack, real, d3)["issues"]}.get("CSLL_NOT_ALLOWED") == "E0720"
    r = ostack.fiscal.post(f"{F}/{real['id']}/drafts", headers=real["h"], json={
        "customer_id": real["customer"], "competence": "2026-10", "amount": "100.00",
        "service": {"national_code": "071601", "description": "x", "location_code": MUN},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "inss_withheld": "10.005"}})
    assert r.status_code == 422  # nunca arredonda em silêncio


def test_previa_canonica_do_backend_e_modalidade_1_nao_confirmada(ostack, real):
    url = f"{F}/{real['id']}/retention-preview"
    body = {"amount": "10000.00", "unconditional_discount": "100.00", "conditional_discount": "50.00",
            "tax_settings": MULTI}
    r = ostack.fiscal.post(url, headers=real["h"], json=body)
    assert r.status_code == 200, r.text
    assert (r.json()["retention_summary"]["total_withheld"], r.json()["retention_summary"]["net_amount"]) == ("1957.85", "7892.15")
    # o rascunho salvo com os mesmos valores devolve exatamente o mesmo resumo
    d = official_draft(ostack, real, amount="10000.00", unconditional_discount="100.00", conditional_discount="50.00",
                       tax_settings=MULTI)
    assert d["retention_summary"] == r.json()["retention_summary"]
    assert ostack.fiscal.post(url, headers=real["h"], json={**body, "amount": "10.005"}).status_code == 422
    assert ostack.fiscal.post(url, json=body).status_code == 401
    c1 = ostack.fiscal.post(url, headers=real["h"], json={"amount": "1000.00", "tax_settings": {
        "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01", "pis_cofins_retention": "1", "csll_withheld": "10.00",
        "pis_amount": "6.50", "cofins_amount": "30.00"}}).json()
    assert c1["retention_summary"]["complete"] is False and "federal.contributions_code_1" in c1["retention_summary"]["pending"]
    assert {i["code"] for i in c1["issues"]} >= {"CONTRIBUTIONS_CODE1_UNCONFIRMED"}
