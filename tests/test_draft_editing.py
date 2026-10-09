"""Edição completa do rascunho: semântica de remoção (null), conflito de versão, invalidação da validação,
bloqueio por estado, retirada segura da fila, corridas com agendamento/worker e XML gerado após a alteração.
Também: domínios oficiais e combinações do Anexo I. Governo simulado (sem rede)."""
from __future__ import annotations

from datetime import timedelta

from lxml import etree

from test_official_integration import (  # noqa: F401  (fixtures)
    APPROX, C, F, MUN, NS, batch_items, gov, official_draft, ostack, real, schedule_now,
)


def _patch(ostack, real, d, **body):  # noqa: ANN001, ANN003, ANN202
    return ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"],
                               json={"expected_version": d["version"], **body})


def _validate(ostack, real, d) -> dict:  # noqa: ANN001
    return ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()


def _get(ostack, real, d) -> dict:  # noqa: ANN001
    return ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()


def test_full_edit_remove_groups_and_validation_invalidated(ostack, real):
    d = official_draft(ostack, real)
    v = _validate(ostack, real, d)
    assert v["valid"] is True and _get(ostack, real, d)["validation"]["status"] == "VALID"
    # Edição de todos os grupos fiscais do rascunho
    r = _patch(ostack, real, d, service_date="2026-10-02", amount="2000.00", deductions="100.00",
               unconditional_discount="50.00", conditional_discount="20.00",
               service={"national_code": "070201", "municipal_code": "002", "description": "Obra (editada)",
                        "nbs_code": "101032000", "location_code": MUN,
                        "obra_address": {"postal_code": "68500000", "street": "Rua X", "number": "1", "district": "Centro"}},
               tax_settings={"iss_retention": "RETAINED_BY_TOMADOR", "pis_cofins_cst": "01", "pis_cofins_retention": "1",
                             "csll_withheld": "10.00", "inss_withheld": "220.00",
                             "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001", "personal_use": "0"}})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["validation"]["status"] == "NOT_VALIDATED" and d["issues"] == []  # validação anterior descartada
    assert d["service"]["obra_address"]["street"] == "Rua X" and d["unconditional_discount"] == "50.00"
    assert d["tax_settings"]["ibs_cbs"]["operation_indicator"] == "100301" and d["deductions"] == "100.00"
    assert _validate(ostack, real, d)["valid"] is True
    # Remoção explícita: null remove campos/grupos opcionais; obra removida exige trocar o subitem (E0372/E0370)
    r = _patch(ostack, real, _get(ostack, real, d), service_date=None, unconditional_discount=None, deductions=None,
               service={"national_code": "071601", "obra_address": None, "municipal_code": None},
               tax_settings={"ibs_cbs": None, "pis_cofins_cst": None, "pis_cofins_retention": None, "csll_withheld": None})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["service_date"] is None and d["unconditional_discount"] is None and d["deductions"] == "0.00"
    assert d["service"]["obra_address"] is None and d["service"]["municipal_code"] is None
    assert d["tax_settings"]["ibs_cbs"] is None and d["tax_settings"]["pis_cofins_cst"] is None
    assert d["conditional_discount"] == "20.00"  # ausente = mantido
    v = _validate(ostack, real, d)
    assert "SERVICE_DATE_REQUIRED" in {i["code"] for i in v["issues"]}  # removido no backend de fato
    # Obrigatórios não aceitam null
    for body in ({"competence": None}, {"amount": None}, {"customer_id": None}, {"service": None},
                 {"tax_settings": None}, {"service": {"description": None}}):
        assert _patch(ostack, real, _get(ostack, real, d), **body).status_code == 422, body
    # Emitente (tenant/CNPJ) não é campo do rascunho
    assert _patch(ostack, real, _get(ostack, real, d), company_cnpj="00000000000000").status_code == 422


