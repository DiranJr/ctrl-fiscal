"""Permissão global create_workspace e criação de workspace: concessão/revogação só pelo SUPER_ADMIN (com auditoria),
revogação imediata em sessão aberta, sem escalada de privilégios, criador vira admin SÓ do novo workspace, transação
única (falha desfaz tudo), CNPJ único, nada copiado de outros workspaces. Somente dados fictícios."""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from conftest import MYSQL, cnpj
from test_admin_users import A, org  # noqa: F401 - fixture reaproveitada

C = "/api/core/v1"


def _criar(stack, h, doc: str, name: str = "Empresa Fictícia Nova", admin=None):  # noqa: ANN001, ANN202
    body = {"name": name, "company": {"cnpj": doc, "name": name + " Ltda"}}
    if admin is not None:
        body["admin"] = admin
    return stack.core.post(f"{A}/workspaces", headers=h, json=body)


def _permitir(stack, h, uid: str, enabled: bool = True):  # noqa: ANN001, ANN202
    return stack.core.put(f"{A}/users/{uid}/create-workspace", headers=h, json={"enabled": enabled})


def _contagens(stack) -> tuple:  # noqa: ANN001
    from core_api.models import Company, CredentialToken, Membership, User, Workspace

    with stack.core_sessions() as s:
        return tuple(s.scalar(select(func.count()).select_from(m)) for m in (Company, Workspace, Membership, User,
                                                                            CredentialToken))


def test_so_super_admin_concede_e_revoga_com_auditoria(stack, org):
    from core_api.models import AuditEvent

    # Ninguém além do SUPER_ADMIN concede — nem a si mesmo, nem a outros.
    assert _permitir(stack, org["h_adm"], org["adm_a"]).status_code == 403
    assert _permitir(stack, org["h_adm"], org["so_a"]).status_code == 403
    r = _permitir(stack, org["h_root"], org["adm_a"])
    assert r.status_code == 200 and r.json()["can_create_workspace"] is True and r.json()["is_super_admin"] is False
    assert stack.core.get(f"{C}/auth/me", headers=stack.login("adm.a@ex.com")).json()["can_create_workspace"] is True
    # Mesmo autorizado a criar, não concede a permissão a ninguém.
    assert _permitir(stack, stack.login("adm.a@ex.com"), org["so_a"]).status_code == 403
    assert _permitir(stack, org["h_root"], org["adm_a"], False).json()["can_create_workspace"] is False
    with stack.core_sessions() as s:
        acoes = [a.action for a in s.scalars(select(AuditEvent).where(AuditEvent.target_id == org["adm_a"])
                                             .order_by(AuditEvent.occurred_at))]
    assert "admin.create_workspace_granted" in acoes and "admin.create_workspace_revoked" in acoes
    assert stack.core.get(f"{C}/auth/me", headers=stack.login("so.a@ex.com")).json()["can_create_workspace"] is False


def test_criador_autorizado_vira_admin_so_do_novo_workspace(stack, org):
    from core_api.models import Company, Customer, Membership, ServiceProfile

    _permitir(stack, org["h_root"], org["so_a"])
    h = stack.login("so.a@ex.com")
    novo = cnpj("556667770001")
    r = _criar(stack, h, novo)
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["status"] == "PREPARING" and w["admin_user_id"] == org["so_a"] and w["credential_link"] is None
    with stack.core_sessions() as s:
        ms = {m.company_id: m.permissions for m in s.scalars(select(Membership).where(Membership.user_id == org["so_a"]))}
        c = s.get(Company, w["id"])
        assert "admin" in ms[w["id"]] and ms[org["a"]] == ["view", "prepare"]  # nada muda nos workspaces existentes
        assert c.dps_series is None and c.nfse_environment == "restricted" and not c.adn_environments
        assert s.scalar(select(func.count()).select_from(Customer).where(Customer.company_id == w["id"])) == 0
        assert s.scalar(select(func.count()).select_from(ServiceProfile).where(ServiceProfile.company_id == w["id"])) == 0
    me = stack.core.get(f"{C}/auth/me", headers=h).json()
    assert me["is_super_admin"] is False and w["id"] in me["admin_company_ids"]
    rd = stack.core.get(f"{C}/companies/{w['id']}/readiness", headers=h).json()
    codigos = {i["code"] for sec in rd["sections"] for i in sec["items"]}
    assert "WORKSPACE_NOT_ACTIVE" in codigos and "DPS_SERIES" in codigos
    # Ativar/suspender continua exclusivo do SUPER_ADMIN; o criador não indica outro admin na criação.
    assert stack.core.patch(f"{A}/workspaces/{w['id']}", headers=h, json={"status": "ACTIVE"}).status_code == 403
    assert _criar(stack, h, cnpj("556667770002"), admin={"user_id": org["so_b"]}).status_code == 403
    # Isolamento: outros usuários não enxergam o workspace criado; o criador não enxerga o de B.
    hb = stack.login("so.b@ex.com")
    assert stack.core.get(f"{C}/companies/{w['id']}/readiness", headers=hb).status_code in (403, 404)
    assert stack.core.get(f"{C}/companies/{org['b']}/readiness", headers=h).status_code in (403, 404)
    lista = stack.core.get(f"{A}/workspaces", headers=h).json()
    assert {x["id"] for x in lista} == {w["id"]}  # só o que administra


