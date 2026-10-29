"""Alíquota do ISS: referência interna (cálculo e conferência) × pAliq enviado na DPS, conforme a Parametrização.

Cobre: regras do Anexo I (E0600/E0602/E0604/E0617/E0619 e variantes do Simples), leitura das respostas oficiais
(formato confirmado em 07/10/2026), estimativa de ISS/retenção/líquido, salvar e reabrir, XML com e sem pAliq,
orientações da validação (sem "corrigir" o que não é erro), Parametrização indisponível e reconciliação com a NFS-e
devolvida pela "Sefin" simulada. Nenhuma chamada sai da máquina (FakeGov)."""
from __future__ import annotations

from datetime import datetime

import pytest
from lxml import etree

from conftest import SP
from test_official_integration import (  # noqa: F401  (fixtures)
    APPROX, F, MUN, NS, _snapshot, batch_items, gov, official_draft, ostack, real, schedule_now,
)

RET = {"issqn_taxation": "TAXABLE", "iss_retention": "RETAINED_BY_TOMADOR", "approx_tax": APPROX}
NAO_OPTANTE = {"simples_option": "1", "special_regime": "0"}


# ---------------------------------------------------------------- regras (Anexo I, linhas 502–513)
@pytest.mark.parametrize("perfil,tax,convenio,status,rule", [
    (NAO_OPTANTE, RET, True, "PROHIBITED", "E0617"),
    (NAO_OPTANTE, RET, False, "REQUIRED", "E0619"),
    (NAO_OPTANTE, RET, None, "UNKNOWN", None),
    ({"simples_option": "1", "special_regime": None}, RET, False, "UNKNOWN", None),
    ({"simples_option": "2", "special_regime": "0"}, RET, None, "PROHIBITED", "E0600"),
    (NAO_OPTANTE, {**RET, "issqn_taxation": "EXPORT"}, None, "PROHIBITED", "E0602"),
    ({"simples_option": "1", "special_regime": "3"}, RET, False, "PROHIBITED", "E0604"),
    ({"simples_option": "3", "simples_regime": "1", "special_regime": "0"}, RET, True, "REQUIRED", "E0621"),
    ({"simples_option": "3", "simples_regime": "1", "special_regime": "0"},
     {**RET, "iss_retention": "NOT_RETAINED"}, True, "PROHIBITED", "E0625"),
    ({"simples_option": "3", "simples_regime": "1", "special_regime": "0"}, RET, False, "REQUIRED", "E0628"),
    ({"simples_option": "3", "simples_regime": "1", "special_regime": "0"},
     {**RET, "iss_retention": "NOT_RETAINED"}, False, "PROHIBITED", "E0631"),
    ({"simples_option": "3", "simples_regime": "2", "special_regime": "0"}, RET, True, "PROHIBITED", "E0635"),
    ({"simples_option": "3", "simples_regime": "3", "special_regime": "0"}, RET, False, "REQUIRED", "E0640"),
    ({"simples_option": "3", "special_regime": "0"}, RET, True, "UNKNOWN", None),  # sem regApTribSN: não decide
])
def test_situacao_paliq_pelas_regras_oficiais(perfil, tax, convenio, status, rule):
    from ctrl_common.aliquota_iss import situacao_paliq

    st = situacao_paliq(perfil, tax, convenio)
    assert (st["status"], st["rule"]) == (status, rule)
    if convenio is None and status == "UNKNOWN" and perfil.get("simples_option") == "1":
        assert st["possible_rules"] == ["E0617", "E0619"]


