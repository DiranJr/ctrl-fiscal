"""Excluir rascunho: exclusão LÓGICA, só sem transmissão e sem vínculo com processamento fiscal; recusa estados
agendado/em fila/transmitindo/incerto/emitido/rejeitado pela Sefin (com o motivo em "deletion"); vínculo antigo com
item retirado ou barrado pela validação local antes do envio não impede; idempotente; isolada por workspace e
permissão "prepare"; protegida contra concorrência com editar, emitir agora e agendar. Somente dados fictícios
(governo simulado)."""
from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from conftest import MYSQL, cnpj
from fixtures_nfse import nota_como_187
from test_emission_queue import TAX, batch, make_draft, schedule
from test_rascunho_referencia import importar, referencia

F = "/api/fiscal/v1/companies"


def excluir(stack, world, d: dict, version: int | None = None, h: dict | None = None, empresa: str | None = None):  # noqa: ANN001, ANN201
    return stack.fiscal.delete(f"{F}/{empresa or world['a']}/drafts/{d['id']}", headers=h or world["ha"],
                               params={"expected_version": version or d["version"]})


def agora(stack, world, d: dict):  # noqa: ANN001, ANN201
    return stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                             json={"drafts": [{"id": d["id"], "expected_version": d["version"]}], "mode": "NOW",
                                   "timezone": "America/Sao_Paulo"})


def atual(stack, world, d: dict) -> dict:  # noqa: ANN001
    return stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()


def test_exclusao_logica_some_da_lista_e_fica_indisponivel(stack, world):
    from ctrl_fiscal.models import AuditEvent, Draft

    d = make_draft(stack, world, amount="321.00")
    outro = make_draft(stack, world, amount="654.00")
    r = excluir(stack, world, d)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["deleted"] and not j["already_deleted"] and j["version"] == d["version"] + 1
    lista = stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).json()
    assert [x["id"] for x in lista["items"]] == [outro["id"]] and lista["total"] == 1
    painel = stack.fiscal.get(f"{F}/{world['a']}/dashboard", headers=world["ha"]).json()
    assert sum(painel["drafts"].values()) == 1
    # Indisponível para abrir, editar, validar, configurar, retirar e emitir.
    url = f"{F}/{world['a']}/drafts/{d['id']}"
    assert stack.fiscal.get(url, headers=world["ha"]).status_code == 410
    assert stack.fiscal.get(url, headers=world["ha"]).json()["code"] == "DRAFT_DELETED"
    assert stack.fiscal.patch(url, headers=world["ha"], json={"expected_version": j["version"], "amount": "1.00"}).status_code == 410
    assert stack.fiscal.post(f"{url}/validate", headers=world["ha"]).status_code == 410
    assert stack.fiscal.post(f"{url}/withdraw", headers=world["ha"], json={"expected_version": j["version"]}).status_code == 410
    r = agora(stack, world, {**d, "version": j["version"]})
    assert r.status_code == 409 and r.json()["errors"][0]["code"] == "DRAFT_DELETED"
    # Repetir é idempotente (mesmo resultado, nada muda).
    r2 = excluir(stack, world, d, version=1)
    assert r2.status_code == 200 and r2.json()["already_deleted"] and r2.json()["deleted_at"] == j["deleted_at"]
    # Exclusão lógica: linha, versão e autoria preservadas; auditoria registrada uma vez.
    with stack.fiscal_sessions() as s:
        row = s.get(Draft, d["id"])
        assert row is not None and row.deleted_at is not None and row.deleted_by == world["ua"]
        assert row.version == j["version"] and row.service_description == d["service"]["description"]
        ev = s.scalars(select(AuditEvent).where(AuditEvent.target_id == d["id"], AuditEvent.action == "draft.deleted")).all()
        assert len(ev) == 1 and ev[0].details["amount"] == "321.00" and ev[0].details["logical"] is True


def test_versao_desatualizada_nao_exclui(stack, world):
    d = make_draft(stack, world)
    stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                       json={"expected_version": d["version"], "amount": "77.00"})
    r = excluir(stack, world, d)  # confirmação feita sobre a versão anterior
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    assert atual(stack, world, d)["amount"] == "77.00"


