"""Conector oficial: assinatura (perfis 1.00/1.01), certificado, cofre, transporte
(allowlist/TLS/limites) e bloqueios de restricted/production. Sem rede real.
Fluxos oficiais completos (Sefin/ADN simulados): test_official_integration.py."""
from __future__ import annotations

import re
import ssl
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from conftest import KEYS, cnpj

CNPJ = cnpj("112223330001")


def _make_ca(cn: str):  # noqa: ANN202
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=400))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


# AC de TESTE (âncora só em APP_ENV=test): o conector exige cadeia até raiz confiável.
_TEST_CA, _TEST_CA_CERT = _make_ca("AC DE TESTE (NAO ICP-BRASIL)")
_OTHER_CA, _OTHER_CA_CERT = _make_ca("AC DESCONHECIDA")
TEST_CA_PEM = _TEST_CA_CERT.public_bytes(serialization.Encoding.PEM)
TEST_TRUST = {"trust_roots_pem": TEST_CA_PEM, "trust_intermediates_pem": b"", "revocation_mode": "test-skip"}


def make_cert(cnpj_value: str = CNPJ, days: int = 365, client_auth: bool = True, start: datetime | None = None,
              self_signed: bool = False, ca: str = "test", crl_url: str | None = None):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    start = start or datetime.now(timezone.utc) - timedelta(days=1)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EMPRESA TESTE (CERTIFICADO DE TESTE)")])
    ca_key, ca_cert = (_TEST_CA, _TEST_CA_CERT) if ca == "test" else (_OTHER_CA, _OTHER_CA_CERT)
    issuer, signer = (name, key) if self_signed else (ca_cert.subject, ca_key)
    other = x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.3"), b"\x04\x0e" + cnpj_value.encode())
    eku = [ExtendedKeyUsageOID.CLIENT_AUTH] if client_auth else [ExtendedKeyUsageOID.EMAIL_PROTECTION]
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(start + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([other]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, True, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage(eku), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if not self_signed:
        cert = cert.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    if crl_url:
        cert = cert.add_extension(x509.CRLDistributionPoints([x509.DistributionPoint(
            [x509.UniformResourceIdentifier(crl_url)], None, None, None)]), critical=False)
    return key, cert.sign(signer, hashes.SHA256())


def make_crl(revoked_serials: list[int], signer=None, issuer=None, next_days: int = 7) -> bytes:  # noqa: ANN001
    now = datetime.now(timezone.utc)
    nxt = now + timedelta(days=next_days)
    b = (x509.CertificateRevocationListBuilder().issuer_name(issuer or _TEST_CA_CERT.subject)
         .last_update(min(now - timedelta(hours=1), nxt - timedelta(days=1))).next_update(nxt))
    for serial in revoked_serials:
        b = b.add_revoked_certificate(x509.RevokedCertificateBuilder().serial_number(serial)
                                      .revocation_date(now - timedelta(hours=2)).build())
    return b.sign(signer or _TEST_CA, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


DPS_100 = (
    '<DPS xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00"><infDPS Id="DPS'
    + "3550308" + "2" + CNPJ + "00001" + "1".zfill(15) + '"><tpAmb>2</tpAmb><serie>1</serie><nDPS>1</nDPS></infDPS></DPS>'
).encode()


def test_signer_enveloped_profile_and_tamper_detection():
    from nfse_api.signer import SignatureProfileUnconfirmed, sign_enveloped, verify_enveloped

    key, cert = make_cert()
    signed = sign_enveloped(DPS_100, key, cert.public_bytes(serialization.Encoding.DER))
    text = signed.decode()
    assert '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in text  # sem prefixo (E1228)
    assert 'Reference URI="#DPS' in text and "rsa-sha1" in text and "REC-xml-c14n-20010315" in text
    assert text.index("</infDPS>") < text.index("<Signature")  # irmã após infDPS
    assert verify_enveloped(signed)
    assert not verify_enveloped(signed.replace(b"<nDPS>1</nDPS>", b"<nDPS>2</nDPS>"))
    assert verify_enveloped(sign_enveloped(DPS_100.replace(b'versao="1.00"', b'versao="1.01"'), key,
                                           cert.public_bytes(serialization.Encoding.DER)))
    with pytest.raises(SignatureProfileUnconfirmed):  # versão de leiaute não verificada
        sign_enveloped(DPS_100.replace(b'versao="1.00"', b'versao="9.99"'), key, b"")


def test_certificate_rules_cnpj_validity_and_usage():
    from nfse_api.certs import inspect_certificate

    from nfse_api.icp import IcpValidator

    _, cert = make_cert()
    rep = inspect_certificate(cert)
    assert rep.subject_cnpj == CNPJ and rep.checks["client_auth"] and rep.checks["non_repudiation"]
    ok, problems = rep.ok_for(CNPJ, datetime.now(timezone.utc))
    assert not ok and [p["code"] for p in problems] == ["CERT_CHAIN_INVALID"]  # CNPJ coincidir NÃO basta
    rep.checks.update(IcpValidator(TEST_CA_PEM, b"", "test-skip").evaluate(cert, [], datetime.now(timezone.utc)))
    ok, problems = rep.ok_for(CNPJ, datetime.now(timezone.utc))
    assert ok and problems == []
    ok, problems = rep.ok_for(cnpj("998887770001"), datetime.now(timezone.utc))
    assert not ok and "CERT_CNPJ_MISMATCH" in {p["code"] for p in problems}
    _, old = make_cert(days=10, start=datetime.now(timezone.utc) - timedelta(days=30))
    assert "CERT_EXPIRED_OR_NOT_YET_VALID" in {p["code"] for p in inspect_certificate(old).ok_for(CNPJ, datetime.now(timezone.utc))[1]}
    _, own = make_cert(self_signed=True)
    assert "CERT_SELF_SIGNED" in {p["code"] for p in inspect_certificate(own).ok_for(CNPJ, datetime.now(timezone.utc))[1]}
    _, no_mtls = make_cert(client_auth=False)
    assert "CERT_NO_CLIENT_AUTH" in {p["code"] for p in inspect_certificate(no_mtls).ok_for(CNPJ, datetime.now(timezone.utc))[1]}


def test_vault_aead_binds_company_and_environment():
    import base64
    import os

    from nfse_api.vault import Vault

    v = Vault(base64.b64encode(os.urandom(32)).decode())
    blob = v.encrypt(b"segredo-pfx", b"empresa:restricted")
    assert b"segredo-pfx" not in blob and v.decrypt(blob, b"empresa:restricted") == b"segredo-pfx"
    with pytest.raises(Exception):
        v.decrypt(blob, b"outra:restricted")
    assert not Vault(None).available


def test_installed_certificate_gates_and_never_exposed(db_urls, stack, world, monkeypatch):
    import base64
    import os

    from nfse_api import cert_import
    from nfse_api.settings import NfseSettings

    key, cert = make_cert(cnpj_value=world["b_cnpj"])
    pfx = pkcs12.serialize_key_and_certificates(b"teste", key, cert, None,
                                                serialization.BestAvailableEncryption(b"senha-de-teste"))
    settings = NfseSettings(database_url=db_urls["nfse"], vault_key_b64=base64.b64encode(os.urandom(32)).decode(),
                            app_env="test", internal_clients={"x": ("k" * 40, set())}, **TEST_TRUST)
    out = cert_import.install(settings, world["b"], "restricted", world["b_cnpj"], pfx, b"senha-de-teste")
    assert out["accepted_locally"] is True
    # Com chave e certificado: BLOCKED enquanto a flag de Produção Restrita estiver desligada; READY com ela.
    from core_api.models import Company

    with stack.core_sessions() as s, s.begin():
        s.get(Company, world["b"]).nfse_environment = "restricted"
    from nfse_api.providers.official import OfficialProvider
    from nfse_api.providers.base import CompanyContext
    from nfse_api.vault import Vault

    prov = OfficialProvider("restricted", stack.nfse_sessions, Vault(settings.vault_key_b64), settings, stack.clock)
    status, problems = prov.blockers(CompanyContext(world["b"], world["b_cnpj"], "restricted"))
    codes = {p["code"] for p in problems}
    assert status == "BLOCKED" and "RESTRICTED_DISABLED" in codes and "OFFICIAL_CONTRACT_UNCONFIRMED" not in codes
    settings.restricted_enabled = True
    status, problems = prov.blockers(CompanyContext(world["b"], world["b_cnpj"], "restricted"))
    assert status == "READY" and problems == []
    prod = OfficialProvider("production", stack.nfse_sessions, Vault(settings.vault_key_b64), settings, stack.clock)
    settings.production_enabled = True
    status, problems = prod.blockers(CompanyContext(world["b"], world["b_cnpj"], "production"))
    assert status in ("BLOCKED", "NOT_CONFIGURED") and "PRODUCTION_EMISSION_BLOCKED" in {p["code"] for p in problems}
    r = stack.core.get(f"/api/core/v1/companies/{world['b']}/integration-status", headers=world["hb"])
    assert r.status_code == 200 and "senha" not in r.text and "BEGIN" not in r.text and "pfx" not in r.text.lower()
    # O app do harness padrão não tem chave de cofre nem flag: envio restricted não é transmitido (ledger BLOCKED).
    from ctrl_common.hashing import canonical_hash

    h = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + KEYS["worker_nfse"]}
    payload = {"x": 1}
    res = stack.nfse.post("/internal/v1/submissions", headers=h, json={
        "submission_id": "00000000-0000-4000-8000-000000000001", "company_id": world["b"], "environment": "restricted",
        "company_cnpj": world["b_cnpj"], "dps": {"id": "DPS3550308" + "2" + world["b_cnpj"] + "00001" + "1".zfill(15),
                                                  "series": "1", "number": 1},
        "payload": payload, "payload_hash": canonical_hash(payload)})
    assert res.status_code == 200 and res.json()["state"] == "BLOCKED" and res.json()["simulated"] is False
    assert stack.mock.transmissions == []


def test_transport_allowlist_tls_limits_and_no_blind_post_retry():
    from nfse_api.transport import ContractNotConfirmed, OfficialTransport, TransportError, build_ssl_context

    ctx = build_ssl_context(None, None)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname and ctx.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert OfficialTransport("restricted", "sefin", ctx).url("/nfse") == "https://sefin.producaorestrita.nfse.gov.br/SefinNacional/nfse"
    assert OfficialTransport("production", "adn", ctx).url("/DFe/0") == "https://adn.nfse.gov.br/contribuintes/DFe/0"
    with pytest.raises(ContractNotConfirmed):  # base path ausente continua bloqueando
        OfficialTransport("restricted", "sefin", ctx, base_paths={"restricted": {"sefin": None}}).url("/nfse")
    with pytest.raises(ValueError):
        OfficialTransport("homologacao-falsa", "sefin", ctx)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}{request.url.path}")
        if request.method == "POST":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path.endswith("/big"):
            return httpx.Response(200, content=b"x" * 2048)
        if request.url.path.endswith("/redir"):
            return httpx.Response(302, headers={"Location": "https://evil.example/"})
        return httpx.Response(503)

    paths = {"restricted": {"sefin": "/base", "adn": "/base"}, "production": {"sefin": None, "adn": None}}
    t = OfficialTransport("restricted", "sefin", ctx, http=httpx.Client(transport=httpx.MockTransport(handler)),
                          base_paths=paths, max_response_bytes=1024, max_retries=2)
    assert t.url("/nfse") == "https://sefin.producaorestrita.nfse.gov.br/base/nfse"
    with pytest.raises(TransportError) as e:
        t.request("POST", "/nfse", json={}, idempotent=False)
    assert e.value.uncertain is True and seen.count("POST sefin.producaorestrita.nfse.gov.br/base/nfse") == 1
    with pytest.raises(TransportError) as e:
        t.request("GET", "/big", idempotent=True)
    assert e.value.code == "RESPONSE_TOO_LARGE"
    with pytest.raises(TransportError) as e:
        t.request("GET", "/redir", idempotent=True)
    assert e.value.code == "REDIRECT_NOT_ALLOWED"
    r = t.request("GET", "/dps/x", idempotent=True)
    assert r.status == 503 and seen.count("GET sefin.producaorestrita.nfse.gov.br/base/dps/x") == 3  # GET com backoff
    assert "senha" not in str(e.value) and "Authorization" not in str(e.value)


def test_dps_id_matches_official_pattern():
    from ctrl_fiscal.domain.emission import build_dps_id

    dps = build_dps_id("3550308", CNPJ, "1", 42)
    assert len(dps) == 45 and re.fullmatch(r"DPS[0-9]{42}", dps)
    assert dps == "DPS3550308" + "2" + CNPJ + "00001" + "000000000000042"


def test_internal_endpoints_require_service_identity(stack, world):
    body = {"company_id": world["a"], "environment": "mock", "cnpj": world["a_cnpj"], "from_nsu": 0}
    assert stack.nfse.post("/internal/v1/distribution/fetch", json=body).status_code == 401
    bad = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + "x" * 64}
    assert stack.nfse.post("/internal/v1/distribution/fetch", headers=bad, json=body).status_code == 401
    assert stack.core.get("/internal/v1/authz", params={"user_id": "x", "session_id": "y", "company_id": "z"}).status_code == 401
    core_as_worker = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + KEYS["worker_core"]}
    r = stack.core.get("/internal/v1/authz", headers=core_as_worker, params={"user_id": "x", "session_id": "y", "company_id": "z"})
    assert r.status_code == 403  # worker não tem escopo de autorização de usuário


def test_official_call_audit_log_masks_identifiers(caplog):
    import logging

    from nfse_api.transport import OfficialTransport, build_ssl_context

    t = OfficialTransport("restricted", "adn", build_ssl_context(None, None),
                          http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"{}"))))
    with caplog.at_level(logging.INFO, logger="ctrl.nfse.chamadas_oficiais"):
        t.request("GET", f"/DFe/10?lote=true&cnpjConsulta={CNPJ}", idempotent=True)
    line = caplog.records[-1].getMessage()
    assert "chamada_oficial ambiente=restricted servico=adn metodo=GET" in line and "resultado=HTTP_200" in line
    assert CNPJ not in line and "cnpjConsulta" not in line
