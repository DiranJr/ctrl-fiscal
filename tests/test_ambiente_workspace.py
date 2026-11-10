"""Troca do ambiente do workspace e da leitura ADN pelo administrador: opções = o que a instalação permite (pendência
global × do workspace), confirmação conferida no servidor, auditoria, nada muda em outros workspaces, certificado e
dados preservados e separados por ambiente, agendamentos/pedidos do ambiente anterior tratados sem transmitir nada e
sem habilitar emissão ou cancelamento. Instalação como a relatada: Produção Restrita DESLIGADA, leitura ADN de Produção
ligada, emissão e cancelamento em Produção desligados. Somente dados fictícios; o governo é simulado (FakeGov)."""
from __future__ import annotations

import base64
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import AuthHeaders, Stack, _truncate, cnpj
from test_nfse_connector import TEST_TRUST
from test_official_integration import C, F, MUN, PFX_PASSWORD, FakeGov, _pfx, official_draft

ENV = "/environment"


@pytest.fixture
def gov():
    return FakeGov()


@pytest.fixture
def vps(db_urls, jwt_keys, clock, tmp_path, gov):
    for url in db_urls.values():
        _truncate(url)
    return Stack(db_urls, jwt_keys, clock, tmp_path, official={
        "vault_key_b64": base64.b64encode(os.urandom(32)).decode(), "restricted_enabled": False,
        "production_distribution_enabled": True, "http_factory": gov.factory, **TEST_TRUST})


def _workspace(st, nome: str, doc: str, dono: str, adn: list[str]) -> dict:  # noqa: ANN001
    cid = st.add_company(nome, doc, environment="restricted", municipality=MUN)
    uid = st.add_user(dono)
    st.grant(uid, cid)
    h = AuthHeaders(st, dono)
    r = st.core.patch(f"{C}/{cid}/fiscal-profile", headers=h, json={
        "simples_option": "1", "special_regime": "0", "adn_environments": adn, "source": "teste automatizado"})
    assert r.status_code == 200, r.text
    r = st.core.post(f"{C}/{cid}/certificate", headers=h, files={"file": ("e.pfx", _pfx(doc), "application/x-pkcs12")},
                     data={"password": PFX_PASSWORD})
    assert r.status_code == 201, r.text
    return {"id": cid, "cnpj": doc, "h": h, "user": uid}


@pytest.fixture
def ws(vps):
    a = _workspace(vps, "Filial Fictícia Norte", cnpj("123456780001"), "dono@ex.com", ["restricted"])
    b = _workspace(vps, "Filial Fictícia Sul", cnpj("223456780001"), "dono.b@ex.com", ["restricted"])
    return {"a": a, "b": b}


def _troca(st, w: dict, env: str = "production", adn: bool = True, **over):  # noqa: ANN001, ANN202
    conf = {"company_id": w["id"], "cnpj": w["cnpj"], "from_environment": "restricted", "to_environment": env, **over}
    return st.core.post(f"{C}/{w['id']}{ENV}", headers=w["h"], json={"environment": env, "adn_read": adn,
                                                                     "confirmation": conf})


def _empresa(st, cid: str) -> dict:  # noqa: ANN001
    from core_api.models import Company, Workspace

    with st.core_sessions() as s:
        c, w = s.get(Company, cid), s.scalar(select(Workspace).where(Workspace.company_id == cid))
        return {"env": c.nfse_environment, "adn": sorted(c.adn_environments or []), "name": c.name, "cnpj": c.cnpj,
                "ws": (w.status, w.name), "serie": c.dps_series}


