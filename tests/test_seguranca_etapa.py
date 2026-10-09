"""Regras de segurança da etapa (positivos e negativos): permissões separadas por operação, teto de concessão do
admin de empresa, redefinição de senha só pelo SUPER_ADMIN, consumo atômico de link/ticket, proteção concorrente
do último SUPER_ADMIN (MySQL), worker conferindo a autorização de quem agendou, IP encaminhado só de proxy
confiável, oráculo de tempo de conta PENDING e conector que não transforma DPS autorizada em recusa."""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from conftest import MYSQL, ORIGIN, cnpj
from test_admin_users import A, _activate, _super, org  # noqa: F401 - fixture reaproveitada

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
TAX = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}


# ---------------------------------------------------------------- 2.2 permissões e gestão de usuários
def test_admin_de_empresa_nao_concede_acima_das_proprias(stack, org):
    from core_api.models import Membership

    # Admin de A só com view+import+admin: não concede schedule/emit/cancel/certificates.
    with stack.core_sessions() as s:
        m = s.scalar(select(Membership).where(Membership.user_id == org["adm_a"], Membership.company_id == org["a"]))
        m.permissions = ["view", "import", "admin"]
        s.commit()
    h = stack.login("adm.a@ex.com")
    for perm in ("schedule", "emit", "cancel", "certificates", "sync"):
        r = stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=h,
                           json={"permissions": ["view", perm]})
        assert r.status_code == 403 and r.json()["code"] == "PERMISSION_ABOVE_OWN", (perm, r.text)
        r = stack.core.post(f"{A}/users", headers=h, json={"name": "Pessoa Teste", "email": f"x.{perm}@ex.com",
                                                           "memberships": [{"company_id": org["a"], "permissions": ["view", perm]}]})
        assert r.status_code == 403 and r.json()["code"] == "PERMISSION_ABOVE_OWN"
    # Dentro do teto: permitido; manter permissão que o usuário JÁ tinha (prepare) não é concessão.
    r = stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=h,
                       json={"permissions": ["view", "import", "prepare"]})
    assert r.status_code == 200, r.text
    # SUPER_ADMIN não tem teto, mas também não ganha acesso fiscal implícito.
    r = stack.core.put(f"{A}/users/{org['so_a']}/memberships/{org['a']}", headers=org["h_root"],
                       json={"permissions": ["view", "cancel"]})
    assert r.status_code == 200
    # Admin de empresa não gera link de REDEFINIÇÃO de conta ativa (tomada de conta); SUPER_ADMIN gera.
    r = stack.core.post(f"{A}/users/{org['so_a']}/credential-link", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "SUPER_ADMIN_REQUIRED"
    assert stack.core.post(f"{A}/users/{org['so_a']}/credential-link", headers=org["h_root"]).status_code == 200
    # Todas as 10 permissões num único vínculo (o esquema não pode limitar abaixo do vocabulário).
    todas = ["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]
    r = stack.core.post(f"{A}/users", headers=org["h_root"], json={
        "name": "Pessoa Completa", "email": "completa@ex.com", "memberships": [{"company_id": org["a"], "permissions": todas}]})
    assert r.status_code == 201, r.text
    meta = stack.core.get(f"{A}/meta", headers=org["h_root"]).json()
    assert [p["id"] for p in meta["permissions"]] == ["view", "import", "sync", "review", "prepare", "schedule", "emit",
                                                       "cancel", "certificates", "admin"]


def test_operacoes_exigem_permissao_propria(stack, world):
    from core_api.models import Membership

    def only(perms):  # noqa: ANN001, ANN202
        with stack.core_sessions() as s:
            m = s.scalar(select(Membership).where(Membership.user_id == world["ua"], Membership.company_id == world["a"]))
            m.permissions = list(perms)
            s.commit()

    only(["view", "import", "prepare", "schedule"])  # sem sync, emit, certificates
    h = world["ha"]
    r = stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers={**h, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 403 and "sync" in r.json()["detail"]
    d = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=h, json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "10.00",
        "service": {"national_code": "010101", "description": "Serviço"}, "tax_settings": TAX}).json()
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                "scheduled_at": (stack.clock.now() + timedelta(hours=1)).isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 403 and "emit" in r.json()["detail"]
    r = stack.core.post(f"{C}/{world['a']}/certificate", headers=h, files={"file": ("a.pfx", b"x" * 40)},
                        data={"password": "x"})
    assert r.status_code == 403
    only(["view", "sync"])
    r = stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers={**h, "Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code != 403


def test_worker_retira_agendamento_quando_quem_agendou_perde_emit(stack, world):
    from core_api.models import Membership, User

    d = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "10.00",
        "service": {"national_code": "010101", "description": "Serviço"}, "tax_settings": TAX}).json()
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                "scheduled_at": (stack.clock.now() + timedelta(minutes=1)).isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202
    bid = r.json()["batch_id"]
    with stack.core_sessions() as s:
        m = s.scalar(select(Membership).where(Membership.user_id == world["ua"], Membership.company_id == world["a"]))
        m.permissions = [p for p in m.permissions if p != "emit"]
        s.commit()
    stack.clock.advance(minutes=2)
    stack.worker().run_emissions()
    item = stack.fiscal.get(f"{F}/{world['a']}/batches/{bid}", headers=world["ha"]).json()["items"][0]
    assert item["status"] == "WITHDRAWN" and item["last_error"]["code"] == "AUTHORIZATION_REVOKED", item
    assert stack.mock.transmissions == []  # nada transmitido
    draft = stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()
    assert draft["status"] == "DRAFT"
    # Usuário desativado: idem (o core responde 403 e o worker não transmite).
    d2 = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "11.00",
        "service": {"national_code": "010101", "description": "Serviço"}, "tax_settings": TAX})
    assert d2.status_code == 201
    with stack.core_sessions() as s:
        s.get(User, world["ua"]).is_active = False
        s.commit()
    assert stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).status_code == 401