def test_revogacao_vale_na_sessao_aberta_e_preserva_vinculos(stack, org):
    _permitir(stack, org["h_root"], org["so_a"])
    h = stack.login("so.a@ex.com")  # sessão aberta antes da revogação
    w = _criar(stack, h, cnpj("667778880001")).json()
    _permitir(stack, org["h_root"], org["so_a"], False)
    r = _criar(stack, h, cnpj("667778880002"))
    assert r.status_code == 403 and r.json()["code"] == "CREATE_WORKSPACE_FORBIDDEN"
    assert stack.core.get(f"{C}/companies/{w['id']}/readiness", headers=h).status_code == 200  # vínculo mantido


def test_sem_permissao_nao_cria(stack, org):
    antes = _contagens(stack)
    for email in ("adm.a@ex.com", "so.a@ex.com", "ambos@ex.com"):
        r = _criar(stack, stack.login(email), cnpj("778889990001"))
        assert r.status_code == 403, email
    assert stack.core.post(f"{A}/workspaces", json={"name": "x", "company": {"cnpj": cnpj("778889990001"),
                                                                           "name": "x"}}).status_code == 401
    assert _contagens(stack) == antes


def test_super_admin_indica_administrador_existente_ou_novo(stack, org):
    from core_api.models import Membership, User

    r = _criar(stack, org["h_root"], cnpj("889990000001"), admin={"user_id": org["so_b"]})
    assert r.status_code == 201, r.text
    w1 = r.json()
    r = _criar(stack, org["h_root"], cnpj("889990000002"),
               admin={"new_user": {"name": "Administradora Fictícia", "email": "nova.adm@ex.com"}})
    assert r.status_code == 201, r.text
    w2 = r.json()
    assert w2["credential_link"]["purpose"] == "ACTIVATE" and "#token=" in w2["credential_link"]["path"]
    with stack.core_sessions() as s:
        nova = s.scalar(select(User).where(User.email == "nova.adm@ex.com"))
        assert nova.password_hash == "!pending" and not nova.is_super_admin and not nova.can_create_workspace
        assert s.scalar(select(Membership).where(Membership.user_id == org["so_b"], Membership.company_id == w1["id"]))
        assert s.scalar(select(Membership).where(Membership.user_id == nova.id, Membership.company_id == w2["id"]))
        # SUPER_ADMIN não ganha vínculo fiscal implícito.
        assert s.scalar(select(Membership).where(Membership.user_id == org["root"], Membership.company_id == w1["id"])) is None
    # E-mail já existente como "novo usuário": recusado e NADA é criado.
    antes = _contagens(stack)
    r = _criar(stack, org["h_root"], cnpj("889990000003"), admin={"new_user": {"name": "Nome Fictício", "email": "so.b@ex.com"}})
    assert r.status_code == 409 and r.json()["errors"][0]["field"] == "admin.new_user.email"
    assert _contagens(stack) == antes
    r = _criar(stack, org["h_root"], cnpj("889990000003"), admin={"user_id": org["so_b"], "new_user": {"name": "Nome Fictício", "email": "y@ex.com"}})
    assert r.status_code == 422


def test_cnpj_invalido_e_duplicado_com_erro_no_campo(stack, org):
    _permitir(stack, org["h_root"], org["so_a"])
    h = stack.login("so.a@ex.com")
    r = _criar(stack, h, "11222333000100")
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "company.cnpj"
    r = _criar(stack, h, cnpj("112223330001"))  # CNPJ da empresa A
    assert r.status_code == 409 and r.json()["code"] == "CNPJ_ALREADY_IN_WORKSPACE"
    assert r.json()["errors"][0]["field"] == "company.cnpj"


def test_falha_no_meio_desfaz_toda_a_transacao(stack, org, monkeypatch):
    import core_api.routes_workspaces as rw

    original = rw.audit

    def falha(s, now, action, **kw):  # noqa: ANN001, ANN003, ANN202
        if action == "workspace.created":
            raise RuntimeError("falha simulada depois de criar empresa, usuário e vínculo")
        return original(s, now, action, **kw)

    monkeypatch.setattr(rw, "audit", falha)
    antes = _contagens(stack)
    with pytest.raises(RuntimeError):
        _criar(stack, org["h_root"], cnpj("990001110001"),
               admin={"new_user": {"name": "Rollback Fictício", "email": "rollback@ex.com"}})
    assert _contagens(stack) == antes  # empresa, workspace, usuário, vínculo e link: nada ficou


@pytest.mark.skipif(not MYSQL, reason="concorrência real exige MySQL")
def test_criacao_concorrente_mesmo_cnpj_por_autorizado_e_super_admin_mysql(stack, org):
    _permitir(stack, org["h_root"], org["so_a"])
    hs = [stack.login("so.a@ex.com"), stack.login("root@ex.com")]
    doc = cnpj("101112130001")
    barreira = threading.Barrier(2)
    out: list[int] = []

    def go(h) -> None:  # noqa: ANN001
        barreira.wait()
        out.append(_criar(stack, h, doc).status_code)

    ts = [threading.Thread(target=go, args=(h,)) for h in hs]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(out) == [201, 409], out
    from core_api.models import Company, Workspace

    with stack.core_sessions() as s:
        assert s.scalar(select(func.count()).select_from(Company).where(Company.cnpj == doc)) == 1
        assert s.scalar(select(func.count()).select_from(Workspace).join(Company, Company.id == Workspace.company_id)
                        .where(Company.cnpj == doc)) == 1
