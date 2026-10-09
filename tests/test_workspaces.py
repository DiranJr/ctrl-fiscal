"""Workspaces (um por empresa/CNPJ): unicidade no banco e na API (inclusive concorrente no MySQL), vínculos com
permissões distintas por workspace, isolamento por identificador cruzado, situação PREPARING bloqueando transmissão,
série da DPS do próprio workspace, perfis de serviço com tabelas oficiais, prontidão por ambiente × operação e o
invariante de UM certificado ativo por workspace (renovação expurga o material do anterior). Somente empresas
fictícias e certificados sintéticos (AC de teste), sem rede."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from conftest import MYSQL, cnpj
from test_admin_users import A, _super, org  # noqa: F401 - fixture reaproveitada
from test_emission_queue import make_draft, schedule
from test_icp_validation import CRL_URL, _install, _validator, secure_stack  # noqa: F401
from test_nfse_connector import make_cert, make_crl

C = "/api/core/v1"
F = "/api/fiscal/v1/companies"
SAO_PAULO = "3550308"


def _novo(stack, h, doc: str, name: str = "Empresa Fictícia C"):  # noqa: ANN001, ANN202
    return stack.core.post(f"{A}/workspaces", headers=h, json={
        "name": name, "company": {"cnpj": doc, "name": name, "municipality_code": SAO_PAULO,
                                  "timezone": "America/Sao_Paulo"}})


def _status(stack, company_id: str, status: str) -> None:  # noqa: ANN001
    from core_api.models import Workspace

    with stack.core_sessions() as s:
        s.scalar(select(Workspace).where(Workspace.company_id == company_id)).status = status
        s.commit()


# ---------------------------------------------------------------- um workspace por CNPJ
def test_um_workspace_por_cnpj_somente_super_admin(stack, org):
    novo = cnpj("223334440001")
    assert _novo(stack, org["h_adm"], novo).status_code == 403  # admin de empresa não cria workspace
    r = _novo(stack, org["h_root"], novo)
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["status"] == "PREPARING" and w["company"]["cnpj"] == novo and w["id"] == w["company"]["id"]
    for doc in (novo, cnpj("112223330001")):  # repetido e CNPJ de workspace já existente
        r = _novo(stack, org["h_root"], doc, "Outro Nome")
        assert r.status_code == 409 and r.json()["code"] == "CNPJ_ALREADY_IN_WORKSPACE"
    assert _novo(stack, org["h_root"], "11222333000100").json()["code"] == "CNPJ_INVALID"
    # SUPER_ADMIN cria o workspace mas não ganha acesso fiscal implícito a ele.
    assert stack.core.get(f"{C}/companies/{w['id']}/readiness", headers=org["h_root"]).status_code in (403, 404)
    from core_api.models import AuditEvent, Company, Workspace

    with stack.core_sessions() as s:
        assert s.scalar(select(Company).where(Company.cnpj == novo)).nfse_environment == "restricted"
        assert len(s.scalars(select(Workspace)).all()) == 3
        assert s.scalar(select(AuditEvent).where(AuditEvent.action == "workspace.created")) is not None


def test_unicidade_de_workspace_no_banco(stack, org):
    from sqlalchemy.exc import IntegrityError

    from core_api.models import Workspace

    now = stack.clock.now()
    with stack.core_sessions() as s:
        s.add(Workspace(id=str(uuid.uuid4()), company_id=org["a"], name="Duplicado", status="PREPARING", created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            s.flush()


@pytest.mark.skipif(not MYSQL, reason="concorrência real exige MySQL")
def test_criacao_concorrente_do_mesmo_cnpj_mysql(stack, org):
    novo = cnpj("334445550001")
    barrier = threading.Barrier(4)
    out: list[int] = []

    def go(i: int) -> None:
        barrier.wait()
        out.append(_novo(stack, org["h_root"], novo, f"Concorrente {i}").status_code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409, 409, 409], out


# ---------------------------------------------------------------- vínculos e isolamento
def test_mesmo_usuario_permissoes_distintas_por_workspace(stack, org):
    from core_api.models import Membership

    with stack.core_sessions() as s:
        m = s.scalar(select(Membership).where(Membership.user_id == org["ambos"], Membership.company_id == org["a"]))
        m.permissions = ["view", "prepare", "admin"]
        s.commit()
    h = stack.login("ambos@ex.com")
    ws = {w["company"]["id"]: w for w in stack.core.get(f"{C}/auth/me", headers=h).json()["workspaces"]}
    assert set(ws) == {org["a"], org["b"]}
    assert ws[org["a"]]["permissions"] == ["view", "prepare", "admin"] and ws[org["b"]]["permissions"] == ["view"]
    assert all(w["id"] == w["company"]["id"] and w["status"] == "ACTIVE" for w in ws.values())
    lista = stack.core.get(f"{C}/workspaces", headers=h).json()
    assert {w["id"] for w in lista} == {org["a"], org["b"]}
    # Admin em A não vira admin em B ao "trocar" de workspace: o servidor decide por vínculo, não pelo navegador.
    body = {"name": "Serviço teste", "national_code": "010101"}
    assert stack.core.post(f"{C}/companies/{org['a']}/service-profiles", headers=h, json=body).status_code == 201
    r = stack.core.post(f"{C}/companies/{org['b']}/service-profiles", headers=h, json=body)
    assert r.status_code == 403


def test_identificador_cruzado_nao_vaza_entre_workspaces(stack, org):
    h_a = org["h_adm"]
    p = stack.core.post(f"{C}/companies/{org['a']}/service-profiles", headers=h_a,
                        json={"name": "Perfil de A", "national_code": "010101"}).json()
    h_b = stack.login("so.b@ex.com")
    for path in (f"{C}/companies/{org['a']}/service-profiles", f"{C}/companies/{org['a']}/readiness",
                 f"{C}/companies/{org['a']}/fiscal-profile"):
        assert stack.core.get(path, headers=h_b).status_code in (403, 404), path
    # Perfil de A pedido pela rota de B (onde o usuário de A não tem vínculo) e pela rota de A por quem é só de B.
    r = stack.core.patch(f"{C}/companies/{org['b']}/service-profiles/{p['id']}", headers=h_a,
                         json={"name": "Invasão", "expected_version": 1})
    assert r.status_code in (403, 404)
    r = stack.core.patch(f"{C}/companies/{org['a']}/service-profiles/{p['id']}", headers=h_b,
                         json={"name": "Invasão", "expected_version": 1})
    assert r.status_code in (403, 404)
    assert stack.core.get(f"{C}/companies/{org['b']}/service-profiles", headers=stack.login("so.b@ex.com")).json() == []


# ---------------------------------------------------------------- situação do workspace e série
def test_workspace_em_preparacao_prepara_mas_nao_transmite(stack, world):
    _status(stack, world["a"], "PREPARING")
    d = make_draft(stack, world)  # rascunho é permitido
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                "scheduled_at": (stack.clock.now() + timedelta(hours=1)).isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "WORKSPACE_NOT_ACTIVE", r.text
    _status(stack, world["a"], "ACTIVE")
    schedule(stack, world, [stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()],
             stack.clock.now() + timedelta(minutes=5))


def test_serie_da_dps_vem_do_workspace(stack, world):
    from core_api.models import Company

    with stack.core_sessions() as s:
        s.get(Company, world["a"]).dps_series = "7"
        s.get(Company, world["a"]).municipality_code = SAO_PAULO
        s.commit()
    h = world["ha"]
    # Série fora da faixa do aplicativo próprio (00001–49999) é recusada; a existente não é alterada.
    r = stack.core.patch(f"{C}/companies/{world['a']}/fiscal-profile", headers=h, json={"dps_series": "50000"})
    assert r.status_code == 422, r.text
    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    w = stack.worker()
    while w.run_emissions():
        pass
    it = stack.fiscal.get(f"{F}/{world['a']}/batches/{b['batch_id']}", headers=h).json()["items"][0]
    assert it["status"] == "AUTHORIZED", it
    assert it["dps_id"][25:30] == "00007", it["dps_id"]  # DPS + cMun(7) + tpInsc(1) + CNPJ(14) + série(5)
    from ctrl_fiscal.models import DpsSequence

    with stack.fiscal_sessions() as s:
        seqs = s.scalars(select(DpsSequence)).all()
        assert [(q.company_id, q.series) for q in seqs] == [(world["a"], "7")]


# ---------------------------------------------------------------- perfis de serviço e prontidão
def test_perfil_de_servico_valida_tabelas_oficiais(stack, org):
    import json
    from pathlib import Path

    import ctrl_common
    from ctrl_common import dominios

    classe = json.loads((Path(ctrl_common.__file__).parent / "dominios" / "cclasstrib_nfse.json")
                        .read_text(encoding="utf-8"))["classificacoes"][0]
    ind = sorted(dominios.dados()["cindop"])[0]
    url, h = f"{C}/companies/{org['a']}/service-profiles", org["h_adm"]
    r = stack.core.post(url, headers=h, json={"name": "Inválido", "national_code": "999999", "ibs_cbs": {
        "operation_indicator": "999999", "cst": "999", "class_code": "999999"}})
    assert r.status_code == 422
    codes = {e["code"] for e in r.json()["errors"]}
    assert codes == {"E0310", "E0901", "E0958"}
    r = stack.core.post(url, headers=h, json={"name": "CST divergente", "national_code": "010101", "ibs_cbs": {
        "operation_indicator": ind, "cst": "200" if classe["cst"] != "200" else "000", "class_code": classe["codigo"]}})
    assert r.status_code == 422 and [e["code"] for e in r.json()["errors"]] == ["E0959"]
    r = stack.core.post(url, headers=h, json={"name": "Válido", "national_code": "010101", "ibs_cbs": {
        "operation_indicator": ind, "cst": classe["cst"], "class_code": classe["codigo"]}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert stack.core.patch(f"{url}/{p['id']}", headers=h, json={"name": "X1", "expected_version": 9}).status_code == 409
    r = stack.core.patch(f"{url}/{p['id']}", headers=h, json={"name": "Renomeado", "expected_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    # Somente leitura (view) não altera perfis.
    assert stack.core.post(url, headers=stack.login("so.a@ex.com"), json={"name": "Y", "national_code": "010101"}) \
        .status_code == 403


def test_prontidao_por_ambiente_e_operacao(stack, world):
    _status(stack, world["a"], "PREPARING")
    r = stack.core.get(f"{C}/companies/{world['a']}/readiness", headers=world["ha"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["workspace"]["status"] == "PREPARING"
    assert [e["environment"] for e in out["environments"]] == ["production", "restricted"]
    for env in out["environments"]:
        ops = {o["operation"]: o for o in env["operations"]}
        assert set(ops) == {"consulta", "emissao", "cancelamento"}
        assert not ops["emissao"]["ready"] and not ops["cancelamento"]["ready"]
        codes = {i["code"] for i in ops["emissao"]["issues"]}
        assert {"WORKSPACE_NOT_ACTIVE", "NO_APPROVED_SERVICE_PROFILE"} <= codes
        assert all(set(i) == {"code", "message", "severity"} for i in ops["emissao"]["issues"])
    # Pendências de A não aparecem para B.
    rb = stack.core.get(f"{C}/companies/{world['b']}/readiness", headers=world["hb"]).json()
    assert rb["workspace"]["status"] == "ACTIVE"
    assert all("WORKSPACE_NOT_ACTIVE" not in {i["code"] for o in e["operations"] for i in o["issues"]}
               for e in rb["environments"])


# ---------------------------------------------------------------- um certificado ativo por workspace
def _cert_rows(st, company_id):  # noqa: ANN001, ANN202
    from nfse_api.models import Certificate

    with st.nfse_sessions() as s:
        return [(r.environment, r.fingerprint_sha256, r.is_active, r.active_slot, bool(r.encrypted_pfx),
                 r.material_purged_at is not None, r.replaced_by_fingerprint)
                for r in s.scalars(select(Certificate).where(Certificate.company_id == company_id)
                                   .order_by(Certificate.created_at, Certificate.environment))]


def test_renovacao_mantem_workspace_e_expurga_material_do_anterior(secure_stack):
    from nfse_api import cert_store

    st = secure_stack
    co = cnpj("556667770001")
    cid = st.add_company("Empresa Fictícia D", co, environment="restricted")
    v, _ = _validator(make_crl([]))
    first = _install(st, cid, co, make_cert(cnpj_value=co, crl_url=CRL_URL), v)
    st.clock.advance(minutes=1)
    second = cert_store.install(
        st.nfse_sessions, __import__("nfse_api.vault", fromlist=["Vault"]).Vault(st.nfse_settings.vault_key_b64),
        company_id=cid, cnpj=co, environments=["restricted", "production"],
        pfx=_pfx(make_cert(cnpj_value=co, crl_url=CRL_URL)), password=b"pfx-teste-1",
        now=datetime.now(timezone.utc) + timedelta(seconds=1), validator=v)
    rows = _cert_rows(st, cid)
    ativos = [r for r in rows if r[2]]
    assert {(r[0], r[1]) for r in ativos} == {("restricted", second["fingerprint_sha256"]),
                                              ("production", second["fingerprint_sha256"])}
    assert all(r[3] == f"{cid}:{r[0]}" and r[4] for r in ativos)
    antigo = [r for r in rows if r[1] == first["fingerprint_sha256"]]
    assert antigo == [("restricted", first["fingerprint_sha256"], False, None, False, True, second["fingerprint_sha256"])]
    hist = cert_store.history(st.nfse_sessions, company_id=cid)
    assert len(hist) == 3 and all("encrypted_pfx" not in h and "password" not in str(h).lower() for h in hist)
    # Certificado de OUTRA empresa não substitui a identidade do workspace; o ativo continua o mesmo.
    outro = cnpj("667778880001")
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, make_cert(cnpj_value=outro, crl_url=CRL_URL), v)
    assert e.value.code == "CERT_CNPJ_MISMATCH"
    # Vencido também é recusado sem tocar no ativo.
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, make_cert(cnpj_value=co, crl_url=CRL_URL, days=10,
                                        start=datetime.now(timezone.utc) - timedelta(days=30)), v)
    assert e.value.code == "CERT_EXPIRED_OR_NOT_YET_VALID"
    assert [r for r in _cert_rows(st, cid) if r[2]] == ativos


def test_slot_ativo_unico_no_banco(secure_stack):
    from sqlalchemy.exc import IntegrityError

    from nfse_api.models import Certificate

    st = secure_stack
    co = cnpj("778889990001")
    cid = st.add_company("Empresa Fictícia E", co, environment="restricted")
    v, _ = _validator(make_crl([]))
    _install(st, cid, co, make_cert(cnpj_value=co, crl_url=CRL_URL), v)
    with st.nfse_sessions() as s:
        r = s.scalar(select(Certificate).where(Certificate.company_id == cid, Certificate.is_active.is_(True)))
        s.add(Certificate(company_id=cid, environment="restricted", subject_cnpj=co, not_before=r.not_before,
                          not_after=r.not_after, fingerprint_sha256="f" * 64, checks={}, encrypted_pfx=b"x",
                          encrypted_password=b"x", key_version=1, is_active=True, created_at=r.created_at,
                          active_slot=f"{cid}:restricted"))
        with pytest.raises(IntegrityError):
            s.flush()


@pytest.mark.skipif(not MYSQL, reason="concorrência real exige MySQL")
def test_ativacao_concorrente_deixa_um_ativo_mysql(secure_stack):
    st = secure_stack
    co = cnpj("889990000001")
    cid = st.add_company("Empresa Fictícia F", co, environment="restricted")
    v, _ = _validator(make_crl([]))
    pares = [make_cert(cnpj_value=co, crl_url=CRL_URL) for _ in range(4)]
    barrier = threading.Barrier(4)
    out: list[str] = []

    def go(par) -> None:  # noqa: ANN001
        barrier.wait()
        try:
            out.append(_install(st, cid, co, par, v)["fingerprint_sha256"])
        except Exception as exc:  # noqa: BLE001
            out.append(getattr(exc, "code", type(exc).__name__))

    ts = [threading.Thread(target=go, args=(p,)) for p in pares]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ativos = [r for r in _cert_rows(st, cid) if r[2]]
    assert len(ativos) == 1 and ativos[0][1] in out, (out, ativos)
    assert all(r[4] is False and r[5] for r in _cert_rows(st, cid) if not r[2])  # nenhum material sobra nos inativos


def _pfx(pair) -> bytes:  # noqa: ANN001
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    key, cert = pair
    return pkcs12.serialize_key_and_certificates(b"t", key, cert, None,
                                                 serialization.BestAvailableEncryption(b"pfx-teste-1"))


# ---------------------------------------------------------------- migração preservando dados existentes
def test_migracoes_preservam_empresa_existente_e_certificados(tmp_path):
    """Base na revisão anterior (core 0004 / nfse 0003) com uma empresa real-like, vínculo e certificados →
    upgrade: o workspace herda o MESMO identificador, série "1", nenhum vínculo é alterado; o certificado ativo
    ocupa o slot único e o inativo perde o material cifrado (fica só o histórico)."""
    from alembic import command
    from sqlalchemy import create_engine, text

    from conftest import ROOT, SERVICES, Config

    def cfg(svc: str, url: str) -> Config:
        c = Config(str(ROOT / SERVICES[svc] / "alembic.ini"))
        c.set_main_option("script_location", str(ROOT / SERVICES[svc] / "migrations"))
        c.set_main_option("sqlalchemy.url", url)
        return c

    core, nfse = f"sqlite:///{tmp_path}/core.db", f"sqlite:///{tmp_path}/nfse.db"
    command.upgrade(cfg("core", core), "0004")
    command.upgrade(cfg("nfse", nfse), "0003")
    cid, uid = str(uuid.uuid4()), str(uuid.uuid4())
    now = "2026-10-08 12:00:00.000000"
    e = create_engine(core)
    with e.begin() as c:
        c.execute(text("INSERT INTO companies (id, name, cnpj, timezone, nfse_environment, is_active, created_at, "
                       "updated_at) VALUES (:i, 'Empresa Existente', :d, 'America/Sao_Paulo', 'production', 1, :n, :n)"),
                  {"i": cid, "d": cnpj("101112130001"), "n": now})
        c.execute(text("INSERT INTO users (id, email, name, password_hash, is_active, is_super_admin, created_at, "
                       "updated_at) VALUES (:u, 'x@ex.com', 'X', 'h', 1, 1, :n, :n)"), {"u": uid, "n": now})
        c.execute(text("INSERT INTO memberships (id, user_id, company_id, permissions, created_at, updated_at) VALUES "
                       "(:m, :u, :c, '[\"view\"]', :n, :n)"), {"m": str(uuid.uuid4()), "u": uid, "c": cid, "n": now})
    n = create_engine(nfse)
    with n.begin() as c:
        for active, fp in ((0, "a" * 64), (1, "b" * 64)):
            c.execute(text("INSERT INTO certificates (id, company_id, environment, subject_cnpj, not_before, not_after, "
                           "fingerprint_sha256, checks, encrypted_pfx, encrypted_password, key_version, is_active, "
                           "created_at) VALUES (:i, :c, 'production', NULL, :n, :n, :f, '{}', :b, :b, 1, :a, :n)"),
                      {"i": str(uuid.uuid4()), "c": cid, "n": now, "f": fp, "b": b"cifrado", "a": active})
    command.upgrade(cfg("core", core), "head")
    command.upgrade(cfg("nfse", nfse), "head")
    with e.connect() as c:
        assert c.execute(text("SELECT id, company_id, status FROM workspaces")).all() == [(cid, cid, "ACTIVE")]
        assert c.execute(text("SELECT dps_series FROM companies")).scalar() == "1"
        assert c.execute(text("SELECT permissions FROM memberships")).scalar() == '["view"]'
        assert c.execute(text("SELECT count(*) FROM audit_events WHERE action = 'workspace.migrated'")).scalar() == 1
    with n.connect() as c:
        rows = c.execute(text("SELECT fingerprint_sha256, is_active, active_slot, length(encrypted_pfx), "
                              "material_purged_at IS NOT NULL FROM certificates ORDER BY fingerprint_sha256")).all()
        assert rows == [("a" * 64, 0, None, 0, 1), ("b" * 64, 1, f"{cid}:production", 7, 0)]
    e.dispose()
    n.dispose()


def test_certificado_do_workspace_no_core_sem_segredos(secure_stack):
    st = secure_stack
    co = cnpj("121314150001")
    cid = st.add_company("Empresa Fictícia G", co, environment="restricted")
    uid = st.add_user("g@ex.com")
    st.grant(uid, cid, perms=("view",))
    h = st.login("g@ex.com")
    url = f"{C}/companies/{cid}/certificates"
    assert st.core.get(url, headers=h).json()["status"] == "ABSENT"
    v, _ = _validator(make_crl([]))
    _install(st, cid, co, make_cert(cnpj_value=co, crl_url=CRL_URL, days=20), v)
    out = st.core.get(url, headers=h).json()
    assert out["status"] == "EXPIRING" and out["active"]["environments"] == ["restricted"]
    assert "encrypted" not in str(out) and "pfx-teste" not in str(out)
    outro = st.add_user("h@ex.com")
    assert st.core.get(url, headers=st.login("h@ex.com")).status_code in (403, 404)
    assert outro