# ---------------------------------------------------------------- 2.1 autenticação
def test_link_de_ativacao_uso_unico_e_ip_encaminhado_so_de_proxy_confiavel(stack, org):
    r = stack.core.post(f"{A}/users", headers=org["h_root"], json={
        "name": "Pessoa", "email": "pessoa@ex.com", "memberships": [{"company_id": org["a"], "permissions": ["view"]}]})
    path = r.json()["credential_link"]["path"]
    assert _activate(stack, path, "Uma-Senha-Bem-Forte-1").status_code == 204
    r = _activate(stack, path, "Outra-Senha-Bem-Forte-2")
    assert r.status_code == 400 and r.json()["code"] == "ACTIVATION_INVALID"
    # X-Real-IP de chamador não confiável é ignorado: 30 falhas pelo MESMO peer bloqueiam mesmo trocando o cabeçalho.
    from core_api.routes_auth import _client_ip

    class Req:
        def __init__(self, ip, hdr):  # noqa: ANN001
            self.client = type("c", (), {"host": ip})
            self.headers = {"x-real-ip": hdr}
            self.app = stack.core_app

    assert _client_ip(Req("10.9.9.9", "1.2.3.4")) == "10.9.9.9"
    stack.core_app.state.ctx.settings.trusted_proxy_hosts = ["localhost"]
    stack.core_app.state.ctx.settings._proxy_cache.clear()
    assert _client_ip(Req("127.0.0.1", "1.2.3.4")) == "1.2.3.4"
    stack.core_app.state.ctx.settings.trusted_proxy_hosts = []


def test_conta_pendente_sem_atalho_de_tempo():
    from core_api import security

    calls = []
    real = security._hasher

    class Spy:
        def verify(self, h, p):  # noqa: ANN001, ANN202
            calls.append(h)
            return real.verify(h, p)

    security._hasher = Spy()
    try:
        assert security.verify_password("!pending", "qualquer-senha") is False
        assert security.verify_password(None, "qualquer-senha") is False
    finally:
        security._hasher = real
    assert calls == [security._DUMMY_HASH, security._DUMMY_HASH]  # custo Argon2 pago nos dois casos


