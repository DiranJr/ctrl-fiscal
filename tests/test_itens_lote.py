"""Detalhe do lote: TODOS os itens (autorizados, rejeitados, retirados, pendentes, bloqueados e incertos) com etapa,
erros com origem (validação local x rejeição oficial), referências, datas, rascunho de origem e orientação.
Somente leitura: nada muda situação, histórico ou auditoria. Isolado por workspace. Somente dados fictícios."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from test_emission_queue import batch, make_draft, schedule

F = "/api/fiscal/v1/companies"


def _detalhe(stack, world, bid, h=None, empresa=None):  # noqa: ANN001, ANN202
    return stack.fiscal.get(f"{F}/{empresa or world['a']}/batches/{bid}", headers=h or world["ha"])


def _rodar(stack, plano: list[str]) -> None:  # noqa: ANN001
    stack.clock.advance(minutes=2)
    stack.mock.plan = list(plano)
    stack.worker_settings.emission_batch_size = 1
    w = stack.worker()
    while w.run_emissions():
        pass


def test_lote_rejeitado_mostra_o_item_com_rejeicao_oficial(stack, world):
    d = make_draft(stack, world, amount="321.00")
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    _rodar(stack, ["reject"])
    j = _detalhe(stack, world, b["batch_id"]).json()
    assert j["status"] == "COMPLETED_WITH_REJECTIONS" and j["total"] == 1 and len(j["items"]) == 1
    it = j["items"][0]
    assert it["status"] == "REJECTED" and it["stage"] == "OFFICIAL_REJECTION" and it["transmitted"] is True
    assert it["errors"][0]["code"] == "MOCK_REJECTION" and it["errors"][0]["source"] == "OFFICIAL"
    assert it["amount"] == "321.00" and it["competence"] == "2026-10" and it["customer_name"]
    assert it["draft"]["id"] == d["id"] and it["draft"]["status"] == "REJECTED" and it["draft"]["linked"] is True
    assert it["dps_id"] and it["dps_number"] and it["submission_id"] and it["finished_at"] and it["scheduled_at"]
    assert "Corrija o rascunho" in it["guidance"]


def test_lote_misto_traz_todos_os_itens(stack, world):
    ds = [make_draft(stack, world, amount=f"{100 + k}.00") for k in range(3)]
    b = schedule(stack, world, ds, stack.clock.now() + timedelta(minutes=1))
    _rodar(stack, ["ok", "reject", "timeout_after_accept"])
    j = _detalhe(stack, world, b["batch_id"]).json()
    assert len(j["items"]) == j["total"] == 3
    por = {i["status"]: i for i in j["items"]}
    assert set(por) == {"AUTHORIZED", "REJECTED", "AWAITING_CONFIRMATION"}, list(por)
    assert por["AUTHORIZED"]["stage"] == "AUTHORIZED" and por["AUTHORIZED"]["document_id"]
    inc = por["AWAITING_CONFIRMATION"]
    assert inc["stage"] == "UNCERTAIN" and inc["transmitted"] is None and "Não reenvie" in inc["guidance"]
    assert inc["errors"] and inc["next_attempt_at"]


def test_retirada_e_bloqueio_antes_do_envio(stack, world):
    from ctrl_fiscal.domain import emission
    from ctrl_fiscal.models import BatchItem

    d1, d2 = make_draft(stack, world), make_draft(stack, world)
    b = schedule(stack, world, [d1, d2], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    w = stack.worker()
    leased = emission.acquire_items(w.sessions, stack.clock, "w1", limit=1, lease_seconds=60, max_in_flight=10, series="1")
    emission._finish(w.sessions, stack.clock, "w1", leased[0],
                     {"state": "BLOCKED", "error": {"code": "CERTIFICATE_NOT_CONFIGURED", "message": "Sem certificado."}})
    with w.sessions() as s:
        bloqueado = s.get(BatchItem, leased[0].id).draft_id
    outro = d2["id"] if bloqueado == d1["id"] else d1["id"]
    with w.sessions() as s, s.begin():
        it = s.scalar(select(BatchItem).where(BatchItem.draft_id == outro))
        it.status, it.finished_at = "WITHDRAWN", stack.clock.now()
    j = _detalhe(stack, world, b["batch_id"]).json()
    por = {i["draft_id"]: i for i in j["items"]}
    assert por[bloqueado]["stage"] == "BLOCKED_BEFORE_SEND" and por[bloqueado]["transmitted"] is False
    assert por[bloqueado]["errors"][0]["code"] == "CERTIFICATE_NOT_CONFIGURED"
    assert por[outro]["stage"] == "WITHDRAWN" and por[outro]["transmitted"] is False


def test_rejeicao_por_validacao_local_nao_foi_transmitida(stack, world):
    from ctrl_fiscal.domain import emission

    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    w = stack.worker()
    it = emission.acquire_items(w.sessions, stack.clock, "w1", limit=5, lease_seconds=60, max_in_flight=10, series="1")[0]
    emission._finish(w.sessions, stack.clock, "w1", it, {"state": "REJECTED", "errors": [
        {"code": "LOCAL_XSD", "message": "Elemento cNBS inválido (fictício)."},
        {"code": "LOCAL_E0617", "message": "Não informe alíquota (fictício)."}]})
    x = _detalhe(stack, world, b["batch_id"]).json()["items"][0]
    assert x["stage"] == "LOCAL_VALIDATION" and x["transmitted"] is False
    assert [e["code"] for e in x["errors"]] == ["LOCAL_XSD", "LOCAL_E0617"]
    assert all(e["source"] == "LOCAL" for e in x["errors"]) and "Nenhuma DPS foi transmitida" in x["guidance"]


def test_detalhe_e_somente_leitura_e_isolado(stack, world):
    from ctrl_fiscal.models import AuditEvent, BatchItem

    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    _rodar(stack, ["reject"])
    with stack.fiscal_sessions() as s:
        antes = (s.scalar(select(func.count()).select_from(AuditEvent)), s.scalar(select(BatchItem.updated_at)))
    for _ in range(2):
        assert _detalhe(stack, world, b["batch_id"]).status_code == 200
    with stack.fiscal_sessions() as s:
        assert (s.scalar(select(func.count()).select_from(AuditEvent)), s.scalar(select(BatchItem.updated_at))) == antes
    assert _detalhe(stack, world, b["batch_id"], h=world["hr"]).status_code == 200  # leitor (view) enxerga
    assert _detalhe(stack, world, b["batch_id"], h=world["hb"], empresa=world["b"]).status_code == 404
    assert _detalhe(stack, world, b["batch_id"], h=world["hb"]).status_code in (403, 404)
    assert stack.fiscal.get(f"{F}/{world['a']}/batches/{b['batch_id']}").status_code == 401
    assert batch(stack, world, b["batch_id"])["items"][0]["status"] == "REJECTED"
