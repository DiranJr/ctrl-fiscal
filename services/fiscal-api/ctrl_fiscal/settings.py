from __future__ import annotations

from dataclasses import dataclass, field

from ctrl_common.config import ConfigError, database_url, env, env_bool, env_int, secret

from .nfse_xml import Limits


@dataclass
class FiscalSettings:
    database_url: str
    app_env: str = "production"
    jwt_public_key_pem: str | None = None
    core_api_url: str | None = None
    core_api_key: str | None = None
    core_service_name: str = "fiscal-api"
    nfse_api_url: str | None = None
    nfse_api_key: str | None = None
    enable_docs: bool = False
    export_dir: str = "/data/exports"
    export_max_rows: int = 200_000
    export_ttl_hours: int = 24  # arquivos de exportação/lote expiram e são apagados pelo worker
    bundle_max_documents_pdf: int = 1000  # PDF ou PDF+XML por arquivo
    bundle_max_documents_xml: int = 5000
    bundle_max_bytes: int = 1024 * 1024 * 1024
    upload_max_bytes: int = 25 * 1024 * 1024
    upload_max_files: int = 50
    job_max_errors_stored: int = 1000
    xml_limits: Limits = field(default_factory=Limits)
    dps_series: str = "1"
    lease_seconds: int = 120
    emission_max_in_flight: int = 10
    emission_batch_size: int = 5
    max_batch_drafts: int = 500
    schedule_past_tolerance_seconds: int = 120
    sync_interval_hours: int = 12
    # Cancelamento em PRODUÇÃO pela plataforma: política própria (separada da emissão), desligada por padrão.
    production_cancellation_enabled: bool = False
    sync_config_recheck_seconds: int = 3600
    sync_empty_cooldown_seconds: int = 3600  # ADN: aguardar ≥1h quando não há mais documentos
    sync_max_pages_per_cycle: int = 20
    sync_error_backoff_seconds: int = 900
    companies_refresh_seconds: int = 600

    def validate(self) -> None:
        if self.app_env not in ("development", "test", "production"):
            raise ConfigError("APP_ENV inválido")
        if self.app_env == "production" and self.enable_docs:
            raise ConfigError("Documentação interativa desabilitada em produção")
        series = int(self.dps_series)
        # Faixa 00001–49999 reservada a aplicativo próprio/API (Anexo I, E0010).
        if not 1 <= series <= 49999:
            raise ConfigError("DPS_SERIES fora da faixa 1–49999 (aplicativo próprio)")

    @staticmethod
    def from_env(service_name: str) -> "FiscalSettings":
        is_worker = service_name == "fiscal-worker"
        s = FiscalSettings(
            database_url=database_url("FISCAL"),
            app_env=env("APP_ENV", "production"),
            jwt_public_key_pem=None if is_worker else secret("JWT_PUBLIC_KEY"),
            core_api_url=env("CORE_API_URL", "http://core-api:8000"),
            core_api_key=secret("INTERNAL_KEY_FISCAL_WORKER_TO_CORE" if is_worker else "INTERNAL_KEY_FISCAL_API_TO_CORE"),
            core_service_name=service_name,
            nfse_api_url=env("NFSE_API_URL", "http://nfse-api:8000") if is_worker else None,
            nfse_api_key=secret("INTERNAL_KEY_FISCAL_WORKER_TO_NFSE") if is_worker else None,
            enable_docs=env_bool("ENABLE_API_DOCS", False),
            export_dir=env("EXPORT_DIR", "/data/exports"),
            export_max_rows=env_int("EXPORT_MAX_ROWS", 200_000),
            export_ttl_hours=env_int("EXPORT_TTL_HOURS", 24),
            bundle_max_documents_pdf=env_int("BUNDLE_MAX_DOCUMENTS_PDF", 1000),
            bundle_max_documents_xml=env_int("BUNDLE_MAX_DOCUMENTS_XML", 5000),
            bundle_max_bytes=env_int("BUNDLE_MAX_BYTES", 1024 * 1024 * 1024),
            upload_max_bytes=env_int("UPLOAD_MAX_BYTES", 25 * 1024 * 1024),
            upload_max_files=env_int("UPLOAD_MAX_FILES", 50),
            xml_limits=Limits(
                max_xml_bytes=env_int("XML_MAX_BYTES", 2 * 1024 * 1024),
                max_zip_entries=env_int("ZIP_MAX_ENTRIES", 500),
                max_zip_total_uncompressed=env_int("ZIP_MAX_TOTAL_UNCOMPRESSED", 200 * 1024 * 1024),
                max_zip_ratio=env_int("ZIP_MAX_RATIO", 100),
                max_gzip_bytes=env_int("GZIP_MAX_BYTES", 5 * 1024 * 1024),
            ),
            dps_series=env("DPS_SERIES", "1"),
            lease_seconds=env_int("LEASE_SECONDS", 120),
            emission_max_in_flight=env_int("EMISSION_MAX_IN_FLIGHT", 10),
            emission_batch_size=env_int("EMISSION_BATCH_SIZE", 5),
            sync_interval_hours=env_int("SYNC_INTERVAL_HOURS", 12),
            sync_max_pages_per_cycle=env_int("SYNC_MAX_PAGES_PER_CYCLE", 20),
            production_cancellation_enabled=env_bool("NFSE_PRODUCTION_CANCELLATION_ENABLED", False),
        )
        s.validate()
        return s