def test_leitura_das_respostas_oficiais_da_parametrizacao():
    from ctrl_common.aliquota_iss import aliquota_parametrizada, codigo_servico_parametrizacao, convenio_ativo

    assert codigo_servico_parametrizacao("071601", None) == "07.16.01.000"
    assert codigo_servico_parametrizacao("070201", "001") == "07.02.01.001"
    assert codigo_servico_parametrizacao("7021", None) is None
    # Respostas reais de 07/10/2026 (produção), sem dados da empresa
    real_conv = {"parametrosConvenio": {"aderenteAmbienteNacional": 1, "aderenteEmissorNacional": 1},
                 "mensagem": "Parâmetros do convênio recuperados com sucesso."}
    assert convenio_ativo(200, real_conv) is True
    assert convenio_ativo(404, {"parametrosConvenio": None}) is False
    assert convenio_ativo(503, {}) is None  # indisponível nunca vira "inativo"
    real_aliq = {"aliquotas": {"07.16.01.000": [{"Incidencia": "SIM", "Aliq": 5.00, "DtIni": "2025-12-24T00:00:00",
                                                 "DtFim": None}]}, "mensagem": "Alíquotas recuperadas com sucesso."}
    a = aliquota_parametrizada(200, real_aliq, "07.16.01.000", "2026-10")
    assert (a["status"], a["rate"], a["valid_from"]) == ("FOUND", "5.00", "2025-12-24")
    assert aliquota_parametrizada(200, real_aliq, "07.16.01.000", "2025-11")["status"] == "NOT_FOUND"  # antes da vigência
    mal = {"aliquotas": None, "mensagem": "Chamada mal formada. O código do serviço deve ser composto por nove dígitos."}
    assert aliquota_parametrizada(400, mal, "071601", "2026-10")["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------- estimativa (retencoes.resumo)
def test_estimativa_usa_pAliq_senao_referencia_e_ausencia_nao_e_zero():
    from ctrl_fiscal.domain.retencoes import resumo

    ref = resumo("1000.00", None, None, None, {**RET, "iss_reference_rate": "5.00", "inss_withheld": "110.00"})
    esperado = {"rate": "5.00", "rate_source": "REFERENCE", "dps_rate": None, "reference_rate": "5.00",
                "estimated_amount": "50.00", "amount": "50.00", "amount_source": "ESTIMATED_FROM_REFERENCE"}
    assert {k: ref["iss"][k] for k in esperado} == esperado
    assert (ref["total_withheld"], ref["net_amount"], ref["complete"], ref["basis"]) == ("160.00", "840.00", True, "ESTIMATE")
    pa = resumo("1000.00", None, None, None, {**RET, "iss_rate": "2.00", "iss_reference_rate": "5.00"})
    assert (pa["iss"]["rate"], pa["iss"]["rate_source"], pa["iss"]["amount"]) == ("2.00", "DPS_PALIQ", "20.00")
    nada = resumo("1000.00", None, None, None, {**RET, "inss_withheld": "110.00"})
    assert nada["iss"]["amount"] is None and nada["iss"]["amount_source"] == "DETERMINED_BY_SEFIN"
    assert (nada["complete"], nada["pending"], nada["net_amount"]) == (False, ["iss.amount"], "890.00")
    nao_retido = resumo("1000.00", None, None, None, {**RET, "iss_retention": "NOT_RETAINED", "iss_reference_rate": "5.00"})
    assert (nao_retido["iss"]["estimated_amount"], nao_retido["iss"]["amount"], nao_retido["net_amount"]) == \
        ("50.00", None, "1000.00")


# ---------------------------------------------------------------- XML da DPS
def test_xml_da_dps_leva_so_o_pAliq_nunca_a_referencia():
    from nfse_api import dps

    agora = datetime(2026, 10, 20, tzinfo=SP)

    def tm(tax: dict):  # noqa: ANN202
        xml = dps.montar(_snapshot(tax_settings={**RET, **tax}), environment="restricted", serie="1", numero=1,
                         agora=agora).xml
        return etree.fromstring(xml).find(f"{NS}infDPS/{NS}valores/{NS}trib/{NS}tribMun"), xml

    sem, xml_sem = tm({"iss_reference_rate": "4.37"})
    assert sem.find(f"{NS}pAliq") is None and b"4.37" not in xml_sem
    com, _ = tm({"iss_rate": "2.00", "iss_reference_rate": "5.00"})
    assert com.findtext(f"{NS}pAliq") == "2.00"


# ---------------------------------------------------------------- salvar, reabrir e validar (convênio ATIVO)
def _validate(ostack, real, d) -> dict:  # noqa: ANN001
    return ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()


def test_referencia_salva_reabre_e_orienta_conforme_parametrizacao(ostack, real, gov):
    gov.aliquota_parametrizada = "5.00"
    d = official_draft(ostack, real, amount="1000.00",
                       tax_settings={**RET, "iss_reference_rate": "5.00", "inss_withheld": "110.00"})
    reaberto = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()
    assert reaberto["tax_settings"]["iss_reference_rate"] == "5.00" and reaberto["tax_settings"]["iss_rate"] is None
    s = reaberto["retention_summary"]
    assert (s["iss"]["amount"], s["iss"]["amount_source"], s["total_withheld"], s["net_amount"]) == \
        ("50.00", "ESTIMATED_FROM_REFERENCE", "160.00", "840.00")
    v = _validate(ostack, real, d)
    assert v["valid"] is True, v["issues"]
    info = next(i for i in v["issues"] if i["code"] == "ISS_RATE_SET_BY_MUNICIPALITY")
    assert info["severity"] == "info" and info["rule"] == "E0617" and info["action"] is None
    assert "determinada pelo município" in info["message"] and "5,00%" in info["message"]
    det = info["details"]
    assert (det["paliq_status"], det["convenio"], det["municipal_rate"], det["service_code"], det["reference_rate"]) == \
        ("PROHIBITED", "ACTIVE", "5.00", "07.16.01.001", "5.00")
    assert not [i for i in v["issues"] if i["severity"] != "info" and i["field"].startswith("tax_settings.iss")]
    calls = [c[3] for c in gov.calls if c[0] == "param"]
    assert f"/parametrizacao/{MUN}/convenio" in calls
    assert f"/parametrizacao/{MUN}/07.16.01.001/2026-10-01T00:00:00/aliquota" in calls


def test_referencia_diferente_da_parametrizada_avisa_sem_mandar_corrigir(ostack, real, gov):
    gov.aliquota_parametrizada = "2.00"
    d = official_draft(ostack, real, tax_settings={**RET, "iss_reference_rate": "5.00"})
    v = _validate(ostack, real, d)
    aviso = next(i for i in v["issues"] if i["code"] == "ISS_REFERENCE_DIFFERS_FROM_MUNICIPAL")
    assert aviso["severity"] == "warning" and "2,00%" in aviso["message"] and "Confira" in aviso["action"]
    assert v["valid"] is True
    # avisos genéricos de campo do rascunho pedem para CONFERIR, não para corrigir
    for i in v["issues"]:
        if i["severity"] != "error" and i.get("action"):
            assert not i["action"].startswith("Corrija"), i


def test_parametrizacao_indisponivel_informa_incerteza_sem_presumir(ostack, real, gov):
    gov.param_indisponivel = True
    d = official_draft(ostack, real, tax_settings={**RET, "iss_reference_rate": "5.00"})
    v = _validate(ostack, real, d)
    w = next(i for i in v["issues"] if i["code"] == "PALIQ_PARAMETRIZACAO_UNAVAILABLE")
    assert w["severity"] == "warning" and "Não foi possível confirmar" in w["message"]
    assert "E0617" in w["message"] and "E0619" in w["message"] and "estimativas" in w["message"]
    assert w["details"]["convenio"] == "UNAVAILABLE" and w["details"]["paliq_status"] == "UNKNOWN"
    assert "ISS_RATE_SET_BY_MUNICIPALITY" not in {i["code"] for i in v["issues"]}  # nada é presumido


# ---------------------------------------------------------------- emissão e reconciliação com o retorno oficial
def _emitir(ostack, real, gov, d) -> tuple[dict, etree._Element, dict]:  # noqa: ANN001
    b = schedule_now(ostack, real, [d])
    ostack.worker().run_emissions()
    item = batch_items(ostack, real, b["batch_id"])[0]
    assert item["status"] == "AUTHORIZED", item
    inf = etree.fromstring(gov.posted_dps[-1]).find(f"{NS}infDPS")
    doc = ostack.fiscal.get(f"{F}/{real['id']}/documents/{item['document_id']}", headers=real["h"]).json()
    return item, inf, doc


def test_reconciliacao_estimado_x_oficial_sem_pAliq(ostack, real, gov):
    gov.embed_posted_dps = True  # a "Sefin" calcula vISSQN/vTotalRet/vLiq com a alíquota parametrizada (sem pAliq)
    gov.aliquota_parametrizada = "2.00"
    d = official_draft(ostack, real, amount="1000.00",
                       tax_settings={**RET, "iss_reference_rate": "5.00", "inss_withheld": "110.00"})
    item, inf, doc = _emitir(ostack, real, gov, d)
    assert inf.find(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}pAliq") is None
    assert (item["total_withheld"], item["net_amount"]) == ("160.00", "840.00")  # estimativa congelada (referência 5%)
    assert (doc["total_retained"], doc["net_amount"], doc["retentions"]["iss"]) == ("130.00", "870.00", "20.00")
    rec = doc["iss_reconciliation"]
    linhas = {x["key"]: x for x in rec["lines"]}
    assert rec["status"] == "DIFFERENT" and rec["dps_rate"] is None and rec["reference_rate"] == "5.00"
    assert (linhas["iss_rate"]["estimated"], linhas["iss_rate"]["official"], linhas["iss_rate"]["status"]) == \
        ("5.00", "2.00", "DIFFERENT")
    assert (linhas["net_amount"]["estimated"], linhas["net_amount"]["official"], linhas["net_amount"]["difference"]) == \
        ("840.00", "870.00", "30.00")
    assert "determinada pelo município" in rec["note"]

    gov.aliquota_parametrizada = "5.00"
    d2 = official_draft(ostack, real, amount="1000.00",
                        tax_settings={**RET, "iss_reference_rate": "5.00", "inss_withheld": "110.00"})
    _, _, doc2 = _emitir(ostack, real, gov, d2)
    assert doc2["iss_reconciliation"]["status"] == "MATCH", doc2["iss_reconciliation"]


def test_reconciliacao_com_pAliq_enviado(ostack, real, gov):
    from core_api import parametrizacao_iss

    parametrizacao_iss._cache.clear()
    gov.embed_posted_dps = True
    gov.convenio_inativo.add(MUN)  # pAliq obrigatório (E0619)
    d = official_draft(ostack, real, amount="1000.00", tax_settings={**RET, "iss_rate": "3.00"})
    v = _validate(ostack, real, d)
    assert "PALIQ_WILL_BE_SENT" in {i["code"] for i in v["issues"]} and v["valid"] is True, v["issues"]
    _, inf, doc = _emitir(ostack, real, gov, d)
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}pAliq") == "3.00"
    rec = doc["iss_reconciliation"]
    assert rec["status"] == "MATCH" and rec["dps_rate"] == "3.00" and rec["rate_source"] == "DPS_PALIQ"


