"""Harness de testes integrando core-api, fiscal-api, nfse-api e fiscal-worker.

Banco: MySQL real quando CTRL_TEST_MYSQL_URL estiver definido (ex.:
mysql+pymysql://root:<senha>@127.0.0.1:3317), senão SQLite em arquivo.
As tabelas são criadas pelas migrations Alembic reais de cada serviço.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("services/shared", "services/core-api", "services/fiscal-api", "services/fiscal-worker", "services/nfse-api"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from ctrl_common.clock import FakeClock  # noqa: E402

MYSQL = os.environ.get("CTRL_TEST_MYSQL_URL")
if not MYSQL and os.environ.get("CTRL_TEST_MYSQL_URL_FILE"):
    # URL lida de arquivo local (secrets/, fora de versionamento), nunca impressa: scripts/pytest_mysql.py
    MYSQL = Path(os.environ["CTRL_TEST_MYSQL_URL_FILE"]).read_text(encoding="utf-8").strip()
SERVICES = {"core": "services/core-api", "fiscal": "services/fiscal-api", "nfse": "services/nfse-api"}
KEYS = {name: uuid.uuid4().hex + uuid.uuid4().hex for name in ("fiscal_api_core", "worker_core", "worker_nfse", "core_nfse")}
ORIGIN = "http://127.0.0.1:8088"
SP = timezone(timedelta(hours=-3))


def _migrate(service: str, url: str) -> None:
    cfg = Config(str(ROOT / SERVICES[service] / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / SERVICES[service] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def db_urls():
    run = uuid.uuid4().hex[:8]
    if MYSQL:
        admin = create_engine(MYSQL + "/mysql")
        urls = {}
        with admin.connect() as c:
            for svc in SERVICES:
                name = f"t{run}_{svc}"
                c.execute(text(f"CREATE DATABASE {name} CHARACTER SET utf8mb4"))
                urls[svc] = f"{MYSQL}/{name}?charset=utf8mb4"
        for svc, url in urls.items():
            _migrate(svc, url)
        yield urls
        with admin.connect() as c:
            for svc in SERVICES:
                c.execute(text(f"DROP DATABASE IF EXISTS t{run}_{svc}"))
        admin.dispose()
    else:
        d = tempfile.mkdtemp(prefix="ctrlfiscal-")
        urls = {svc: f"sqlite:///{d}/{svc}.db" for svc in SERVICES}
        for svc, url in urls.items():
            _migrate(svc, url)
        yield urls


def _truncate(url: str) -> None:
    eng = create_engine(url)
    with eng.begin() as c:
        if eng.dialect.name == "mysql":
            c.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            # Dados de referência semeados por migração (alíquotas com vigência) não são apagados entre testes.
            tables = [r[0] for r in c.execute(text("SHOW TABLES")) if r[0] not in ("alembic_version", "tax_rate_parameters")]
            for t in tables:
                c.execute(text(f"TRUNCATE TABLE `{t}`"))
            c.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        else:
            c.execute(text("PRAGMA foreign_keys=OFF"))
            tables = [r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
                      if r[0] not in ("alembic_version", "tax_rate_parameters") and not r[0].startswith("sqlite_")]
            for t in tables:
                c.execute(text(f'DELETE FROM "{t}"'))
    eng.dispose()


@pytest.fixture(scope="session")
def jwt_keys():
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


class Stack:
    """Serviços reais em processo, ligados por TestClient (sem rede)."""

    def __init__(self, urls: dict, keys: tuple[str, str], clock: FakeClock, tmp: Path, official: dict | None = None) -> None:
        """official: opções do conector oficial em teste (vault_key_b64, restricted_enabled,
        production_distribution_enabled, http_factory). Sem rede: o governo é um httpx.MockTransport."""
        from core_api.app import create_app as core_app
        from core_api.settings import CoreSettings
        from ctrl_fiscal.api.app import create_app as fiscal_app
        from ctrl_fiscal.clients import CoreClient, NfseClient
        from ctrl_fiscal.settings import FiscalSettings
        from ctrl_common.db import make_engine, make_sessionmaker
        from fiscal_worker.worker import Worker
        from nfse_api.app import create_app as nfse_app
        from nfse_api.providers.mock import MockProvider
        from nfse_api.settings import NfseSettings

        self.clock = clock
        self.urls = urls
        official = dict(official or {})
        http_factory = official.pop("http_factory", None)
        nfse_settings = NfseSettings(
            database_url=urls["nfse"], vault_key_b64=official.pop("vault_key_b64", None), app_env="test",
            internal_clients={
                "fiscal-worker": (KEYS["worker_nfse"], {"submissions:write", "submissions:read", "distribution:read", "integration:read",
                                                   "events:write", "events:read"}),
                "core-api": (KEYS["core_nfse"], {"integration:read", "certificates:write"}),
            },
            mock_page_size=3, **official,
        )
        self.nfse_settings = nfse_settings
        self.nfse_sessions = make_sessionmaker(make_engine(urls["nfse"]))
        self.mock = MockProvider(self.nfse_sessions, clock, page_size=3)
        self.nfse_app = nfse_app(nfse_settings, clock, self.mock, official_http_factory=http_factory)
        self.nfse = TestClient(self.nfse_app)

        self.core_settings = CoreSettings(
            database_url=urls["core"], jwt_private_key_pem=keys[0], jwt_public_key_pem=keys[1],
            csrf_secret="c" * 40, app_env="test", allowed_origins=[ORIGIN], cookie_secure=False,
            allow_insecure_cookie=True,
            internal_clients={
                "fiscal-api": (KEYS["fiscal_api_core"], {"authz", "customers:read", "companies:read",
                                                          "service_profiles:read", "service_profiles:prepare",
                                                          "provider:complete"}),
                "fiscal-worker": (KEYS["worker_core"], {"companies:read", "customers:read", "authz:job", "service_profiles:read"}),
            },
            nfse_api_url="http://nfse", nfse_api_key=KEYS["core_nfse"],
        )
        self.core_app = core_app(self.core_settings, clock, nfse_http=self.nfse)
        self.core = TestClient(self.core_app)
        self.core_sessions = self.core_app.state.ctx.sessions

        self.fiscal_settings = FiscalSettings(
            database_url=urls["fiscal"], app_env="test", jwt_public_key_pem=keys[1], core_api_url="http://core",
            core_api_key=KEYS["fiscal_api_core"], core_service_name="fiscal-api", export_dir=str(tmp / "exports"),
            lease_seconds=60, emission_max_in_flight=10, emission_batch_size=5,
        )
        self.fiscal_app = fiscal_app(self.fiscal_settings, clock, core_http=TestClient(self.core_app))
        self.fiscal = TestClient(self.fiscal_app)
        self.fiscal_sessions = self.fiscal_app.state.ctx.sessions
        self.worker_settings = FiscalSettings(
            database_url=urls["fiscal"], app_env="test", core_api_key=KEYS["worker_core"],
            core_service_name="fiscal-worker", nfse_api_key=KEYS["worker_nfse"], export_dir=str(tmp / "exports"),
            lease_seconds=60, emission_max_in_flight=10, emission_batch_size=5,
        )
        self._Worker, self._NfseClient, self._CoreClient = Worker, NfseClient, CoreClient
        self._make_sessions = lambda: make_sessionmaker(make_engine(urls["fiscal"]))

    def worker(self, worker_id: str = "w1", settings=None):  # noqa: ANN001, ANN201
        return self._Worker(
            settings or self.worker_settings, self._make_sessions(),
            self._NfseClient(TestClient(self.nfse_app), KEYS["worker_nfse"]),
            self._CoreClient(TestClient(self.core_app), "fiscal-worker", KEYS["worker_core"]),
            self.clock, worker_id=worker_id,
        )

    # ------------------------------------------------------------ dados
    def add_user(self, email: str, password: str = "senha-forte-123", name: str = "Usuário Teste") -> str:
        from core_api.models import User
        from core_api.security import hash_password

        now = self.clock.now()
        with self.core_sessions() as s:
            u = User(email=email, name=name, password_hash=hash_password(password), is_active=True, created_at=now, updated_at=now)
            s.add(u)
            s.commit()
            return u.id

    def add_company(self, name: str, cnpj: str, environment: str = "mock", tz: str = "America/Sao_Paulo",
                    municipality: str | None = None) -> str:
        from core_api.models import Company

        now = self.clock.now()
        with self.core_sessions() as s:
            from core_api.workspaces import ensure_workspace

            c = Company(name=name, cnpj=cnpj, timezone=tz, nfse_environment=environment, municipality_code=municipality,
                        dps_series="1", is_active=True, created_at=now, updated_at=now)
            s.add(c)
            s.flush()
            ensure_workspace(s, c, now, status="ACTIVE")
            s.commit()
            return c.id

    def grant(self, user_id: str, company_id: str, perms=("view", "import", "sync", "review", "prepare", "schedule", "emit", "cancel", "certificates", "admin")) -> None:
        from core_api.models import Membership

        now = self.clock.now()
        with self.core_sessions() as s:
            s.add(Membership(user_id=user_id, company_id=company_id, permissions=list(perms), created_at=now, updated_at=now))
            s.commit()

    def login(self, email: str, password: str = "senha-forte-123", client: TestClient | None = None) -> dict:
        r = (client or self.core).post("/api/core/v1/auth/login", json={"email": email, "password": password},
                                       headers={"Origin": ORIGIN})
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + r.json()["access_token"]}


class AuthHeaders(Mapping):
    """Cabeçalho Bearer que refaz login quando o relógio falso avança (token de 15 min)."""

    def __init__(self, stack: "Stack", email: str) -> None:
        self._stack, self._email = stack, email
        self._at = None
        self._h: dict = {}

    def _current(self) -> dict:
        now = self._stack.clock.now()
        if self._at is None or abs((now - self._at).total_seconds()) > 600:
            self._h, self._at = self._stack.login(self._email), now
        return self._h

    def __getitem__(self, k: str) -> str:
        return self._current()[k]

    def __iter__(self):  # noqa: ANN204
        return iter(self._current())

    def __len__(self) -> int:
        return len(self._current())


def cnpj(base12: str) -> str:
    from ctrl_common.fiscal_types import _cnpj_dv

    return base12 + _cnpj_dv(base12)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 10, 20, 10, 0, tzinfo=SP))


@pytest.fixture
def stack(db_urls, jwt_keys, clock, tmp_path):
    for url in db_urls.values():
        _truncate(url)
    return Stack(db_urls, jwt_keys, clock, tmp_path)


@pytest.fixture
def world(stack):
    """Duas empresas, um usuário em cada e um usuário com acesso só de leitura."""
    a_cnpj, b_cnpj = cnpj("112223330001"), cnpj("998887770001")
    ca = stack.add_company("Empresa A", a_cnpj)
    cb = stack.add_company("Empresa B", b_cnpj)
    ua = stack.add_user("a@ex.com")
    ub = stack.add_user("b@ex.com")
    ur = stack.add_user("leitor@ex.com")
    stack.grant(ua, ca)
    stack.grant(ub, cb)
    stack.grant(ur, ca, perms=("view",))
    ha = AuthHeaders(stack, "a@ex.com")
    cust = stack.core.post(f"/api/core/v1/companies/{ca}/customers", headers=ha, json={
        "name": "Cliente Demonstração 1 (LOCAL)", "document_type": "CNPJ", "document": cnpj("445556660001")})
    assert cust.status_code == 201, cust.text
    return {
        "a": ca, "b": cb, "a_cnpj": a_cnpj, "b_cnpj": b_cnpj, "ha": ha, "hb": AuthHeaders(stack, "b@ex.com"),
        "hr": AuthHeaders(stack, "leitor@ex.com"), "customer": cust.json()["id"], "ua": ua,
    }


@pytest.fixture(autouse=True)
def _dispose_engines():
    """MySQL: cada Stack cria pools de conexão por serviço; descartá-los ao fim de cada teste evita esgotar
    max_connections da instância de testes (erro 1040) com a suíte crescendo."""
    yield
    if MYSQL:
        import gc

        from sqlalchemy.engine import Engine

        for obj in gc.get_objects():
            if isinstance(obj, Engine):
                obj.dispose()
