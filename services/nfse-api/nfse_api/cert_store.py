"""Instalação do certificado A1 no cofre do conector (usado pelo endpoint interno e pelo CLI).

O PFX e a senha são cifrados com AES-256-GCM vinculados a "empresa:ambiente" (AAD). Nada é registrado em log.
Recusa: PFX/senha inválidos, CNPJ diferente do da empresa, fora da validade, sem "Autenticação Cliente",
sem "Assinatura Digital"/"Não Recusa" (regras E1200/E1203/E1209/E0716 do Anexo I).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .certs import inspect_certificate, load_pfx
from .icp import IcpValidator
from .models import Certificate, CertificateLock
from .vault import Vault

# Todo problema é bloqueante (inclui cadeia ICP-Brasil e revogação): nada é instalado com pendência.
BLOCKING = {"CERT_NO_CNPJ", "CERT_CNPJ_MISMATCH", "CERT_EXPIRED_OR_NOT_YET_VALID", "CERT_NOT_V3", "CERT_IS_CA",
            "CERT_NO_CLIENT_AUTH", "CERT_NO_DIGITAL_SIGNATURE", "CERT_NO_NON_REPUDIATION", "CERT_SELF_SIGNED",
            "CERT_CHAIN_INVALID", "CERT_REVOKED", "CERT_REVOCATION_UNCONFIRMED"}


class CertificateRejected(Exception):
    def __init__(self, code: str, message: str, problems: list[dict] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.problems = problems or []


def install(sessions: sessionmaker, vault: Vault, *, company_id: str, cnpj: str, environments: list[str],
            pfx: bytes, password: bytes, now: datetime, validator: IcpValidator) -> dict:
    if not vault.available:
        raise CertificateRejected("VAULT_KEY_NOT_CONFIGURED", "Cofre sem chave configurada no conector.")
    if not environments or any(e not in ("restricted", "production") for e in environments):
        raise CertificateRejected("INVALID_ENVIRONMENTS", "Certificado só se aplica a restricted/production.")
    try:
        _, cert, extra = load_pfx(pfx, password)
    except Exception:
        raise CertificateRejected("PFX_INVALID", "Arquivo PFX inválido ou senha incorreta.")
    report = inspect_certificate(cert)
    report.checks.update(validator.evaluate(cert, extra, now))
    _, problems = report.ok_for(cnpj, now)
    blocking = [p for p in problems if p["code"] in BLOCKING]
    if blocking:
        raise CertificateRejected(blocking[0]["code"], blocking[0]["message"], problems)
    with sessions() as s, s.begin():
        _lock(s, company_id)
        # UM certificado ativo por workspace: o novo (renovação da MESMA empresa — CNPJ conferido acima) substitui o
        # anterior em TODOS os ambientes; o anterior vira histórico sem material secreto.
        _retire(s, company_id, now, replaced_by=report.fingerprint_sha256)
        for env in environments:
            aad = f"{company_id}:{env}".encode()
            s.add(Certificate(
                company_id=company_id, environment=env, subject_cnpj=report.subject_cnpj, not_before=report.not_before,
                not_after=report.not_after, fingerprint_sha256=report.fingerprint_sha256, checks=report.checks,
                encrypted_pfx=vault.encrypt(pfx, aad), encrypted_password=vault.encrypt(password, aad),
                key_version=1, is_active=True, created_at=now, active_slot=f"{company_id}:{env}",
            ))
        try:
            s.flush()
        except IntegrityError:
            raise CertificateRejected("CERTIFICATE_ACTIVATION_CONFLICT", "Ativação concorrente de certificado; tente novamente.")
    return {
        "company_id": company_id, "environments": sorted(environments),
        "subject_cnpj_matches": report.subject_cnpj == cnpj, "not_before": report.not_before,
        "not_after": report.not_after, "days_to_expiry": (report.not_after - now).days,
        "fingerprint_sha256": report.fingerprint_sha256, "accepted": True, "problems": problems,
        "chain": report.checks.get("chain_cns"), "revocation": report.checks.get("revocation"),
    }


def _lock(s: Session, company_id: str) -> None:
    """Serializa operações de certificado do workspace (linha-trava + SELECT ... FOR UPDATE)."""
    if s.get(CertificateLock, company_id) is None:
        try:
            with s.begin_nested():
                s.add(CertificateLock(company_id=company_id))
                s.flush()
        except IntegrityError:
            pass
    s.scalar(select(CertificateLock).where(CertificateLock.company_id == company_id).with_for_update())


def _retire(s: Session, company_id: str, now: datetime, *, replaced_by: str | None,
            environment: str | None = None) -> int:
    q = update(Certificate).where(Certificate.company_id == company_id, Certificate.is_active.is_(True))
    if environment:
        q = q.where(Certificate.environment == environment)
    return s.execute(q.values(is_active=False, active_slot=None, replaced_at=now, replaced_by_fingerprint=replaced_by,
                              encrypted_pfx=b"", encrypted_password=b"", material_purged_at=now)
                     .execution_options(synchronize_session=False)).rowcount


def deactivate(sessions: sessionmaker, *, company_id: str, environment: str, now: datetime | None = None) -> int:
    from datetime import timezone

    with sessions() as s, s.begin():
        _lock(s, company_id)
        return _retire(s, company_id, now or datetime.now(timezone.utc), replaced_by=None, environment=environment)


def history(sessions: sessionmaker, *, company_id: str) -> list[dict]:
    """Histórico de certificados do workspace — SÓ metadados (nunca PFX, senha ou chave)."""
    with sessions() as s:
        rows = s.scalars(select(Certificate).where(Certificate.company_id == company_id)
                         .order_by(Certificate.created_at.desc(), Certificate.environment)).all()
        return [{"environment": r.environment, "fingerprint_sha256": r.fingerprint_sha256, "subject_cnpj": r.subject_cnpj,
                 "not_before": r.not_before, "not_after": r.not_after, "is_active": r.is_active, "created_at": r.created_at,
                 "replaced_at": r.replaced_at, "replaced_by_fingerprint": r.replaced_by_fingerprint,
                 "material_purged": r.material_purged_at is not None or not r.encrypted_pfx} for r in rows]