def test_agendado_exige_cancelar_antes_e_depois_pode_excluir(stack, world):
    d = make_draft(stack, world)
    schedule(stack, world, [d], stack.clock.now() + timedelta(hours=2))
    ag = atual(stack, world, d)
    assert ag["deletion"]["allowed"] is False and ag["deletion"]["reason_code"] == "DRAFT_SCHEDULED"
    r = excluir(stack, world, ag)
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_DELETABLE" and "Retire-o da fila" in r.json()["detail"]
    w = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/withdraw", headers=world["ha"],
                          json={"expected_version": ag["version"]})
    assert w.status_code == 200 and w.json()["status"] == "DRAFT" and w.json()["deletion"]["allowed"] is True
    r = excluir(stack, world, w.json())
    assert r.status_code == 200, r.text  # item retirado antes de qualquer envio não é vínculo de processamento


@pytest.mark.parametrize("plano,status_item,motivo", [
    ([], "AUTHORIZED", "DRAFT_AUTHORIZED"), (["timeout_after_accept"], "AWAITING_CONFIRMATION", "DRAFT_UNCERTAIN"),
    (["reject"], "REJECTED", "DRAFT_TRANSMITTED")])
def test_estados_com_processamento_fiscal_sao_recusados(stack, world, plano, status_item, motivo):
    from ctrl_fiscal.models import BatchItem, Document

    d = make_draft(stack, world)
    b = schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = list(plano)
    stack.worker().run_emissions()
    assert [i["status"] for i in batch(stack, world, b["batch_id"])["items"]] == [status_item]
    with stack.fiscal_sessions() as s:
        docs_antes = s.scalar(select(func.count()).select_from(Document))
    cur = atual(stack, world, d)
    assert cur["deletion"]["allowed"] is False and cur["deletion"]["reason_code"] == motivo and cur["deletion"]["reason"]
    r = excluir(stack, world, cur)
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_DELETABLE", r.text
    assert r.json()["detail"] == cur["deletion"]["reason"]  # o painel mostra o mesmo motivo que o servidor aplica
    if status_item == "REJECTED":
        # Rejeição da Sefin (DPS transmitida): corrigido, volta a DRAFT, mas o histórico de transmissão impede excluir.
        fixed = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                                   json={"expected_version": cur["version"], "amount": "999.00"}).json()
        assert fixed["status"] == "DRAFT" and fixed["deletion"]["reason_code"] == "DRAFT_TRANSMITTED"
        assert "histórico de transmissão" in fixed["deletion"]["reason"]
        r = excluir(stack, world, fixed)
        assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_DELETABLE"
    with stack.fiscal_sessions() as s:  # nada de nota, item ou submissão foi tocado
        assert s.scalar(select(func.count()).select_from(Document)) == docs_antes
        it = s.scalar(select(BatchItem).where(BatchItem.draft_id == d["id"]))
        assert it.status == status_item
    assert atual(stack, world, d)["id"] == d["id"]  # rascunho mantido


LOCAL = {"state": "REJECTED", "errors": [{"code": "LOCAL_E0617",
                                           "message": "Não informe a alíquota (fictício). Nada foi transmitido."}]}


def _tentativa(stack, resp: dict) -> None:  # noqa: ANN001
    """Uma tentativa do worker (lease + resultado), pelo mesmo caminho do worker."""
    from ctrl_fiscal.domain import emission

    w = stack.worker()
    it = emission.acquire_items(w.sessions, stack.clock, "w1", limit=5, lease_seconds=60, max_in_flight=10, series="1")[0]
    emission._finish(w.sessions, stack.clock, "w1", it, resp)


def _rejeitar_localmente(stack, world, d: dict, antes: list[dict] = (), resp: dict = LOCAL) -> dict:  # noqa: ANN001
    """Tentativa barrada pela validação local do conector (LOCAL_*), opcionalmente depois de outras tentativas."""
    schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    for r in [*antes, resp]:
        stack.clock.advance(hours=2)
        _tentativa(stack, r)
    return atual(stack, world, d)


