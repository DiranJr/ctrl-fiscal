"""fiscal-worker: orquestra filas persistentes do domínio fiscal.

Não contém regra de negócio própria: usa ctrl_fiscal.domain (o mesmo código
da fiscal-api). Várias réplicas podem rodar ao mesmo tempo; a exclusividade
vem dos leases no banco (não há cron por réplica da API).
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime

import httpx
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock, SystemClock
from ctrl_common.config import env
from ctrl_common.db import make_engine, make_sessionmaker
from ctrl_common.logs import configure_logging
from ctrl_fiscal.clients import CoreClient, NfseClient, UpstreamError
from ctrl_fiscal.domain import bundles, cancelamento_worker, emission, jobs, sync
from ctrl_fiscal.settings import FiscalSettings

log = logging.getLogger("ctrl.worker")


class Worker:
    def __init__(
        self, settings: FiscalSettings, sessions: sessionmaker, nfse: NfseClient, core: CoreClient | None,
        clock: Clock | None = None, worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.nfse = nfse
        self.core = core
        self.clock = clock or SystemClock()
        self.id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._last_company_refresh: datetime | None = None
        self._companies: dict = {}
        self._last_purge: datetime | None = None
        self.after_external = None  # gancho de teste para simular queda
        # Sem core configurado (somente testes unitários antigos) não há como conferir: bloqueia por segurança.
        self.authorize = emission.core_authorizer(core) if core is not None else (lambda it: None)
        self.authorize_cancel = cancelamento_worker.core_authorizer(core) if core is not None else (lambda it: None)

    # -------------------------------------------------- emissão
    def run_emissions(self) -> int:
        st = self.settings
        leased = emission.acquire_items(
            self.sessions, self.clock, self.id, limit=st.emission_batch_size, lease_seconds=st.lease_seconds,
            max_in_flight=st.emission_max_in_flight, series=st.dps_series,
        )
        for it in leased:
            try:
                emission.process_item(self.sessions, self.nfse, self.clock, self.id, it, self.after_external,
                                      authorize=self.authorize)
            except Exception as exc:  # o lease expira e outro worker reconcilia
                if isinstance(exc, SimulatedCrash):
                    raise
                log.error("falha ao processar item=%s tipo=%s", it.id, type(exc).__name__)
        return len(leased)

    # -------------------------------------------------- jobs
    def run_jobs(self) -> int:
        st = self.settings
        acquired = jobs.acquire_job(self.sessions, self.clock, self.id, st.lease_seconds)
        if acquired is None:
            return 0
        job_id, token = acquired
        with self.sessions() as s:
            from ctrl_fiscal.models import Job

            kind = s.get(Job, job_id).type
        try:
            if kind == "IMPORT":
                jobs.run_import(self.sessions, self.clock, self.id, job_id, token, st.xml_limits,
                                st.job_max_errors_stored, st.lease_seconds)
            elif kind == "EXPORT":
                jobs.run_export(self.sessions, self.clock, self.id, job_id, token, st.export_dir, st.export_max_rows,
                                st.export_ttl_hours)
            elif kind == "BUNDLE":
                bundles.run_bundle(self.sessions, self.clock, self.id, job_id, token, st.export_dir,
                                   st.export_ttl_hours, st.lease_seconds, st.bundle_max_bytes)
        except Exception as exc:
            log.error("falha no job=%s tipo=%s", job_id, type(exc).__name__)
        return 1

    # -------------------------------------------------- sincronização
    def refresh_companies(self, force: bool = False) -> None:
        if self.core is None:
            return
        now = self.clock.now()
        if not force and self._last_company_refresh and (
            (now - self._last_company_refresh).total_seconds() < self.settings.companies_refresh_seconds
        ):
            return
        try:
            companies = self.core.companies()
        except UpstreamError:
            log.warning("core indisponível para atualizar agendas de sincronização")
            return
        self._companies = {c.id: c for c in companies}
        for company in companies:
            with self.sessions() as s, s.begin():
                try:
                    for env in sorted({company.environment, *(company.adn_environments or [])}):
                        sync.ensure_schedule(s, company, now, self.settings.sync_interval_hours, env)
                except Exception as exc:
                    log.warning("agenda não criada empresa=%s tipo=%s", company.id, type(exc).__name__)
        active = {(c.id, env) for c in companies for env in {c.environment, *(c.adn_environments or [])}}
        with self.sessions() as s, s.begin():
            sync.disable_absent(s, active, now)
        self._last_company_refresh = now

    def run_sync(self) -> int:
        st = self.settings
        acquired = sync.acquire_schedule(self.sessions, self.clock, self.id, st.lease_seconds)
        if acquired is None:
            return 0
        sid, token = acquired
        sync.run_cycle(
            self.sessions, self.nfse, self.clock, self.id, sid, token, limits=st.xml_limits,
            max_pages=st.sync_max_pages_per_cycle, config_recheck_seconds=st.sync_config_recheck_seconds,
            empty_cooldown_seconds=st.sync_empty_cooldown_seconds, error_backoff_seconds=st.sync_error_backoff_seconds,
        )
        return 1

    def purge_exports(self) -> int:
        """Remove arquivos de exportação/lote vencidos (no máximo a cada 5 min)."""
        now = self.clock.now()
        if self._last_purge and (now - self._last_purge).total_seconds() < 300:
            return 0
        self._last_purge = now
        try:
            return bundles.purge_expired(self.sessions, self.clock, self.settings.export_dir)
        except Exception as exc:  # noqa: BLE001
            log.error("falha na limpeza de exportações tipo=%s", type(exc).__name__)
            return 0

    # -------------------------------------------------- cancelamento (eventos do emitente)
    def run_cancellations(self) -> int:
        st = self.settings
        leased = cancelamento_worker.acquire(self.sessions, self.clock, self.id, limit=st.emission_batch_size,
                                             lease_seconds=st.lease_seconds)
        if leased and any(it.company_id not in self._companies for it in leased):
            self.refresh_companies(force=True)
        for it in leased:
            try:
                cancelamento_worker.process(self.sessions, self.nfse, self.clock, self.id, it,
                                            self._companies.get(it.company_id), self.authorize_cancel, st.xml_limits)
            except Exception as exc:  # o lease expira e a próxima tentativa reconcilia
                if isinstance(exc, SimulatedCrash):
                    raise
                log.error("falha no cancelamento=%s tipo=%s", it.id, type(exc).__name__)
        try:  # acompanhamento das solicitações de análise fiscal (consulta somente leitura de 105104/105105)
            cancelamento_worker.follow_analysis(self.sessions, self.nfse, self.clock, self._companies.get, st.xml_limits)
        except Exception as exc:  # noqa: BLE001 - nunca interrompe o ciclo do worker
            log.error("falha no acompanhamento da análise fiscal tipo=%s", type(exc).__name__)
        return len(leased)

    def tick(self) -> int:
        self.refresh_companies()
        self.purge_exports()
        return self.run_emissions() + self.run_cancellations() + self.run_jobs() + self.run_sync()


class SimulatedCrash(Exception):
    """Usada em testes para simular queda do processo."""


def main() -> None:
    configure_logging(env("LOG_LEVEL", "INFO"))
    settings = FiscalSettings.from_env("fiscal-worker")
    engine = make_engine(settings.database_url)
    nfse = NfseClient(httpx.Client(base_url=settings.nfse_api_url, timeout=httpx.Timeout(60.0)), settings.nfse_api_key)
    core = CoreClient(httpx.Client(base_url=settings.core_api_url, timeout=httpx.Timeout(5.0)),
                      "fiscal-worker", settings.core_api_key)
    worker = Worker(settings, make_sessionmaker(engine), nfse, core)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    idle = float(env("WORKER_IDLE_SECONDS", "2"))
    heartbeat = env("WORKER_HEARTBEAT_FILE", "/tmp/worker-heartbeat")
    log.info("worker iniciado id=%s", worker.id)
    while not stop.is_set():
        try:
            busy = worker.tick()
        except Exception as exc:
            log.error("falha no ciclo do worker tipo=%s", type(exc).__name__)
            busy = 0
        try:
            with open(heartbeat, "w") as fh:
                fh.write(str(int(time.time())))
        except OSError:
            pass
        if not busy:
            stop.wait(idle)
    log.info("worker encerrado id=%s", worker.id)


if __name__ == "__main__":
    main()
