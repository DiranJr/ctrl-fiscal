"""Tributos aproximados (totTrib, Lei 12.741/2012) pelo contrato existente: VALUES = três valores em R$ (vTotTrib),
PERCENT = três percentuais (pTotTrib) em approx_tax.federal/state/municipal. Zero explícito preservado, campo vazio
nunca vira zero e XML com cada número na sua tag. Sem alteração de backend. Somente dados fictícios."""
from __future__ import annotations

from datetime import datetime

from lxml import etree

from test_emission_queue import make_draft
from test_official_integration import NS, SP, _snapshot

F = "/api/fiscal/v1/companies"


def _gravar(stack, world, d: dict, approx: dict) -> dict:  # noqa: ANN001
    r = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                           json={"expected_version": d["version"], "tax_settings": {"approx_tax": approx}})
    assert r.status_code == 200, r.text
    return r.json()


def _ler(stack, world, d: dict) -> dict:  # noqa: ANN001
    return stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()["tax_settings"]["approx_tax"]


def test_tres_percentuais_salvos_e_reabertos_com_zero_preservado(stack, world):
    d = _gravar(stack, world, make_draft(stack, world), {"mode": "PERCENT", "federal": "5.25", "state": "0.00",
                                                         "municipal": "2.00"})
    ap = _ler(stack, world, d)
    assert ap["mode"] == "PERCENT" and (ap["federal"], ap["state"], ap["municipal"]) == ("5.25", "0.00", "2.00")


def test_campo_vazio_nao_vira_zero(stack, world):
    d = _gravar(stack, world, make_draft(stack, world), {"mode": "PERCENT", "federal": "5.25", "state": "0.00"})
    ap = _ler(stack, world, d)
    assert ap["state"] == "0.00" and ap.get("municipal") is None


def test_valores_em_reais_preservados(stack, world):
    d = _gravar(stack, world, make_draft(stack, world), {"mode": "VALUES", "federal": "10.50", "state": "0.00",
                                                         "municipal": "3.00"})
    ap = _ler(stack, world, d)
    assert ap["mode"] == "VALUES" and (ap["federal"], ap["state"], ap["municipal"]) == ("10.50", "0.00", "3.00")


def test_xml_percentuais_e_valores_nas_tags_corretas():
    from nfse_api import dps

    def tot(approx: dict):  # noqa: ANN202
        snap = _snapshot(tax_settings={"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": approx})
        m = dps.montar(snap, environment="restricted", serie="1", numero=9, agora=datetime(2026, 10, 20, 13, 0, tzinfo=SP))
        return etree.fromstring(m.xml).find(f"{NS}infDPS/{NS}valores/{NS}trib/{NS}totTrib")

    p = tot({"mode": "PERCENT", "federal": "5.25", "state": "0.00", "municipal": "2.00"})
    assert [p.findtext(f"{NS}pTotTrib/{NS}{t}") for t in ("pTotTribFed", "pTotTribEst", "pTotTribMun")] == ["5.25", "0.00", "2.00"]
    assert p.find(f"{NS}vTotTrib") is None
    v = tot({"mode": "VALUES", "federal": "10.50", "state": "0.00", "municipal": "3.00"})
    assert [v.findtext(f"{NS}vTotTrib/{NS}{t}") for t in ("vTotTribFed", "vTotTribEst", "vTotTribMun")] == ["10.50", "0.00", "3.00"]
    assert v.find(f"{NS}pTotTrib") is None
