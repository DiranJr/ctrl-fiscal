"""Rascunho a partir de nota emitida ("Usar como referência"): defeito da NFS-e nº 187 (nota da empresa emitida no
Emissor Web e importada do ADN, tomador ainda não cadastrado → 422 CUSTOMER_NOT_LINKED sem caminho de solução).

Fixture com a MESMA estrutura da nota 187 (grupos e campos presentes), valores fictícios. Cobre: nota do ADN com
cadastro assistido do tomador a partir do XML, nota gerada pela plataforma, retenções/campos opcionais, dados
incompletos (pendências no rascunho), acesso entre empresas, clique repetido/timeout (idempotência) e a nota
original inalterada. Nada é transmitido."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from conftest import cnpj
from fixtures_nfse import MUN, chave, nota_como_187  # noqa: F401

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"


def importar(stack, world, xml: bytes, nome: str) -> dict:  # noqa: ANN001
    r = stack.fiscal.post(f"{F}/{world['a']}/documents/import", headers=world["ha"],
                          files=[("files", (nome, xml, "application/xml"))])
    assert r.status_code == 202, r.text
    w = stack.worker()
    while w.run_jobs():
        pass
    docs = stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"],
                            params={"direction": "ISSUED", "environment": "mock", "query": nome.split(".")[0]}).json()
    items = [d for d in stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"],
                                         params={"direction": "ISSUED", "environment": "mock", "page_size": 100}).json()["items"]]
    assert docs is not None
    return items


def referencia(stack, world, doc_id: str, key: str | None = None, h=None):  # noqa: ANN001, ANN201
    return stack.fiscal.post(f"{F}/{world['a']}/drafts/from-document",
                             headers={**(h or world["ha"]), "Idempotency-Key": key or uuid.uuid4().hex},
                             json={"source_document_id": doc_id})


def doc_por_numero(stack, world, numero: str) -> dict:  # noqa: ANN001
    items = stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"],
                             params={"direction": "ISSUED", "environment": "mock", "page_size": 100}).json()["items"]
    return next(d for d in items if d["number"] == numero)


def test_nota_do_adn_com_tomador_sem_cadastro_reproduz_e_corrige(stack, world):
    toma = cnpj("556677880001")
    importar(stack, world, nota_como_187(world["a_cnpj"], toma, 187), "n187.xml")
    doc = doc_por_numero(stack, world, "187")
    original = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    # Reprodução: sem tomador cadastrado o servidor recusa — agora com código e mensagem acionáveis.
    r = referencia(stack, world, doc["id"])
    assert r.status_code == 422 and r.json()["code"] == "CUSTOMER_NOT_LINKED", r.text
    assert "Clientes" in r.json()["detail"]
    # Tomador lido do XML oficial (nada inventado; o que falta é listado).
    rec = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}/recipient", headers=world["ha"]).json()
    assert rec["document"] == toma and rec["document_type"] == "CNPJ" and rec["name"] == "TOMADOR FICTICIO"
    assert rec["municipality_code"] == MUN and rec["postal_code"] == "68515000" and rec["source"] == "XML"
    assert rec["address"] == {"street": "Rua Um", "number": "123", "complement": "Sala 2", "district": "Centro"}
    assert rec["email"] is None and rec["phone"] is None and rec["missing"] == [] and rec["registered_customer_id"] is None
    # Cadastro assistido pela API pública de clientes (permissão do próprio usuário), depois o rascunho.
    c = stack.core.post(f"{C}/{world['a']}/customers", headers=world["ha"], json={
        "name": rec["name"], "document_type": rec["document_type"], "document": rec["document"],
        "municipality_code": rec["municipality_code"], "postal_code": rec["postal_code"],
        "address": {k: v for k, v in rec["address"].items()}})
    assert c.status_code == 201, c.text
    key = uuid.uuid4().hex
    r1 = referencia(stack, world, doc["id"], key)
    assert r1.status_code == 201, r1.text
    d = r1.json()
    # Identidade nova: nada da identidade fiscal da nota original.
    for proibido in ("number", "access_key", "dps_id", "protocol", "signature", "events", "status_code"):
        assert proibido not in d
    assert d["status"] == "DRAFT" and d["version"] == 1 and d["current_item_id"] is None and d["source_document_id"] == doc["id"]
    assert d["customer_id"] == c.json()["id"]
    assert d["service"]["national_code"] == "072101" and d["service"]["location_code"] == MUN
    # cNBS fictício da fixture não existe na tabela NBS vigente: NÃO é copiado e vira pendência específica.
    assert d["service"]["nbs_code"] is None and d["amount"] == "15000.00"
    assert {(p["field"], p["code"]) for p in d["origin"]["pending"]} >= {("service.nbs_code", "REFERENCE_CODE_INVALID")}
    assert d["origin"]["kind"] == "document" and d["origin"]["fields"]["service.national_code"]["origem"] == "xml"
    assert d["service_date"] is None  # dCompet depende da nova prestação: fica pendente
    ts = d["tax_settings"]
    assert ts["iss_retention"] == "NOT_RETAINED" and ts["pis_cofins_cst"] == "01" and ts["issqn_taxation"] == "TAXABLE"
    # Clique repetido / nova tentativa após timeout com a MESMA chave: o MESMO rascunho, sem duplicar.
    r2 = referencia(stack, world, doc["id"], key)
    assert r2.status_code == 201 and r2.json()["id"] == d["id"]
    drafts = stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).json()
    assert sum(1 for x in drafts["items"] if x["source_document_id"] == doc["id"]) == 1
    # Edição e salvamento do rascunho criado; validação aponta as pendências (não inventa dados).
    p = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                           json={"expected_version": 1, "service_date": "2026-10-09",
                                 "service": {"description": "Manutencao predial - outubro (rascunho editado)"}})
    assert p.status_code == 200 and p.json()["version"] == 2, p.text
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"])
    assert v.status_code == 200
    # Nota original intacta.
    depois = stack.fiscal.get(f"{F}/{world['a']}/documents/{doc['id']}", headers=world["ha"]).json()
    for campo in ("status", "number", "access_key", "version", "amount", "events"):
        assert depois[campo] == original[campo], campo
    # Sem Idempotency-Key a operação é recusada (repetição acidental não fica sem proteção).
    sem = stack.fiscal.post(f"{F}/{world['a']}/drafts/from-document", headers=world["ha"],
                            json={"source_document_id": doc["id"]})
    assert sem.status_code in (400, 422) and "dempot" in sem.text


def test_retencoes_campos_opcionais_e_dados_incompletos(stack, world):
    toma = cnpj("667788990001")
    importar(stack, world, nota_como_187(world["a_cnpj"], toma, 201, retencoes=True), "n201.xml")
    importar(stack, world, nota_como_187(world["a_cnpj"], cnpj("778899000001"), 202, sem_endereco=True), "n202.xml")
    stack.core.post(f"{C}/{world['a']}/customers", headers=world["ha"],
                    json={"name": "TOMADOR FICTICIO", "document_type": "CNPJ", "document": toma})
    d = referencia(stack, world, doc_por_numero(stack, world, "201")["id"])
    assert d.status_code == 201, d.text
    ts = d.json()["tax_settings"]
    assert ts["iss_retention"] == "RETAINED_BY_TOMADOR" and ts["pis_cofins_retention"] == "4"
    assert ts["irrf_withheld"] == "225.00" and ts["csll_withheld"] == "150.00"
    # Tomador sem endereço no XML: a tela recebe a lista do que falta; o rascunho nasce com as pendências.
    n202 = doc_por_numero(stack, world, "202")
    rec = stack.fiscal.get(f"{F}/{world['a']}/documents/{n202['id']}/recipient", headers=world["ha"]).json()
    assert {"municipality_code", "postal_code", "address.street", "address.number", "address.district"} <= set(rec["missing"])
    c = stack.core.post(f"{C}/{world['a']}/customers", headers=world["ha"],
                        json={"name": rec["name"], "document_type": "CNPJ", "document": rec["document"]})
    assert c.status_code == 201, c.text
    d2 = referencia(stack, world, n202["id"])
    assert d2.status_code == 201
    v = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d2.json()['id']}/validate", headers=world["ha"]).json()
    assert v["issues"], v  # pendências identificadas (ex.: data da prestação, endereço do tomador)


def test_nota_da_plataforma_e_isolamento_entre_empresas(stack, world):
    # Nota emitida pela plataforma (simulação): tomador já é cliente → rascunho direto.
    r = stack.fiscal.post(f"{F}/{world['a']}/drafts", headers=world["ha"], json={
        "customer_id": world["customer"], "competence": "2026-10", "amount": "321.00",
        "service": {"national_code": "010101", "description": "Serviço emitido pela plataforma"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED"}}).json()
    stack.fiscal.post(f"{F}/{world['a']}/batches", headers={**world["ha"], "Idempotency-Key": uuid.uuid4().hex},
                      json={"drafts": [{"id": r["id"], "expected_version": r["version"]}],
                            "scheduled_at": (stack.clock.now() + timedelta(minutes=1)).isoformat(),
                            "timezone": "America/Sao_Paulo"})
    stack.clock.advance(minutes=2)
    w = stack.worker()
    while w.run_emissions():
        pass
    emitida = next(d for d in stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"],
                                                params={"direction": "ISSUED", "environment": "mock"}).json()["items"]
                   if d["source"] == "EMISSION")
    d = referencia(stack, world, emitida["id"])
    assert d.status_code == 201 and d.json()["customer_id"] == world["customer"], d.text
    # Outra empresa: o id da nota de A não existe na rota de B; e o usuário de B não usa a rota de A.
    rb = stack.fiscal.post(f"{F}/{world['b']}/drafts/from-document", headers={**world["hb"], "Idempotency-Key": "x" * 12},
                           json={"source_document_id": emitida["id"]})
    assert rb.status_code == 404
    assert referencia(stack, world, emitida["id"], h=world["hb"]).status_code == 403
    assert stack.fiscal.get(f"{F}/{world['b']}/documents/{emitida['id']}/recipient", headers=world["hb"]).status_code == 404
    # Leitor (sem 'prepare') não cria rascunho.
    assert referencia(stack, world, emitida["id"], h=world["hr"]).status_code == 403
