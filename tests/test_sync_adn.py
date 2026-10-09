"""Gate: sincronização ADN a cada 12h (relógio injetável), sem duplicatas, origem
mista (NFS-e + evento), retenção ausente/PCC, quarentena e NOT_CONFIGURED."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from conftest import SP

F = "/api/fiscal/v1/companies"


def status(stack, world, company="a", h="ha") -> dict:  # noqa: ANN001
    r = stack.fiscal.get(f"{F}/{world[company]}/sync/status", headers=world[h])
    assert r.status_code == 200, r.text
    return r.json()


def docs(stack, world, **params) -> dict:  # noqa: ANN003
    r = stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"], params=params)
    assert r.status_code == 200, r.text
    return r.json()


def run_all(w) -> int:  # noqa: ANN001
    n = 0
    while w.run_sync():
        n += 1
    return n


def test_first_cycle_drains_pages_and_classifies_mixed_origin(stack, world):
    start = datetime(2026, 10, 20, 8, 0, tzinfo=SP)
    stack.clock.set(start)
    w = stack.worker()
    w.refresh_companies(force=True)
    assert run_all(w) == 2  # empresas A e B (mock), um ciclo cada
    assert stack.mock.distribution_calls.count(0) == 2  # pagina 3 em 3: NSU 0, 3, 6 por empresa
    st = status(stack, world)
    assert st["status"] == "OK" and st["interval_hours"] == 12 and st["simulated"] is True
    assert st["received_count"] == 7 and st["pending_parse_count"] == 1  # XML truncado em quarentena
    assert datetime.fromisoformat(st["next_run_at"]) == start + timedelta(hours=12)
    assert "SIMULAÇÃO" in st["coverage_message"]

    received = docs(stack, world, direction="RECEIVED")
    issued = docs(stack, world, direction="ISSUED")
    assert received["total"] == 2 and issued["total"] == 1  # intermediário e filial ficam fora
    by_num = {d["number"]: d for d in received["items"]}
    # Retenções conhecidas (nota 101) — valores separados das indicações; PCC nunca derivado.
    d1 = by_num["101"]
    assert d1["retentions"] == {"iss": "500.00", "irrf": "150.00", "inss": "1100.00", "pis": None, "cofins": None,
                                "csll": "100.00", "pcc_total": None}
    ind = d1["retention_indicators"]
    assert ind["iss"]["status"] == "RETAINED_BY_TOMADOR"
    assert ind["pis"] == {"status": "RETAINED", "source": "tpRetPisCofins=3", "informed_amount": "65.00", "informed_field": "vPis"}
    assert ind["pcc_total"]["status"] == "NOT_IN_LAYOUT" and d1["total_retained"] == "1850.00"
    assert d1["review_hints"] == [] and d1["review_status"] == "PENDING" and d1["source"] == "ADN"
    # Retenção ausente = null (não zero) e evento de cancelamento aplicado (nota 202).
    d2 = by_num["202"]
    assert all(v is None for v in d2["retentions"].values())
    assert d2["retention_indicators"]["irrf"]["status"] == "NOT_INFORMED"
    assert d2["status"] == "CANCELLED"
    detail = stack.fiscal.get(f"{F}/{world['a']}/documents/{d2['id']}", headers=world["ha"]).json()
    assert [e["event_type"] for e in detail["events"]] == ["e101101"] and detail["events"][0]["cancels_document"]
    # Documento da filial (mesma raiz) não aparece na empresa.
    assert docs(stack, world, query="Filial")["total"] == 0

    from ctrl_fiscal.models import DfeItem

    with stack.fiscal_sessions() as s:
        rows = s.execute(select(DfeItem.parse_status, DfeItem.parse_error_code).where(DfeItem.company_id == world["a"])).all()
    states = sorted((r[0], r[1] or "") for r in rows)
    assert ("OUT_OF_SCOPE", "INTERMEDIARY_ROLE_NOT_LISTED") in states
    assert ("OUT_OF_SCOPE", "OTHER_BRANCH_SAME_ROOT") in states
    assert ("QUARANTINED", "XML_MALFORMED") in states


def test_twelve_hour_interval_without_duplicates_and_replay(stack, world):
    start = datetime(2026, 10, 20, 8, 0, tzinfo=SP)
    stack.clock.set(start)
    w = stack.worker()
    w.refresh_companies(force=True)
    run_all(w)
    calls = len(stack.mock.distribution_calls)
    stack.clock.set(start + timedelta(hours=6))
    assert run_all(w) == 0  # fora da janela de 12h
    stack.clock.set(start + timedelta(hours=11, minutes=59))
    assert run_all(w) == 0
    stack.clock.set(start + timedelta(hours=12, seconds=1))
    assert run_all(w) == 2
    assert len(stack.mock.distribution_calls) == calls + 2  # 1 chamada por empresa a partir do cursor 7
    assert stack.mock.distribution_calls[-1] == 7
    st = status(stack, world)
    assert st["received_count"] == 0 and datetime.fromisoformat(st["next_run_at"]) == start + timedelta(hours=24, seconds=1)
    total_before = docs(stack, world)["total"]
    # Replay forçado do NSU 0 (ex.: cursor corrompido manualmente): nenhuma duplicata.
    from ctrl_fiscal.models import Document, DocumentEvent, SyncSchedule

    with stack.fiscal_sessions() as s, s.begin():
        s.execute(update(SyncSchedule).values(cursor_nsu=0, next_run_at=stack.clock.now()))
    run_all(w)
    assert docs(stack, world)["total"] == total_before
    with stack.fiscal_sessions() as s:
        assert s.scalar(select(func.count()).select_from(DocumentEvent).where(DocumentEvent.company_id == world["a"])) == 1
        assert s.scalar(select(func.count()).select_from(Document).where(Document.company_id == world["a"])) == 3


def test_manual_run_queues_job_without_concurrency_and_respects_cooldown(stack, world):
    stack.clock.set(datetime(2026, 10, 20, 8, 0, tzinfo=SP))
    w = stack.worker()
    w.refresh_companies(force=True)
    run_all(w)  # ciclo inicial: ADN sem mais documentos => espera mínima de 1h
    h = {**world["ha"], "Idempotency-Key": "sync-1"}
    r1 = stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers=h)
    assert r1.status_code == 202 and r1.json()["status"] == "QUEUED"
    r2 = stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers={**world["ha"], "Idempotency-Key": "sync-2"})
    assert r2.json()["job_id"] == r1.json()["job_id"]  # não cria concorrência
    assert stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers=h).json() == r1.json()  # idempotente
    calls = len(stack.mock.distribution_calls)
    assert run_all(w) == 0  # cooldown de 1h documentado pelo ADN ainda vigente
    assert len(stack.mock.distribution_calls) == calls  # nenhuma consulta feita no request HTTP
    stack.clock.advance(hours=1, seconds=1)
    assert run_all(w) == 1
    job = stack.fiscal.get(f"{F}/{world['a']}/jobs/{r1.json()['job_id']}", headers=world["ha"]).json()
    assert job["type"] == "SYNC" and job["status"] == "SUCCEEDED"


def test_not_configured_is_not_success_nor_empty(stack, world):
    from core_api.models import Company

    from conftest import cnpj

    # Empresa NÃO demonstrativa (os CNPJs do world coincidem com os do seed de demonstração, que têm trava própria).
    with stack.core_sessions() as s, s.begin():
        co = s.get(Company, world["b"])
        co.nfse_environment = "restricted"
        co.cnpj = cnpj("556667770001")
    stack.clock.set(datetime(2026, 10, 20, 8, 0, tzinfo=SP))
    w = stack.worker()
    w.refresh_companies(force=True)
    run_all(w)
    st = status(stack, world, "b", "hb")
    assert st["status"] == "NOT_CONFIGURED" and st["environment"] == "restricted"
    assert st["last_success_at"] is None and "NÃO significa ausência" in st["coverage_message"]
    assert st["last_error"]["code"] == "RESTRICTED_DISABLED" or st["last_error"]["code"].startswith(("CERT", "VAULT"))
    assert datetime.fromisoformat(st["next_run_at"]) == stack.clock.now() + timedelta(hours=1)
    # Integração pública via core: estado sem segredos.
    r = stack.core.get(f"/api/core/v1/companies/{world['b']}/integration-status", headers=world["hb"])
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "NOT_CONFIGURED" and body["simulated"] is False and body["capabilities"]["cancellation"] is False
    assert "pfx" not in r.text.lower() and "password" not in r.text.lower()
    mock = stack.core.get(f"/api/core/v1/companies/{world['a']}/integration-status", headers=world["ha"]).json()
    assert mock["status"] == "SIMULATED" and mock["simulated"] is True


def test_storage_failure_keeps_cursor_then_recovers(stack, world, monkeypatch):
    import ctrl_fiscal.domain.sync as sync_mod
    from ctrl_fiscal.models import SyncSchedule

    stack.clock.set(datetime(2026, 10, 20, 8, 0, tzinfo=SP))
    w = stack.worker()
    w.refresh_companies(force=True)
    original = sync_mod.store_blob
    calls = {"n": 0}

    def flaky(*a, **k):  # noqa: ANN002, ANN003, ANN202
        if a[1] == world["a"]:
            calls["n"] += 1
            if calls["n"] == 5:  # 2º item da segunda página da empresa A
                raise OSError("disco cheio")
        return original(*a, **k)

    monkeypatch.setattr(sync_mod, "store_blob", flaky)
    run_all(w)
    with stack.fiscal_sessions() as s:
        sched = s.scalar(select(SyncSchedule).where(SyncSchedule.company_id == world["a"]))
        failed_cursor, failed_status = sched.cursor_nsu, sched.status
    assert (failed_cursor, failed_status) == (3, "ERROR")  # página 2 inteira descartada; cursor preservado
    assert docs(stack, world)["total"] == 3  # página 1 (NSU 1–3) gravada; página 2 descartada inteira
    assert docs(stack, world, status="CANCELLED")["total"] == 0  # evento (NSU 6) ainda não gravado
    monkeypatch.setattr(sync_mod, "store_blob", original)
    stack.clock.advance(hours=1)
    run_all(w)
    st = status(stack, world)
    assert st["status"] == "OK"
    assert docs(stack, world)["total"] == 3  # nada perdido, nada duplicado
    assert docs(stack, world, status="CANCELLED")["total"] == 1


def test_schedule_lease_single_owner(stack, world):
    from ctrl_fiscal.domain import sync

    stack.clock.set(datetime(2026, 10, 20, 8, 0, tzinfo=SP))
    stack.worker().refresh_companies(force=True)
    s1 = sync.acquire_schedule(stack.fiscal_sessions, stack.clock, "w1", 60)
    s2 = sync.acquire_schedule(stack.fiscal_sessions, stack.clock, "w2", 60)
    s3 = sync.acquire_schedule(stack.fiscal_sessions, stack.clock, "w3", 60)
    assert s1 and s2 and s1[0] != s2[0] and s3 is None  # 2 agendas, cada uma com um dono
    stack.clock.advance(seconds=61)
    again = sync.acquire_schedule(stack.fiscal_sessions, stack.clock, "w3", 60)
    assert again is not None and again[1] == 2  # novo fencing token após expiração


def test_job_requested_mid_cycle_is_not_completed_by_earlier_cycle(stack, world):
    """Um job só termina em ciclo iniciado DEPOIS do pedido; sucesso anterior não comprova o job novo."""
    import uuid

    stack.clock.set(datetime(2026, 10, 20, 8, 0, tzinfo=SP))
    w = stack.worker()
    w.refresh_companies(force=True)
    original = w.nfse.distribution_page
    holder: dict = {}

    def mid_cycle(company_id, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if company_id == world["a"] and "job" not in holder:  # pedido chega com o ciclo da empresa A em andamento
            stack.clock.advance(seconds=5)
            r = stack.fiscal.post(f"{F}/{world['a']}/sync/run", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex})
            assert r.status_code == 202, r.text
            holder["job"] = r.json()["job_id"]
        return original(company_id, *args, **kwargs)

    w.nfse.distribution_page = mid_cycle
    run_all(w)
    w.nfse.distribution_page = original
    job = stack.fiscal.get(f"{F}/{world['a']}/jobs/{holder['job']}", headers=world["ha"]).json()
    st = status(stack, world)
    assert st["last_success_at"] is not None  # o ciclo em andamento terminou com sucesso…
    assert job["status"] == "QUEUED" and job["finished_at"] is None  # …mas NÃO comprova o job pedido durante ele
    # Próximo ciclo permitido (após a espera de fila vazia): ciclo próprio do job, estado terminal próprio.
    stack.clock.advance(hours=1, minutes=1)
    run_all(w)
    job = stack.fiscal.get(f"{F}/{world['a']}/jobs/{holder['job']}", headers=world["ha"]).json()
    assert job["status"] == "SUCCEEDED" and job["finished_at"]
    assert datetime.fromisoformat(job["started_at"]) >= datetime.fromisoformat(job["created_at"])
