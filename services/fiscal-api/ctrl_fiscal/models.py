"""Modelo de dados do domínio fiscal (banco ctrl_fiscal), compartilhado por
fiscal-api e fiscal-worker. Não há chaves estrangeiras para outros bancos:
company_id/customer_id/user_id referem-se ao core por valor."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ctrl_common.db import Blob, JsonType, UTCDateTime, new_id

MONEY = Numeric(15, 2)


class Base(DeclarativeBase):
    pass


class StoredBlob(Base):
    """XML/arquivos brutos protegidos (gzip), fora de qualquer raiz pública."""

    __tablename__ = "blobs"
    __table_args__ = (Index("ix_blobs_company_sha", "company_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    content_gz: Mapped[bytes] = mapped_column(Blob)
    kind: Mapped[str] = mapped_column(String(20))  # XML | UPLOAD
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("company_id", "environment", "access_key", name="uq_document_key"),
        Index("ix_documents_list", "company_id", "environment", "direction", "issued_at"),
        Index("ix_documents_recipient", "company_id", "environment", "direction", "recipient_doc", "status"),
        Index("ix_documents_competence", "company_id", "environment", "competence"),
        Index("ix_documents_retention", "company_id", "environment", "ret_class"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str] = mapped_column(String(10))  # ISSUED | RECEIVED
    company_role: Mapped[str] = mapped_column(String(16))  # PRESTADOR | TOMADOR
    access_key: Mapped[str] = mapped_column(String(50))
    nfse_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dps_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(12))  # AUTHORIZED | CANCELLED | UNKNOWN
    status_code: Mapped[str | None] = mapped_column(String(6), nullable=True)  # cStat (original, preservado)
    # Substituição: esta NFS-e substitui outra (DPS/subst) / foi substituída por outra (evento e105102 ou NFS-e substituta).
    substitutes_access_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    substitution_reason: Mapped[str | None] = mapped_column(String(2), nullable=True)  # subst/cMotivo
    substituted_by_access_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cancellation_event_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # evento que cancelou
    layout_version: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tp_amb: Mapped[str | None] = mapped_column(String(1), nullable=True)
    competence: Mapped[str | None] = mapped_column(String(7), nullable=True)  # YYYY-MM
    service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # dCompet
    issued_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # dhEmi
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)  # dhProc
    issuer_doc: Mapped[str | None] = mapped_column(String(14), nullable=True)
    issuer_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recipient_doc: Mapped[str | None] = mapped_column(String(14), nullable=True)
    recipient_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    intermediary_doc: Mapped[str | None] = mapped_column(String(14), nullable=True)
    intermediary_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_national_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    service_municipal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nbs_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    service_location_code: Mapped[str | None] = mapped_column(String(7), nullable=True)  # cLocPrestacao
    obra_code: Mapped[str | None] = mapped_column(String(30), nullable=True)  # serv/obra/cObra
    # serv/obra (cCIB, inscImobFisc, end) e serv/infoCompl lidos do XML — referência para reutilização
    service_extra: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vServ
    deductions: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    net_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vLiq
    iss_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vISSQN
    ret_iss: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_irrf: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_inss: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_pis: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_cofins: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_csll: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_pcc_total: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    total_retained: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vTotalRet
    retention_indicators: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    review_hints: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    tax_settings: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    # IBS/CBS oficiais da NFS-e (infNFSe/IBSCBS, calculados pela Sefin) — separados da prévia local.
    ibs_cbs_official: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    xml_blob_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("blobs.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(12))  # ADN | IMPORT | EMISSION
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Retenções normalizadas para filtro (domain/retencoes_doc.classify): efetiva ≠ informada; sem rateio.
    ret_class: Mapped[str | None] = mapped_column(String(8), nullable=True)  # WITH | WITHOUT | UNKNOWN
    ret_has_iss: Mapped[bool] = mapped_column(Boolean, default=False)
    ret_has_inss: Mapped[bool] = mapped_column(Boolean, default=False)
    ret_has_irrf: Mapped[bool] = mapped_column(Boolean, default=False)
    ret_has_contrib: Mapped[bool] = mapped_column(Boolean, default=False)
    ret_pis_cofins_code: Mapped[str | None] = mapped_column(String(1), nullable=True)  # tpRetPisCofins
    ret_contrib_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    ret_total_effective: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DocumentEvent(Base):
    __tablename__ = "document_events"
    __table_args__ = (
        UniqueConstraint("company_id", "environment", "event_id", name="uq_event_identity"),
        Index("ix_events_key", "company_id", "environment", "access_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    access_key: Mapped[str] = mapped_column(String(50))
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_id: Mapped[str] = mapped_column(String(70))
    event_type: Mapped[str] = mapped_column(String(10))
    sequence: Mapped[str | None] = mapped_column(String(3), nullable=True)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancels_document: Mapped[bool] = mapped_column(Boolean, default=False)
    substitute_access_key: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e105102/chSubstituta
    blob_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("blobs.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(12))
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (Index("ix_drafts_company", "company_id", "environment", "status", "updated_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    customer_id: Mapped[str] = mapped_column(String(36))
    customer_snapshot: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    competence: Mapped[str] = mapped_column(String(7))
    service_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # dCompet (AAAA-MM-DD)
    service_national_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    service_municipal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    service_nbs_code: Mapped[str | None] = mapped_column(String(9), nullable=True)  # cNBS
    service_location_code: Mapped[str | None] = mapped_column(String(7), nullable=True)  # cLocPrestacao
    service_obra_code: Mapped[str | None] = mapped_column(String(30), nullable=True)  # obra/cObra
    service_obra_address: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # obra/end (alternativa a cObra)
    # obra/cCIB, obra/inscImobFisc e infoCompl (idDocTec, docRef, xPed, gItemPed, xInfComp)
    service_extra: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    # Perfil de serviço aplicado e a VERSÃO vigente na aplicação (o conteúdo de cada versão fica no core).
    service_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    service_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_unconditional: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vDescCondIncond/vDescIncond
    discount_conditional: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)  # vDescCondIncond/vDescCond
    validated_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # versão a que `issues` se refere
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    service_description: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    deductions: Mapped[Decimal] = mapped_column(MONEY)
    tax_settings: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    issues: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    current_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (Index("ix_batches_company", "company_id", "environment", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    timezone: Mapped[str] = mapped_column(String(64))
    scheduled_local: Mapped[str] = mapped_column(String(40))  # ISO com offset, como informado
    total: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class BatchItem(Base):
    """Tarefa persistente de transmissão (fila). Snapshot congelado no agendamento."""

    __tablename__ = "batch_items"
    __table_args__ = (
        UniqueConstraint("submission_id", name="uq_item_submission"),
        UniqueConstraint("company_id", "environment", "dps_series", "dps_number", name="uq_item_dps_number"),
        Index("ix_items_due", "status", "next_attempt_at"),
        Index("ix_items_batch", "batch_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(String(36), ForeignKey("batches.id"))
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    draft_id: Mapped[str] = mapped_column(String(36))
    draft_version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JsonType)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24))
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime())
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime())
    submission_id: Mapped[str] = mapped_column(String(36))
    submit_phase: Mapped[str] = mapped_column(String(12), default="NONE")  # NONE | SUBMITTING
    dps_series: Mapped[str | None] = mapped_column(String(5), nullable=True)
    dps_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dps_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    late_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DpsSequence(Base):
    __tablename__ = "dps_sequences"
    __table_args__ = (UniqueConstraint("company_id", "environment", "series", name="uq_dps_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    series: Mapped[str] = mapped_column(String(5))
    next_number: Mapped[int] = mapped_column(BigInteger, default=1)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_due", "status", "created_at"),
        Index("ix_jobs_company", "company_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(10))  # IMPORT | EXPORT | SYNC | BUNDLE
    status: Mapped[str] = mapped_column(String(12))  # QUEUED | RUNNING | SUCCEEDED | PARTIAL | FAILED
    params: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    company_info: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    output_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class JobDocument(Base):
    """Conjunto de documentos FIXADO na criação de um job BUNDLE (download em lote)."""

    __tablename__ = "job_documents"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_document_seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str] = mapped_column(String(36))


class DownloadTicket(Base):
    """Ticket de uso único (60 s) para baixar o arquivo de um job por POST de formulário, sem token na URL."""

    __tablename__ = "download_tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 do ticket; o ticket em si não é guardado
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    session_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class JobFile(Base):
    __tablename__ = "job_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    blob_id: Mapped[str] = mapped_column(String(36), ForeignKey("blobs.id"))
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


class JobError(Base):
    __tablename__ = "job_errors"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_error_seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    file: Mapped[str | None] = mapped_column(String(300), nullable=True)
    code: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(String(500))


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", "operation", "key", name="uq_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    company_id: Mapped[str] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SyncSchedule(Base):
    """Agenda persistente de distribuição ADN por empresa/CNPJ/ambiente."""

    __tablename__ = "sync_schedules"
    __table_args__ = (
        UniqueConstraint("company_id", "environment", name="uq_sync_company_env"),
        Index("ix_sync_due", "enabled", "next_run_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    cnpj: Mapped[str] = mapped_column(String(14))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16))  # PENDING|NOT_CONFIGURED|BLOCKED|RUNNING|OK|ERROR
    interval_hours: Mapped[int] = mapped_column(Integer, default=12)
    cursor_nsu: Mapped[int] = mapped_column(BigInteger, default=0)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime())
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cycle_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    received_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DfeItem(Base):
    """Registro bruto de cada NSU recebido do ADN (inclusive quarentena e papéis
    que não viram documento listável, como intermediário)."""

    __tablename__ = "dfe_items"
    __table_args__ = (
        UniqueConstraint("company_id", "environment", "nsu", name="uq_dfe_nsu"),
        Index("ix_dfe_status", "company_id", "environment", "parse_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    nsu: Mapped[int] = mapped_column(BigInteger)
    access_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    doc_kind: Mapped[str | None] = mapped_column(String(10), nullable=True)  # NFSE | EVENT
    company_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    blob_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("blobs.id"), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(20))  # STORED|PARSED|QUARANTINED|OUT_OF_SCOPE
    parse_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    parse_attempts: Mapped[int] = mapped_column(Integer, default=0)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_row_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AuditEvent(Base):
    """Auditoria do domínio fiscal. Sem endpoint de alteração/exclusão; o
    administrador do banco ainda pode alterá-la (não é imutável)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_fiscal_audit_company", "company_id", "occurred_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CancellationRequest(Base):
    """Pedido de evento do emitente (101101 cancelamento / 101103 análise fiscal), processado pelo worker.

    A situação da NOTA só muda quando o evento oficial é aplicado (document_events). Um pedido ativo por nota:
    active_document_id = document_id enquanto não final (NULL depois), com unicidade — sem depender de gap lock."""

    __tablename__ = "cancellation_requests"
    __table_args__ = (
        UniqueConstraint("company_id", "active_document_id", name="uq_cancellation_active"),
        Index("ix_cancellation_due", "status", "next_attempt_at"),
        Index("ix_cancellation_document", "document_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36))
    environment: Mapped[str] = mapped_column(String(16))
    document_id: Mapped[str] = mapped_column(String(36))
    active_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    access_key: Mapped[str] = mapped_column(String(50))
    company_cnpj: Mapped[str] = mapped_column(String(14))
    kind: Mapped[str] = mapped_column(String(10))  # CANCEL | ANALYSIS
    event_code: Mapped[str] = mapped_column(String(6))
    reason_code: Mapped[str] = mapped_column(String(2))
    reason_text: Mapped[str] = mapped_column(String(255))
    # QUEUED | PROCESSING | AWAITING_CONFIRMATION | REGISTERED | REJECTED | BLOCKED | WITHDRAWN
    status: Mapped[str] = mapped_column(String(24))
    submit_phase: Mapped[str] = mapped_column(String(12), default="NONE")  # NONE | SUBMITTING
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime())
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    errors: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    event_row_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(36))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class TaxRateParameter(Base):
    """Alíquota-padrão de IBS/CBS com VIGÊNCIA (data do fato gerador), fonte oficial e autorização registradas.
    Alterações só por migração (com evento de auditoria); nenhum endpoint altera estes valores."""

    __tablename__ = "tax_rate_parameters"
    __table_args__ = (UniqueConstraint("tributo", "esfera", "vigencia_inicio", name="uq_tax_rate_vigencia"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tributo: Mapped[str] = mapped_column(String(3))  # IBS | CBS
    esfera: Mapped[str] = mapped_column(String(3))  # UF | MUN | FED
    aliquota: Mapped[Decimal] = mapped_column(Numeric(7, 4))  # percentual (0.1000 = 0,1%)
    vigencia_inicio: Mapped[date] = mapped_column(Date)
    vigencia_fim: Mapped[date] = mapped_column(Date)
    fonte: Mapped[str] = mapped_column(String(600))
    autorizacao: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
