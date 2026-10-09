from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from ctrl_common.fiscal_types import is_valid_cnpj, is_valid_cpf, only_alnum

Permission = Literal["view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin"]
Environment = Literal["mock", "restricted", "production"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    name: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    user: UserOut


class CsrfResponse(BaseModel):
    csrf_token: str


class CompanyAccess(BaseModel):
    id: str
    name: str
    cnpj: str
    timezone: str
    permissions: list[Permission]
    environment: Environment = Field(description="Ambiente NFS-e ativo (adição compatível ao contrato v0.1).")


class MeResponse(BaseModel):
    id: str
    name: str
    email: str
    companies: list[CompanyAccess]
    is_super_admin: bool = Field(False, description="Gestão global de usuários. NÃO dá acesso fiscal às empresas.")
    admin_company_ids: list[str] = Field(default_factory=list, description="Empresas em que o usuário é admin.")
    workspaces: list[dict] = Field(default_factory=list, description="Workspaces do usuário (um por empresa).")


# ---------------------------------------------------------------- administração de usuários
AdminPermission = Permission


class AdminMembershipIn(Strict):
    permissions: list[AdminPermission] = Field(min_length=1, max_length=10)  # uma entrada por permissão existente (10)


class AdminMembershipCreate(AdminMembershipIn):
    company_id: str = Field(min_length=36, max_length=36)


class AdminUserCreate(Strict):
    name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    memberships: list[AdminMembershipCreate] = Field(default_factory=list, max_length=50)


class AdminUserPatch(Strict):
    expected_version: int = Field(ge=1)
    name: str | None = Field(None, min_length=2, max_length=160)
    email: EmailStr | None = None
    is_active: bool | None = None


class SuperAdminIn(Strict):
    enabled: bool


class AdminMembershipOut(BaseModel):
    company_id: str
    company_name: str
    company_cnpj: str
    company_active: bool
    permissions: list[AdminPermission]


class AdminUserOut(BaseModel):
    id: str
    name: str
    email: str
    status: Literal["ACTIVE", "INACTIVE", "PENDING"]
    is_active: bool
    is_super_admin: bool
    version: int
    memberships: list[AdminMembershipOut] = Field(description="Somente vínculos dentro do escopo de quem consulta.")
    other_companies: int = Field(description="Vínculos fora do escopo do consultante (apenas a contagem).")
    can_manage_identity: bool
    active_sessions: int
    password_set_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserPage(BaseModel):
    items: list[AdminUserOut]
    page: int
    page_size: int
    total: int


class CredentialLink(BaseModel):
    purpose: Literal["ACTIVATE", "RESET"]
    expires_at: datetime
    path: str = Field(description="Link de uso único (token no fragmento #). Exibido uma única vez; repasse ao usuário.")


class AdminUserCreated(BaseModel):
    user: AdminUserOut
    credential_link: CredentialLink


class AdminCompanyOut(BaseModel):
    id: str
    name: str
    cnpj: str


class AdminPermissionInfo(BaseModel):
    id: AdminPermission
    label: str


class AdminMeta(BaseModel):
    is_super_admin: bool
    admin_company_ids: list[str]
    permissions: list[AdminPermissionInfo]


class ActivateRequest(Strict):
    token: str = Field(min_length=20, max_length=100)
    password: str = Field(min_length=12, max_length=256)


class CompanyOut(BaseModel):
    id: str
    name: str
    cnpj: str
    timezone: str
    environment: Environment
    permissions: list[Permission]


class Address(Strict):
    street: str | None = Field(None, max_length=200)
    number: str | None = Field(None, max_length=20)
    complement: str | None = Field(None, max_length=100)
    district: str | None = Field(None, max_length=100)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, min_length=2, max_length=2)


def _municipio_ibge(v: str | None) -> str | None:
    """Município precisa EXISTIR na tabela IBGE oficial (Anexo A do SN NFS-e) — 7 dígitos não bastam (E0238)."""
    from ctrl_common import dominios

    if v and not dominios.municipio(v):
        raise ValueError("Código de município não existe na tabela IBGE (Anexo A do Sistema Nacional NFS-e)")
    return v


class CustomerBase(Strict):
    name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None  # estrutura de e-mail do tomador (Anexo I E0247); DV do documento: E0188/E0206
    phone: str | None = Field(None, pattern=r"^[0-9]{6,20}$", description="toma/fone (somente dígitos, DDD incluso).")
    municipal_registration: str | None = Field(None, max_length=20, pattern=r"^[0-9A-Za-z.\-/]*$")
    municipality_code: str | None = Field(None, pattern=r"^\d{7}$", description="Código IBGE do município (7 dígitos).")
    postal_code: str | None = Field(None, pattern=r"^\d{8}$")
    address: Address | None = None

    @field_validator("municipality_code")
    @classmethod
    def _mun(cls, v: str | None) -> str | None:
        return _municipio_ibge(v)


