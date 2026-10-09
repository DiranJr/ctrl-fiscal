"""Validação ICP-Brasil do certificado A1 da empresa: cadeia, revogação, uso e identidade.

- Cadeia: verificador estrito RFC 5280 da biblioteca `cryptography` (ClientVerifier: exige uso estendido de
  autenticação de cliente) até uma raiz OFICIAL ICP-Brasil. Âncoras e ACs intermediárias vêm do pacote do ITI
  (nfse_api/icp_ancoras/, gerado por scripts/atualizar_ancoras_icp.py com SHA-512 conferido); ACs incluídas no PFX são
  aceitas apenas como caminho, nunca como âncora.
- Revogação: LCR do ponto de distribuição do certificado (HTTP/HTTPS, sem redirecionamento, tamanho limitado),
  com assinatura conferida pela AC emissora já validada na cadeia, emissor coincidente e nextUpdate vigente.
  Resultado GOOD / REVOKED / UNAVAILABLE. Só GOOD libera o uso.
- O modo de teste (`revocation_mode="test-skip"`) só é aceito com APP_ENV=test (ver NfseSettings.validate).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

BUNDLE = Path(__file__).resolve().parent / "icp_ancoras"
MAX_CRL_BYTES = 40 * 1024 * 1024
REVOCATION_MAX_AGE = timedelta(hours=24)


def _cn(cert: x509.Certificate) -> str:
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else cert.subject.rfc4514_string()


@dataclass
class ChainResult:
    ok: bool
    chain: list[x509.Certificate] = field(default_factory=list)
    error: str | None = None

    @property
    def issuer(self) -> x509.Certificate | None:
        return self.chain[1] if len(self.chain) > 1 else None


class _Fetched:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


def _public_host(host: str) -> bool:
    """SSRF: o endereço de LCR vem de certificado já validado até a raiz ICP-Brasil, mas ainda assim só se aceita
    destino que resolva para IP público (nada de loopback, rede interna, link-local/metadados)."""
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return bool(infos)


def _fetch_crl(url: str, timeout: float) -> _Fetched:
    host = httpx.URL(url).host
    if not _public_host(host):
        return _Fetched(599, b"")
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as c, c.stream("GET", url) as r:
        if r.status_code != 200:
            return _Fetched(r.status_code, b"")
        buf = bytearray()
        for chunk in r.iter_bytes():
            buf += chunk
            if len(buf) > MAX_CRL_BYTES:
                return _Fetched(413, b"")
        return _Fetched(200, bytes(buf))


class IcpValidator:
    def __init__(self, roots_pem: bytes | None = None, intermediates_pem: bytes | None = None,
                 revocation_mode: str = "online", http_get: Callable[[str], httpx.Response] | None = None,
                 timeout: float = 30.0) -> None:
        self.roots = x509.load_pem_x509_certificates(roots_pem or (BUNDLE / "raizes.pem").read_bytes())
        inter = intermediates_pem if intermediates_pem is not None else (BUNDLE / "intermediarias.pem").read_bytes()
        self.intermediates = x509.load_pem_x509_certificates(inter) if inter.strip() else []
        if revocation_mode not in ("online", "test-skip"):
            raise ValueError("revocation_mode inválido")
        self.revocation_mode = revocation_mode
        self._http_get = http_get or (lambda url: _fetch_crl(url, timeout))
        self._crl_cache: dict[str, tuple[x509.CertificateRevocationList, datetime]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ cadeia
    def verify_chain(self, leaf: x509.Certificate, extra: list[x509.Certificate], now: datetime) -> ChainResult:
        verifier = PolicyBuilder().store(Store(self.roots)).time(now).build_client_verifier()
        try:
            verified = verifier.verify(leaf, [*extra, *self.intermediates])
        except VerificationError as exc:
            return ChainResult(False, error=str(exc)[:300])
        except Exception as exc:  # noqa: BLE001 - certificado malformado para o verificador
            return ChainResult(False, error=f"{type(exc).__name__}: {str(exc)[:250]}")
        return ChainResult(True, chain=list(verified.chain))

    # ------------------------------------------------------------------ revogação
    def revocation(self, leaf: x509.Certificate, issuer: x509.Certificate | None, now: datetime) -> tuple[str, dict]:
        if self.revocation_mode == "test-skip":
            return "GOOD", {"mode": "test-skip"}
        if issuer is None:
            return "UNAVAILABLE", {"reason": "emissor não identificado na cadeia"}
        try:
            dps = leaf.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
        except x509.ExtensionNotFound:
            return "UNAVAILABLE", {"reason": "certificado sem ponto de distribuição de LCR"}
        urls = [n.value for dp in dps for n in (dp.full_name or [])
                if isinstance(n, x509.UniformResourceIdentifier) and n.value.lower().startswith(("http://", "https://"))]
        reasons = []
        for url in urls:
            crl, why = self._crl(url, issuer, now)
            if crl is None:
                reasons.append(why)
                continue
            revoked = crl.get_revoked_certificate_by_serial_number(leaf.serial_number)
            detail = {"crl_url_host": url.split("/")[2], "crl_next_update": crl.next_update_utc.isoformat()}
            return ("REVOKED", detail) if revoked is not None else ("GOOD", detail)
        return "UNAVAILABLE", {"reason": "; ".join(reasons)[:300] or "sem URL HTTP de LCR"}

    def _crl(self, url: str, issuer: x509.Certificate, now: datetime):  # noqa: ANN202
        with self._lock:
            hit = self._crl_cache.get(url)
        if hit and hit[1] > now:
            return hit[0], None
        try:
            r = self._http_get(url)
        except Exception as exc:  # noqa: BLE001
            return None, f"{url.split('/')[2]}: {type(exc).__name__}"
        if r.status_code != 200 or len(r.content) > MAX_CRL_BYTES:
            return None, f"{url.split('/')[2]}: HTTP {r.status_code}"
        try:
            data = r.content
            crl = x509.load_pem_x509_crl(data) if data.lstrip().startswith(b"-----") else x509.load_der_x509_crl(data)
        except ValueError:
            return None, f"{url.split('/')[2]}: LCR ilegível"
        if crl.issuer != issuer.subject or not crl.is_signature_valid(issuer.public_key()):
            return None, f"{url.split('/')[2]}: LCR não assinada pela AC emissora"
        nxt = crl.next_update_utc
        if nxt is None or nxt < now:
            return None, f"{url.split('/')[2]}: LCR vencida"
        with self._lock:
            self._crl_cache[url] = (crl, nxt)
        return crl, None

    # ------------------------------------------------------------------ avaliação completa
    def evaluate(self, leaf: x509.Certificate, extra: list[x509.Certificate], now: datetime) -> dict:
        """Resultado gravado em Certificate.checks (somente dados públicos)."""
        chain = self.verify_chain(leaf, extra, now)
        out: dict = {"icp_brasil_chain": chain.ok, "chain_error": chain.error,
                     "leaf_pem": leaf.public_bytes(Encoding.PEM).decode(),
                     "chain_cns": [_cn(c) for c in chain.chain[1:]],
                     "chain_pem": [c.public_bytes(Encoding.PEM).decode() for c in chain.chain[1:]] if chain.ok else []}
        if chain.ok:
            status, detail = self.revocation(leaf, chain.issuer, now)
        else:
            status, detail = "UNAVAILABLE", {"reason": "cadeia não validada"}
        out.update({"revocation": status, "revocation_detail": detail, "revocation_checked_at": now.isoformat()})
        return out

    def recheck_revocation(self, checks: dict, now: datetime) -> dict | None:
        """Reconfirma a revogação quando a última verificação passou de 24 h. Devolve checks atualizados ou None."""
        last = checks.get("revocation_checked_at")
        leaf_pem = (checks.get("leaf_pem") or "").encode()
        if not leaf_pem or not checks.get("icp_brasil_chain") or not checks.get("chain_pem"):
            return None
        if last and now - datetime.fromisoformat(last) < REVOCATION_MAX_AGE:
            return None
        leaf = x509.load_pem_x509_certificate(leaf_pem)
        issuer = x509.load_pem_x509_certificate(checks["chain_pem"][0].encode())
        status, detail = self.revocation(leaf, issuer, now)
        return {**checks, "revocation": status, "revocation_detail": detail, "revocation_checked_at": now.isoformat()}