def test_pAliq_obrigatorio_sem_pAliq_bloqueia_com_orientacao(ostack, real, gov):
    from core_api import parametrizacao_iss

    parametrizacao_iss._cache.clear()
    gov.convenio_inativo.add(MUN)
    d = official_draft(ostack, real, tax_settings={**RET, "iss_reference_rate": "4.00"})
    v = _validate(ostack, real, d)
    e = next(i for i in v["issues"] if i["code"] == "PALIQ_REQUIRED_BY_CONVENIO")
    assert e["severity"] == "error" and e["rule"] == "E0619" and "NÃO ativo" in e["message"]
    assert "Enviar a referência como pAliq" in e["action"] and "4,00%" in e["action"]
    # nada é convertido sozinho: a referência continua só referência
    reaberto = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()
    assert reaberto["tax_settings"]["iss_rate"] is None and reaberto["tax_settings"]["iss_reference_rate"] == "4.00"


# ---------------------------------------------------------------- cache da Parametrização (core-api)
def test_cache_da_parametrizacao_isolado_por_contexto():
    from datetime import timedelta, timezone

    from core_api import parametrizacao_iss as pi

    class Http:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def get(self, path, headers=None, params=None):  # noqa: ANN001, ANN201
            import httpx

            self.calls.append((path, dict(params or {})))
            if path.endswith("/aliquota"):
                body = {"http_status": 200, "body": {"aliquotas": {params["servico"]: [
                    {"Aliq": 5.0, "DtIni": "2025-01-01T00:00:00", "DtFim": None}]}}}
            else:
                ativo = 1 if params["environment"] == "production" else 0
                body = {"http_status": 200, "body": {"parametrosConvenio": {"aderenteAmbienteNacional": ativo}}}
            return httpx.Response(200, json=body)

    pi._cache.clear()
    http, t0 = Http(), datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    base = dict(company_id="A", cnpj="11111111000111", environment="production", municipio=MUN,
                servico="07.16.01.000", competencia="2026-10")
    r1 = pi.consultar(http, "k", **base, now=t0)
    r1["convenio"]["status"] = "ADULTERADO"  # quem recebe não altera o cache
    r2 = pi.consultar(http, "k", **base, now=t0 + timedelta(minutes=5))
    assert len(http.calls) == 2 and r2["convenio"]["status"] == "ACTIVE"
    # Qualquer parâmetro diferente é outro contexto: nova consulta oficial, sem reaproveitar resultado.
    variantes = [{"company_id": "B"}, {"cnpj": "22222222000122"}, {"environment": "restricted"},
                 {"municipio": "1501402"}, {"servico": "07.02.01.000"}, {"competencia": "2026-11"}]
    for v in variantes:
        pi.consultar(http, "k", **{**base, **v}, now=t0 + timedelta(minutes=5))
    assert len(http.calls) == 2 + 2 * len(variantes)
    restrita = pi.consultar(http, "k", **{**base, "environment": "restricted"}, now=t0 + timedelta(minutes=6))
    assert restrita["convenio"]["status"] == "INACTIVE" and len(http.calls) == 14  # do cache do próprio ambiente
    pi.consultar(http, "k", **base, now=t0 + timedelta(minutes=11))  # vencido: consulta de novo
    assert len(http.calls) == 16
    pi._cache.clear()


