"""Classificação única de rascunhos (pendentes/emitidos/todos) e de lotes (em andamento/exigem atenção/histórico/
todos), aplicada no servidor antes da paginação e igual aos indicadores do painel. Somente leitura: listar nunca muda
estado. Emitido só após confirmação oficial; nada reabre um rascunho emitido. Somente dados fictícios."""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select

from test_emission_queue import make_draft, schedule
from test_excluir_rascunho import agora, atual

F = "/api/fiscal/v1/companies"


def lista(stack, world, view: str, **params):  # noqa: ANN001, ANN201
    return stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"], params={"view": view, **params}).json()


def lotes(stack, world, view: str, h=None, empresa=None, **params):  # noqa: ANN001, ANN201
    return stack.fiscal.get(f"{F}/{empresa or world['a']}/batches", headers=h or world["ha"],
                            params={"view": view, **params}).json()


def painel(stack, world) -> dict:  # noqa: ANN001
    return stack.fiscal.get(f"{F}/{world['a']}/dashboard", headers=world["ha"]).json()["views"]


def rodar(stack, plano: list[str]) -> None:  # noqa: ANN001
    stack.clock.advance(minutes=2)
    stack.mock.plan = list(plano)
    stack.worker_settings.emission_batch_size = 1
    w = stack.worker()
    while w.run_emissions():
        pass


def test_rascunho_emitido_sai_dos_pendentes_e_nao_reabre(stack, world):
    from ctrl_fiscal.models import Document

    d = make_draft(stack, world, amount="101.00")
    outro = make_draft(stack, world, amount="202.00")
    schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    rodar(stack, [])
    pend, emit = lista(stack, world, "pending"), lista(stack, world, "issued")
    assert [x["id"] for x in pend["items"]] == [outro["id"]]
    assert [x["id"] for x in emit["items"]] == [d["id"]] and emit["items"][0]["status"] == "AUTHORIZED"
    nota = emit["items"][0]["issued"]
    assert nota["status"] == "AUTHORIZED" and nota["document_id"] and nota["number"]
    assert pend["counts"] == {"pending": 1, "issued": 1, "all": 2} == painel(stack, world)["drafts"]
    assert lista(stack, world, "all")["total"] == 2
    # Emitido não é editado, excluído nem reagendado (as travas do servidor continuam valendo).
    cur = atual(stack, world, d)
    assert stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                              json={"expected_version": cur["version"], "amount": "1.00"}).status_code == 409
    assert stack.fiscal.delete(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                               params={"expected_version": cur["version"]}).status_code == 409
    assert agora(stack, world, cur).status_code == 409
    # Cancelada e depois substituída: a situação atual aparece, sem reabrir o rascunho.
    with stack.fiscal_sessions() as s:
        s.get(Document, nota["document_id"]).status = "CANCELLED"
        s.commit()
    x = lista(stack, world, "issued")["items"][0]
    assert x["issued"]["status"] == "CANCELLED" and x["status"] == "AUTHORIZED"
    with stack.fiscal_sessions() as s:
        doc = s.get(Document, nota["document_id"])
        doc.status, doc.substituted_by_access_key = "AUTHORIZED", "9" * 50
        s.commit()
    assert lista(stack, world, "issued")["items"][0]["issued"]["status"] == "SUBSTITUTED"
    assert lista(stack, world, "pending")["counts"]["pending"] == 1


def test_registro_autorizado_antigo_aparece_em_emitidos_sem_migracao(stack, world):
    from ctrl_fiscal.models import Draft

    d = make_draft(stack, world)
    with stack.fiscal_sessions() as s:  # registro antigo: só a situação gravada, sem item nem nota vinculados
        s.get(Draft, d["id"]).status = "AUTHORIZED"
        s.commit()
    emit = lista(stack, world, "issued")
    assert [x["id"] for x in emit["items"]] == [d["id"]] and emit["items"][0]["issued"] is None
    assert lista(stack, world, "pending")["total"] == 0


def test_incerto_e_rejeitado_continuam_pendentes(stack, world):
    d1, d2 = make_draft(stack, world, amount="11.00"), make_draft(stack, world, amount="22.00")
    schedule(stack, world, [d1, d2], stack.clock.now() + timedelta(minutes=1))
    rodar(stack, ["timeout_after_accept", "reject"])
    pend = {x["id"]: x["status"] for x in lista(stack, world, "pending")["items"]}
    assert sorted(pend.values()) == ["AWAITING_CONFIRMATION", "REJECTED"]  # só emitido após confirmação oficial
    assert lista(stack, world, "issued")["total"] == 0


