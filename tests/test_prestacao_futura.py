"""Prestação futura (dCompet) x data de emissão (Anexo I, E0015: dCompet ≤ data do dhEmi; E0008: dhEmi ≤ dhProc).
Rascunho com prestação futura é salvo e agendado; "Emitir agora" e agendamento anterior à prestação são recusados com
explicação; limites de dia no fuso da empresa; dhEmi gerado na execução (inclusive atrasada); nada é transmitido antes
do dia da prestação e não há duplicidade. Governo simulado (sem rede), dados fictícios."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from lxml import etree
from sqlalchemy import select

from conftest import SP
from test_official_integration import (  # noqa: F401  (fixtures)
    F, NS, batch_items, gov, official_draft, ostack, real,
)

HOJE = datetime(2026, 10, 20, 10, 0, tzinfo=SP)  # relógio inicial da suíte


def _lote(ostack, real, drafts, *, at: datetime | None = None, mode: str = "SCHEDULED"):  # noqa: ANN001, ANN202
    body = {"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts], "mode": mode,
            "timezone": "America/Sao_Paulo"}
    if at is not None:
        body["scheduled_at"] = at.isoformat()
    return ostack.fiscal.post(f"{F}/{real['id']}/batches", headers={**real["h"], "Idempotency-Key": uuid.uuid4().hex},
                              json=body)


def _erros(r) -> list[dict]:  # noqa: ANN001
    return [e for e in (r.json().get("errors") or []) if e["code"] == "SERVICE_DATE_AFTER_EMISSION"]


def _get(ostack, real, d) -> dict:  # noqa: ANN001
    return ostack.fiscal.get(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"]).json()


def _inf(xml: bytes):  # noqa: ANN202
    return etree.fromstring(xml).find(f"{NS}infDPS")


def test_rascunho_com_prestacao_futura_salva_reabre_e_so_avisa(ostack, real):
    d = official_draft(ostack, real, service_date="2026-10-25")
    assert _get(ostack, real, d)["service_date"] == "2026-10-25"
    r = ostack.fiscal.patch(f"{F}/{real['id']}/drafts/{d['id']}", headers=real["h"],
                            json={"expected_version": d["version"], "competence": "2026-11", "service_date": "2026-11-03"})
    assert r.status_code == 200, r.text
    assert _get(ostack, real, d)["service_date"] == "2026-11-03"
    issues = ostack.fiscal.post(f"{F}/{real['id']}/drafts/{d['id']}/validate", headers=real["h"]).json()["issues"]
    datas = [i for i in issues if i["field"] == "service_date"]
    assert [(i["code"], i["severity"], i["rule"]) for i in datas] == [("SERVICE_DATE_FUTURE", "warning", "E0015")]
    assert "03/11/2026" in datas[0]["message"]
    assert not [i for i in issues if i["code"] == "COMPETENCE_IN_FUTURE"]  # o aviso preciso é o da prestação


def test_emitir_agora_bloqueado_com_prestacao_futura(ostack, real, gov):
    d = official_draft(ostack, real, service_date="2026-10-25")
    r = _lote(ostack, real, [d], mode="NOW")
    assert r.status_code == 422 and r.json()["code"] == "DRAFT_INVALID", r.text
    msg = _erros(r)[0]["message"]
    assert "não é possível emitir agora" in msg and "25/10/2026" in msg and "20/10/2026" in msg and "E0015" in msg
    assert _get(ostack, real, d)["status"] == "DRAFT" and gov.posts() == []
    # Prestação de hoje (ou passada) continua emitindo agora.
    hoje = official_draft(ostack, real, service_date="2026-10-20")
    assert _lote(ostack, real, [hoje], mode="NOW").status_code == 202


def test_agendamento_anterior_a_prestacao_recusado_e_limite_do_dia_no_fuso(ostack, real, gov):
    d = official_draft(ostack, real, service_date="2026-10-25")
    # 24/10 23:59 em São Paulo já é 25/10 em UTC: vale o dia NO FUSO DA EMPRESA.
    r = _lote(ostack, real, [d], at=datetime(2026, 10, 24, 23, 59, tzinfo=SP))
    assert r.status_code == 422, r.text
    msg = _erros(r)[0]["message"]
    assert "24/10/2026 23:59" in msg and "America/Sao_Paulo" in msg and "25/10/2026 ou depois" in msg
    assert _get(ostack, real, d)["status"] == "DRAFT"
    # 25/10 00:00 em São Paulo (03:00 UTC): mesmo dia da prestação, aceito.
    r = _lote(ostack, real, [d], at=datetime(2026, 10, 25, 0, 0, tzinfo=SP))
    assert r.status_code == 202, r.text
    assert gov.posts() == []


def test_mesmo_dia_e_dia_posterior_emitem_com_dhemi_da_execucao_sem_duplicidade(ostack, real, gov):
    d1 = official_draft(ostack, real, service_date="2026-10-25")
    d2 = official_draft(ostack, real, service_date="2026-10-25", amount="1600.00")
    b1 = _lote(ostack, real, [d1], at=datetime(2026, 10, 25, 0, 0, tzinfo=SP))
    b2 = _lote(ostack, real, [d2], at=datetime(2026, 10, 27, 9, 0, tzinfo=SP))
    assert b1.status_code == b2.status_code == 202, (b1.text, b2.text)
    w = ostack.worker()
    # 00:00 do dia da prestação: ainda não transmite (o dhEmi, com a margem do relógio oficial, cairia no dia 24).
    ostack.clock.set(datetime(2026, 10, 25, 0, 0, 30, tzinfo=SP))
    w.run_emissions()
    assert [p for p in gov.posted_dps] == []
    it = batch_items(ostack, real, b1.json()["batch_id"])[0]
    assert it["status"] == "SCHEDULED" and (it["last_error"] or {}).get("code") == "SERVICE_DATE_NOT_REACHED"
    ostack.clock.set(datetime(2026, 10, 25, 0, 2, 10, tzinfo=SP))
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 1
    inf = _inf(gov.posted_dps[0])
    assert inf.findtext(f"{NS}dCompet") == "2026-10-25"
    assert inf.findtext(f"{NS}dhEmi").startswith("2026-10-25T00:02:") and inf.findtext(f"{NS}dhEmi").endswith("-03:00")
    # Execução ATRASADA (worker parado): dhEmi é o momento real da transmissão, não a data do rascunho nem a programada.
    ostack.clock.set(datetime(2026, 10, 27, 15, 30, tzinfo=SP))
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 2
    inf2 = _inf(gov.posted_dps[1])
    assert inf2.findtext(f"{NS}dhEmi").startswith("2026-10-27T15:2") and inf2.findtext(f"{NS}dCompet") == "2026-10-25"
    it2 = batch_items(ostack, real, b2.json()["batch_id"])[0]
    assert it2["status"] == "AUTHORIZED" and it2["late_seconds"] >= 6 * 3600
    # Nova rodada não reenvia nada.
    ostack.clock.advance(hours=1)
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 2


def test_worker_revalida_a_data_antes_de_transmitir(ostack, real, gov):
    """Item elegível antes do dia da prestação (ex.: horário alterado por fora): nada transmitido, nova tentativa às
    00:02 do dia da prestação no fuso da empresa, mesma DPS, sem duplicidade."""
    from ctrl_fiscal.models import BatchItem

    d = official_draft(ostack, real, service_date="2026-10-25")
    b = _lote(ostack, real, [d], at=datetime(2026, 10, 25, 9, 0, tzinfo=SP)).json()
    w = ostack.worker()
    with w.sessions() as s, s.begin():
        item = s.scalar(select(BatchItem).where(BatchItem.batch_id == b["batch_id"]))
        item.next_attempt_at = datetime(2026, 10, 24, 22, 0, tzinfo=SP).astimezone(timezone.utc)
    ostack.clock.set(datetime(2026, 10, 24, 23, 30, tzinfo=SP))
    w.run_emissions()
    assert gov.posted_dps == []
    it = batch_items(ostack, real, b["batch_id"])[0]
    assert it["status"] == "SCHEDULED" and (it["last_error"] or {}).get("code") == "SERVICE_DATE_NOT_REACHED"
    with w.sessions() as s:
        nxt = s.scalar(select(BatchItem.next_attempt_at).where(BatchItem.batch_id == b["batch_id"]))
    nxt = nxt if nxt.tzinfo else nxt.replace(tzinfo=timezone.utc)
    assert nxt == datetime(2026, 10, 25, 0, 2, tzinfo=SP)
    ostack.clock.set(datetime(2026, 10, 25, 0, 2, 30, tzinfo=SP))
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 1 and batch_items(ostack, real, b["batch_id"])[0]["status"] == "AUTHORIZED"
    ostack.clock.advance(hours=2)
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 1


def test_relogio_oficial_ainda_no_dia_anterior_nao_envia(ostack, real, gov):
    """Relógio local adiantado em relação à Sefin: o dhEmi (alinhado ao oficial) ainda seria do dia anterior. O conector
    não envia (E0015), o worker reconsulta e só então transmite, com a mesma DPS."""
    d = official_draft(ostack, real, service_date="2026-10-25")
    b = _lote(ostack, real, [d], at=datetime(2026, 10, 25, 0, 2, tzinfo=SP)).json()
    gov.server_now = lambda: ostack.clock.now() - timedelta(minutes=3)  # Sefin 3 min atrás (limite: 5 min)
    w = ostack.worker()
    ostack.clock.set(datetime(2026, 10, 25, 0, 2, 30, tzinfo=SP))
    w.run_emissions()
    assert gov.posted_dps == []
    assert batch_items(ostack, real, b["batch_id"])[0]["status"] == "AWAITING_CONFIRMATION"
    ostack.clock.set(datetime(2026, 10, 25, 0, 10, tzinfo=SP))
    while w.run_emissions():
        pass
    assert len(gov.posted_dps) == 1
    dh = _inf(gov.posted_dps[0]).findtext(f"{NS}dhEmi")
    assert dh.startswith("2026-10-25T00:0") and batch_items(ostack, real, b["batch_id"])[0]["status"] == "AUTHORIZED"
