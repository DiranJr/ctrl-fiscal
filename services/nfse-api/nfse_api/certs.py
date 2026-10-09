"""Conferência de certificado A1 contra as regras oficiais documentadas.

Fonte: Anexo I (RN_RECEPCAO_DPS) (planilha em docs/oficial/):
- X.509 v3, não AC (E1200/E0716);
- "Autenticação Cliente" para mTLS (E1200) e "Assinatura Digital"+"Não Recusa"
  para assinatura da DPS (E0716);
- validade (E1203/E0715); CNPJ em OtherName OID 2.16.76.1.3.3 (E1209/E0716).
Cadeia ICP-Brasil e revogação (E1205–E1208) são avaliadas por nfse_api.icp na instalação (resultado em
checks) e exigidas aqui: sem cadeia válida até raiz oficial e LCR confirmada (GOOD), o certificado não é apto.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

OID_CNPJ = "2.16.76.1.3.3"
OID_CPF = "2.16.76.1.3.1"


@dataclass
class CertificateReport:
    subject_cnpj: str | None
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    checks: dict[str, bool | None]

    def ok_for(self, cnpj: str, now: datetime) -> tuple[bool, list[dict[str, str]]]:
        problems: list[dict[str, str]] = []
        if self.subject_cnpj is None:
            problems.append({"code": "CERT_NO_CNPJ", "message": "Certificado sem CNPJ (OID 2.16.76.1.3.3)."})
        elif self.subject_cnpj != cnpj:
            problems.append({"code": "CERT_CNPJ_MISMATCH", "message": "CNPJ do certificado difere do CNPJ da empresa."})
        if now < self.not_before or now >= self.not_after:
            problems.append({"code": "CERT_EXPIRED_OR_NOT_YET_VALID", "message": "Certificado fora da validade."})
        for key, code, msg in (
            ("version3", "CERT_NOT_V3", "Certificado não é X.509 v3."),
            ("not_ca", "CERT_IS_CA", "Certificado de AC não é aceito."),
            ("client_auth", "CERT_NO_CLIENT_AUTH", "Sem uso 'Autenticação Cliente' (mTLS)."),
            ("digital_signature", "CERT_NO_DIGITAL_SIGNATURE", "Sem uso 'Assinatura Digital'."),
            ("non_repudiation", "CERT_NO_NON_REPUDIATION", "Sem uso 'Não Recusa'."),
            ("not_self_signed", "CERT_SELF_SIGNED", "Certificado autoassinado não é e-CNPJ ICP-Brasil."),
        ):
            if self.checks.get(key) is False:
                problems.append({"code": code, "message": msg})
        if self.checks.get("icp_brasil_chain") is not True:
            problems.append({"code": "CERT_CHAIN_INVALID",
                             "message": "Cadeia não validada até uma raiz oficial ICP-Brasil"
                                        + (f": {self.checks['chain_error']}" if self.checks.get("chain_error") else ".")})
        elif self.checks.get("revocation") == "REVOKED":
            problems.append({"code": "CERT_REVOKED", "message": "Certificado revogado (LCR da AC emissora)."})
        elif self.checks.get("revocation") != "GOOD":
            problems.append({"code": "CERT_REVOCATION_UNCONFIRMED",
                             "message": "Situação de revogação não confirmada (LCR indisponível ou vencida)."})
        return not problems, problems


def _other_name_digits(cert: x509.Certificate, oid: str) -> str | None:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return None
    for name in san.get_values_for_type(x509.OtherName):
        if name.type_id.dotted_string == oid:
            # Valor DER (OCTET/PrintableString); extrai os 14 caracteres do CNPJ.
            text = name.value.decode("latin-1", errors="ignore")
            m = re.search(r"[0-9A-Z]{12}[0-9]{2}", text)
            if m:
                return m.group(0)
    return None


def inspect_certificate(cert: x509.Certificate) -> CertificateReport:
    checks: dict[str, bool | None] = {"version3": cert.version == x509.Version.v3}
    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        checks["not_ca"] = not bc.ca
    except x509.ExtensionNotFound:
        checks["not_ca"] = True
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        checks["digital_signature"] = ku.digital_signature
        checks["non_repudiation"] = ku.content_commitment
    except x509.ExtensionNotFound:
        checks["digital_signature"] = False
        checks["non_repudiation"] = False
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        checks["client_auth"] = ExtendedKeyUsageOID.CLIENT_AUTH in eku
    except x509.ExtensionNotFound:
        checks["client_auth"] = False
    # Um e-CNPJ ICP-Brasil é sempre emitido por uma AC: emissor == titular indica certificado de teste/forjado,
    # que nunca deve ser usado contra os ambientes oficiais (bloqueante). Não substitui a verificação da cadeia.
    checks["not_self_signed"] = cert.issuer != cert.subject
    checks["icp_brasil_chain"] = None  # não verificado (ver docstring)
    der = cert.public_bytes(Encoding.DER)
    return CertificateReport(
        subject_cnpj=_other_name_digits(cert, OID_CNPJ),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        fingerprint_sha256=hashlib.sha256(der).hexdigest(),
        checks=checks,
    )


def load_pfx(pfx: bytes, password: bytes):  # noqa: ANN201
    key, cert, extra = pkcs12.load_key_and_certificates(pfx, password)
    if key is None or cert is None:
        raise ValueError("PFX sem chave privada ou certificado")
    return key, cert, extra or []