def test_version_conflict_and_customer_refresh(ostack, real):
    d = official_draft(ostack, real)
    assert _patch(ostack, real, d, amount="1600.00").status_code == 200
    r = _patch(ostack, real, d, amount="1700.00")  # mesma versão antiga
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    assert _get(ostack, real, d)["amount"] == "1600.00"
    # Tomador editado no core: recarregado no rascunho ao informar o customer_id
    ostack.core.patch(f"{C}/{real['id']}/customers/{real['customer']}", headers=real["h"], json={"name": "Tomador Renomeado"})
    cur = _get(ostack, real, d)
    r = _patch(ostack, real, cur, customer_id=real["customer"])
    assert r.status_code == 200 and r.json()["customer"]["name"] == "Tomador Renomeado"


def test_scheduled_draft_withdraw_edit_reschedule_and_xml(ostack, real, gov):
    d = official_draft(ostack, real)
    at = ostack.clock.now() + timedelta(hours=2)
    b = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "b1"},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                 "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"}).json()
    cur = _get(ostack, real, d)
    assert cur["status"] == "SCHEDULED" and cur["editable"] is False and cur["validation"]["status"] == "VALID"
    r = _patch(ostack, real, cur, amount="999.00")
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_LOCKED"
    w = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/withdraw", headers=real["h"], json={"expected_version": 1})
    assert w.status_code == 409 and w.json()["code"] == "VERSION_CONFLICT"
    w = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/withdraw", headers=real["h"],
                           json={"expected_version": cur["version"]})
    assert w.status_code == 200 and w.json()["status"] == "DRAFT" and w.json()["editable"] is True
    assert batch_items(ostack, real, b["batch_id"])[0]["status"] == "WITHDRAWN"
    # Edição após retirada; novo agendamento valida o conteúdo ATUAL e gera DPS com os dados novos.
    r = _patch(ostack, real, w.json(), unconditional_discount="30.00",
               service={"description": "Descrição EDITADA após retirada"},
               tax_settings={"issqn_taxation": "IMMUNE", "immunity_type": "3", "iss_retention": "NOT_RETAINED", "iss_rate": None})
    assert r.status_code == 200, r.text
    d2 = r.json()
    b2 = schedule_now(ostack, real, [d2])
    ostack.worker().run_emissions()
    assert batch_items(ostack, real, b2["batch_id"])[0]["status"] == "AUTHORIZED"
    assert len(gov.posts()) == 1  # o item retirado nunca foi transmitido
    inf = etree.fromstring(gov.posted_dps[0]).find(f"{NS}infDPS")
    assert inf.findtext(f"{NS}serv/{NS}cServ/{NS}xDescServ") == "Descrição EDITADA após retirada"
    assert inf.findtext(f"{NS}valores/{NS}vDescCondIncond/{NS}vDescIncond") == "30.00"
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}tribISSQN") == "2"
    assert inf.findtext(f"{NS}valores/{NS}trib/{NS}tribMun/{NS}tpImunidade") == "3"
    assert [c.tag.replace(NS, "") for c in inf.find(f"{NS}valores")][:2] == ["vServPrest", "vDescCondIncond"]
    after = _get(ostack, real, d2)
    assert after["status"] == "AUTHORIZED" and after["editable"] is False
    assert _patch(ostack, real, after, amount="1.00").status_code == 409


def test_withdraw_refused_while_leased_or_uncertain(ostack, real, gov):
    from ctrl_fiscal.domain import emission

    d = official_draft(ostack, real)
    schedule_now(ostack, real, [d])
    w = ostack.worker()
    leased = emission.acquire_items(w.sessions, ostack.clock, "w1", limit=5, lease_seconds=60, max_in_flight=10, series="1")
    cur = _get(ostack, real, d)
    assert cur["status"] == "PROCESSING"
    r = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/withdraw", headers=real["h"],
                           json={"expected_version": cur["version"]})
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_WITHDRAWABLE"
    gov.sefin_plan = ["500_after_accept"]
    emission.process_item(w.sessions, w.nfse, ostack.clock, "w1", leased[0])
    cur = _get(ostack, real, d)
    assert cur["status"] == "AWAITING_CONFIRMATION"  # incerto: nem edição nem retirada
    assert ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/withdraw", headers=real["h"],
                              json={"expected_version": cur["version"]}).status_code == 409
    assert _patch(ostack, real, cur, amount="1.00").status_code == 409


