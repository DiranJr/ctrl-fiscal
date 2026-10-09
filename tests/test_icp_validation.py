"""Validação ICP-Brasil do certificado (cadeia, revogação por LCR, usos, identidade) e garantia de que o worker não
faz conexão oficial para empresa de demonstração nem para certificado inválido. Nenhum acesso à rede: a LCR e o
governo são simulados (http_get / httpx.MockTransport)."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from conftest import KEYS, AuthHeaders, Stack, _truncate, cnpj
from test_nfse_connector import TEST_CA_PEM, TEST_TRUST, _OTHER_CA, _OTHER_CA_CERT, make_cert, make_crl

CRL_URL = "http://lcr.teste.invalid/ac-teste.crl"


def _validator(crl: bytes | None, status: int = 200):  # noqa: ANN202
    from nfse_api.icp import IcpValidator

    calls: list[str] = []

    def http_get(url: str) -> httpx.Response:
        calls.append(url)
        if crl is None:
            raise httpx.ConnectError("sem rede", request=httpx.Request("GET", url))
        return httpx.Response(status, content=crl)

    v = IcpValidator(TEST_CA_PEM, b"", "online", http_get=http_get)
    return v, calls


def test_real_icp_bundle_is_official_and_loaded():
    from nfse_api.icp import IcpValidator
    import json
    from pathlib import Path

    v = IcpValidator()
    m = json.loads((Path(__file__).resolve().parents[1] / "services/nfse-api/nfse_api/icp_ancoras/manifesto.json").read_text(encoding="utf-8"))
    assert len(v.roots) == len(m["raizes"]) >= 1 and all("Raiz Brasileira" in r["cn"] for r in m["raizes"])
    assert len(v.intermediates) == m["intermediarias"]
    _, cert = make_cert()  # AC de teste NÃO é ICP-Brasil
    res = v.verify_chain(cert, [], datetime.now(timezone.utc))
    assert not res.ok and res.error


def test_revocation_good_revoked_wrong_signer_and_unavailable():
    now = datetime.now(timezone.utc)
    _, cert = make_cert(crl_url=CRL_URL)
    v, calls = _validator(make_crl([]))
    chk = v.evaluate(cert, [], now)
    assert chk["icp_brasil_chain"] is True and chk["revocation"] == "GOOD" and calls == [CRL_URL]
    v2, _ = _validator(make_crl([cert.serial_number]))
    assert v2.evaluate(cert, [], now)["revocation"] == "REVOKED"
    v3, _ = _validator(make_crl([], signer=_OTHER_CA, issuer=_OTHER_CA_CERT.subject))  # LCR de outra AC
    assert v3.evaluate(cert, [], now)["revocation"] == "UNAVAILABLE"
    v4, _ = _validator(None)  # LCR inacessível
    assert v4.evaluate(cert, [], now)["revocation"] == "UNAVAILABLE"
    v5, _ = _validator(make_crl([], next_days=-1))  # LCR vencida
    assert v5.evaluate(cert, [], now)["revocation"] == "UNAVAILABLE"
    _, nocrl = make_cert()
    v6, _ = _validator(make_crl([]))
    assert v6.evaluate(nocrl, [], now)["revocation"] == "UNAVAILABLE"


def _install(stack, company_id, company_cnpj, cert_key_pair, validator):  # noqa: ANN001, ANN202
    from nfse_api import cert_store
    from nfse_api.vault import Vault

    key, cert = cert_key_pair
    pfx = pkcs12.serialize_key_and_certificates(b"t", key, cert, None, serialization.BestAvailableEncryption(b"pfx-teste-1"))
    return cert_store.install(stack.nfse_sessions, Vault(stack.nfse_settings.vault_key_b64), company_id=company_id,
                              cnpj=company_cnpj, environments=["restricted"], pfx=pfx, password=b"pfx-teste-1",
                              now=datetime.now(timezone.utc), validator=validator)


@pytest.fixture
def secure_stack(db_urls, jwt_keys, clock, tmp_path):
    for url in db_urls.values():
        _truncate(url)
    calls: list[tuple] = []

    def forbidden(service: str) -> httpx.Client:
        def handler(req: httpx.Request) -> httpx.Response:
            calls.append((service, req.method, req.url.host, req.url.path))
            return httpx.Response(599)
        return httpx.Client(transport=httpx.MockTransport(handler))

    st = Stack(db_urls, jwt_keys, clock, tmp_path, official={
        "vault_key_b64": base64.b64encode(os.urandom(32)).decode(), "restricted_enabled": True,
        "production_distribution_enabled": True, "http_factory": forbidden,
        "trust_roots_pem": TEST_CA_PEM, "trust_intermediates_pem": b"", "revocation_mode": "online"})
    st.gov_calls = calls
    return st


def test_install_rejects_untrusted_revoked_and_unconfirmed(secure_stack):
    from nfse_api import cert_store

    st = secure_stack
    co = cnpj("123456780001")
    cid = st.add_company("Empresa", co, environment="restricted", municipality="1505536")
    v_good, _ = _validator(make_crl([]))
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, make_cert(cnpj_value=co, ca="other", crl_url=CRL_URL), v_good)
    assert e.value.code == "CERT_CHAIN_INVALID"
    pair = make_cert(cnpj_value=co, crl_url=CRL_URL)
    v_rev, _ = _validator(make_crl([pair[1].serial_number]))
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, pair, v_rev)
    assert e.value.code == "CERT_REVOKED"
    v_off, _ = _validator(None)
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, pair, v_off)
    assert e.value.code == "CERT_REVOCATION_UNCONFIRMED"
    with pytest.raises(cert_store.CertificateRejected) as e:
        _install(st, cid, co, make_cert(cnpj_value=co, client_auth=False, crl_url=CRL_URL), v_good)
    assert e.value.code in ("CERT_CHAIN_INVALID", "CERT_NO_CLIENT_AUTH")  # verificador exige clientAuth
    out = _install(st, cid, co, pair, v_good)
    assert out["accepted"] is True and out["revocation"] == "GOOD" and out["chain"] == ["AC DE TESTE (NAO ICP-BRASIL)"]


def test_readiness_rechecks_revocation_after_24h(secure_stack):
    from nfse_api.providers.base import CompanyContext
    from nfse_api.providers.official import OfficialProvider
    from nfse_api.vault import Vault

    st = secure_stack
    co = cnpj("123456780001")
    cid = st.add_company("Empresa", co, environment="restricted", municipality="1505536")
    pair = make_cert(cnpj_value=co, crl_url=CRL_URL)
    v_good, _ = _validator(make_crl([]))
    _install(st, cid, co, pair, v_good)
    ctx = CompanyContext(cid, co, "restricted")
    prov = OfficialProvider("restricted", st.nfse_sessions, Vault(st.nfse_settings.vault_key_b64), st.nfse_settings,
                            st.clock, None, v_good)
    st.clock.set(datetime.now(timezone.utc))
    assert prov.blockers(ctx, "distribution")[0] == "READY"
    v_rev, calls = _validator(make_crl([pair[1].serial_number]))
    prov.validator = v_rev
    st.clock.advance(hours=25)
    status, problems = prov.blockers(ctx, "distribution")
    assert status == "BLOCKED" and "CERT_REVOKED" in {p["code"] for p in problems} and calls == [CRL_URL]


def test_worker_makes_no_official_connection_for_demo_or_invalid_certificate(secure_stack):
    """Empresa de demonstração em restricted (mesmo com certificado de teste instalado diretamente no banco) e
    empresa com certificado sem cadeia válida: o worker não abre conexão oficial (transporte proibido não é chamado)."""
    from core_api.seed import DEMO_COMPANIES
    from nfse_api.models import Certificate
    from nfse_api.vault import Vault

    st = secure_stack
    demo_cnpj = DEMO_COMPANIES[1][1]
    demo = st.add_company("Demonstração B (LOCAL)", demo_cnpj, environment="restricted", municipality="1505536")
    bad_co = cnpj("123456780001")
    bad = st.add_company("Empresa com certificado inválido", bad_co, environment="restricted", municipality="1505536")
    # Certificado de teste gravado DIRETO no banco (contornando a instalação), como no incidente de 07/10.
    vault = Vault(st.nfse_settings.vault_key_b64)
    for cid, co in ((demo, demo_cnpj), (bad, bad_co)):
        key, cert = make_cert(cnpj_value=co, self_signed=True)
        pfx = pkcs12.serialize_key_and_certificates(b"t", key, cert, None, serialization.BestAvailableEncryption(b"x"))
        aad = f"{cid}:restricted".encode()
        with st.nfse_sessions() as s, s.begin():
            s.add(Certificate(company_id=cid, environment="restricted", subject_cnpj=co, not_before=cert.not_valid_before_utc,
                              not_after=cert.not_valid_after_utc, fingerprint_sha256="0" * 64,
                              checks={"version3": True, "not_ca": True, "client_auth": True, "digital_signature": True,
                                      "non_repudiation": True, "not_self_signed": True},
                              encrypted_pfx=vault.encrypt(pfx, aad), encrypted_password=vault.encrypt(b"x", aad),
                              key_version=1, is_active=True, created_at=datetime.now(timezone.utc)))
    w = st.worker()
    w.refresh_companies(force=True)
    for _ in range(5):
        w.run_sync()
    assert st.gov_calls == []  # nenhuma conexão oficial
    from ctrl_fiscal.models import SyncSchedule
    from sqlalchemy import select

    with st.fiscal_sessions() as s:
        sch = {(x.company_id, x.environment): x for x in s.scalars(select(SyncSchedule))}
    assert sch[(demo, "restricted")].enabled is False and sch[(demo, "restricted")].last_error_code == "DEMO_COMPANY_OFFICIAL_BLOCKED"
    assert sch[(bad, "restricted")].status == "BLOCKED" and sch[(bad, "restricted")].last_error_code == "CERT_CHAIN_INVALID"
    # Emissão: empresa de demonstração não agenda lote oficial.
    uid = st.add_user("demo@ex.com")
    st.grant(uid, demo)
    h = AuthHeaders(st, "demo@ex.com")
    cust = st.core.post(f"/api/core/v1/companies/{demo}/customers", headers=h, json={
        "name": "Cliente", "document_type": "CNPJ", "document": cnpj("445556660001")}).json()
    d = st.fiscal.post(f"/api/fiscal/v1/companies/{demo}/drafts", headers=h, json={
        "customer_id": cust["id"], "competence": "2026-10", "service_date": "2026-10-01", "amount": "10.00",
        "service": {"national_code": "010101", "description": "x", "location_code": "1505536"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "approx_tax": {"mode": "NOT_INFORMED"}}}).json()
    r = st.fiscal.post(f"/api/fiscal/v1/companies/{demo}/batches", headers={**h, "Idempotency-Key": "demo-1"}, json={
        "drafts": [{"id": d["id"], "expected_version": d["version"]}],
        "scheduled_at": (st.clock.now() + timedelta(hours=1)).isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "DEMO_COMPANY_OFFICIAL_BLOCKED"
    # Core: empresa de demonstração não recebe certificado nem leitura ADN oficial.
    key, cert = make_cert(cnpj_value=demo_cnpj)
    pfx = pkcs12.serialize_key_and_certificates(b"t", key, cert, None, serialization.BestAvailableEncryption(b"pfx-teste-1"))
    up = st.core.post(f"/api/core/v1/companies/{demo}/certificate", headers=h,
                      files={"file": ("e.pfx", pfx, "application/x-pkcs12")}, data={"password": "pfx-teste-1"})
    assert up.status_code == 422 and up.json()["code"] == "DEMO_COMPANY_NO_CERTIFICATE"
    pp = st.core.patch(f"/api/core/v1/companies/{demo}/fiscal-profile", headers=h, json={"adn_environments": ["production"]})
    assert pp.status_code == 422 and pp.json()["code"] == "DEMO_COMPANY_OFFICIAL_BLOCKED"
    assert st.gov_calls == []