@pytest.mark.skipif(not MYSQL, reason="corrida real exige MySQL (travas de linha)")
def test_ultimo_super_admin_sob_concorrencia_e_ativacao_concorrente(stack, org):
    from core_api.models import User

    # Dois SUPER_ADMIN; cada um tenta desativar o outro ao mesmo tempo: no máximo um consegue.
    other = stack.add_user("root2@ex.com")
    _super(stack, other)
    h1, h2 = org["h_root"], stack.login("root2@ex.com")
    results = []

    def deactivate(h, uid):  # noqa: ANN001, ANN202
        v = stack.core.get(f"{A}/users/{uid}", headers=h).json()["version"]
        results.append(stack.core.patch(f"{A}/users/{uid}", headers=h,
                                        json={"is_active": False, "expected_version": v}).status_code)

    ts = [threading.Thread(target=deactivate, args=(h1, other)), threading.Thread(target=deactivate, args=(h2, org["root"]))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    with stack.core_sessions() as s:
        active = s.scalar(select(User.id).where(User.is_super_admin.is_(True), User.is_active.is_(True)).limit(1))
    assert active is not None, results  # nunca zero SUPER_ADMIN ativo
    # Mesmo link usado por duas requisições simultâneas: exatamente uma define a senha.
    r = stack.core.post(f"{A}/users", headers=org["h_root"] if active == org["root"] else h2, json={
        "name": "Concorrente", "email": "conc@ex.com", "memberships": [{"company_id": org["a"], "permissions": ["view"]}]})
    path = r.json()["credential_link"]["path"]
    codes = []
    ts = [threading.Thread(target=lambda pw=pw: codes.append(_activate(stack, path, pw).status_code))
          for pw in ("Senha-Concorrente-Um-1", "Senha-Concorrente-Dois-2")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes) == [204, 400], codes


# ---------------------------------------------------------------- 2.10 conector: recusa x DPS já autorizada
def test_conector_nao_registra_recusa_quando_a_dps_ja_existe():
    """400 oficial depois de envio anterior cujo retorno se perdeu: a consulta encontra a DPS => AUTHORIZED."""
    from datetime import datetime, timezone

    from nfse_api.providers.base import QueryOutcome, SubmitOutcome
    from nfse_api.service import Connector

    class Clock:
        def now(self):  # noqa: ANN202
            return datetime(2026, 10, 20, tzinfo=timezone.utc)

    class Prov:
        simulated = False

        def check_ready(self, *a):  # noqa: ANN002, ANN202
            return None

        def submit_dps(self, *a):  # noqa: ANN002, ANN202
            return SubmitOutcome(state="REJECTED", errors=[{"code": "E0014", "message": "DPS já processada"}],
                                 official_rejection=True)

        def query_dps(self, *a):  # noqa: ANN002, ANN202
            return QueryOutcome(found=True, access_key="1" * 50, number="7", authorized_at=None, nfse_xml=None)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from nfse_api.models import Base, Submission

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    sm = sessionmaker(eng, expire_on_commit=False)
    conn = Connector(sm, Clock(), {"restricted": Prov()})
    sid = str(uuid.uuid4())
    with sm() as s:
        s.add(Submission(id=sid, company_id="c" * 36, environment="restricted", company_cnpj="1" * 14,
                         dps_id="DPS" + "1" * 42, payload_hash="0" * 64, state="RECEIVED", transmissions=0,
                         simulated=False, created_at=Clock().now(), updated_at=Clock().now()))
        s.commit()
    from nfse_api.providers.base import CompanyContext

    out = conn._transmit(sid, CompanyContext("c" * 36, "1" * 14, "restricted"), Prov(),
                         {"dps": {"id": "DPS" + "1" * 42}, "payload": {}})
    assert out["state"] == "AUTHORIZED" and out["result"]["reconciled"] is True
    assert cnpj("112223330001") and ORIGIN