def test_race_schedule_after_edit_uses_version(ostack, real):
    d = official_draft(ostack, real)
    _patch(ostack, real, d, amount="1800.00")  # outra pessoa edita
    r = ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "race"},
                           json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                 "scheduled_at": (ostack.clock.now() + timedelta(hours=1)).isoformat(),
                                 "timezone": "America/Sao_Paulo"})
    assert r.status_code == 409  # agendamento com a versão revisada antiga é recusado


def test_withdraw_then_worker_finds_nothing(ostack, real, gov):
    from ctrl_fiscal.domain import emission

    d = official_draft(ostack, real)
    at = ostack.clock.now() + timedelta(minutes=1)
    ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": "wd"},
                       json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                             "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"})
    cur = _get(ostack, real, d)
    assert ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/withdraw", headers=real["h"],
                              json={"expected_version": cur["version"]}).status_code == 200
    ostack.clock.advance(minutes=5)
    w = ostack.worker()
    assert emission.acquire_items(w.sessions, ostack.clock, "w1", limit=5, lease_seconds=60, max_in_flight=10, series="1") == []
    assert gov.posts() == []


def _codes(ostack, real, **over) -> set[str]:  # noqa: ANN003
    d = official_draft(ostack, real, **over)
    return {i["code"] for i in _validate(ostack, real, d)["issues"] if i["severity"] == "error"}


def test_domain_and_combination_rules(ostack, real):
    base_tax = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX}
    svc = {"national_code": "071601", "description": "x", "location_code": MUN}
    assert "NATIONAL_CODE_NOT_IN_LIST" in _codes(ostack, real, service={**svc, "national_code": "999999"})
    assert "NBS_NOT_IN_LIST" in _codes(ostack, real, service={**svc, "nbs_code": "999999999"})
    assert "LOCATION_NOT_IN_IBGE" in _codes(ostack, real, service={**svc, "location_code": "1234567"})
    assert "MUNICIPAL_CODE_000" in _codes(ostack, real, service={**svc, "municipal_code": "000"})
    assert "OBRA_REQUIRED" in _codes(ostack, real, service={**svc, "national_code": "070201"})
    assert "OBRA_NOT_ALLOWED" in _codes(ostack, real, service={**svc, "obra_code": "123"})
    assert "APPROX_MODE_NOT_ALLOWED" in _codes(ostack, real, tax_settings={**base_tax, "approx_tax": {"mode": "NOT_INFORMED"}})
    assert {"RETENTION_NOT_ALLOWED_TAXATION", "IMMUNITY_TYPE_REQUIRED"} <= _codes(
        ostack, real, tax_settings={**base_tax, "issqn_taxation": "IMMUNE", "iss_retention": "RETAINED_BY_TOMADOR"})
    assert "EXPORT_NOT_SUPPORTED" in _codes(ostack, real, tax_settings={**base_tax, "issqn_taxation": "EXPORT"})
    assert "INTERMEDIARY_NOT_SUPPORTED" in _codes(ostack, real, tax_settings={**base_tax, "iss_retention": "RETAINED_BY_INTERMEDIARY"})
    assert "DISCOUNT_RANGE" in _codes(ostack, real, unconditional_discount="1500.00")
    assert "CSLL_REQUIRED" in _codes(ostack, real, tax_settings={**base_tax, "pis_cofins_cst": "01", "pis_cofins_retention": "3"})
    assert "PIS_COFINS_CALC_MISMATCH" in _codes(ostack, real, tax_settings={
        **base_tax, "pis_cofins_cst": "01", "pis_cofins_base": "1500.00", "pis_rate": "0.65", "pis_amount": "1.00"})
    assert "CLASS_TRIB_CST_MISMATCH" in _codes(ostack, real, service={**svc, "nbs_code": "101032000"}, tax_settings={
        **base_tax, "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "200001"}})
    assert "CINDOP_NOT_IN_LIST" in _codes(ostack, real, service={**svc, "nbs_code": "101032000"}, tax_settings={
        **base_tax, "ibs_cbs": {"operation_indicator": "999999", "cst": "000", "class_code": "000001"}})
    assert "SERVICE_DATE_AFTER_EMISSION" in _codes(ostack, real, competence="2026-10", service_date="2026-10-31")
    # Combinação válida não acusa nada
    assert _codes(ostack, real) == set()
