"""Propostas de revisão de perfis de serviço: registradas pelo sistema SEM alterar campos do perfil, bloqueiam a
aprovação enquanto abertas, são idempotentes, conferem os códigos nas tabelas oficiais e só uma pessoa autorizada as
aplica (o perfil continua pendente de revisão) ou descarta. Somente dados fictícios."""
from __future__ import annotations

from sqlalchemy import select

from conftest import KEYS
from test_perfis_servico import _carregar

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
INTERNO = {"X-Service-Name": "fiscal-api", "Authorization": "Bearer " + KEYS["fiscal_api_core"]}
ORIGEM = "071601:101032000"


def _proposta(pid: str = "PR-TESTE-1", **kw) -> dict:
    return {
        "id": pid, "resumo": "Serviço sobre imóvel; classificação depende do contrato.",
        "alternativas": [{
            "id": "A", "rotulo": "Serviço sobre bem imóvel com redução",
            "condicao": "Somente se a contabilidade confirmar o enquadramento.",
            "campos": {"ibs_cbs": {"operation_indicator": "020201", "cst": "200", "class_code": "200046"},
                       "pis_cofins_cst": "01"},
            "fundamento": ["LC 214/2025, art. 11, II", "LC 214/2025, arts. 252, V, e 261"],
        }, {
            "id": "B", "rotulo": "Somente NBS", "condicao": "Se o NBS correto for o de silvicultura.",
            "campos": {"nbs_code": "119013000"}, "fundamento": ["NBS 2.0"],
        }],
        "perguntas": [{"id": "Q1", "texto": "O contrato é de construção civil?", "responsavel": "Contabilidade"}],
        "proxima_acao": "Enviar Q1 à contabilidade.",
        "analise": {"servico": "Supressão vegetal (fictício)", "por_emissao": ["Endereço da obra", "Valores"]},
        **kw,
    }


def _perfil(stack, world) -> dict:  # noqa: ANN001
    _carregar(stack, world)
    stack.fiscal.post(f"{F}/{world['a']}/service-profiles/prepare", headers=world["ha"])
    return {p["origin_key"]: p for p in stack.core.get(f"{C}/{world['a']}/service-profiles",
                                                        headers=world["ha"]).json()}[ORIGEM]


def _registrar(stack, world, *props: dict, headers: dict | None = None):  # noqa: ANN001, ANN202
    return stack.core.post(f"/internal/v1/companies/{world['a']}/service-profiles/proposals",
                           headers=INTERNO if headers is None else headers,
                           json={"actor_type": "system", "actor_id": "analise-notas",
                                 "items": [{"origin_key": ORIGEM, "proposal": p} for p in props]})


def test_registro_nao_altera_campos_e_bloqueia_aprovacao(stack, world):
    antes = _perfil(stack, world)
    r = _registrar(stack, world, _proposta())
    assert r.status_code == 200, r.text
    assert r.json()["registered"] == [{"origin_key": ORIGEM, "proposal": "PR-TESTE-1", "version": 2}]
    url = f"{C}/{world['a']}/service-profiles/{antes['id']}"
    p = stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()
    p = next(x for x in p if x["id"] == antes["id"])
    for k in ("national_code", "nbs_code", "issqn_taxation", "pis_cofins_cst", "ibs_cbs", "field_sources", "status"):
        assert p[k] == antes[k], k
    assert p["version"] == 2 and not p["can_approve"]
    assert any(i["code"] == "PROPOSTA_EM_ABERTO" and "Q1" in i["message"] for i in p["pending"])
    prop = next(x for x in p["suggestions"] if x["tipo"] == "proposta_revisao")
    assert prop["status"] == "ABERTA" and prop["registrada_por"] == "system:analise-notas"
    r = stack.core.post(f"{url}/approve", headers=world["ha"], json={"expected_version": 2})
    assert r.status_code == 409 and "PROPOSTA_EM_ABERTO" in {e["code"] for e in r.json()["errors"]}
    # Idempotente: a mesma proposta não é registrada de novo nem substituída.
    r = _registrar(stack, world, _proposta(resumo="Outro texto"))
    assert r.json()["existing"] == [{"origin_key": ORIGEM, "proposal": "PR-TESTE-1"}] and not r.json()["registered"]
    hist = stack.core.get(f"{url}/versions", headers=world["ha"]).json()
    assert [h["action"] for h in hist] == ["proposal_registered", "prepared"]
    assert hist[0]["actor_type"] == "system"
    from core_api.models import AuditEvent

    with stack.core_sessions() as s:
        ev = s.scalar(select(AuditEvent).where(AuditEvent.target_id == antes["id"],
                                               AuditEvent.action == "service_profile.proposal_registered"))
    assert ev.details["alternatives"] == ["A", "B"] and ev.details["questions"] == ["Q1"]


def test_registro_exige_credencial_e_codigos_oficiais(stack, world):
    antes = _perfil(stack, world)
    assert _registrar(stack, world, _proposta(), headers={}).status_code == 401
    worker = {"X-Service-Name": "fiscal-worker", "Authorization": "Bearer " + KEYS["worker_core"]}
    assert _registrar(stack, world, _proposta(), headers=worker).status_code == 403
    ruim = _proposta()
    ruim["alternativas"][0]["campos"]["ibs_cbs"] = {"operation_indicator": "020201", "cst": "200",
                                                    "class_code": "999999"}
    r = _registrar(stack, world, ruim)
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "E0958"
    ruim["alternativas"][0]["campos"]["ibs_cbs"] = {"operation_indicator": "020201", "cst": "000",
                                                    "class_code": "200046"}
    assert _registrar(stack, world, ruim).status_code == 422  # CST incompatível com o cClassTrib (E0959)
    p = stack.core.get(f"{C}/{world['a']}/service-profiles", headers=world["ha"]).json()
    assert next(x for x in p if x["id"] == antes["id"])["version"] == 1  # nada foi gravado


