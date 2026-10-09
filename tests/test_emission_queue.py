"""Gate: lote 00:01 com competência distinta; concorrência/reinício/timeout sem
reenvio cego; replay/mudança de payload; edição concorrente; backlog; idempotência."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from conftest import MYSQL, SP

F = "/api/fiscal/v1/companies"
TAX = {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}


def make_draft(stack, world, competence="2026-10", amount="1000.00") -> dict:
    r = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": competence, "amount": amount,
        "service": {"national_code": "010101", "description": "Serviço mensal"}, "tax_settings": TAX})
    assert r.status_code == 201, r.text
    return r.json()


def schedule(stack, world, drafts, at: datetime, key: str | None = None) -> dict:
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": key or uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]} for d in drafts],
                                "scheduled_at": at.isoformat(), "timezone": "America/Sao_Paulo"})
    assert r.status_code == 202, r.text
    return r.json()


def batch(stack, world, bid):  # noqa: ANN001, ANN201
    return stack.fiscal.get(f"{F}/{world['a']}/batches/{bid}", headers=world["ha"]).json()


def items_status(stack, world, bid) -> list[str]:
    return [i["status"] for i in batch(stack, world, bid)["items"]]


def test_batch_0001_next_month_with_previous_competence(stack, world):
    stack.clock.set(datetime(2026, 10, 31, 18, 0, tzinfo=SP))
    drafts = [make_draft(stack, world, "2026-10", f"{100 + i}.00") for i in range(3)]
    at = datetime(2026, 11, 1, 0, 1, tzinfo=SP)
    b = schedule(stack, world, drafts, at)
    assert b["status"] == "SCHEDULED" and b["total"] == 3
    assert stack.fiscal.get(f"{F}/{world['a']}/drafts/{drafts[0]['id']}", headers=world["ha"]).json()["status"] == "SCHEDULED"
    w = stack.worker()
    stack.clock.set(datetime(2026, 10, 31, 23, 59, 59, tzinfo=SP))
    assert w.run_emissions() == 0  # antes do horário: nada é transmitido
    stack.clock.set(datetime(2026, 11, 1, 0, 1, tzinfo=SP))
    while w.run_emissions():
        pass
    detail = batch(stack, world, b["batch_id"])
    assert [i["status"] for i in detail["items"]] == ["AUTHORIZED"] * 3
    assert detail["status"] == "COMPLETED" and detail["simulated"] is True
    item = detail["items"][0]
    assert item["result"]["simulated"] is True and "SIMULADA" in item["result"]["label"]
    assert item["late_seconds"] == 0 and item["dps_id"].startswith("DPS0000000")
    doc = stack.fiscal.get(f"{F}/{world['a']}/documents/{item['document_id']}", headers=world["ha"]).json()
    assert doc["competence"] == "2026-10"
    assert doc["issued_at"].startswith("2026-11-01T03:01")  # 00:01 em São Paulo
    assert doc["simulated"] is True and doc["source"] == "EMISSION" and doc["xml_available"] is False
    # Sem download fictício de XML para simulação.
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}/xml", headers=world["ha"]).status_code == 409
    assert len(stack.mock.transmissions) == 3


def test_schedule_validations(stack, world):
    d = make_draft(stack, world)
    past = stack.clock.now() - timedelta(hours=1)
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "k1"},
                          json={"drafts": [{"id": d["id"], "expected_version": 1}], "scheduled_at": past.isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "SCHEDULE_IN_PAST"
    fut = stack.clock.now() + timedelta(days=2)
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "k2"},
                          json={"drafts": [{"id": d["id"], "expected_version": 1}], "scheduled_at": fut.isoformat(),
                                "timezone": "America/Manaus"})
    assert r.status_code == 422 and r.json()["code"] == "TIMEZONE_MISMATCH"
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers=world["ha"],
                          json={"drafts": [{"id": d["id"], "expected_version": 1}], "scheduled_at": fut.isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "k3"},
                          json={"drafts": [{"id": d["id"], "expected_version": 1}], "scheduled_at": "2026-11-01T00:01:00",
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422  # sem offset
    # Rascunho inválido (sem tributação ISSQN) não é agendado.
    bad = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "10.00",
        "service": {"description": "x"}}).json()
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "k4"},
                          json={"drafts": [{"id": bad["id"], "expected_version": 1}], "scheduled_at": fut.isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 422 and r.json()["code"] == "DRAFT_INVALID"
    codes = {e["code"] for e in r.json()["errors"]}
    assert {"NATIONAL_CODE_REQUIRED", "ISSQN_TAXATION_REQUIRED", "ISS_RETENTION_REQUIRED"} <= codes


def test_idempotency_key_same_payload_same_result_other_payload_409(stack, world):
    d1, d2 = make_draft(stack, world), make_draft(stack, world)
    at = stack.clock.now() + timedelta(days=1)
    first = schedule(stack, world, [d1], at, key="lote-1")
    again = schedule(stack, world, [d1], at, key="lote-1")
    assert again == first
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "lote-1"},
                          json={"drafts": [{"id": d2["id"], "expected_version": 1}], "scheduled_at": at.isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert stack.fiscal.get(f"{F}/{world['a']}/batches", headers=world["ha"]).json()["total"] == 1


def test_concurrent_edit_and_frozen_snapshot(stack, world):
    d = make_draft(stack, world)
    url = f"{F}/{world['a']}/drafts/{d['id']}"
    ok = stack.fiscal.patch(url, headers=world["ha"], json={"expected_version": 1, "amount": "1500.00"})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = stack.fiscal.patch(url, headers=world["ha"], json={"expected_version": 1, "amount": "9.00"})
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    # Agendar com versão antiga é conflito.
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": "x"},
                          json={"drafts": [{"id": d["id"], "expected_version": 1}],
                                "scheduled_at": (stack.clock.now() + timedelta(days=1)).isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 409
    b = schedule(stack, world, [ok.json()], stack.clock.now() + timedelta(days=1))
    # Snapshot congelado: edição após agendar é recusada.
    locked = stack.fiscal.patch(url, headers=world["ha"], json={"expected_version": 3, "amount": "1.00"})
    assert locked.status_code == 409 and locked.json()["code"] == "DRAFT_LOCKED"
    assert batch(stack, world, b["batch_id"])["items"][0]["amount"] == "1500.00"


def test_cancel_pending_only_unstarted(stack, world):
    drafts = [make_draft(stack, world) for _ in range(3)]
    at = stack.clock.now() + timedelta(minutes=5)
    b = schedule(stack, world, drafts, at)
    stack.clock.advance(minutes=6)
    w = stack.worker()
    stack.worker_settings.emission_batch_size = 1
    stack.mock.plan = ["timeout_after_accept"]
    w.run_emissions()  # 1 item fica aguardando confirmação
    r = stack.fiscal.post(f"{F}/{world['a']}/batches/{b['batch_id']}/cancel-pending", headers=world["ha"])
    assert r.status_code == 200 and r.json()["withdrawn"] == 2 and r.json()["not_withdrawn"] == 1
    st = sorted(items_status(stack, world, b["batch_id"]))
    assert st == ["AWAITING_CONFIRMATION", "WITHDRAWN", "WITHDRAWN"]
    # Rascunhos retirados voltam a ser editáveis.
    back = [stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()["status"] for d in drafts]
    assert sorted(back) == ["AWAITING_CONFIRMATION", "DRAFT", "DRAFT"]


def test_crash_after_external_accept_before_local_commit_reconciles_without_resend(stack, world):
    from fiscal_worker.worker import SimulatedCrash

    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    w1 = stack.worker("w1")

    def crash(it, resp):  # noqa: ANN001
        assert resp["state"] == "AUTHORIZED"  # governo (simulado) aceitou
        raise SimulatedCrash()

    w1.after_external = crash
    with pytest.raises(SimulatedCrash):
        w1.run_emissions()
    assert items_status(stack, world, b["batch_id"]) == ["PROCESSING"]
    w2 = stack.worker("w2")  # "reinício": outro processo
    assert w2.run_emissions() == 0  # lease ainda válido
    stack.clock.advance(seconds=61)  # lease expira
    assert w2.run_emissions() == 1
    assert items_status(stack, world, b["batch_id"]) == ["AUTHORIZED"]
    assert len(stack.mock.transmissions) == 1  # nada foi reenviado
    docs = stack.fiscal.get(f"{F}/{world['a']}/documents?direction=ISSUED", headers=world["ha"]).json()
    assert docs["total"] == 1
    # O worker antigo, se voltasse, não consegue mais gravar (fencing).
    from ctrl_fiscal.domain.emission import LeasedItem, _finish

    from ctrl_fiscal.models import BatchItem

    with stack.fiscal_sessions() as s:
        it = s.scalar(select(BatchItem))
        stale = LeasedItem(it.id, it.fencing_token - 1, it.submission_id, "SUBMITTING", it.environment, it.company_id,
                           it.draft_id, it.dps_id, it.dps_series, it.dps_number, it.snapshot, it.snapshot_hash, 1)
    assert _finish(stack.fiscal_sessions, stack.clock, "w1", stale, {"state": "REJECTED"}) == "LOST_LEASE"


def test_timeout_after_accept_awaits_then_reconciles(stack, world):
    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = ["timeout_after_accept"]
    w = stack.worker()
    w.run_emissions()
    detail = batch(stack, world, b["batch_id"])["items"][0]
    assert detail["status"] == "AWAITING_CONFIRMATION"  # nunca REJECTED por timeout
    assert w.run_emissions() == 0  # respeita backoff
    stack.clock.advance(minutes=1)
    w.run_emissions()
    assert items_status(stack, world, b["batch_id"]) == ["AUTHORIZED"]
    assert len(stack.mock.transmissions) == 1


def test_timeout_before_accept_reconcile_confirms_absence_then_resends_same_dps(stack, world):
    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = ["timeout_before_accept"]
    w = stack.worker()
    w.run_emissions()
    assert items_status(stack, world, b["batch_id"]) == ["AWAITING_CONFIRMATION"]
    stack.clock.advance(minutes=1)
    w.run_emissions()
    assert items_status(stack, world, b["batch_id"]) == ["AUTHORIZED"]
    # Reenvio somente após consulta confirmar ausência, com a MESMA identidade DPS.
    assert len(stack.mock.transmissions) == 2 and len(set(stack.mock.transmissions)) == 1


def test_confirmed_rejection_is_final_and_draft_editable(stack, world):
    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = ["reject"]
    w = stack.worker()
    w.run_emissions()
    item = batch(stack, world, b["batch_id"])["items"][0]
    assert item["status"] == "REJECTED" and item["result"]["errors"][0]["code"] == "MOCK_REJECTION"
    stack.clock.advance(hours=2)
    assert w.run_emissions() == 0  # sem repetição cega
    dr = stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()
    assert dr["status"] == "REJECTED"
    fixed = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                               json={"expected_version": dr["version"], "amount": "999.00"})
    assert fixed.status_code == 200 and fixed.json()["status"] == "DRAFT"


def test_connector_replay_returns_same_and_payload_change_409(stack, world):
    from conftest import KEYS
    from ctrl_common.hashing import canonical_hash

    h = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + KEYS["worker_nfse"]}
    payload = {"amount": "10.00", "company": {"cnpj": world["a_cnpj"]}}
    sid = str(uuid.uuid4())
    dps = "DPS0000000" + "2" + world["a_cnpj"] + "00001" + "1".zfill(15)
    req = {"submission_id": sid, "company_id": world["a"], "environment": "mock", "company_cnpj": world["a_cnpj"],
           "dps": {"id": dps, "series": "1", "number": 1}, "payload": payload, "payload_hash": canonical_hash(payload)}
    first = stack.nfse.post("/internal/v1/submissions", headers=h, json=req)
    again = stack.nfse.post("/internal/v1/submissions", headers=h, json=req)
    assert first.status_code == 200 and first.json()["state"] == "AUTHORIZED" and first.json()["simulated"] is True
    assert again.json()["result"] == first.json()["result"]
    assert len(stack.mock.transmissions) == 1
    changed = {**req, "payload": {**payload, "amount": "11.00"}}
    changed["payload_hash"] = canonical_hash(changed["payload"])
    r = stack.nfse.post("/internal/v1/submissions", headers=h, json=changed)
    assert r.status_code == 409 and r.json()["code"] == "SUBMISSION_PAYLOAD_MISMATCH"
    other = {**req, "submission_id": str(uuid.uuid4())}
    assert stack.nfse.post("/internal/v1/submissions", headers=h, json=other).json()["code"] == "DPS_ID_CONFLICT"
    # Sem identidade de serviço: recusado.
    assert stack.nfse.post("/internal/v1/submissions", json=req).status_code == 401
    core_key = {"X-Service-Name": "core-api", "Authorization": "Bearer " + KEYS["core_nfse"]}
    assert stack.nfse.post("/internal/v1/submissions", headers=core_key, json=req).status_code == 403


def test_backlog_after_downtime_records_delay_and_respects_concurrency(stack, world):
    stack.clock.set(datetime(2026, 10, 31, 12, 0, tzinfo=SP))
    drafts = [make_draft(stack, world, amount=f"{10 + i}.00") for i in range(12)]
    b = schedule(stack, world, drafts, datetime(2026, 11, 1, 0, 1, tzinfo=SP))
    stack.clock.set(datetime(2026, 11, 1, 3, 1, tzinfo=SP))  # worker volta 3h depois
    w = stack.worker()
    rounds = 0
    while True:
        n = w.run_emissions()
        assert n <= stack.worker_settings.emission_batch_size
        if not n:
            break
        rounds += 1
    detail = batch(stack, world, b["batch_id"])
    assert all(i["status"] == "AUTHORIZED" for i in detail["items"]) and rounds >= 3
    assert all(i["late_seconds"] == 3 * 3600 for i in detail["items"])
    nums = sorted(int(i["dps_id"][-15:]) for i in detail["items"])
    assert nums == list(range(1, 13))  # numeração DPS reservada sem duplicidade


def test_restart_does_not_create_new_notes(stack, world):
    drafts = [make_draft(stack, world) for _ in range(4)]
    b = schedule(stack, world, drafts, stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.worker("antes").run_emissions()
    for wid in ("depois-1", "depois-2"):
        stack.worker(wid).run_emissions()
        stack.clock.advance(minutes=5)
    assert items_status(stack, world, b["batch_id"]).count("AUTHORIZED") == 4
    assert len(stack.mock.transmissions) == 4
    from ctrl_fiscal.models import Document

    with stack.fiscal_sessions() as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 4


@pytest.mark.skipif(not MYSQL, reason="Exige MySQL (locks reais); defina CTRL_TEST_MYSQL_URL")
def test_two_workers_in_parallel_mysql_each_item_once(stack, world):
    stack.clock.set(datetime(2026, 10, 31, 12, 0, tzinfo=SP))
    drafts = [make_draft(stack, world, amount=f"{50 + i}.00") for i in range(20)]
    b = schedule(stack, world, drafts, datetime(2026, 11, 1, 0, 1, tzinfo=SP))
    stack.clock.set(datetime(2026, 11, 1, 0, 2, tzinfo=SP))
    workers = [stack.worker("par-1"), stack.worker("par-2")]
    errors: list[Exception] = []

    def loop(w):  # noqa: ANN001
        try:
            for _ in range(30):
                w.run_emissions()
        except Exception as exc:  # pragma: no cover - diagnóstico
            errors.append(exc)

    threads = [threading.Thread(target=loop, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    st = items_status(stack, world, b["batch_id"])
    assert st.count("AUTHORIZED") == 20
    assert len(stack.mock.transmissions) == 20 and len(set(stack.mock.transmissions)) == 20
    from ctrl_fiscal.models import Document

    with stack.fiscal_sessions() as s:
        assert s.scalar(select(func.count()).select_from(Document)) == 20


def test_schedule_offset_variants_keep_company_timezone_and_competence(stack, world):
    """Z / -03:00 / +00:00 do mesmo instante => mesmo agendamento; competência intacta;
    fuso validado registrado; offset 'errado' do cliente nunca é corrigido em silêncio."""
    from core_api.models import Company
    from ctrl_fiscal.models import AuditEvent, BatchItem

    stack.clock.set(datetime(2026, 10, 30, 12, 0, tzinfo=SP))
    variants = ["2026-11-01T00:01:00-03:00", "2026-11-01T03:01:00Z", "2026-11-01T03:01:00+00:00"]
    seen = []
    for v in variants:
        d = make_draft(stack, world, competence="2026-10")
        r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                              json={"drafts": [{"id": d["id"], "expected_version": 1}], "scheduled_at": v,
                                    "timezone": "America/Sao_Paulo"})
        assert r.status_code == 202, r.text
        assert r.json()["scheduled_at"] == "2026-11-01T03:01:00+00:00"  # resposta sempre normalizada em UTC
        b = batch(stack, world, r.json()["batch_id"])
        seen.append((b["scheduled_at"], b["scheduled_local"], b["timezone"], b["items"][0]["competence"]))
    assert len(set(seen)) == 1
    utc_at, local, tz, comp = seen[0]
    assert datetime.fromisoformat(utc_at) == datetime(2026, 11, 1, 3, 1, tzinfo=timezone.utc)
    assert local == "2026-11-01T00:01:00-03:00" and tz == "America/Sao_Paulo" and comp == "2026-10"
    with stack.fiscal_sessions() as s:
        ev = s.scalars(select(AuditEvent).where(AuditEvent.action == "batch.created")).all()
        assert all(e.details["timezone_validated"] == "America/Sao_Paulo" for e in ev)
        assert {e.details["scheduled_local"] for e in ev} == {"2026-11-01T00:01:00-03:00"}
        assert {i.snapshot["competence"] for i in s.scalars(select(BatchItem))} == {"2026-10"}
    # O fuso enviado precisa ser o da empresa; não é possível "trocar" o fuso pelo payload.
    d = make_draft(stack, world)
    for tzname in ("UTC", "America/Manaus", "Mars/Olympus"):
        r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                              json={"drafts": [{"id": d["id"], "expected_version": 1}],
                                    "scheduled_at": "2026-11-01T03:01:00Z", "timezone": tzname})
        assert r.status_code == 422 and r.json()["code"] in ("TIMEZONE_MISMATCH", "TIMEZONE_INVALID")
    # Empresa em Manaus (-04:00): cliente que fixa "-03:00" agenda 23:01 local do dia anterior.
    # O backend converte o instante fielmente e expõe scheduled_local para a UI confirmar.
    with stack.core_sessions() as s, s.begin():
        s.get(Company, world["a"]).timezone = "America/Manaus"
    d2 = make_draft(stack, world, competence="2026-10")
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d2["id"], "expected_version": 1}],
                                "scheduled_at": "2026-11-01T00:01:00-03:00", "timezone": "America/Manaus"})
    b = batch(stack, world, r.json()["batch_id"])
    assert b["scheduled_local"] == "2026-10-31T23:01:00-04:00" and b["timezone"] == "America/Manaus"
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": make_draft(stack, world)["id"], "expected_version": 1}],
                                "scheduled_at": "2026-11-01T00:01:00-04:00", "timezone": "America/Manaus"})
    assert batch(stack, world, r.json()["batch_id"])["scheduled_local"] == "2026-11-01T00:01:00-04:00"


def test_blocked_item_returns_to_phase_none_and_can_be_withdrawn(stack, world):
    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = ["blocked"] if hasattr(stack.mock, "plan") else []
    from ctrl_fiscal.models import BatchItem
    from sqlalchemy import select

    # Simula resposta BLOCKED do conector (garantia: nada transmitido).
    from ctrl_fiscal.domain import emission

    w = stack.worker()
    leased = emission.acquire_items(w.sessions, stack.clock, "w1", limit=5, lease_seconds=60, max_in_flight=10, series="1")
    it = leased[0]
    with w.sessions() as s, s.begin():
        s.get(BatchItem, it.id).submit_phase = "SUBMITTING"
    it.submit_phase = "SUBMITTING"
    emission._finish(w.sessions, stack.clock, "w1", it, {"state": "BLOCKED", "error": {"code": "CERTIFICATE_NOT_CONFIGURED"}})
    with w.sessions() as s:
        row = s.get(BatchItem, it.id)
        assert row.status == "SCHEDULED" and row.submit_phase == "NONE" and row.attempts >= 1
    r = stack.fiscal.post(f"{F}/{world['a']}/batches/{b['batch_id']}/cancel-pending", headers=world["ha"])
    assert r.status_code == 200 and r.json()["withdrawn"] == 1
