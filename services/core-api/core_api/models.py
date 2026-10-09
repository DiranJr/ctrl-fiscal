from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ctrl_common.db import JsonType, UTCDateTime, new_id


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))  # "!pending" = conta sem senha definida (não autentica)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Papel GLOBAL de gestão de usuários. Não concede acesso fiscal: documentos exigem Membership na empresa.
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    password_set_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class CredentialToken(Base):
    """Ativação/redefinição de senha de USO ÚNICO com expiração. Só o hash SHA-256 é guardado."""

    __tablename__ = "credential_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(10))  # ACTIVATE | RESET
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str] = mapped_column(String(14), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    # Ambiente fiscal ativo: mock | restricted | production. Produção nunca é
    # promovida automaticamente; exige ação administrativa registrada.
    nfse_environment: Mapped[str] = mapped_column(String(16), default="mock")
    municipality_code: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Perfil fiscal do prestador (leiaute DPS v1.01: prest/IM e prest/regTrib). Nada é presumido:
    # campos ausentes bloqueiam a emissão oficial na validação.
    municipal_registration: Mapped[str | None] = mapped_column(String(15), nullable=True)  # prest/IM
    simples_option: Mapped[str | None] = mapped_column(String(1), nullable=True)  # regTrib/opSimpNac 1|2|3
    simples_regime: Mapped[str | None] = mapped_column(String(1), nullable=True)  # regTrib/regApTribSN 1|2|3
    special_regime: Mapped[str | None] = mapped_column(String(1), nullable=True)  # regTrib/regEspTrib 0..6|9
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)  # prest/fone
    email: Mapped[str | None] = mapped_column(String(80), nullable=True)  # prest/email
    fiscal_profile_source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fiscal_profile_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Ambientes cuja distribuição ADN é lida (ex.: ["restricted", "production"]); somente leitura.
    adn_environments: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    # Série da DPS DESTE workspace (antes configuração global do worker). Numeração por empresa/ambiente/série.
    dps_series: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Endereço do prestador (cadastro do workspace; preenchimento explícito).
    address: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id", name="uq_membership_user_company"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    permissions: Mapped[list] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("company_id", "document", name="uq_customer_company_document"),
        Index("ix_customers_company_name", "company_id", "name"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200))
    document_type: Mapped[str] = mapped_column(String(4))  # CNPJ | CPF
    document: Mapped[str] = mapped_column(String(14))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)  # toma/fone
    municipal_registration: Mapped[str | None] = mapped_column(String(20), nullable=True)
    municipality_code: Mapped[str | None] = mapped_column(String(7), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    address: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class RefreshSession(Base):
    """Família de refresh tokens. O id é o `sid` do JWT de acesso."""

    __tablename__ = "refresh_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("refresh_sessions.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email", "email_hash", "attempted_at"),
        Index("ix_login_attempts_ip", "ip", "attempted_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email_hash: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(64))
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    success: Mapped[bool] = mapped_column(Boolean)


class AuditEvent(Base):
    """Trilha de auditoria. Não há endpoint de alteração/exclusão; um
    administrador com acesso direto ao banco ainda consegue alterá-la."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_company_time", "company_id", "occurred_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Workspace(Base):
    """Unidade de isolamento operacional: UMA empresa (company_id único) e, no conector, UM certificado ativo.

    O identificador do workspace é o MESMO da empresa (1:1): todos os registros já vinculados por company_id
    pertencem ao workspace sem reescrever identificadores. CNPJ único (companies.cnpj) => um workspace por CNPJ.
    Não existe operação que troque a empresa de um workspace; outra empresa = outro workspace."""

    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="RESTRICT"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # PREPARING (cadastro em andamento: não transmite) | ACTIVE | SUSPENDED
    status: Mapped[str] = mapped_column(String(12), default="PREPARING")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ServiceProfile(Base):
    """Perfil tributário de um SERVIÇO do workspace (separado da configuração da empresa e dos dados da nota):
    códigos do serviço e classificação IBS/CBS definidos pela contabilidade; aplicado explicitamente ao rascunho."""

    __tablename__ = "service_profiles"
    __table_args__ = (Index("ix_service_profiles_company", "company_id", "is_active"),
                      UniqueConstraint("company_id", "origin_key", name="uq_service_profile_origin"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(120))
    national_code: Mapped[str] = mapped_column(String(6))
    municipal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nbs_code: Mapped[str | None] = mapped_column(String(9), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    issqn_taxation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pis_cofins_cst: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ibs_cbs: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # PENDING_REVIEW (preparado ou editado: ainda não serve para transmitir) | APPROVED | INACTIVE
    status: Mapped[str] = mapped_column(String(16), default="PENDING_REVIEW")
    # Chave do agrupamento do inventário (cTribNac:cNBS) — preparação idempotente; NULL para perfis manuais.
    origin_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # notas de referência e variações
    field_sources: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # campo → origem/confirmação
    suggestions: Mapped[list | None] = mapped_column(JsonType, nullable=True)  # sugestões com fonte e justificativa
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ServiceProfileVersion(Base):
    """Cada versão de um perfil (criação, preparação, edição, aprovação, inativação): conteúdo completo preservado,
    para saber exatamente qual configuração um rascunho/lote usou."""

    __tablename__ = "service_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_service_profile_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(String(36), ForeignKey("service_profiles.id", ondelete="RESTRICT"))
    company_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(24))
    snapshot: Mapped[dict] = mapped_column(JsonType)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