def test_visoes_de_lotes_pelos_estados_reais(stack, world):
    # 1) agendado no futuro; 2) emitido agora (concluído); 3) misto (autorizado + rejeitado + incerto); 4) retirado.
    futuro = make_draft(stack, world)
    b1 = schedule(stack, world, [futuro], stack.clock.now() + timedelta(days=5))["batch_id"]
    ok = make_draft(stack, world)
    r = agora(stack, world, ok)
    assert r.status_code == 202 and r.json()["mode"] == "NOW"
    b2 = r.json()["batch_id"]
    stack.mock.plan = []
    w = stack.worker()
    w.run_emissions()
    ms = [make_draft(stack, world, amount=f"{30 + k}.00") for k in range(3)]
    b3 = schedule(stack, world, ms, stack.clock.now() + timedelta(minutes=1))["batch_id"]
    rodar(stack, ["ok", "reject", "timeout_after_accept"])
    ret = make_draft(stack, world)
    b4 = schedule(stack, world, [ret], stack.clock.now() + timedelta(days=5))["batch_id"]
    stack.fiscal.post(f"{F}/{world['a']}/batches/{b4}/cancel-pending", headers=world["ha"])
    ids = lambda v: {x["id"] for x in lotes(stack, world, v)["items"]}  # noqa: E731
    assert ids("in_progress") == {b1, b3}  # agendado e incerto
    assert ids("attention") == {b3}  # rejeição não resolvida
    assert ids("history") == {b2, b4}
    assert ids("all") == {b1, b2, b3, b4}
    pagina = lotes(stack, world, "all", page_size=1)
    assert pagina["total"] == 4 and len(pagina["items"]) == 1
    assert pagina["counts"] == {"in_progress": 2, "attention": 1, "history": 2, "all": 4} == painel(stack, world)["batches"]
    modos = {x["id"]: x["mode"] for x in lotes(stack, world, "all")["items"]}
    assert modos[b2] == "NOW" and modos[b1] == "SCHEDULED"
    misto = next(x for x in lotes(stack, world, "all")["items"] if x["id"] == b3)
    assert misto["needs_attention"] is True and misto["counts"]["AUTHORIZED"] == 1 and misto["counts"]["REJECTED"] == 1
    assert misto["counts"]["AWAITING_CONFIRMATION"] == 1
    # Reconciliado: sai de "em andamento", vai ao histórico e continua em "exigem atenção" pela rejeição.
    stack.clock.advance(minutes=5)
    while w.run_emissions():
        pass
    assert b3 in ids("history") and b3 in ids("attention") and b3 not in ids("in_progress")
    # Rejeição resolvida (rascunho corrigido e reagendado): o lote deixa de exigir atenção.
    rej = next(atual(stack, world, m) for m in ms if atual(stack, world, m)["status"] == "REJECTED")
    fix = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{rej['id']}", headers=world["ha"],
                             json={"expected_version": rej["version"], "amount": "99.00"}).json()
    schedule(stack, world, [fix], stack.clock.now() + timedelta(days=3))
    assert b3 not in ids("attention")


def test_isolamento_somente_leitura_e_ambiente(stack, world):
    from ctrl_fiscal.models import AuditEvent, Batch, BatchItem

    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))["batch_id"]
    rodar(stack, ["reject"])
    with stack.fiscal_sessions() as s:
        antes = (s.scalar(select(func.count()).select_from(AuditEvent)), s.scalar(select(BatchItem.status)))
    for v in ("in_progress", "attention", "history", "all"):
        lotes(stack, world, v)
    for v in ("pending", "issued", "all"):
        lista(stack, world, v)
    stack.fiscal.get(f"{F}/{world['a']}/batches/{b}", headers=world["ha"])
    with stack.fiscal_sessions() as s:
        assert (s.scalar(select(func.count()).select_from(AuditEvent)), s.scalar(select(BatchItem.status))) == antes
    lb = lotes(stack, world, "all", h=world["hb"], empresa=world["b"])
    assert lb["total"] == 0 and lb["counts"]["all"] == 0
    assert stack.fiscal.get(f"{F}/{world['b']}/drafts", headers=world["hb"], params={"view": "all"}).json()["total"] == 0
    # Outro ambiente do mesmo workspace não entra nas listas nem nas contagens.
    with stack.fiscal_sessions() as s:
        s.get(Batch, b).environment = "restricted"
        s.commit()
    assert lotes(stack, world, "all")["total"] == 0 and lotes(stack, world, "all")["counts"]["attention"] == 0
    assert stack.fiscal.get(f"{F}/{world['a']}/batches", headers=world["ha"], params={"view": "x"}).status_code == 422
    assert uuid.UUID(b)