@pytest.mark.parametrize("antes,resp,permitido", [
    ([], {**LOCAL, "transmissions": 1}, True),  # o conector registra UMA chamada, barrada antes do POST
    ([{"state": "DEFER", "error": {"code": "SERVICE_DATE_NOT_REACHED", "message": "Nada transmitido."}}],
     {**LOCAL, "transmissions": 1}, True),  # adiamento antes do conector não conta como envio
    ([{"state": "UNCERTAIN", "error": {"code": "TIMEOUT", "message": "Sem confirmação."}}], LOCAL, False),
    ([{"state": "UNCERTAIN", "error": {"code": "TIMEOUT", "message": "Sem confirmação."}}],
     {**LOCAL, "transmissions": 2}, False),  # houve resultado incerto antes: o prefixo LOCAL_ não basta
    ([], {**LOCAL, "transmissions": 2}, False),
])
def test_rejeicao_local_exige_evidencia_de_que_nada_foi_transmitido(stack, world, antes, resp, permitido):
    d = make_draft(stack, world)
    cur = _rejeitar_localmente(stack, world, d, antes, resp)
    assert cur["status"] == "REJECTED", cur["status"]
    assert cur["deletion"]["allowed"] is permitido, cur["deletion"]
    if not permitido:
        assert cur["deletion"]["reason_code"] == "DRAFT_SEND_UNPROVEN" and "comprovação" in cur["deletion"]["reason"]
    assert excluir(stack, world, cur).status_code == (200 if permitido else 409)


def test_rejeicao_local_corrigida_pode_ser_excluida_e_preserva_o_vinculo(stack, world):
    """Rejeitado só pela validação local e corrigido: volta a DRAFT ("Rascunho") com o vínculo antigo ao item
    encerrado. Lista, detalhe e exclusão liberam; o vínculo e o item ficam como estavam (nada apagado no banco)."""
    from ctrl_fiscal.models import AuditEvent, BatchItem, Draft

    d = make_draft(stack, world)
    rej = _rejeitar_localmente(stack, world, d)
    assert rej["status"] == "REJECTED" and rej["current_item_id"] and rej["deletion"]["allowed"] is True
    fixed = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                               json={"expected_version": rej["version"], "amount": "500.00"}).json()
    assert fixed["status"] == "DRAFT" and fixed["current_item_id"] == rej["current_item_id"]
    assert fixed["deletion"] == {"allowed": True, "reason_code": None, "reason": None}
    lista = stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).json()
    assert next(x for x in lista["items"] if x["id"] == d["id"])["deletion"]["allowed"] is True
    assert excluir(stack, world, fixed, h=world["hr"]).status_code == 403  # sem "prepare" continua recusado
    r = excluir(stack, world, fixed)
    assert r.status_code == 200, r.text
    with stack.fiscal_sessions() as s:
        row = s.get(Draft, d["id"])
        assert row.deleted_at is not None and row.current_item_id == rej["current_item_id"]
        it = s.get(BatchItem, rej["current_item_id"])
        assert it.status == "REJECTED" and it.result["errors"][0]["code"] == "LOCAL_E0617"
        ev = s.scalar(select(AuditEvent).where(AuditEvent.target_id == d["id"], AuditEvent.action == "draft.deleted"))
        assert ev.details["items"] == [{"id": it.id, "status": "REJECTED", "stage": "LOCAL_VALIDATION", "attempts": 1,
                                        "submit_phase": "NONE", "connector_transmissions": None}]


def test_rejeicao_local_seguida_de_transmissao_continua_recusada(stack, world):
    d = make_draft(stack, world)
    rej = _rejeitar_localmente(stack, world, d)
    fixed = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                               json={"expected_version": rej["version"], "amount": "501.00"}).json()
    schedule(stack, world, [fixed], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    stack.mock.plan = ["reject"]
    stack.worker().run_emissions()
    cur = atual(stack, world, d)
    assert cur["status"] == "REJECTED" and cur["deletion"]["reason_code"] == "DRAFT_TRANSMITTED"
    assert excluir(stack, world, cur).status_code == 409


def test_em_transmissao_e_recusado(stack, world):
    from fiscal_worker.worker import SimulatedCrash

    d = make_draft(stack, world)
    schedule(stack, world, [d], stack.clock.now() + timedelta(minutes=1))
    stack.clock.advance(minutes=2)
    w = stack.worker("w1")

    def crash(it, resp):  # noqa: ANN001
        raise SimulatedCrash()

    w.after_external = crash
    with pytest.raises(SimulatedCrash):
        w.run_emissions()
    cur = atual(stack, world, d)
    assert cur["status"] == "PROCESSING" and cur["deletion"]["reason_code"] == "DRAFT_PROCESSING"
    r = excluir(stack, world, cur)
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_DELETABLE"


def test_referencia_preservada(stack, world):
    from ctrl_fiscal.models import Document, StoredBlob

    toma = cnpj("445566770001")
    docs = importar(stack, world, nota_como_187(world["a_cnpj"], toma, 5150), "n5150.xml")
    doc = next(x for x in docs if x["number"] == "5150")
    assert stack.core.post(f"/api/core/v1/companies/{world['a']}/customers", headers=world["ha"], json={
        "name": "Tomador Referência", "document_type": "CNPJ", "document": toma}).status_code == 201
    r = referencia(stack, world, doc["id"])
    assert r.status_code == 201, r.text
    with stack.fiscal_sessions() as s:
        blobs = s.scalar(select(func.count()).select_from(StoredBlob))
    assert excluir(stack, world, r.json()).status_code == 200
    with stack.fiscal_sessions() as s:
        assert s.get(Document, doc["id"]) is not None
        assert s.scalar(select(func.count()).select_from(StoredBlob)) == blobs
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).status_code == 200
    # A nota continua servindo de referência para um novo rascunho.
    assert referencia(stack, world, doc["id"]).status_code == 201


