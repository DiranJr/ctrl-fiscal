"""Emissão de NFS-e em PRODUÇÃO com Sefin simulada (httpx.MockTransport; nenhuma rede, nenhuma nota real).

Cobre: chave NFSE_PRODUCTION_EMISSION_ENABLED desligada (padrão) e independente do cancelamento; permissão "emit";
perfil de serviço aprovado e compatível (ausente, pendente, divergente); confirmação individual do lote vinculada à
versão revisada (ausente, divergente, desatualizada após edição); contexto incorreto; duplicidade; envio ao host de
produção com tpAmb=1 e XSD de produção; timeout após aceite reconciliado por consulta sem reenvio."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from lxml import etree
from sqlalchemy import select

from conftest import MYSQL, cnpj
from test_official_integration import (  # noqa: F401 - fixtures reaproveitadas
    APPROX, MUN, NS, batch_items, gov, official_draft, ostack, real,
)

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
IBS = {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}
SERVICO = {"national_code": "071601", "municipal_code": "001", "description": "Serviço de supressão vegetal (teste)",
           "location_code": MUN, "nbs_code": "101032000"}
TAX = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": APPROX, "ibs_cbs": IBS}


def _producao(ostack, real, *, ligar: bool = True) -> None:  # noqa: ANN001
    from core_api.models import Company

    with ostack.core_sessions() as s:
        s.get(Company, real["id"]).nfse_environment = "production"
        s.commit()
    ostack.nfse_settings.production_emission_enabled = ligar
    ostack.fiscal_app.state.ctx.settings.production_emission_enabled = ligar


def _perfil(ostack, real, aprovar: bool = True) -> dict:  # noqa: ANN001
    r = ostack.core.post(f"{C}/{real['id']}/service-profiles", headers=real["h"], json={
        "name": "Supressão vegetal", "national_code": "071601", "nbs_code": "101032000", "issqn_taxation": "TAXABLE",
        "ibs_cbs": IBS})
    assert r.status_code == 201, r.text
    p = r.json()
    if aprovar:
        r = ostack.core.post(f"{C}/{real['id']}/service-profiles/{p['id']}/approve", headers=real["h"],
                             json={"expected_version": p["version"], "valid_from": "2026-10-01"})
        assert r.status_code == 200, r.text
        p = r.json()
    return p


def _rascunho(ostack, real, perfil: dict | None, amount: str = "1500.00") -> dict:  # noqa: ANN001
    extra = {"service_profile_id": perfil["id"]} if perfil else {}
    return official_draft(ostack, real, service=SERVICO, tax_settings=TAX, amount=amount, **extra)


def _confirmacao(real, drafts: list[dict], **over) -> dict:  # noqa: ANN001
    from decimal import Decimal

    total = sum(Decimal(d["amount"]) for d in drafts)
    conf = {"company_cnpj": real["cnpj"], "environment": "production", "count": len(drafts),
            "total_amount": f"{total:.2f}",
            "documents": [{"id": d["id"], "version": d["version"], "amount": d["amount"]} for d in drafts]}
    conf.update(over)
    return conf


def _agendar(ostack, real, drafts, confirmacao=..., key: str | None = None):  # noqa: ANN001, ANN202
    body = {"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts],
            "scheduled_at": (ostack.clock.now() + timedelta(minutes=1)).isoformat(), "timezone": "America/Sao_Paulo"}
    if confirmacao is ...:
        confirmacao = _confirmacao(real, drafts)
    if confirmacao is not None:
        body["production_confirmation"] = confirmacao
    return ostack.fiscal.post(f"{F}/{real['id']}/batches",
                              headers={**real["h"], "Idempotency-Key": key or uuid.uuid4().hex}, json=body)


def _validar(ostack, real, d) -> dict:  # noqa: ANN001
    v = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()
    return {i["code"]: i["severity"] for i in v["issues"]}


def test_chave_desligada_bloqueia_e_e_independente_do_cancelamento(ostack, real, gov):
    _producao(ostack, real, ligar=False)
    ostack.nfse_settings.production_cancellation_enabled = True  # cancelamento ligado NÃO libera emissão
    d = _rascunho(ostack, real, _perfil(ostack, real))
    assert _validar(ostack, real, d).get("PRODUCTION_EMISSION_BLOCKED") == "error"
    r = _agendar(ostack, real, [d])
    assert r.status_code == 422 and r.json()["code"] == "PRODUCTION_EMISSION_BLOCKED"
    from nfse_api.providers.base import CompanyContext, ProviderBlocked

    prov = ostack.nfse_app.state.providers["production"]
    with pytest.raises(ProviderBlocked) as e:
        prov.check_ready(CompanyContext(real["id"], real["cnpj"], "production"), "submit")
    assert e.value.code == "PRODUCTION_EMISSION_BLOCKED"
    prov.check_ready(CompanyContext(real["id"], real["cnpj"], "production"), "event")  # cancelamento segue ok
    assert not gov.posts()


def test_permissao_emit_e_perfil_aprovado_obrigatorios(ostack, real, gov):
    from core_api.models import Membership

    _producao(ostack, real)
    sem_perfil = _rascunho(ostack, real, None)
    assert _validar(ostack, real, sem_perfil).get("SERVICE_PROFILE_REQUIRED_PRODUCTION") == "error"
    pendente = _rascunho(ostack, real, _perfil(ostack, real, aprovar=False))
    assert _validar(ostack, real, pendente).get("SERVICE_PROFILE_NOT_APPROVED") == "error"
    r = _agendar(ostack, real, [pendente])
    assert r.status_code == 422 and "SERVICE_PROFILE_NOT_APPROVED" in r.text
    # Rascunho divergente do perfil aprovado (operação diferente): em produção é erro.
    ok = _perfil(ostack, real)
    div = official_draft(ostack, real, service={**SERVICO, "national_code": "070201", "obra_code": "CNO-1"},
                         tax_settings=TAX, service_profile_id=ok["id"])
    assert _validar(ostack, real, div).get("SERVICE_PROFILE_DIVERGENT") == "error"
    # Sem a permissão "emit" ninguém agenda, mesmo com tudo pronto.
    d = _rascunho(ostack, real, ok)
    with ostack.core_sessions() as s:
        m = s.scalar(select(Membership).where(Membership.company_id == real["id"]))
        m.permissions = [p for p in m.permissions if p != "emit"]
        s.commit()
    r = _agendar(ostack, real, [d])
    assert r.status_code == 403
    assert not gov.posts()


def test_confirmacao_ausente_divergente_e_desatualizada(ostack, real, gov):
    _producao(ostack, real)
    p = _perfil(ostack, real)
    d1, d2 = _rascunho(ostack, real, p, "1500.00"), _rascunho(ostack, real, p, "250.00")
    r = _agendar(ostack, real, [d1, d2], confirmacao=None)
    assert r.status_code == 422 and r.json()["code"] == "PRODUCTION_CONFIRMATION_REQUIRED"
    for over in ({"total_amount": "1700.00"}, {"count": 1}, {"company_cnpj": cnpj("998877660001")},
                 {"documents": [{"id": d1["id"], "version": d1["version"], "amount": d1["amount"]}]}):
        r = _agendar(ostack, real, [d1, d2], confirmacao=_confirmacao(real, [d1, d2], **over))
        assert r.status_code == 409 and r.json()["code"] == "PRODUCTION_CONFIRMATION_MISMATCH", (over, r.text)
    # Confirmação feita, depois o rascunho foi editado: a confirmação antiga não vale para a nova versão.
    conf_antiga = _confirmacao(real, [d1])
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d1['id']}", headers=real["h"],
                            json={"expected_version": d1["version"], "amount": "1600.00"})
    assert r.status_code == 200
    d1n = r.json()
    r = _agendar(ostack, real, [d1n], confirmacao=conf_antiga)
    assert r.status_code == 409 and r.json()["code"] == "PRODUCTION_CONFIRMATION_MISMATCH"
    assert not gov.posts()


def test_emissao_producao_completa_idempotente_e_sem_duplicidade(ostack, real, gov):
    from nfse_api import dps

    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    v = _validar(ostack, real, d)
    assert v.get("PRODUCTION_REAL") == "info" and not [k for k, s in v.items() if s == "error"], v
    key = uuid.uuid4().hex
    r = _agendar(ostack, real, [d], key=key)
    assert r.status_code == 202, r.text
    again = _agendar(ostack, real, [d], key=key)  # mesmo clique repetido: mesmo lote
    assert again.status_code == 202 and again.json()["batch_id"] == r.json()["batch_id"]
    outro = _agendar(ostack, real, [d])  # outro pedido para o mesmo rascunho: recusado
    assert outro.status_code == 409
    ostack.clock.advance(minutes=2)
    ostack.worker().run_emissions()
    posts = [c for c in gov.posts() if c[3] == "/SefinNacional/nfse"]
    assert [c[2] for c in posts] == ["sefin.nfse.gov.br"], batch_items(ostack, real, r.json()["batch_id"])[0]["last_error"]
    enviado = gov.posted_dps[-1]
    inf = etree.fromstring(enviado).find(f"{NS}infDPS")
    assert inf.findtext(f"{NS}tpAmb") == "1" and inf.findtext(f"{NS}serie") == "1"
    dps.validar_xsd(enviado, "production")
    it = batch_items(ostack, real, r.json()["batch_id"])[0]
    assert it["status"] == "AUTHORIZED", it
    docs = ostack.fiscal.get(f"{F}/{real['id']}/documents", headers=real["h"],
                             params={"direction": "ISSUED", "environment": "production"}).json()["items"]
    assert len(docs) == 1 and docs[0]["simulated"] is False
    from ctrl_fiscal.models import AuditEvent

    with ostack.fiscal_sessions() as s:
        ev = s.scalar(select(AuditEvent).where(AuditEvent.action == "batch.created"))
        assert ev.details["production_confirmation"]["count"] == 1


def _emitir_agora(ostack, real, drafts, confirmacao=..., key: str | None = None):  # noqa: ANN001, ANN202
    """“Emitir agora”: sem scheduled_at; o instante é o do servidor."""
    body = {"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts], "mode": "NOW",
            "timezone": "America/Sao_Paulo"}
    if confirmacao is ...:
        confirmacao = _confirmacao(real, drafts, mode="NOW")
    if confirmacao is not None:
        body["production_confirmation"] = confirmacao
    return ostack.fiscal.post(f"{F}/{real['id']}/batches",
                              headers={**real["h"], "Idempotency-Key": key or uuid.uuid4().hex}, json=body)


def _sefin_posts(gov) -> int:  # noqa: ANN001
    return len([c for c in gov.posts() if c[3] == "/SefinNacional/nfse"])


def test_emitir_agora_processa_imediatamente_sem_esperar_o_dia_01(ostack, real, gov):
    from ctrl_fiscal.models import AuditEvent

    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    r = _emitir_agora(ostack, real, [d])
    assert r.status_code == 202, r.text
    assert r.json()["mode"] == "NOW"
    from datetime import datetime

    assert abs((datetime.fromisoformat(r.json()["scheduled_at"]) - ostack.clock.now()).total_seconds()) < 1
    ostack.worker().run_emissions()  # sem avançar o relógio: processado no ciclo seguinte do worker
    assert _sefin_posts(gov) == 1
    b = ostack.fiscal.get(f"{F}/{real['id']}/batches/{r.json()['batch_id']}", headers=real["h"]).json()
    assert b["mode"] == "NOW" and b["items"][0]["status"] == "AUTHORIZED", b
    with ostack.fiscal_sessions() as s:
        ev = s.scalar(select(AuditEvent).where(AuditEvent.action == "batch.created"))
        assert ev.details["mode"] == "NOW" and ev.details["production_confirmation"]["mode"] == "NOW"


def test_emitir_agora_exige_confirmacao_da_acao_e_da_versao_revisada(ostack, real, gov):
    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    r = _emitir_agora(ostack, real, [d], confirmacao=None)
    assert r.status_code == 422 and r.json()["code"] == "PRODUCTION_CONFIRMATION_REQUIRED"
    # Confirmou AGENDAR, pediu EMITIR AGORA: recusado (a ação faz parte da confirmação).
    r = _emitir_agora(ostack, real, [d], confirmacao=_confirmacao(real, [d], mode="SCHEDULED"))
    assert r.status_code == 409 and r.json()["code"] == "PRODUCTION_CONFIRMATION_MISMATCH"
    assert any(e["field"] == "production_confirmation.mode" for e in r.json()["errors"])
    # Confirmação feita e o rascunho editado depois: desatualizada.
    antiga = _confirmacao(real, [d], mode="NOW")
    d2 = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"],
                             json={"expected_version": d["version"], "amount": "1499.00"}).json()
    r = _emitir_agora(ostack, real, [d2], confirmacao=antiga)
    assert r.status_code == 409 and r.json()["code"] == "PRODUCTION_CONFIRMATION_MISMATCH"
    r = _emitir_agora(ostack, real, [d], confirmacao=antiga)  # versão antiga do próprio rascunho
    assert r.status_code == 409
    assert _sefin_posts(gov) == 0


def test_emitir_agora_clique_duplicado_e_agendamento_do_mesmo_rascunho(ostack, real, gov):
    _producao(ostack, real)
    p = _perfil(ostack, real)
    d1, d2 = _rascunho(ostack, real, p, "100.00"), _rascunho(ostack, real, p, "200.00")
    key = uuid.uuid4().hex
    r1 = _emitir_agora(ostack, real, [d1], key=key)
    r1b = _emitir_agora(ostack, real, [d1], key=key)  # clique repetido / nova tentativa após timeout
    assert r1.status_code == r1b.status_code == 202 and r1.json()["batch_id"] == r1b.json()["batch_id"]
    outro = _emitir_agora(ostack, real, [d1])  # outro pedido para o mesmo rascunho
    assert outro.status_code == 409 and outro.json()["code"] == "BATCH_CONFLICT"
    assert _agendar(ostack, real, [d1]).status_code == 409  # agendar o que já está emitindo
    # Agendado para o dia 01 e depois "emitir agora": recusado até retirar da fila; retirado, emite uma vez só.
    ag = _agendar(ostack, real, [d2])
    assert ag.status_code == 202
    r = _emitir_agora(ostack, real, [d2])
    assert r.status_code == 409 and "retire-o da fila" in r.text
    atual = ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d2['id']}", headers=real["h"]).json()
    w = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d2['id']}/withdraw", headers=real["h"],
                           json={"expected_version": atual["version"]})
    assert w.status_code == 200, w.text
    d2n = w.json()
    r2 = _emitir_agora(ostack, real, [d2n])
    assert r2.status_code == 202, r2.text
    wk = ostack.worker()
    wk.run_emissions()
    ostack.clock.advance(minutes=5)
    wk.run_emissions()
    assert _sefin_posts(gov) == 2  # uma transmissão por rascunho
    itens = [batch_items(ostack, real, b)[0]["status"] for b in (r1.json()["batch_id"], r2.json()["batch_id"])]
    assert itens == ["AUTHORIZED", "AUTHORIZED"]
    assert batch_items(ostack, real, ag.json()["batch_id"])[0]["status"] == "WITHDRAWN"


def test_emitir_agora_com_timeout_reconcilia_antes_de_reenviar(ostack, real, gov):
    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    gov.sefin_plan = ["500_after_accept"]
    r = _emitir_agora(ostack, real, [d])
    assert r.status_code == 202
    w = ostack.worker()
    w.run_emissions()
    assert batch_items(ostack, real, r.json()["batch_id"])[0]["status"] == "AWAITING_CONFIRMATION"
    ostack.clock.advance(minutes=15)
    w.run_emissions()
    assert batch_items(ostack, real, r.json()["batch_id"])[0]["status"] == "AUTHORIZED"
    assert _sefin_posts(gov) == 1


@pytest.mark.skipif(not MYSQL, reason="concorrência real exige MySQL")
def test_emitir_agora_concorrente_com_agendamento_mysql(ostack, real, gov):
    import threading

    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    barrier = threading.Barrier(2)
    out: list[int] = []

    def go(fn) -> None:  # noqa: ANN001
        barrier.wait()
        out.append(fn().status_code)

    ts = [threading.Thread(target=go, args=(lambda: _emitir_agora(ostack, real, [d]),)),
          threading.Thread(target=go, args=(lambda: _agendar(ostack, real, [d]),))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [202, 409], out
    from ctrl_fiscal.models import BatchItem

    with ostack.fiscal_sessions() as s:
        assert len(s.scalars(select(BatchItem).where(BatchItem.draft_id == d["id"])).all()) == 1


def test_contexto_alterado_depois_do_agendamento_nao_transmite(ostack, real, gov):
    """Entre o agendamento (dia 01 do mês seguinte, por exemplo) e a transmissão o contexto pode mudar: o worker confere
    de novo workspace, ambiente e perfil (mesma versão aprovada) e nada é enviado se divergir."""
    from core_api.models import Company, Workspace

    _producao(ostack, real)
    p = _perfil(ostack, real)
    a, b, c = (_rascunho(ostack, real, p, v) for v in ("100.00", "200.00", "300.00"))
    lotes = [_agendar(ostack, real, [d]).json()["batch_id"] for d in (a, b, c)]
    w = ostack.worker()

    # 1) Workspace suspenso: adia sem transmitir (continua agendado).
    with ostack.core_sessions() as s:
        s.get(Workspace, real["id"]).status = "SUSPENDED"
        s.commit()
    ostack.clock.advance(minutes=2)
    w.run_emissions()
    for bid in lotes:
        it = batch_items(ostack, real, bid)[0]
        assert it["status"] == "SCHEDULED" and it["last_error"]["code"] == "WORKSPACE_NOT_ACTIVE", it
    assert not gov.posts()
    with ostack.core_sessions() as s:
        s.get(Workspace, real["id"]).status = "ACTIVE"
        s.commit()

    # 2) Perfil inativado depois da confirmação: os itens são retirados (rascunho volta a DRAFT), nada enviado.
    atual = ostack.core.get(f"{C}/{real['id']}/service-profiles", headers=real["h"]).json()
    atual = next(x for x in (atual.get("items") if isinstance(atual, dict) else atual) if x["id"] == p["id"])
    r = ostack.core.post(f"{C}/{real['id']}/service-profiles/{p['id']}/inactivate", headers=real["h"],
                         json={"expected_version": atual["version"]})
    assert r.status_code == 200, r.text
    ostack.clock.advance(hours=2)
    w.run_emissions()
    for bid in lotes:
        it = batch_items(ostack, real, bid)[0]
        assert it["status"] == "WITHDRAWN" and it["last_error"]["code"] == "SERVICE_PROFILE_CHANGED", it
    assert not gov.posts()

    # 3) Ambiente de emissão trocado depois do agendamento: retirado com motivo próprio.
    p2 = _perfil(ostack, real)
    d = _rascunho(ostack, real, p2)
    bid = _agendar(ostack, real, [d]).json()["batch_id"]
    with ostack.core_sessions() as s:
        s.get(Company, real["id"]).nfse_environment = "restricted"
        s.commit()
    ostack.clock.advance(minutes=2)
    w.run_emissions()
    it = batch_items(ostack, real, bid)[0]
    assert it["status"] == "WITHDRAWN" and it["last_error"]["code"] == "ENVIRONMENT_CHANGED", it
    assert not gov.posts()


def test_prontidao_distingue_funcionalidade_habilitada_de_empresa_pronta(ostack, real, gov):
    def emissao_prod() -> dict:
        r = ostack.core.get(f"{C}/{real['id']}/readiness", headers=real["h"])
        assert r.status_code == 200, r.text
        env = next(e for e in r.json()["environments"] if e["environment"] == "production")
        return next(o for o in env["operations"] if o["operation"] == "emissao")

    _producao(ostack, real, ligar=False)
    op = emissao_prod()
    assert op["enabled"] is False and op["ready"] is False
    assert "PRODUCTION_EMISSION_BLOCKED" in {i["code"] for i in op["issues"]}
    _producao(ostack, real)  # chave ligada, mas nenhum perfil aprovado: habilitada ≠ pronta
    op = emissao_prod()
    assert op["enabled"] is True and op["ready"] is False
    assert "NO_APPROVED_SERVICE_PROFILE" in {i["code"] for i in op["issues"]}
    _perfil(ostack, real)
    op = emissao_prod()
    assert op["enabled"] is True and op["ready"] is True, op
    assert not gov.posts()


def test_timeout_apos_aceite_reconcilia_sem_reenviar(ostack, real, gov):
    _producao(ostack, real)
    d = _rascunho(ostack, real, _perfil(ostack, real))
    gov.sefin_plan = ["500_after_accept"]
    r = _agendar(ostack, real, [d])
    assert r.status_code == 202, r.text
    ostack.clock.advance(minutes=2)
    w = ostack.worker()
    w.run_emissions()
    it = batch_items(ostack, real, r.json()["batch_id"])[0]
    assert it["status"] == "AWAITING_CONFIRMATION", it
    ostack.clock.advance(minutes=15)
    w.run_emissions()
    it = batch_items(ostack, real, r.json()["batch_id"])[0]
    assert it["status"] == "AUTHORIZED", it
    assert len([c for c in gov.posts() if c[3] == "/SefinNacional/nfse"]) == 1  # sem reenvio
    assert [c for c in gov.calls if c[1] == "GET" and c[3].startswith("/SefinNacional/dps/")]