class CustomerCreate(CustomerBase):
    document_type: Literal["CNPJ", "CPF"]
    document: str = Field(min_length=11, max_length=18)

    @field_validator("document")
    @classmethod
    def _doc(cls, v: str, info) -> str:  # noqa: ANN001
        v = only_alnum(v)
        kind = info.data.get("document_type")
        if kind == "CNPJ" and not is_valid_cnpj(v):
            raise ValueError("CNPJ inválido")
        if kind == "CPF" and not is_valid_cpf(v):
            raise ValueError("CPF inválido")
        return v


class CustomerPatch(Strict):
    _mun = field_validator("municipality_code")(classmethod(lambda cls, v: _municipio_ibge(v)))
    name: str | None = Field(None, min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, pattern=r"^[0-9]{6,20}$")
    municipal_registration: str | None = Field(None, max_length=20, pattern=r"^[0-9A-Za-z.\-/]*$")
    municipality_code: str | None = Field(None, pattern=r"^\d{7}$")
    postal_code: str | None = Field(None, pattern=r"^\d{8}$")
    address: Address | None = None
    is_active: bool | None = None
    expected_version: int | None = Field(None, ge=1, description="Se informado, conflito de versão retorna 409.")


class CustomerOut(BaseModel):
    id: str
    company_id: str
    name: str
    document_type: Literal["CNPJ", "CPF"]
    document: str
    email: str | None
    phone: str | None = None
    municipal_registration: str | None
    municipality_code: str | None
    postal_code: str | None
    address: Address | None
    is_active: bool
    version: int
    created_at: datetime
    updated_at: datetime


class IntegrationCertificate(BaseModel):
    present: bool
    subject_cnpj_matches: bool | None = None
    not_before: datetime | None = None
    not_after: datetime | None = None
    days_to_expiry: int | None = None


class IntegrationStatus(BaseModel):
    company_id: str
    environment: Environment
    status: Literal["SIMULATED", "NOT_CONFIGURED", "BLOCKED", "READY"]
    simulated: bool
    provider: Literal["mock", "official"]
    certificate: IntegrationCertificate
    capabilities: dict[str, bool]
    blockers: list[dict[str, str]]
    checked_at: datetime


# ---------------------------------------------------------------- perfil fiscal (Sprint 02)
SimplesOption = Literal["1", "2", "3"]  # TSOpSimpNac
SimplesRegime = Literal["1", "2", "3"]  # TSRegimeApuracaoSimpNac (somente se opSimpNac=3)
SpecialRegime = Literal["0", "1", "2", "3", "4", "5", "6", "9"]  # TSRegEspTrib


class FiscalProfileOut(BaseModel):
    company_id: str
    cnpj: str
    name: str
    timezone: str
    environment: Environment
    adn_environments: list[Environment]
    municipality_code: str | None = Field(description="cLocEmi / município do emitente (IBGE, 7 dígitos).")
    municipal_registration: str | None = Field(description="prest/IM.")
    simples_option: SimplesOption | None = Field(description="prest/regTrib/opSimpNac.")
    simples_regime: SimplesRegime | None = Field(description="prest/regTrib/regApTribSN (só para opSimpNac=3).")
    special_regime: SpecialRegime | None = Field(description="prest/regTrib/regEspTrib.")
    phone: str | None = Field(description="prest/fone.")
    email: str | None = Field(description="prest/email.")
    source: str | None
    updated_at: datetime | None
    complete_for_emission: bool
    missing: list[str]
    dps_series: str | None = None
    postal_code: str | None = None
    address: dict | None = None
    workspace_status: str | None = None


class FiscalProfilePatch(Strict):
    _mun = field_validator("municipality_code")(classmethod(lambda cls, v: _municipio_ibge(v)))
    municipality_code: str | None = Field(None, pattern=r"^\d{7}$")
    municipal_registration: str | None = Field(None, min_length=1, max_length=15)
    simples_option: SimplesOption | None = None
    simples_regime: SimplesRegime | None = None
    special_regime: SpecialRegime | None = None
    phone: str | None = Field(None, pattern=r"^[0-9]{6,20}$")
    email: EmailStr | None = Field(None, max_length=80)  # estrutura de e-mail do prestador (Anexo I E0148)
    source: str | None = Field(None, max_length=300)
    dps_series: str | None = Field(None, pattern=r"^[0-9]{1,5}$", description="Série da DPS deste workspace "
                                   "(aplicativo próprio: 1 a 49999). Não reinicia a numeração existente.")
    postal_code: str | None = Field(None, pattern=r"^\d{8}$", description="CEP do prestador.")
    address: Address | None = Field(None, description="Endereço do prestador (cadastro do workspace).")
    environment: Literal["mock", "restricted"] | None = Field(
        None, description="Ambiente de emissão. 'production' não é aceito nesta etapa.")
    adn_environments: list[Environment] | None = Field(
        None, description="Ambientes com leitura ADN (somente leitura). 'production' exige certificado válido.")


class CertificateInfo(BaseModel):
    company_id: str
    environments: list[Environment]
    subject_cnpj_matches: bool
    not_before: datetime
    not_after: datetime
    days_to_expiry: int
    fingerprint_sha256: str
    accepted: bool
    problems: list[dict[str, str]]
