"""Transporte HTTP oficial (Sefin Nacional / ADN Contribuintes).

- Hosts fixos por ambiente (allowlist); nenhuma URL vem de entrada externa.
- TLS com verificação ativa (CERT_REQUIRED + hostname) e mTLS com o A1.
- Timeout, backoff limitado, teto de tamanho de resposta e erros sanitizados.
Paths confirmados nos manuais oficiais; BASE PATHS e campos JSON NÃO estão
confirmados (Swagger exige certificado) — especificações em docs/oficial/.
O provider oficial só usa este transporte quando o contrato estiver confirmado.
"""
from __future__ import annotations

import os
import ssl
import tempfile
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import logging
import re

import httpx

_audit = logging.getLogger("ctrl.nfse.chamadas_oficiais")


def _mask(path: str) -> str:
    """Caminho sem query e sem identificadores (CNPJ, chave de acesso, Id DPS, NSU longos)."""
    p = path.split("?", 1)[0]
    p = re.sub(r"DPS[0-9]{42}", "DPS<id>", p)
    return re.sub(r"(?<![0-9])[0-9A-Z]{11,}(?![0-9A-Z])", "<id>", p) + ("?<query>" if "?" in path else "")

# Hosts e base paths CONFIRMADOS em 07/10/2026 (docs/oficial/manifesto-contratos.json):
# - Sefin: Swagger 2.0 oficial (host/basePath) em .../SefinNacional/swagger/docs/v1 → basePath /SefinNacional.
# - ADN Contribuintes: OpenAPI sem "servers"; base confirmada por GET somente leitura
#   (/contribuintes/DFe/0 → 200; /DFe/0 → 404).
# - Parametrização: OpenAPI "servers": [{"url": "/parametrizacao"}].
HOSTS = {
    "restricted": {"sefin": "sefin.producaorestrita.nfse.gov.br", "adn": "adn.producaorestrita.nfse.gov.br",
                   "param": "adn.producaorestrita.nfse.gov.br"},
    "production": {"sefin": "sefin.nfse.gov.br", "adn": "adn.nfse.gov.br", "param": "adn.nfse.gov.br"},
}
BASE_PATHS: dict[str, dict[str, str | None]] = {
    "restricted": {"sefin": "/SefinNacional", "adn": "/contribuintes", "param": "/parametrizacao"},
    "production": {"sefin": "/SefinNacional", "adn": "/contribuintes", "param": "/parametrizacao"},
}
# Paths relativos CONFIRMADOS nas especificações oficiais.
PATHS = {
    "sefin_post_nfse": "/nfse",
    "sefin_get_nfse": "/nfse/{chave}",
    "sefin_get_dps": "/dps/{id}",
    "sefin_head_dps": "/dps/{id}",
    "adn_dfe": "/DFe/{nsu}",
    "adn_events": "/NFSe/{chave}/Eventos",
    "param_convenio": "/{municipio}/convenio",
}


