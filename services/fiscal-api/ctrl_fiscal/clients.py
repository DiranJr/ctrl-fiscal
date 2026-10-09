"""Clientes internos (core-api e nfse-api) com identidade de serviço."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ctrl_common.service_auth import service_headers


class UpstreamError(Exception):
    """Falha de comunicação com serviço interno (sem detalhes sensíveis)."""

    def __init__(self, code: str, status: int | None = None, body: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.body = body or {}


@dataclass
class CompanyInfo:
    id: str
    name: str
    cnpj: str
    timezone: str
    environment: str
    municipality_code: str | None
    fiscal_profile: dict | None = None
    adn_environments: list | None = None
    is_demo: bool = False
    workspace_id: str | None = None
    workspace_status: str | None = None
    dps_series: str | None = None

    @staticmethod
    def from_dict(d: dict) -> "CompanyInfo":
        return CompanyInfo(
            id=d["id"], name=d["name"], cnpj=d["cnpj"], timezone=d["timezone"],
            environment=d["environment"], municipality_code=d.get("municipality_code"),
            fiscal_profile=d.get("fiscal_profile"), adn_environments=d.get("adn_environments"),
            is_demo=bool(d.get("is_demo")), workspace_id=d.get("workspace_id"),
            workspace_status=d.get("workspace_status"), dps_series=d.get("dps_series"),
        )

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "cnpj": self.cnpj, "timezone": self.timezone,
            "environment": self.environment, "municipality_code": self.municipality_code,
            "fiscal_profile": self.fiscal_profile, "adn_environments": self.adn_environments,
            "is_demo": self.is_demo, "workspace_id": self.workspace_id,
            "workspace_status": self.workspace_status, "dps_series": self.dps_series,
        }

    def readable_environments(self) -> list[str]:
        """Ambientes cujos documentos o usuário pode consultar (emissão + leitura ADN)."""
        return sorted({self.environment, *(self.adn_environments or [])})


@dataclass
class AuthzResult:
    user: dict
    company: CompanyInfo
    permissions: list[str]


class CoreClient:
    def __init__(self, http: httpx.Client, service_name: str, key: str) -> None:
        self._http = http
        self._headers = service_headers(service_name, key)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            return self._http.get(path, params=params, headers=self._headers, timeout=5.0)
        except httpx.HTTPError as exc:
            raise UpstreamError("CORE_UNAVAILABLE") from exc

    def authorize(self, user_id: str, session_id: str, company_id: str) -> AuthzResult:
        r = self._get("/internal/v1/authz", {"user_id": user_id, "session_id": session_id, "company_id": company_id})
        if r.status_code == 200:
            d = r.json()
            return AuthzResult(user=d["user"], company=CompanyInfo.from_dict(d["company"]), permissions=d["permissions"])
        body = _safe_json(r)
        if r.status_code in (401, 403):
            raise UpstreamError(body.get("code", "FORBIDDEN"), r.status_code, body)
        raise UpstreamError("CORE_UNAVAILABLE", r.status_code)

    def job_permissions(self, user_id: str, company_id: str) -> list[str] | None:
        """Permissões ATUAIS de quem pediu o job; None = sem acesso (inativo/sem vínculo). Erro = indisponível."""
        authz = self.job_authorization(user_id, company_id)
        return None if authz is None else list(authz.get("permissions") or [])

    def job_authorization(self, user_id: str, company_id: str) -> dict | None:
        """Autorização ATUAL completa (permissões, situação do workspace e ambiente de emissão vigente)."""
        r = self._get("/internal/v1/authz/job", {"user_id": user_id, "company_id": company_id})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (403, 404):
            return None
        raise UpstreamError("CORE_UNAVAILABLE", r.status_code)

    def customer(self, company_id: str, customer_id: str) -> dict | None:
        r = self._get(f"/internal/v1/companies/{company_id}/customers/{customer_id}")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise UpstreamError("CORE_UNAVAILABLE", r.status_code)

    def customer_by_document(self, company_id: str, document: str) -> dict | None:
        r = self._get(f"/internal/v1/companies/{company_id}/customers", {"document": document})
        if r.status_code == 200:
            return r.json()
        if r.status_code in (404, 422):
            return None
        raise UpstreamError("CORE_UNAVAILABLE", r.status_code)

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
            return self._http.post(path, json=body, headers=self._headers, timeout=15.0)
        except httpx.HTTPError as exc:
            raise UpstreamError("CORE_UNAVAILABLE") from exc

    def service_profile(self, company_id: str, profile_id: str) -> dict | None:
        """Perfil de serviço (situação, versão e pendências bloqueantes) — conferido na validação do rascunho."""
        r = self._get(f"/internal/v1/companies/{company_id}/service-profiles/{profile_id}")
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise UpstreamError("CORE_UNAVAILABLE", r.status_code)

    def prepare_service_profiles(self, company_id: str, proposals: list[dict], actor_id: str | None,
                                 actor_type: str = "user") -> dict:
        r = self._post(f"/internal/v1/companies/{company_id}/service-profiles/prepare",
                       {"actor_id": actor_id, "actor_type": actor_type, "proposals": proposals})
        if r.status_code == 200:
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "CORE_UNAVAILABLE"), r.status_code, _safe_json(r))

    def complete_provider(self, company_id: str, values: dict, sources: dict, actor_id: str | None,
                          actor_type: str = "user") -> dict:
        r = self._post(f"/internal/v1/companies/{company_id}/provider-data/complete",
                       {"actor_id": actor_id, "actor_type": actor_type, "values": values, "sources": sources})
        if r.status_code == 200:
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "CORE_UNAVAILABLE"), r.status_code, _safe_json(r))

    def companies(self) -> list[CompanyInfo]:
        out: list[CompanyInfo] = []
        page = 1
        while True:
            r = self._get("/internal/v1/companies", {"page": page, "page_size": 200})
            if r.status_code != 200:
                raise UpstreamError("CORE_UNAVAILABLE", r.status_code)
            d = r.json()
            out.extend(CompanyInfo.from_dict(c) for c in d["items"])
            if page * d["page_size"] >= d["total"]:
                return out
            page += 1


class NfseClient:
    """Chamadas do worker ao nfse-api (conector). Nenhuma chamada sai do browser."""

    def __init__(self, http: httpx.Client, key: str, service_name: str = "fiscal-worker") -> None:
        self._http = http
        self._headers = service_headers(service_name, key)

    def _call(self, method: str, path: str, **kw: Any) -> httpx.Response:
        try:
            return self._http.request(method, path, headers=self._headers, timeout=kw.pop("timeout", 60.0), **kw)
        except httpx.TimeoutException as exc:
            raise UpstreamError("NFSE_TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("NFSE_UNAVAILABLE") from exc

    def submit(self, payload: dict) -> dict:
        r = self._call("POST", "/internal/v1/submissions", json=payload)
        if r.status_code in (200, 201):
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))

    def get_submission(self, submission_id: str, reconcile: bool = True) -> dict | None:
        r = self._call("GET", f"/internal/v1/submissions/{submission_id}", params={"reconcile": str(reconcile).lower()})
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))

    def register_event(self, payload: dict) -> dict:
        r = self._call("POST", "/internal/v1/events", json=payload)
        if r.status_code in (200, 201):
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))

    def get_event(self, request_id: str, reconcile: bool = True) -> dict | None:
        r = self._call("GET", f"/internal/v1/events/{request_id}", params={"reconcile": str(reconcile).lower()})
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))

    def query_event(self, company_id: str, environment: str, cnpj: str, access_key: str, event_code: str) -> dict:
        r = self._call("GET", f"/internal/v1/companies/{company_id}/nfse/{access_key}/events/{event_code}",
                       params={"environment": environment, "cnpj": cnpj})
        if r.status_code == 200:
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))

    def distribution_page(self, company_id: str, environment: str, cnpj: str, from_nsu: int) -> dict:
        r = self._call(
            "POST",
            "/internal/v1/distribution/fetch",
            json={"company_id": company_id, "environment": environment, "cnpj": cnpj, "from_nsu": from_nsu},
        )
        if r.status_code == 200:
            return r.json()
        raise UpstreamError(_safe_json(r).get("code", "NFSE_ERROR"), r.status_code, _safe_json(r))


def _safe_json(r: httpx.Response) -> dict:
    try:
        d = r.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}