def test_aplicar_alternativa_mantem_pendente_e_nao_apaga_campos(stack, world):
    antes = _perfil(stack, world)
    _registrar(stack, world, _proposta(), _proposta("PR-TESTE-2"))
    url = f"{C}/{world['a']}/service-profiles/{antes['id']}/proposals"
    # Leitor não resolve; outro workspace não enxerga o perfil.
    assert stack.core.post(f"{url}/PR-TESTE-1/resolve", headers=world["hr"],
                           json={"expected_version": 3, "decision": "DISCARD", "note": "teste"}).status_code in (403, 404)
    r = stack.core.post(f"/api/core/v1/companies/{world['b']}/service-profiles/{antes['id']}/proposals/PR-TESTE-1/resolve",
                        headers=world["hb"], json={"expected_version": 3, "decision": "DISCARD", "note": "teste"})
    assert r.status_code == 404
    r = stack.core.post(f"{url}/PR-TESTE-1/resolve", headers=world["ha"],
                        json={"expected_version": 3, "decision": "APPLY", "note": "Contrato conferido"})
    assert r.status_code == 422 and r.json()["code"] == "PROPOSAL_ALTERNATIVE_REQUIRED"
    r = stack.core.post(f"{url}/PR-TESTE-1/resolve", headers=world["ha"],
                        json={"expected_version": 2, "decision": "APPLY", "alternative": "A", "note": "x" * 6})
    assert r.status_code == 409 and r.json()["code"] == "VERSION_CONFLICT"
    r = stack.core.post(f"{url}/PR-TESTE-1/resolve", headers=world["ha"],
                        json={"expected_version": 3, "decision": "APPLY", "alternative": "A",
                              "note": "Contabilidade confirmou por e-mail"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "PENDING_REVIEW" and p["approved_by"] is None and p["version"] == 4
    assert p["ibs_cbs"] == {"operation_indicator": "020201", "cst": "200", "class_code": "200046"}
    assert p["field_sources"]["ibs_cbs"]["origem"] == "usuario"
    assert p["field_sources"]["ibs_cbs"]["confirmado_por"] == world["ua"]
    assert p["field_sources"]["ibs_cbs"]["proposta"] == "PR-TESTE-1/A"
    assert p["national_code"] == antes["national_code"] and p["nbs_code"] == antes["nbs_code"]
    feita = next(x for x in p["suggestions"] if x.get("id") == "PR-TESTE-1")
    assert feita["status"] == "APLICADA" and feita["alternativa_aplicada"] == "A" and feita["resolvida_por"] == world["ua"]
    # A outra proposta continua aberta e bloqueando.
    assert [i for i in p["pending"] if i["code"] == "PROPOSTA_EM_ABERTO" and "PR-TESTE-2" in i["message"]]
    assert stack.core.post(f"{url}/PR-TESTE-1/resolve", headers=world["ha"],
                           json={"expected_version": 4, "decision": "DISCARD", "note": "de novo"}).status_code == 409
    # Alternativa só de NBS não apaga a classificação já aplicada.
    r = stack.core.post(f"{url}/PR-TESTE-2/resolve", headers=world["ha"],
                        json={"expected_version": 4, "decision": "APPLY", "alternative": "B", "note": "NBS conferido"})
    p = r.json()
    assert p["nbs_code"] == "119013000" and p["ibs_cbs"]["class_code"] == "200046"
    assert not [i for i in p["pending"] if i["code"] == "PROPOSTA_EM_ABERTO"]
    assert p["status"] == "PENDING_REVIEW"  # aprovar continua sendo um ato separado
    hist = stack.core.get(f"{C}/{world['a']}/service-profiles/{antes['id']}/versions", headers=world["ha"]).json()
    assert [h["action"] for h in hist][:4] == ["proposal_applied", "proposal_applied", "proposal_registered",
                                               "proposal_registered"]


def test_descartar_nao_altera_campos_e_perfil_aprovado_nao_recebe_proposta(stack, world):
    antes = _perfil(stack, world)
    _registrar(stack, world, _proposta())
    url = f"{C}/{world['a']}/service-profiles/{antes['id']}"
    r = stack.core.post(f"{url}/proposals/PR-TESTE-1/resolve", headers=world["ha"],
                        json={"expected_version": 2, "decision": "DISCARD", "note": "Contrato diferente"})
    p = r.json()
    assert r.status_code == 200 and p["ibs_cbs"] == antes["ibs_cbs"] and p["field_sources"] == antes["field_sources"]
    assert next(x for x in p["suggestions"] if x.get("id") == "PR-TESTE-1")["status"] == "DESCARTADA"
    # Aprovado (com classificação definida pelo usuário): nova proposta não é registrada.
    stack.core.patch(url, headers=world["ha"], json={"expected_version": 3, "ibs_cbs": {
        "operation_indicator": "100301", "cst": "000", "class_code": "000001"}})
    assert stack.core.post(f"{url}/approve", headers=world["ha"], json={"expected_version": 4}).status_code == 200
    r = _registrar(stack, world, _proposta("PR-TESTE-3"))
    assert r.json()["skipped"] == [{"origin_key": ORIGEM, "reason": "APPROVED"}]