def test_workspace_permissao_e_autenticacao(stack, world):
    d = make_draft(stack, world)
    # Outro workspace: pelo próprio caminho não acha (404); pelo caminho de A não tem acesso (403). Nada é revelado.
    assert excluir(stack, world, d, h=world["hb"], empresa=world["b"]).status_code == 404
    assert excluir(stack, world, d, h=world["hb"]).status_code in (403, 404)
    assert excluir(stack, world, d, h=world["hr"]).status_code == 403  # só leitura (sem "prepare")
    assert stack.fiscal.delete(f"{F}/{world['a']}/drafts/{d['id']}", params={"expected_version": 1}).status_code == 401
    assert atual(stack, world, d)["id"] == d["id"]
    # Repetição de exclusão de rascunho de A por B continua sem revelar nada.
    assert excluir(stack, world, d).status_code == 200
    assert excluir(stack, world, d, h=world["hb"], empresa=world["b"]).status_code == 404


def test_excluido_nao_entra_na_fila_mesmo_com_versao_antiga(stack, world):
    d = make_draft(stack, world)
    assert excluir(stack, world, d).status_code == 200
    r = stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                          json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                "scheduled_at": (stack.clock.now() + timedelta(hours=1)).isoformat(),
                                "timezone": "America/Sao_Paulo"})
    assert r.status_code == 409 and r.json()["errors"][0]["code"] == "DRAFT_DELETED"


@pytest.mark.skipif(not MYSQL, reason="concorrência real exige MySQL (travas de linha)")
@pytest.mark.parametrize("rival", ["agora", "agendar", "editar"])
def test_excluir_concorrente_com_emitir_agendar_e_editar_mysql(stack, world, rival):
    from ctrl_fiscal.models import BatchItem, Draft

    for _ in range(4):
        d = make_draft(stack, world, amount="123.00")
        barreira = threading.Barrier(2)
        out: dict[str, int] = {}

        def go(nome: str, fn) -> None:  # noqa: ANN001
            barreira.wait()
            out[nome] = fn().status_code

        acoes = {
            "agora": lambda: agora(stack, world, d),
            "agendar": lambda: stack.fiscal.post(
                f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                      "scheduled_at": (stack.clock.now() + timedelta(hours=1)).isoformat(),
                      "timezone": "America/Sao_Paulo"}),
            "editar": lambda: stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                                                 json={"expected_version": d["version"], "amount": "9.00"}),
        }
        ts = [threading.Thread(target=go, args=("excluir", lambda: excluir(stack, world, d))),
              threading.Thread(target=go, args=(rival, acoes[rival]))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        ok = {k for k, v in out.items() if v in (200, 202)}
        assert len(ok) == 1, out  # exatamente uma das duas operações vence
        with stack.fiscal_sessions() as s:
            row = s.get(Draft, d["id"])
            itens = s.scalars(select(BatchItem).where(BatchItem.draft_id == d["id"])).all()
            if "excluir" in ok:
                assert row.deleted_at is not None and itens == [] and row.amount == 123
            else:
                assert row.deleted_at is None
                assert (len(itens) == 1) if rival != "editar" else (row.amount == 9)


def test_criacao_e_edicao_seguem_normais(stack, world):
    """Sem regressão: rascunho novo não nasce excluído e a lista mostra os não excluídos."""
    d = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "10.00",
        "service": {"national_code": "010101", "description": "Serviço"}, "tax_settings": TAX}).json()
    assert stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).json()["total"] == 1
    assert atual(stack, world, d)["status"] == "DRAFT"
