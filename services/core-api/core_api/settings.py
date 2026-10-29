from __future__ import annotations

from dataclasses import dataclass, field

from ctrl_common.config import ConfigError, database_url, env, env_bool, env_int, env_list, secret

PERMISSIONS = ("view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin")
ENVIRONMENTS = ("mock", "restricted", "production")


@dataclass
class CoreSettings:
    database_url: str
    jwt_private_key_pem: str
    jwt_public_key_pem: str
    csrf_secret: str
    app_env: str = "production"
    jwt_kid: str = "core-1"
    access_ttl_seconds: int = 900
    refresh_ttl_seconds: int = 7 * 24 * 3600
    session_max_seconds: int = 30 * 24 * 3600
    refresh_reuse_grace_seconds: int = 5
    allowed_origins: list[str] = field(default_factory=list)
    cookie_secure: bool = True
    allow_insecure_cookie: bool = False
    login_max_failures_per_email: int = 5
    login_max_failures_per_ip: int = 30
    login_window_seconds: int = 900
    internal_clients: dict[str, tuple[str, set[str]]] = field(default_factory=dict)
    nfse_api_url: str | None = None
    nfse_api_key: str | None = None
    enable_docs: bool = False
    # Proxies cujo X-Real-IP é aceito (nomes DNS da rede interna, ex.: "gateway"); resolvidos a cada minuto.
    trusted_proxy_hosts: list[str] = field(default_factory=list)
    _proxy_cache: dict = field(default_factory=dict, repr=False)

    def is_trusted_proxy(self, peer: str) -> bool:
        import socket
        import time

        if not self.trusted_proxy_hosts:
            return False
        now = time.monotonic()
        if self._proxy_cache.get("until", 0) < now:
            ips: set[str] = set()
            for h in self.trusted_proxy_hosts:
                try:
                    ips |= {i[4][0] for i in socket.getaddrinfo(h, None)}
                except OSError:
                    pass
            self._proxy_cache.update(ips=ips, until=now + 60)
        return peer in self._proxy_cache.get("ips", set())

    def validate(self) -> None:
        if self.app_env not in ("development", "test", "production"):
            raise ConfigError("APP_ENV inválido")
        if not self.cookie_secure:
            if self.app_env == "production":
                raise ConfigError("Produção não inicia com cookie sem Secure")
            if not self.allow_insecure_cookie:
                raise ConfigError("Cookie sem Secure exige ALLOW_INSECURE_DEV_COOKIE=true explícito")
        if not self.allowed_origins:
            raise ConfigError("ALLOWED_ORIGINS obrigatório")
        if self.app_env == "production":
            for origin in self.allowed_origins:
                if not origin.startswith("https://"):
                    raise ConfigError("Produção exige origens HTTPS")
            if self.enable_docs:
                raise ConfigError("Documentação interativa desabilitada em produção")
        if len(self.csrf_secret) < 32:
            raise ConfigError("CSRF_SECRET curto")

    @property
    def cookie_name(self) -> str:
        return "__Secure-ctrl_refresh" if self.cookie_secure else "ctrl_refresh"

    @staticmethod
    def from_env() -> "CoreSettings":
        clients: dict[str, tuple[str, set[str]]] = {}
        k = secret("INTERNAL_KEY_FISCAL_API_TO_CORE", required=False)
        if k:
            clients["fiscal-api"] = (k, {"authz", "customers:read", "companies:read", "service_profiles:read",
                                         "service_profiles:prepare", "provider:complete", "parametrizacao:read"})
        k = secret("INTERNAL_KEY_FISCAL_WORKER_TO_CORE", required=False)
        if k:
            clients["fiscal-worker"] = (k, {"companies:read", "customers:read", "authz:job", "service_profiles:read"})
        s = CoreSettings(
            database_url=database_url("CORE"),
            jwt_private_key_pem=secret("JWT_PRIVATE_KEY"),
            jwt_public_key_pem=secret("JWT_PUBLIC_KEY"),
            csrf_secret=secret("CSRF_SECRET", min_length=32),
            app_env=env("APP_ENV", "production"),
            jwt_kid=env("JWT_KID", "core-1"),
            access_ttl_seconds=env_int("ACCESS_TOKEN_TTL_SECONDS", 900),
            refresh_ttl_seconds=env_int("REFRESH_TOKEN_TTL_SECONDS", 7 * 24 * 3600),
            session_max_seconds=env_int("SESSION_MAX_SECONDS", 30 * 24 * 3600),
            refresh_reuse_grace_seconds=env_int("REFRESH_REUSE_GRACE_SECONDS", 5),
            allowed_origins=env_list("ALLOWED_ORIGINS"),
            cookie_secure=not env_bool("ALLOW_INSECURE_DEV_COOKIE", False),
            allow_insecure_cookie=env_bool("ALLOW_INSECURE_DEV_COOKIE", False),
            login_max_failures_per_email=env_int("LOGIN_MAX_FAILURES_PER_EMAIL", 5),
            login_max_failures_per_ip=env_int("LOGIN_MAX_FAILURES_PER_IP", 30),
            login_window_seconds=env_int("LOGIN_WINDOW_SECONDS", 900),
            internal_clients=clients,
            nfse_api_url=env("NFSE_API_URL"),
            nfse_api_key=secret("INTERNAL_KEY_CORE_TO_NFSE", required=False),
            enable_docs=env_bool("ENABLE_API_DOCS", False),
            trusted_proxy_hosts=env_list("TRUSTED_PROXY_HOSTS"),
        )
        s.validate()
        return s
