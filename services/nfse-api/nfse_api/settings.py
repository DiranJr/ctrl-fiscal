from __future__ import annotations

from dataclasses import dataclass, field

from ctrl_common.config import ConfigError, database_url, env, env_bool, env_int, secret


@dataclass
class NfseSettings:
    database_url: str
    vault_key_b64: str | None
    app_env: str = "production"
    internal_clients: dict[str, tuple[str, set[str]]] = field(default_factory=dict)
    restricted_enabled: bool = False
    # Produção: emissão/consulta de DPS bloqueadas em código. Esta flag habilita SOMENTE a leitura ADN.
    production_enabled: bool = False
    production_distribution_enabled: bool = False
    # Cancelamento (eventos do emitente) em PRODUÇÃO: política própria, separada da emissão. Desligado por padrão.
    production_cancellation_enabled: bool = False
    production_emission_enabled: bool = False  # NFSE_PRODUCTION_EMISSION_ENABLED: padrão desligada
    # Mock somente em testes automatizados; em execução fica desligado (sem fallback).
    mock_enabled: bool = True
    mock_page_size: int = 3
    # Validação ICP-Brasil: "online" (LCR real) em execução; "test-skip" só com APP_ENV=test.
    revocation_mode: str = "online"
    trust_roots_pem: bytes | None = None  # testes: âncora de AC de teste; None = pacote oficial ITI
    trust_intermediates_pem: bytes | None = None
    http_timeout_seconds: float = 30.0
    max_response_bytes: int = 10 * 1024 * 1024
    sending_lease_seconds: int = 300

    def validate(self) -> None:
        if self.app_env not in ("development", "test", "production"):
            raise ConfigError("APP_ENV inválido")
        if not self.internal_clients:
            raise ConfigError("Nenhum cliente interno configurado para o nfse-api")
        if self.revocation_mode not in ("online", "test-skip"):
            raise ConfigError("revocation_mode inválido")
        if self.app_env != "test" and (self.revocation_mode != "online" or self.trust_roots_pem is not None
                                       or self.trust_intermediates_pem is not None):
            raise ConfigError("Âncoras de teste e revogação desligada só são permitidas com APP_ENV=test")

    def icp_validator(self):  # noqa: ANN201
        from .icp import IcpValidator

        return IcpValidator(self.trust_roots_pem, self.trust_intermediates_pem, self.revocation_mode,
                            timeout=self.http_timeout_seconds)

    @staticmethod
    def from_env() -> "NfseSettings":
        clients: dict[str, tuple[str, set[str]]] = {}
        k = secret("INTERNAL_KEY_FISCAL_WORKER_TO_NFSE", required=False)
        if k:
            clients["fiscal-worker"] = (k, {"submissions:write", "submissions:read", "distribution:read", "integration:read",
                                            "events:write", "events:read"})
        k = secret("INTERNAL_KEY_CORE_TO_NFSE", required=False)
        if k:
            clients["core-api"] = (k, {"integration:read", "certificates:write"})
        s = NfseSettings(
            database_url=database_url("NFSE"),
            vault_key_b64=secret("NFSE_VAULT_KEY", required=False),
            app_env=env("APP_ENV", "production"),
            internal_clients=clients,
            restricted_enabled=env_bool("NFSE_RESTRICTED_ENABLED", False),
            production_enabled=env_bool("NFSE_PRODUCTION_ENABLED", False),
            production_distribution_enabled=env_bool("NFSE_PRODUCTION_DISTRIBUTION_ENABLED", False),
            production_cancellation_enabled=env_bool("NFSE_PRODUCTION_CANCELLATION_ENABLED", False),
            production_emission_enabled=env_bool("NFSE_PRODUCTION_EMISSION_ENABLED", False),
            mock_enabled=env_bool("NFSE_MOCK_ENABLED", False),
            mock_page_size=env_int("NFSE_MOCK_PAGE_SIZE", 3),
        )
        s.validate()
        return s
