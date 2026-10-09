"""Provider MOCK: simulação local explícita. Nenhuma NFS-e real é gerada.

Todos os retornos carregam simulated=True. A 'autoridade' simulada persiste em
mock_authority_records para que a reconciliação por DPS funcione após quedas.
Injeção de falhas (somente testes): MockProvider.plan.
"""
from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ctrl_common.clock import Clock

from ..models import MockAuthorityRecord, MockEventRecord
from .base import (
    CompanyContext,
    DistributionPage,
    EventOutcome,
    EventQuery,
    ProviderBlocked,
    QueryOutcome,
    SubmitOutcome,
    TransmissionNotSent,
    TransmissionUncertain,
)
from .mock_data import demo_dataset, mock_access_key


class MockProvider:
    environment = "mock"
    simulated = True

    def __init__(self, sessions: sessionmaker, clock: Clock, page_size: int = 3) -> None:
        self.sessions = sessions
        self.clock = clock
        self.page_size = page_size
        self.plan: list[str] = []  # ex.: ["timeout_after_accept", "reject", "connect_error", "timeout_before_accept"]
        self.transmissions: list[str] = []  # dps_ids recebidos (contagem para testes)
        self.distribution_calls: list[int] = []
        self.distribution_failure: str | None = None
        self._lock = threading.Lock()

    def check_ready(self, company: CompanyContext, operation: str) -> None:
        if company.environment != "mock":
            raise ProviderBlocked("BLOCKED", "WRONG_PROVIDER", "Provider mock atende somente o ambiente mock.")

    def _next_behavior(self) -> str | None:
        with self._lock:
            return self.plan.pop(0) if self.plan else None

    def _authorize(self, company: CompanyContext, dps_id: str) -> MockAuthorityRecord:
        now = self.clock.now()
        with self.sessions() as s:
            existing = s.scalar(select(MockAuthorityRecord).where(MockAuthorityRecord.dps_id == dps_id))
            if existing:
                return existing
            number = (s.scalar(select(func.max(MockAuthorityRecord.number)).where(
                MockAuthorityRecord.company_cnpj == company.cnpj)) or 0) + 1
            rec = MockAuthorityRecord(company_cnpj=company.cnpj, dps_id=dps_id,
                                      access_key=mock_access_key(company.cnpj, number, now, dps_id),
                                      number=number, authorized_at=now, created_at=now)
            s.add(rec)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return s.scalar(select(MockAuthorityRecord).where(MockAuthorityRecord.dps_id == dps_id))
            return rec

    def submit_dps(self, company: CompanyContext, dps: dict, payload: dict) -> SubmitOutcome:
        dps_id = dps["id"]
        behavior = self._next_behavior()
        if behavior == "connect_error":
            raise TransmissionNotSent("CONNECT_FAILED")
        with self._lock:
            self.transmissions.append(dps_id)
        if behavior == "timeout_before_accept":
            raise TransmissionUncertain("TIMEOUT")
        if behavior == "reject":
            return SubmitOutcome(state="REJECTED", errors=[{"code": "MOCK_REJECTION",
                                                             "message": "Rejeição SIMULADA para teste."}])
        rec = self._authorize(company, dps_id)
        if behavior == "timeout_after_accept":
            raise TransmissionUncertain("TIMEOUT")
        return SubmitOutcome(state="AUTHORIZED", access_key=rec.access_key, number=str(rec.number),
                             authorized_at=rec.authorized_at)

    def query_dps(self, company: CompanyContext, dps_id: str) -> QueryOutcome:
        with self.sessions() as s:
            rec = s.scalar(select(MockAuthorityRecord).where(MockAuthorityRecord.dps_id == dps_id))
        if rec is None:
            return QueryOutcome(found=False)
        return QueryOutcome(found=True, access_key=rec.access_key, number=str(rec.number), authorized_at=rec.authorized_at)

    def fetch_distribution(self, company: CompanyContext, from_nsu: int) -> DistributionPage:
        with self._lock:
            self.distribution_calls.append(from_nsu)
        if self.distribution_failure:
            raise TransmissionNotSent(self.distribution_failure)
        data = demo_dataset(company.cnpj)
        pending = [d for d in data if d["nsu"] > from_nsu]
        page = pending[: self.page_size]
        max_nsu = data[-1]["nsu"] if data else 0
        last = page[-1]["nsu"] if page else max(from_nsu, 0)
        return DistributionPage(items=page, last_nsu=last, max_nsu=max_nsu, has_more=last < max_nsu)


    # ------------------------------------------------------------------ eventos (SIMULADOS)
    def _event_xml(self, rec: MockEventRecord, cnpj: str) -> bytes:
        """XML no formato `evento` do leiaute, SEM assinatura e com verAplic "SIMULACAO" (ambGer 2 só por forma).
        Serve para o fiscal-api aplicar o evento pelo mesmo caminho dos eventos oficiais, sempre como simulado."""
        from lxml import etree

        from ..eventos import EVENTOS_EMITENTE, NS

        grupo, x_desc, _ = EVENTOS_EMITENTE[rec.event_code]
        root = etree.Element(f"{{{NS}}}evento", nsmap={None: NS}, versao="1.01")
        inf = etree.SubElement(root, f"{{{NS}}}infEvento", Id="EVT" + rec.pre_id[3:] + "001")
        for tag, val in (("verAplic", "SIMULACAO"), ("ambGer", "2"), ("nSeqEvento", "1"),
                         ("dhProc", rec.registered_at.replace(microsecond=0).isoformat()), ("nDFSe", "0")):
            etree.SubElement(inf, f"{{{NS}}}{tag}").text = val
        ped = etree.SubElement(inf, f"{{{NS}}}pedRegEvento", versao="1.01")
        ip = etree.SubElement(ped, f"{{{NS}}}infPedReg", Id=rec.pre_id)
        for tag, val in (("tpAmb", "2"), ("verAplic", "SIMULACAO"), ("dhEvento", rec.registered_at.replace(microsecond=0).isoformat()),
                         ("CNPJAutor", cnpj), ("chNFSe", rec.access_key)):
            etree.SubElement(ip, f"{{{NS}}}{tag}").text = val
        g = etree.SubElement(ip, f"{{{NS}}}{grupo}")
        for tag, val in (("xDesc", x_desc), ("cMotivo", rec.c_motivo), ("xMotivo", rec.x_motivo)):
            etree.SubElement(g, f"{{{NS}}}{tag}").text = val
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    def register_event(self, company: CompanyContext, access_key: str, code: str, c_motivo: str,
                       x_motivo: str) -> EventOutcome:
        """Regras SIMULADAS mínimas, só para exercitar o fluxo: nota precisa existir na autoridade simulada (E1831),
        evento único (cancelamento depois de cancelamento → recusa). Marcadores no texto, SÓ no mock, simulam a
        recusa municipal de prazo ([SIMULAR-E0822]) e o timeout depois do aceite ([SIMULAR-TIMEOUT])."""
        from ..eventos import EventoInvalido, montar

        behavior = self._next_behavior()
        if behavior == "connect_error":
            raise TransmissionNotSent("CONNECT_FAILED")
        try:
            ped = montar(environment="restricted", access_key=access_key, cnpj_autor=company.cnpj, codigo=code,
                         c_motivo=c_motivo, x_motivo=x_motivo, agora=self.clock.now())
        except EventoInvalido as exc:
            return EventOutcome(state="REJECTED", errors=[{"code": "LOCAL_EVENTO", "message": m} for m in exc.erros])
        with self._lock:
            self.transmissions.append(ped.pre_id)
        if behavior == "timeout_before_accept":
            raise TransmissionUncertain("TIMEOUT")
        with self.sessions() as s:
            if s.scalar(select(MockAuthorityRecord.id).where(MockAuthorityRecord.access_key == access_key)) is None:
                return EventOutcome(state="REJECTED", official_rejection=True, errors=[
                    {"code": "E1831", "message": "SIMULADO: NFS-e indicada não existe na autoridade simulada."}])
            cancelled = s.scalar(select(MockEventRecord).where(MockEventRecord.access_key == access_key,
                                                                MockEventRecord.event_code == "101101"))
            if cancelled is not None:
                return EventOutcome(state="REJECTED", official_rejection=True, errors=[
                    {"code": "E0840", "message": "SIMULADO: evento de cancelamento já vinculado à NFS-e."}])
            if code == "101101" and "[SIMULAR-E0822]" in x_motivo:
                return EventOutcome(state="REJECTED", official_rejection=True, errors=[
                    {"code": "E0822", "message": "SIMULADO: prazo para o cancelamento expirou (parametrização do município)."}])
            if code == "101101" and s.scalar(select(MockEventRecord.id).where(
                    MockEventRecord.access_key == access_key, MockEventRecord.event_code == "101103")) is not None:
                return EventOutcome(state="REJECTED", official_rejection=True, errors=[
                    {"code": "E0840", "message": "SIMULADO: solicitação de análise fiscal pendente vinculada à NFS-e."}])
            rec = MockEventRecord(access_key=access_key, event_code=code, pre_id=ped.pre_id, c_motivo=c_motivo,
                                  x_motivo=x_motivo, registered_at=self.clock.now())
            s.add(rec)
            try:
                s.commit()
            except IntegrityError:
                s.rollback()
                return EventOutcome(state="REJECTED", official_rejection=True, errors=[
                    {"code": "E0840", "message": "SIMULADO: evento já vinculado à NFS-e."}])
            xml = self._event_xml(rec, company.cnpj)
        if behavior == "timeout_after_accept" or "[SIMULAR-TIMEOUT]" in x_motivo:
            raise TransmissionUncertain("TIMEOUT")
        return EventOutcome(state="REGISTERED", event_xml=xml, processed_at=rec.registered_at, request_xml=ped.xml)

    def query_event(self, company: CompanyContext, access_key: str, code: str) -> EventQuery:
        with self.sessions() as s:
            rec = s.scalar(select(MockEventRecord).where(MockEventRecord.access_key == access_key,
                                                          MockEventRecord.event_code == code))
            if rec is None:
                return EventQuery(found=False)
            return EventQuery(found=True, event_xml=self._event_xml(rec, company.cnpj))


def _now_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