def test_opcoes_separam_pendencia_global_e_do_workspace(vps, ws):
    r = vps.core.get(f"{C}/{ws['a']['id']}{ENV}", headers=ws["a"]["h"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["environment"] == "restricted" and j["adn_environments"] == ["restricted"]
    op = {o["environment"]: o for o in j["options"]}
    assert op["restricted"]["available"] is False and op["restricted"]["read_available"] is False
    assert [i["code"] for i in op["restricted"]["global_issues"]] == ["RESTRICTED_DISABLED"]
    assert "NFSE_RESTRICTED_ENABLED" in op["restricted"]["global_issues"][0]["message"]
    assert op["restricted"]["company_issues"] == []  # certificado válido: não é pendência do workspace
    assert op["production"]["available"] is True and op["production"]["read_available"] is True
    assert op["production"]["installation"] == {"distribution": True, "emission": False, "cancellation": False}
    assert op["production"]["certificate"]["present"] is True and op["production"]["certificate"]["subject_cnpj_matches"]


def test_troca_para_producao_com_leitura_adn_confirmada_e_auditada(vps, ws):
    from core_api.models import AuditEvent

    a, b = ws["a"], ws["b"]
    antes_b = _empresa(vps, b["id"])
    certs = vps.core.get(f"{C}/{a['id']}/certificates", headers=a["h"]).json()
    # Confirmação divergente (outro CNPJ, origem errada): nada muda.
    assert _troca(vps, a, cnpj=b["cnpj"]).status_code == 409
    r = _troca(vps, a, from_environment="production")
    assert r.status_code == 409 and r.json()["code"] == "ENVIRONMENT_CONFIRMATION_MISMATCH"
    # Destino desabilitado na instalação: recusado com o motivo global.
    r = _troca(vps, a, env="restricted")
    assert r.status_code == 409 and r.json()["code"] == "ENVIRONMENT_UNAVAILABLE"
    assert r.json()["errors"][0]["code"] == "RESTRICTED_DISABLED"
    assert _empresa(vps, a["id"])["env"] == "restricted"
    r = _troca(vps, a)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["changed"] is True and j["environment"] == "production" and j["adn_environments"] == ["production"]
    assert j["previous"] == {"environment": "restricted", "adn_environments": ["restricted"]}
    depois = _empresa(vps, a["id"])
    assert depois["env"] == "production" and depois["adn"] == ["production"] and depois["ws"][0] == "ACTIVE"
    assert _empresa(vps, b["id"]) == antes_b  # outro workspace intacto
    assert vps.core.get(f"{C}/{a['id']}/certificates", headers=a["h"]).json() == certs  # certificado não reinstalado
    # Emissão e cancelamento continuam desligados (instalação) — a troca não habilita nada.
    rd = vps.core.get(f"{C}/{a['id']}/readiness", headers=a["h"]).json()
    prod = {e["environment"]: e for e in rd["environments"]}["production"]
    ops = {o["operation"]: o for o in prod["operations"]}
    assert ops["consulta"]["ready"] is True
    assert ops["emissao"]["enabled"] is False and ops["cancelamento"]["enabled"] is False
    with vps.core_sessions() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "company.environment_changed")).all()
    assert len(ev) == 1 and ev[0].company_id == a["id"] and ev[0].actor_id == a["user"]
    assert ev[0].details["from"] == {"environment": "restricted", "adn_environments": ["restricted"]}
    assert ev[0].details["to"] == {"environment": "production", "adn_environments": ["production"]}
    assert ev[0].details["emission_enabled_by_change"] is False
    # Repetir com a mesma confirmação (origem já mudou) é recusado; nada é auditado de novo.
    assert _troca(vps, a).status_code == 409


def test_autorizacao_no_servidor(vps, ws):
    a, b = ws["a"], ws["b"]
    sem_admin = vps.add_user("leitor@ex.com")
    vps.grant(sem_admin, a["id"], perms=("view", "sync", "prepare"))
    h = AuthHeaders(vps, "leitor@ex.com")
    assert vps.core.get(f"{C}/{a['id']}{ENV}", headers=h).status_code == 403
    assert _troca(vps, {**a, "h": h}).status_code == 403
    assert _troca(vps, {**a, "h": b["h"]}).status_code == 403  # admin de outro workspace
    assert vps.core.post(f"{C}/{a['id']}{ENV}", json={}).status_code == 401
    assert vps.fiscal.get(f"{F}/{a['id']}/environment-impact", headers=h).status_code == 403
    assert _empresa(vps, a["id"])["env"] == "restricted"