class TransportError(Exception):
    """Erro sanitizado: sem corpo bruto, cabeçalhos ou segredos."""

    def __init__(self, code: str, *, uncertain: bool, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain  # True = pode ter sido processado do outro lado
        self.status = status


class ContractNotConfirmed(Exception):
    pass


@dataclass
class TransportResponse:
    status: int
    body: bytes
    content_type: str


def build_ssl_context(cert_pem: bytes | None, key_pem: bytes | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert_pem and key_pem:
        # load_cert_chain exige arquivo: grava em diretório privado e remove em seguida.
        d = tempfile.mkdtemp(prefix="mtls-")
        cp, kp = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
        try:
            for path, data in ((cp, cert_pem), (kp, key_pem)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            ctx.load_cert_chain(cp, kp)
        finally:
            for path in (cp, kp):
                try:
                    os.remove(path)
                except OSError:
                    pass
            os.rmdir(d)
    return ctx


class OfficialTransport:
    def __init__(
        self, environment: str, service: str, ssl_context: ssl.SSLContext | None, *, timeout: float = 30.0,
        max_response_bytes: int = 10 * 1024 * 1024, max_retries: int = 2,
        http: httpx.Client | None = None, base_paths: dict | None = None,
    ) -> None:
        if environment not in HOSTS or service not in ("sefin", "adn", "param"):
            raise ValueError("ambiente/serviço oficial desconhecido")
        self.environment = environment
        self.service = service
        self.host = HOSTS[environment][service]
        self.base_path = (base_paths or BASE_PATHS)[environment][service]
        self.max_response_bytes = max_response_bytes
        self.max_retries = max_retries
        self._http = http or httpx.Client(
            verify=ssl_context or build_ssl_context(None, None), timeout=httpx.Timeout(timeout),
            follow_redirects=False, trust_env=False,
        )

    def url(self, path: str) -> str:
        if self.base_path is None:
            raise ContractNotConfirmed(f"Base path oficial de {self.service}/{self.environment} não confirmado")
        url = f"https://{self.host}{self.base_path}{path}"
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != self.host:
            raise TransportError("HOST_NOT_ALLOWED", uncertain=False)
        return url

    def request(self, method: str, path: str, *, json: dict | None = None, idempotent: bool) -> TransportResponse:
        """Executa e registra a chamada oficial (auditoria em log, sem dados fiscais nem identificadores)."""
        t0 = time.monotonic()
        try:
            resp = self._request(method, path, json=json, idempotent=idempotent)
        except TransportError as exc:
            _audit.info("chamada_oficial ambiente=%s servico=%s metodo=%s host=%s caminho=%s resultado=%s incerto=%s ms=%d",
                        self.environment, self.service, method, self.host, _mask(path), exc.code, exc.uncertain,
                        (time.monotonic() - t0) * 1000)
            raise
        _audit.info("chamada_oficial ambiente=%s servico=%s metodo=%s host=%s caminho=%s resultado=HTTP_%s bytes=%d ms=%d",
                    self.environment, self.service, method, self.host, _mask(path), resp.status, len(resp.body),
                    (time.monotonic() - t0) * 1000)
        return resp

    def _request(self, method: str, path: str, *, json: dict | None = None, idempotent: bool) -> TransportResponse:
        """POST não idempotente nunca é repetido aqui: quem decide é a reconciliação."""
        url = self.url(path)
        attempts = self.max_retries + 1 if idempotent else 1
        for attempt in range(attempts):
            try:
                with self._http.stream(method, url, json=json, headers={"Accept": "application/json"}) as r:
                    if r.is_redirect:
                        raise TransportError("REDIRECT_NOT_ALLOWED", uncertain=method != "GET", status=r.status_code)
                    chunks, size = [], 0
                    for chunk in r.iter_bytes():
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise TransportError("RESPONSE_TOO_LARGE", uncertain=method != "GET", status=r.status_code)
                        chunks.append(chunk)
                    if r.status_code >= 500 and idempotent and attempt + 1 < attempts:
                        time.sleep(min(8.0, 0.5 * 2 ** attempt))
                        continue
                    return TransportResponse(r.status_code, b"".join(chunks), r.headers.get("content-type", ""))
            except httpx.ConnectError as exc:
                # Falha antes de enviar o pedido: seguro afirmar que não foi processado.
                if idempotent and attempt + 1 < attempts:
                    time.sleep(min(8.0, 0.5 * 2 ** attempt))
                    continue
                raise TransportError("CONNECT_FAILED", uncertain=False) from exc
            except httpx.TimeoutException as exc:
                if idempotent and attempt + 1 < attempts:
                    time.sleep(min(8.0, 0.5 * 2 ** attempt))
                    continue
                raise TransportError("TIMEOUT", uncertain=not idempotent) from exc
            except httpx.HTTPError as exc:
                raise TransportError("TRANSPORT_FAILED", uncertain=not idempotent) from exc
        raise TransportError("RETRIES_EXHAUSTED", uncertain=not idempotent)