def test_ausencia_e_zero_sao_diferentes_ao_salvar_e_reabrir(ostack, real, gov):
    gov.aliquota_parametrizada = "5.00"
    zero = official_draft(ostack, real, amount="1000.00", tax_settings={**RET, "iss_reference_rate": "0"})
    ausente = official_draft(ostack, real, amount="1000.00", tax_settings={**RET})
    rz = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{zero['id']}", headers=real["h"]).json()
    ra = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{ausente['id']}", headers=real["h"]).json()
    assert rz["tax_settings"]["iss_reference_rate"] == "0" and ra["tax_settings"]["iss_reference_rate"] is None
    sz, sa = rz["retention_summary"], ra["retention_summary"]
    assert (sz["iss"]["amount"], sz["iss"]["amount_source"], sz["complete"]) == ("0.00", "ESTIMATED_FROM_REFERENCE", True)
    assert (sa["iss"]["amount"], sa["iss"]["amount_source"], sa["complete"]) == (None, "DETERMINED_BY_SEFIN", False)
    # edição explícita remove a referência (null) sem mexer no pAliq
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{zero['id']}", headers=real["h"],
                            json={"expected_version": rz["version"], "tax_settings": {**RET, "iss_reference_rate": None}})
    assert r.status_code == 200 and r.json()["tax_settings"]["iss_reference_rate"] is None
    assert r.json()["tax_settings"]["iss_rate"] is None