def test_agendamento_e_sincronizacao_do_ambiente_anterior(vps, ws, gov):
    from ctrl_fiscal.models import BatchItem, Draft, Job, JobError, SyncSchedule

    a = ws["a"]
    cust = vps.core.post(f"{C}/{a['id']}/customers", headers=a["h"], json={
        "name": "Tomador Fictício", "document_type": "CNPJ", "document": cnpj("445556660001"),
        "municipality_code": MUN, "postal_code": "68500000",
        "address": {"street": "Rua A", "number": "10", "district": "Centro", "city": "Marabá", "state": "PA"}})
    assert cust.status_code == 201, cust.text
    d = official_draft(vps, {**a, "customer": cust.json()["id"]})
    at = vps.clock.now() + timedelta(hours=2)
    r = vps.fiscal.post(f"{F}/{a['id']}/batches", headers={**a["h"], "Idempotency-Key": uuid.uuid4().hex},
                        json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                              "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202, r.text
    w = vps.worker()
    w.refresh_companies(force=True)
    with vps.fiscal_sessions() as s:
        ag = s.scalar(select(SyncSchedule).where(SyncSchedule.company_id == a["id"], SyncSchedule.environment == "restricted"))
        ag.cursor_nsu = 41  # cursor já avançado (fictício): precisa ficar guardado
        s.commit()
    pedido = vps.fiscal.post(f"{F}/{a['id']}/sync/run", headers={**a["h"], "Idempotency-Key": uuid.uuid4().hex})
    assert pedido.status_code == 202, pedido.text
    imp = vps.fiscal.get(f"{F}/{a['id']}/environment-impact", headers=a["h"], params={"target": "production"}).json()
    linha = {e["environment"]: e for e in imp["environments"]}
    assert linha["restricted"]["current"] and linha["restricted"]["drafts"] == 1
    assert linha["restricted"]["scheduled_not_started"] == 1 and linha["restricted"]["active_jobs"] == 1
    assert linha["restricted"]["sync"]["cursor_nsu"] == 41 and linha["production"]["target"]

    assert _troca(vps, a).status_code == 200
    w.refresh_companies(force=True)
    vps.clock.advance(hours=3)
    w.run_emissions()
    with vps.fiscal_sessions() as s:
        it = s.scalar(select(BatchItem).where(BatchItem.draft_id == d["id"]))
        assert it.status == "WITHDRAWN" and it.last_error_code == "ENVIRONMENT_CHANGED" and it.submit_phase == "NONE"
        dr = s.get(Draft, d["id"])
        assert dr.status == "DRAFT" and dr.environment == "restricted" and dr.deleted_at is None
        ags = {x.environment: x for x in s.scalars(select(SyncSchedule).where(SyncSchedule.company_id == a["id"]))}
        assert ags["restricted"].enabled is False and ags["restricted"].cursor_nsu == 41
        assert ags["production"].enabled is True and ags["production"].cursor_nsu == 0
        job = s.get(Job, pedido.json()["job_id"])
        assert job.status == "FAILED" and ags["restricted"].pending_job_id is None
        assert s.scalar(select(JobError.code).where(JobError.job_id == job.id)) == "COMPANY_OR_ENVIRONMENT_INACTIVE"
    assert [c for c in gov.calls if c[1] == "POST"] == []  # nada transmitido
    # O ambiente anterior continua consultável por quem tem acesso: só o atual aparece nas listas.
    assert vps.fiscal.get(f"{F}/{a['id']}/drafts", headers=a["h"]).json()["total"] == 0


def test_empresa_de_demonstracao_nao_usa_ambiente_oficial(vps):
    from core_api.seed import DEMO_CNPJS

    doc = sorted(DEMO_CNPJS)[0]
    cid = vps.add_company("Demonstração", doc, environment="mock")
    uid = vps.add_user("demo@ex.com")
    vps.grant(uid, cid)
    h = AuthHeaders(vps, "demo@ex.com")
    r = vps.core.post(f"{C}/{cid}{ENV}", headers=h, json={"environment": "production", "adn_read": True, "confirmation": {
        "company_id": cid, "cnpj": doc, "from_environment": "mock", "to_environment": "production"}})
    assert r.status_code == 422 and r.json()["code"] == "DEMO_COMPANY_OFFICIAL_BLOCKED"
