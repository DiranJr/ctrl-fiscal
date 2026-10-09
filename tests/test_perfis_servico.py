"""Perfis de serviço a partir das notas EMITIDAS: inventário (recebidas não entram), agrupamento por cTribNac+cNBS com
variações tributárias, preparação idempotente (pendente, sem sobrescrever), revisão/aprovação com versões e
auditoria, aplicação ao rascunho (perfil pendente não transmite), cadastro do prestador só em campos vazios e
isolamento por workspace. Somente dados fictícios."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from conftest import cnpj
from fixtures_nfse import nota_como_187
from test_rascunho_referencia import importar

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
IBS = {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}


def _nota(prest: str, toma: str, numero: int, *, ctn: str = "072101", nbs: str = "101010100", **kw) -> bytes:
    x = nota_como_187(prest, toma, numero, **kw).decode()
    return x.replace("<cTribNac>072101</cTribNac>", f"<cTribNac>{ctn}</cTribNac>") \
            .replace("<cNBS>101010100</cNBS>", f"<cNBS>{nbs}</cNBS>").encode()


def _carregar(stack, world) -> None:  # noqa: ANN001
    toma = cnpj("556677880001")
    importar(stack, world, _nota(world["a_cnpj"], toma, 301), "n301.xml")
    importar(stack, world, _nota(world["a_cnpj"], toma, 302, retencoes=True), "n302.xml")
    importar(stack, world, _nota(world["a_cnpj"], toma, 303, ibscbs=True), "n303.xml")
    importar(stack, world, _nota(world["a_cnpj"], toma, 304, ctn="071601", nbs="101032000"), "n304.xml")
    # Nota RECEBIDA pela empresa A (A é tomadora): não pode virar evidência de serviço prestado.
    r = stack.fiscal.post(f"{F}/{world['a']}/documents/import", headers=world["ha"], files=[(
        "files", ("r1.xml", _nota(cnpj("771122330001"), world["a_cnpj"], 999, ctn="010101", nbs="115021000"),
                  "application/xml"))])
    assert r.status_code == 202
    w = stack.worker()
    while w.run_jobs():
        pass


def test_inventario_so_notas_emitidas_e_agrupa_com_variacoes(stack, world):
    _carregar(stack, world)
    inv = stack.fiscal.get(f"{F}/{world['a']}/service-inventory", headers=world["ha"]).json()
    assert inv["total_notas"] == 4  # a recebida ficou de fora
    g = {x["origin_key"]: x for x in inv["grupos"]}
    assert set(g) == {"072101:101010100", "071601:101032000"}
    principal = g["072101:101010100"]
    assert principal["total_notas"] == 3
    assert {(i["retencao"], i["notas"]) for i in principal["iss"]} == {("Não retido", 2), ("Retido pelo tomador", 1)}
    assert {c["CST"] for c in principal["pis_cofins"]} == {"01"}
    assert principal["ibs_cbs"][0]["cIndOp"] == "100301" and principal["ibs_cbs_ausente"] == 2
    assert principal["retencoes_federais"]["IRRF"] == 1
    assert {e["numero"] for e in principal["exemplos"]} == {"301", "302", "303"}
    assert any(o["code"] == "RETENCAO_ISS_VARIA" for o in principal["observacoes"])
    assert not any(k.startswith("010101:") for k in g)


def test_preparacao_idempotente_pendente_sem_sobrescrever(stack, world):
    _carregar(stack, world)
    r = stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["ha"])
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 2 and r.json()["existing"] == []
    perfis = {p["origin_key"]: p for p in stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()}
    p = perfis["072101:101010100"]
    assert p["status"] == "PENDING_REVIEW" and p["version"] == 1
    assert p["field_sources"]["national_code"]["origem"] == "xml"
    assert p["field_sources"]["ibs_cbs"]["origem"] == "xml_historico"  # evidência histórica, não garantia
    assert p["ibs_cbs"]["operation_indicator"] == "100301"
    assert p["evidence"]["total_notas"] == 3 and p["evidence"]["exemplos"]
    assert any(i["code"] == "CONFIRMAR" for i in p["pending"])
    assert p["descriptions"]["operation_indicator"]["fonte"].startswith("Anexo C")
    # Edição do usuário e nova preparação: nada é duplicado nem sobrescrito.
    r = stack.core.patch(f"{C}/{world['a']}/service-profiles/{p['id']}", headers=world["ha"],
                         json={"name": "Apoio de mina (revisado)", "expected_version": 1})
    assert r.status_code == 200 and r.json()["field_sources"]["name"]["origem"] == "usuario"
    r = stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["ha"]).json()
    assert r["created"] == [] and len(r["existing"]) == 2
    again = {x["origin_key"]: x for x in stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()}
    assert again["072101:101010100"]["name"] == "Apoio de mina (revisado)" and len(again) == 2
    # Somente admin prepara; leitor não.
    assert stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["hr"]).status_code == 403


def test_revisao_aprovacao_versoes_e_auditoria(stack, world):
    _carregar(stack, world)
    stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["ha"])
    p = {x["origin_key"]: x for x in stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()}
    sem_ibs = p["071601:101032000"]  # nenhuma nota com IBS/CBS: classificação pendente com motivo exato
    url = f"{C}/{world['a']}/service-profiles/{sem_ibs['id']}"
    codigos = {i["code"] for i in sem_ibs["pending"] if i["severity"] == "error"}
    assert "IBS_CBS_REQUIRED" in codigos and not sem_ibs["can_approve"]
    r = stack.core.post(f"{url}/approve", headers=world["ha"], json={"expected_version": 1})
    assert r.status_code == 409 and r.json()["code"] == "SERVICE_PROFILE_HAS_PENDENCIES"
    assert {e["code"] for e in r.json()["errors"]} >= {"IBS_CBS_REQUIRED"}
    # Compatibilidade CST × cClassTrib conferida.
    r = stack.core.patch(url, headers=world["ha"], json={"expected_version": 1, "ibs_cbs": {**IBS, "cst": "200"}})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "E0959"
    r = stack.core.patch(url, headers=world["ha"], json={"expected_version": 1, "ibs_cbs": IBS})
    assert r.status_code == 200 and r.json()["can_approve"] and r.json()["version"] == 2
    r = stack.core.post(f"{url}/approve", headers=world["ha"], json={"expected_version": 2})
    assert r.status_code == 200, r.text
    ap = r.json()
    assert ap["status"] == "APPROVED" and ap["approved_by"] == world["ua"] and ap["valid_from"]
    assert all(v.get("confirmado_por") == world["ua"] for k, v in ap["field_sources"].items() if ap.get(k))
    # Editar um perfil aprovado devolve à revisão (nova versão).
    r = stack.core.patch(url, headers=world["ha"], json={"expected_version": 3, "description": "Supressão vegetal"})
    assert r.json()["status"] == "PENDING_REVIEW" and r.json()["approved_by"] is None
    hist = stack.core.get(f"{url}/versions", headers=world["ha"]).json()
    assert [h["action"] for h in hist] == ["updated", "approved", "updated", "prepared"]
    assert hist[1]["snapshot"]["status"] == "APPROVED" and hist[1]["actor_id"] == world["ua"]
    from core_api.models import AuditEvent

    with stack.core_sessions() as s:
        acoes = [a.action for a in s.scalars(select(AuditEvent).where(AuditEvent.target_id == sem_ibs["id"])
                                             .order_by(AuditEvent.occurred_at))]
        upd = s.scalar(select(AuditEvent).where(AuditEvent.target_id == sem_ibs["id"],
                                                AuditEvent.action == "service_profile.updated"))
    assert acoes.count("service_profile.updated") == 2 and "service_profile.approved" in acoes
    assert "ibs_cbs" in upd.details["changes"] or "description" in upd.details["changes"]
    # Inativar e reativar (volta à revisão).
    r = stack.core.post(f"{url}/inactivate", headers=world["ha"], json={"expected_version": 4})
    assert r.json()["status"] == "INACTIVE" and not r.json()["is_active"]
    r = stack.core.post(f"{url}/reactivate", headers=world["ha"], json={"expected_version": 5})
    assert r.json()["status"] == "PENDING_REVIEW"


def test_perfil_no_rascunho_pendente_nao_transmite(stack, world):
    _carregar(stack, world)
    stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["ha"])
    p = {x["origin_key"]: x for x in stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()}
    perfil = p["071601:101032000"]
    body = {"customer_id": world["customer"], "competence": "2026-10", "service_date": "2026-10-09", "amount": "100.00",
            "service_profile_id": perfil["id"],
            "service": {"national_code": "071601", "nbs_code": "101032000", "description": "Supressão vegetal"},
            "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "ibs_cbs": IBS}}
    d = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json=body).json()
    assert d["service_profile_id"] == perfil["id"] and d["service_profile_version"] == 1
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()
    assert "SERVICE_PROFILE_NOT_APPROVED" in {i["code"] for i in v["issues"] if i["severity"] == "error"}
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex}, json={
        "drafts": [{"id": d["id"], "expected_version": d["version"]}],
        "scheduled_at": (stack.clock.now() + timedelta(hours=1)).isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and "SERVICE_PROFILE_NOT_APPROVED" in r.text
    url = f"{C}/{world['a']}/service-profiles/{perfil['id']}"
    stack.core.patch(url, headers=world["ha"], json={"expected_version": 1, "ibs_cbs": IBS})
    stack.core.post(f"{url}/approve", headers=world["ha"], json={"expected_version": 2})
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()
    codes = {i["code"]: i["severity"] for i in v["issues"]}
    assert "SERVICE_PROFILE_NOT_APPROVED" not in codes and codes.get("SERVICE_PROFILE_CHANGED") == "warning"
    # Reaplicar registra a versão vigente; campos próprios da operação seguem editáveis (divergência só avisa).
    r = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"], json={
        "expected_version": d["version"], "service_profile_id": perfil["id"],
        "service": {"national_code": "072101"}})
    assert r.status_code == 200 and r.json()["service_profile_version"] == 3
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()
    codes = {i["code"]: i["severity"] for i in v["issues"]}
    assert codes.get("SERVICE_PROFILE_DIVERGENT") == "warning" and "SERVICE_PROFILE_CHANGED" not in codes
    # Perfil de outro workspace não pode ser aplicado.
    r = stack.fiscal.post(f"{F}/{world['b']}/drafts", headers=world["hb"], json={**body, "customer_id": str(uuid.uuid4())})
    assert r.status_code in (404, 422)
    hb_cust = stack.core.post(f"{C}/{world['b']}/customers", headers=world["hb"], json={
        "name": "Cliente B", "document_type": "CNPJ", "document": cnpj("123123120001")}).json()
    r = stack.fiscal.post(f"{F}/{world['b']}/drafts", headers=world["hb"], json={**body, "customer_id": hb_cust["id"]})
    assert r.status_code == 422 and r.json()["code"] == "SERVICE_PROFILE_NOT_FOUND"
    assert stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["hb"]).status_code == 403


def test_cadastro_do_prestador_so_campos_vazios(stack, world):
    from core_api.models import AuditEvent, Company

    _carregar(stack, world)
    with stack.core_sessions() as s:
        s.get(Company, world["a"]).phone = "9400000000"  # divergente do XML (nunca sobrescrito)
        s.commit()
    dados = stack.fiscal.get(f"{F}/{world['a']}/provider-data-from-documents", headers=world["ha"]).json()
    assert dados["total_notas"] == 4 and dados["campos"]["municipal_registration"]["consistente"]
    assert stack.fiscal.post(f"{F}/{world['a']}/provider-data-from-documents/apply", headers=world["hr"]).status_code == 403
    r = stack.fiscal.post(f"{F}/{world['a']}/provider-data-from-documents/apply", headers=world["ha"]).json()
    assert {"municipal_registration", "email", "postal_code", "address.street"} <= set(r["applied"])
    assert [d["field"] for d in r["divergent"]] == ["phone"]
    with stack.core_sessions() as s:
        c = s.get(Company, world["a"])
        assert c.phone == "9400000000" and c.municipal_registration == "123456789012345"
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "company.provider_data_completed")) is not None
    r2 = stack.fiscal.post(f"{F}/{world['a']}/provider-data-from-documents/apply", headers=world["ha"]).json()
    assert r2["applied"] == {} and "municipal_registration" in r2["equal"]


def test_prontidao_em_secoes(stack, world):
    r = stack.core.get(f"{C}/{world['a']}/readiness", headers=world["ha"]).json()
    ids = [x["id"] for x in r["sections"]]
    assert ids == ["cadastro", "certificado", "perfil", "nota", "ambiente"]
    cad = {i["code"]: i for i in r["sections"][0]["items"]}
    assert cad["PROVIDER_ADDRESS_NOT_SENT"]["severity"] == "info"  # endereço/CEP não bloqueiam (E0128)
    assert "NO_APPROVED_SERVICE_PROFILE" in {i["code"] for i in r["sections"][2]["items"]}


def test_migracao_core_0006_nao_aprova_perfis_existentes(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine, text

    from conftest import ROOT, SERVICES, Config

    url = f"sqlite:///{tmp_path}/core.db"
    cfg = Config(str(ROOT / SERVICES["core"] / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / SERVICES["core"] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0005")
    e = create_engine(url)
    cid, n = str(uuid.uuid4()), "2026-10-09 10:00:00.000000"
    with e.begin() as c:
        c.execute(text("INSERT INTO companies (id, name, cnpj, timezone, nfse_environment, is_active, created_at, "
                       "updated_at) VALUES (:i, 'X', :d, 'America/Sao_Paulo', 'mock', 1, :n, :n)"),
                  {"i": cid, "d": cnpj("101112130001"), "n": n})
        for ativo in (1, 0):
            c.execute(text("INSERT INTO service_profiles (id, company_id, name, national_code, is_active, version, "
                           "created_at, updated_at) VALUES (:i, :c, 'P', '010101', :a, 3, :n, :n)"),
                      {"i": str(uuid.uuid4()), "c": cid, "a": ativo, "n": n})
    command.upgrade(cfg, "head")
    with e.connect() as c:
        assert sorted(r[0] for r in c.execute(text("SELECT status FROM service_profiles"))) == ["INACTIVE", "PENDING_REVIEW"]
        assert c.execute(text("SELECT count(*) FROM service_profile_versions WHERE action='migrated' AND version=3")).scalar() == 2
    e.dispose()
