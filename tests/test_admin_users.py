"""SUPER_ADMIN e gestão de usuários: escopos (global × admin de empresa), isolamento entre empresas, tentativas de
escalada, revogação de acessos já abertos, proteção do último SUPER_ADMIN, ativação de uso único e auditoria sem
segredos. Somente fixtures em banco isolado (conftest)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import ORIGIN, cnpj

A = "/api/core/v1/admin"
PW = "senha-forte-123"


def _super(stack, uid: str) -> None:  # noqa: ANN001
    from core_api.models import User

    with stack.core_sessions() as s:
        s.get(User, uid).is_super_admin = True
        s.commit()


@pytest.fixture
def org(stack):  # noqa: ANN001, ANN201
    ca = stack.add_company("Empresa A", cnpj("112223330001"))
    cb = stack.add_company("Empresa B", cnpj("998887770001"))
    root = stack.add_user("root@ex.com")
    _super(stack, root)
    adm_a = stack.add_user("adm.a@ex.com")
    stack.grant(adm_a, ca)  # todas as permissões, inclusive admin
    so_a = stack.add_user("so.a@ex.com")
    stack.grant(so_a, ca, perms=("view", "prepare"))
    so_b = stack.add_user("so.b@ex.com")
    stack.grant(so_b, cb, perms=("view",))
    ambos = stack.add_user("ambos@ex.com")
    stack.grant(ambos, ca, perms=("view",))
    stack.grant(ambos, cb, perms=("view",))
    return {"a": ca, "b": cb, "root": root, "adm_a": adm_a, "so_a": so_a, "so_b": so_b, "ambos": ambos,
            "h_root": stack.login("root@ex.com"), "h_adm": stack.login("adm.a@ex.com")}


def _activate(stack, path: str, password: str):  # noqa: ANN001, ANN202
    token = path.split("#token=", 1)[1]
    return stack.core.post("/api/core/v1/auth/activate", json={"token": token, "password": password}, headers={"Origin": ORIGIN})


def test_super_admin_global_management_without_fiscal_access(stack, org):
    me = stack.core.get("/api/core/v1/auth/me", headers=org["h_root"]).json()
    assert me["is_super_admin"] is True and me["companies"] == []  # gestão global ≠ acesso a empresas
    r = stack.fiscal.get(f"/api/fiscal/v1/companies/{org['a']}/documents", headers=org["h_root"])
    assert r.status_code == 403  # SUPER_ADMIN não lê documentos sem vínculo
    users = stack.core.get(f"{A}/users", headers=org["h_root"]).json()
    assert users["total"] == 5
    # cria usuário com vínculo; nasce sem senha (PENDING), recebe link de uso único no fragmento
    r = stack.core.post(f"{A}/users", headers=org["h_root"], json={
        "name": "Nova Pessoa", "email": "nova@ex.com", "memberships": [{"company_id": org["b"], "permissions": ["view", "review"]}]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["status"] == "PENDING" and body["credential_link"]["path"].startswith("/ativar#token=")
    login = stack.core.post("/api/core/v1/auth/login", json={"email": "nova@ex.com", "password": "qualquer-coisa-1"},
                            headers={"Origin": ORIGIN})
    assert login.status_code == 401  # sem senha não autentica
    assert _activate(stack, body["credential_link"]["path"], "curta").status_code == 422
    assert _activate(stack, body["credential_link"]["path"], "Senha-Nova-Forte-2026").status_code == 204
    assert _activate(stack, body["credential_link"]["path"], "Outra-Senha-Forte-2026").status_code == 400  # uso único
    h_nova = stack.login("nova@ex.com", "Senha-Nova-Forte-2026")
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{org['b']}/documents", headers=h_nova).status_code == 200
    # redefinição: link expira em 24 h
    link = stack.core.post(f"{A}/users/{body['user']['id']}/credential-link", headers=org["h_root"]).json()
    assert link["purpose"] == "RESET"
    stack.clock.advance(hours=25)
    assert _activate(stack, link["path"], "Senha-Expirada-2026").status_code == 400


def test_company_admin_scope_and_escalation_attempts(stack, org):
    h = org["h_adm"]
    seen = {u["email"]: u for u in stack.core.get(f"{A}/users", headers=h).json()["items"]}
    assert set(seen) == {"adm.a@ex.com", "so.a@ex.com", "ambos@ex.com"}  # só quem tem vínculo em A
    amb = seen["ambos@ex.com"]
    assert [m["company_id"] for m in amb["memberships"]] == [org["a"]] and amb["other_companies"] == 1  # B não vaza
    assert amb["can_manage_identity"] is False
    assert stack.core.get(f"{A}/users/{org['so_b']}", headers=h).status_code == 404
    assert stack.core.get(f"{A}/users", headers=h, params={"company_id": org["b"]}).status_code == 403
    # escaladas
    assert stack.core.put(f"{A}/users/{org['so_a']}/super-admin", headers=h, json={"enabled": True}).status_code == 403
    assert stack.core.patch(f"{A}/users/{org['so_a']}", headers=h,
                            json={"expected_version": 1, "is_super_admin": True}).status_code == 422
    assert stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['b']}", headers=h,
                          json={"permissions": ["view"]}).status_code == 403
    assert stack.core.put(f"{A}/users/{org['so_b']}/memberships/{org['a']}", headers=h,
                          json={"permissions": ["view"]}).status_code == 404  # não enxerga usuário de outra empresa
    assert stack.core.post(f"{A}/users", headers=h, json={"name": "X Y", "email": "x@ex.com",
                           "memberships": [{"company_id": org["b"], "permissions": ["view"]}]}).status_code == 403
    assert stack.core.post(f"{A}/users", headers=h, json={"name": "X Y", "email": "x@ex.com"}).status_code == 422
    assert stack.core.put(f"{A}/users/{org['adm_a']}/memberships/{org['a']}", headers=h,
                          json={"permissions": ["view"]}).status_code == 409  # não mexe no próprio vínculo
    assert stack.core.patch(f"{A}/users/{org['ambos']}", headers=h,
                            json={"expected_version": 1, "name": "Outro"}).status_code == 403  # vínculo fora do escopo
    assert stack.core.post(f"{A}/users/{org['ambos']}/credential-link", headers=h).status_code == 403
    # dentro do escopo funciona
    r = stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=h, json={"permissions": ["view", "review"]})
    assert r.status_code == 200 and r.json()["memberships"][0]["permissions"] == ["view", "review"]
    r = stack.core.patch(f"{A}/users/{org['so_a']}", headers=h, json={"expected_version": 1, "name": "Só A"})
    assert r.status_code == 200 and r.json()["name"] == "Só A"
    assert stack.core.patch(f"{A}/users/{org['so_a']}", headers=h, json={"expected_version": 1, "name": "Xy"}).status_code == 409
    # usuário sem papel de administração não acessa a área
    h_so = stack.login("so.a@ex.com")
    assert stack.core.get(f"{A}/users", headers=h_so).status_code == 403


def test_reduction_deactivation_and_revocation_invalidate_open_sessions(stack, org):
    h_user = stack.login("so.a@ex.com")
    docs = f"/api/fiscal/v1/companies/{org['a']}/documents"
    assert stack.fiscal.get(docs, headers=h_user).status_code == 200
    # redução de permissões revoga as sessões abertas do afetado
    r = stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=org["h_adm"], json={"permissions": ["view"]})
    assert r.status_code == 200
    assert stack.core.get("/api/core/v1/auth/me", headers=h_user).status_code == 401
    assert stack.fiscal.get(docs, headers=h_user).status_code == 401
    # remoção do vínculo: novo login não enxerga mais a empresa
    h_user = stack.login("so.a@ex.com")
    assert stack.core.delete(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=org["h_adm"]).status_code == 200
    h_user2 = stack.login("so.a@ex.com")
    assert stack.fiscal.get(docs, headers=h_user2).status_code == 403
    # desativação: sessão aberta cai e login é recusado
    h_b = stack.login("so.b@ex.com")
    u = stack.core.get(f"{A}/users/{org['so_b']}", headers=org["h_root"]).json()
    r = stack.core.patch(f"{A}/users/{org['so_b']}", headers=org["h_root"], json={"expected_version": u["version"], "is_active": False})
    assert r.status_code == 200 and r.json()["status"] == "INACTIVE"
    assert stack.fiscal.get(f"/api/fiscal/v1/companies/{org['b']}/documents", headers=h_b).status_code == 401
    assert stack.core.post("/api/core/v1/auth/login", json={"email": "so.b@ex.com", "password": PW},
                           headers={"Origin": ORIGIN}).status_code == 401
    # revogação explícita
    h_amb = stack.login("ambos@ex.com")
    r = stack.core.post(f"{A}/users/{org['ambos']}/revoke-sessions", headers=org["h_root"])
    assert r.status_code == 200 and r.json()["active_sessions"] == 0
    assert stack.core.get("/api/core/v1/auth/me", headers=h_amb).status_code == 401


def test_last_super_admin_protection(stack, org):
    h = org["h_root"]
    me = stack.core.get(f"{A}/users/{org['root']}", headers=h).json()
    assert stack.core.patch(f"{A}/users/{org['root']}", headers=h,
                            json={"expected_version": me["version"], "is_active": False}).json()["code"] == "SELF_DEACTIVATION"
    r = stack.core.put(f"{A}/users/{org['root']}/super-admin", headers=h, json={"enabled": False})
    assert r.status_code == 409 and r.json()["code"] == "LAST_SUPER_ADMIN"
    # segundo super admin: conta pendente não pode receber o papel
    novo = stack.core.post(f"{A}/users", headers=h, json={"name": "Pendente", "email": "p@ex.com"}).json()["user"]
    assert stack.core.put(f"{A}/users/{novo['id']}/super-admin", headers=h, json={"enabled": True}).json()["code"] == "USER_NOT_ACTIVE"
    assert stack.core.put(f"{A}/users/{org['adm_a']}/super-admin", headers=h, json={"enabled": True}).status_code == 200
    # o segundo não pode ser desativado enquanto for o único restante após remover o primeiro
    h2 = stack.login("adm.a@ex.com")
    assert stack.core.put(f"{A}/users/{org['root']}/super-admin", headers=h2, json={"enabled": False}).status_code == 200
    v = stack.core.get(f"{A}/users/{org['adm_a']}", headers=h2).json()["version"]
    assert stack.core.put(f"{A}/users/{org['adm_a']}/super-admin", headers=h2, json={"enabled": False}).json()["code"] == "LAST_SUPER_ADMIN"
    assert stack.core.patch(f"{A}/users/{org['adm_a']}", headers=h2, json={"expected_version": v, "is_active": False}).json()["code"] == "SELF_DEACTIVATION"


def test_bootstrap_cli_and_audit_without_secrets(stack, org, db_urls):
    from core_api.models import AuditEvent
    from core_api.super_admin import grant

    assert grant(db_urls["core"], "nao-existe@ex.com") == "NOT_FOUND"
    assert grant(db_urls["core"], "so.b@ex.com") == "GRANTED"
    assert grant(db_urls["core"], "so.b@ex.com") == "ALREADY"
    r = stack.core.post(f"{A}/users", headers=org["h_root"], json={"name": "Aud Teste", "email": "aud@ex.com",
                        "memberships": [{"company_id": org["a"], "permissions": ["view"]}]}).json()
    token = r["credential_link"]["path"].split("#token=")[1]
    assert _activate(stack, r["credential_link"]["path"], "Senha-Aud-Forte-2026").status_code == 204
    stack.core.put(f"{A}/users/{r['user']['id']}/memberships/{org['b']}", headers=org["h_root"], json={"permissions": ["view"]})
    stack.core.post(f"{A}/users/{r['user']['id']}/revoke-sessions", headers=org["h_root"])
    with stack.core_sessions() as s:
        rows = s.query(AuditEvent).filter(AuditEvent.target_id == r["user"]["id"]).all()
        actions = {e.action for e in rows}
        blob = " ".join(str(e.details) + str(e.note) for e in rows)
    assert {"admin.user_created", "auth.password_set", "admin.membership_set", "admin.sessions_revoked"} <= actions
    assert token not in blob and "Senha-Aud-Forte-2026" not in blob and "aud@ex.com" not in blob
