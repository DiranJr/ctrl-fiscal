"""Interface de provider NFS-e (mock | restricted | production)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class ProviderBlocked(Exception):
    """Nada foi transmitido: integração não configurada ou bloqueada."""

    def __init__(self, status: str, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status  # NOT_CONFIGURED | BLOCKED
        self.code = code
        self.message = message


class TransmissionUncertain(Exception):
    """O pedido pode ter sido processado (ex.: timeout após envio)."""

    def __init__(self, code: str = "TIMEOUT") -> None:
        super().__init__(code)
        self.code = code


class TransmissionNotSent(Exception):
    """Falha garantidamente anterior ao envio (ex.: conexão recusada)."""

    def __init__(self, code: str = "CONNECT_FAILED") -> None:
        super().__init__(code)
        self.code = code


@dataclass
class SubmitOutcome:
    state: str  # AUTHORIZED | REJECTED
    access_key: str | None = None
    number: str | None = None
    authorized_at: datetime | None = None
    errors: list[dict] = field(default_factory=list)
    nfse_xml: bytes | None = None  # XML oficial devolvido (NFS-e), quando houver
    alerts: list[dict] = field(default_factory=list)
    # True quando a recusa veio da API oficial DEPOIS da transmissão (HTTP 400). Recusas locais (XSD, regra de
    # convênio) são False: nada saiu. Uma recusa oficial pode ser "DPS já processada" de um envio anterior cujo
    # resultado se perdeu; o conector confirma por consulta antes de registrar REJECTED.
    official_rejection: bool = False


@dataclass
class QueryOutcome:
    found: bool
    access_key: str | None = None
    number: str | None = None
    authorized_at: datetime | None = None
    nfse_xml: bytes | None = None


@dataclass
class EventOutcome:
    state: str  # REGISTERED | REJECTED
    event_xml: bytes | None = None  # XML `evento` gerado pela Sefin (201)
    processed_at: datetime | None = None
    errors: list[dict] = field(default_factory=list)
    request_xml: bytes | None = None  # pedRegEvento assinado efetivamente enviado
    official_rejection: bool = False  # True = 400 da API oficial (pode ser "evento já vinculado")


@dataclass
class EventQuery:
    found: bool
    event_xml: bytes | None = None


@dataclass
class DistributionPage:
    items: list[dict]
    last_nsu: int
    max_nsu: int | None
    has_more: bool
    retry_after_seconds: int | None = None


@dataclass
class CompanyContext:
    company_id: str
    cnpj: str
    environment: str


class NfseProvider(Protocol):
    environment: str
    simulated: bool

    def check_ready(self, company: CompanyContext, operation: str) -> None:
        """Levanta ProviderBlocked se a operação não pode ser executada."""

    def submit_dps(self, company: CompanyContext, dps: dict, payload: dict) -> SubmitOutcome: ...

    def query_dps(self, company: CompanyContext, dps_id: str) -> QueryOutcome: ...

    def fetch_distribution(self, company: CompanyContext, from_nsu: int) -> DistributionPage: ...

    def register_event(self, company: CompanyContext, access_key: str, code: str, c_motivo: str,
                       x_motivo: str) -> EventOutcome: ...

    def query_event(self, company: CompanyContext, access_key: str, code: str) -> EventQuery: ...
