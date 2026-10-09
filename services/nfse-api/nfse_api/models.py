"""Banco ctrl_nfse: cofre de certificados, ledger de envios e autoridade simulada."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ctrl_common.db import Blob, JsonType, UTCDateTime, new_id


class Base(DeclarativeBase):
    pass


class Certificate(Base):
    """Certificado A1 cifrado (AES-256-GCM). A chave do cofre fica fora do banco."""

    __tablename__ = "certificates"
    __table_args__ = (Index("ix_cert_company_env", "company_id", "environment", "is_active"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    subject_cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True)
    not_before: Mapped[datetime] = mapped_column(UTCDateTime())
    not_after: Mapped[datetime] = mapped_column(UTCDateTime())
    fingerprint_sha256: Mapped[str] = mapped_column(String(64))
    checks: Mapped[dict] = mapped_column(JsonType)
    encrypted_pfx: Mapped[bytes] = mapped_column(Blob)
    encrypted_password: Mapped[bytes] = mapped_column(Blob)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # UM certificado ativo por workspace: "empresa:ambiente" enquanto ativo (NULL depois) — único no banco.
    active_slot: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=False)
    # Histórico (sem material secreto): substituição e expurgo do PFX/senha cifrados do certificado substituído.
    replaced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replaced_by_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    material_purged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CertificateLock(Base):
    """Linha-trava por workspace: serializa ativação/renovação concorrente de certificado."""

    __tablename__ = "certificate_locks"
    company_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class Submission(Base):
    """Ledger transacional por submission_id/DPS/ambiente, gravado ANTES de transmitir."""

    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("environment", "dps_id", name="uq_submission_env_dps"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # submission_id
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    company_cnpj: Mapped[str] = mapped_column(String(14))
    dps_id: Mapped[str] = mapped_column(String(60))
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    transmissions: Mapped[int] = mapped_column(Integer, default=0)
    sending_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    errors: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    nfse_xml_gz: Mapped[bytes | None] = mapped_column(Blob, nullable=True)  # XML oficial devolvido (gzip)
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    certificate_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # certificado usado
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MockAuthorityRecord(Base):
    """'Autoridade' SIMULADA do ambiente mock. Nunca representa NFS-e real."""

    __tablename__ = "mock_authority_records"
    __table_args__ = (UniqueConstraint("dps_id", name="uq_mock_dps"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_cnpj: Mapped[str] = mapped_column(String(14))
    dps_id: Mapped[str] = mapped_column(String(60))
    access_key: Mapped[str] = mapped_column(String(50), unique=True)
    number: Mapped[int] = mapped_column(BigInteger)
    authorized_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class EventRequest(Base):
    """Ledger de PEDIDOS DE REGISTRO DE EVENTO do emitente (101101 cancelamento, 101103 análise fiscal).

    Gravado ANTES de transmitir. O Id do pedido (PRE + chave + código) é determinístico e o evento é único por nota.
    Um pedido recusado (ex.: prazo municipal) é final; um novo pedido do mesmo evento gera nova linha."""

    __tablename__ = "event_requests"
    __table_args__ = (UniqueConstraint("environment", "active_pre_id", name="uq_event_env_active_pre"),
                      Index("ix_event_env_pre", "environment", "pre_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # request_id (fiscal)
    # = pre_id enquanto o pedido não é final; NULL quando REGISTERED/REJECTED. Unicidade (ambiente, active_pre_id)
    # impede dois pedidos ATIVOS para o mesmo evento sem depender de gap lock (o banco usa READ COMMITTED).
    active_pre_id: Mapped[str | None] = mapped_column(String(59), nullable=True)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    company_cnpj: Mapped[str] = mapped_column(String(14))
    access_key: Mapped[str] = mapped_column(String(50))
    event_code: Mapped[str] = mapped_column(String(6))
    pre_id: Mapped[str] = mapped_column(String(59))
    c_motivo: Mapped[str] = mapped_column(String(2))
    x_motivo: Mapped[str] = mapped_column(String(255))
    payload_hash: Mapped[str] = mapped_column(String(64))
    # RECEIVED | SENDING | UNCERTAIN | NOT_FOUND | BLOCKED | REGISTERED | REJECTED
    state: Mapped[str] = mapped_column(String(16))
    transmissions: Mapped[int] = mapped_column(Integer, default=0)
    sending_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    request_xml_gz: Mapped[bytes | None] = mapped_column(Blob, nullable=True)  # pedRegEvento assinado enviado
    event_xml_gz: Mapped[bytes | None] = mapped_column(Blob, nullable=True)  # evento oficial devolvido
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    errors: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    certificate_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)  # certificado usado
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class MockEventRecord(Base):
    """Eventos registrados pela 'autoridade' SIMULADA (ambiente mock). Nunca representa evento real."""

    __tablename__ = "mock_event_records"
    __table_args__ = (UniqueConstraint("access_key", "event_code", name="uq_mock_event"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    access_key: Mapped[str] = mapped_column(String(50))
    event_code: Mapped[str] = mapped_column(String(6))
    pre_id: Mapped[str] = mapped_column(String(59))
    c_motivo: Mapped[str] = mapped_column(String(2))
    x_motivo: Mapped[str] = mapped_column(String(255))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime())
