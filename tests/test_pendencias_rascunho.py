"""Pendências da validação do rascunho com detalhamento (seção, campo, bloqueio/aviso, motivo oficial do Anexo I,
caminho XML, ação e destino), sem alterar regra, severidade nem bloqueio fiscal. Somente dados fictícios."""
from __future__ import annotations

import json
from pathlib import Path

from test_emission_queue import make_draft

F = "/api/fiscal/v1/companies"
ROOT = Path(__file__).resolve().parents[1]


def _com_pendencias(stack, world) -> dict:  # noqa: ANN001
    d = make_draft(stack, world)
    r = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"], json={
        "expected_version": d["version"], "competence": "2099-12",
        "service": {"nbs_code": "999999999", "location_code": "9999999", "description": "Descrição " + "longa " * 300}})
    assert r.status_code == 200, r.text
    return r.json()


def test_validacao_devolve_todas_as_pendencias_detalhadas(stack, world):
    d = _com_pendencias(stack, world)
    r = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"])
    assert r.status_code == 200
    j = r.json()
    issues = j["issues"]
    assert j["validated_version"] == d["version"] and len(issues) >= 3
    assert all(i["section"] and i["label"] and i["reason_source"] for i in issues)
    # Bloqueios primeiro; blocking = severidade "error" (nada é rebaixado nem promovido).
    sev = [i["severity"] for i in issues]
    assert sev == sorted(sev, key={"error": 0, "warning": 1, "info": 2}.get)
    assert all(i["blocking"] == (i["severity"] == "error") for i in issues)
    assert j["valid"] is (not any(i["blocking"] for i in issues))
    nbs = next(i for i in issues if i["code"] == "NBS_NOT_IN_LIST")
    oficial = json.loads((ROOT / "docs/oficial/regras-dps-anexo-i.json").read_text(encoding="utf-8"))
    e0316 = next(x for x in oficial["regras"] if x["codigo"] == "E0316")
    assert nbs["rule"] == "E0316" and nbs["reason"] == e0316["regra"]  # texto oficial, sem alteração
    assert nbs["official_message"] == e0316["mensagem"] and "regra E0316" in nbs["reason_source"]
    assert nbs["xml_path"] == e0316["elemento"].removeprefix("NFSe/infNFSe/")
    assert nbs["section"] == "Serviço" and nbs["target"] == {"kind": "draft_field", "field": "service.nbs_code"}
    assert "Código NBS" in nbs["action"]
    futura = next(i for i in issues if i["code"] == "COMPETENCE_IN_FUTURE")
    assert futura["reason"] is None and "Validação local" in futura["reason_source"]  # sem código oficial: nada inventado
    assert futura["xml_path"] == "DPS/infDPS/dCompet" and futura["section"] == "Datas"
    assert futura["severity"] == "warning" and futura["blocking"] is False
    info = [i for i in issues if i["severity"] == "info"]
    assert all(i["action"] is None for i in info)


def test_detalhamento_nao_muda_o_resultado_da_validacao(stack, world):
    """Mesmas pendências (código, campo, severidade, mensagem) que a validação de domínio produz."""
    from ctrl_fiscal.domain import drafts as drafts_d
    from ctrl_fiscal.models import Draft

    d = _com_pendencias(stack, world)
    api = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()["issues"]
    with stack.fiscal_sessions() as s:
        bruto = s.get(Draft, d["id"]).issues
    chave = lambda i: (i["code"], i["field"], i["severity"], i["message"])  # noqa: E731
    assert sorted(map(chave, api)) == sorted(map(chave, bruto))
    assert drafts_d.pendencias.detalhar_todas(bruto) == drafts_d.pendencias.detalhar_todas(
        drafts_d.pendencias.detalhar_todas(bruto))  # idempotente


def test_lista_e_editor_recebem_as_mesmas_pendencias_e_edicao_invalida(stack, world):
    d = _com_pendencias(stack, world)
    val = stack.fiscal.post(f"{F}/{world['a']}/drafts/{d['id']}/validate", headers=world["ha"]).json()
    lista = stack.fiscal.get(f"{F}/{world['a']}/drafts", headers=world["ha"]).json()["items"]
    item = next(x for x in lista if x["id"] == d["id"])
    assert item["issues"] == val["issues"] and item["validation"]["validated_version"] == d["version"]
    um = stack.fiscal.get(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"]).json()
    assert um["issues"] == val["issues"]
    # Editar descarta as pendências antigas: a tela mostra a versão validada e pede nova validação.
    e = stack.fiscal.patch(f"{F}/{world['a']}/drafts/{d['id']}", headers=world["ha"],
                           json={"expected_version": d["version"], "amount": "12.34"}).json()
    assert e["issues"] == [] and e["validation"]["status"] == "NOT_VALIDATED"
    assert e["validation"]["validated_version"] == d["version"] and e["version"] == d["version"] + 1


def test_destinos_de_cliente_perfil_empresa_e_certificado():
    from ctrl_fiscal.domain.pendencias import detalhar

    class R:
        customer_id, service_profile_id = "c1", "p1"

    def alvo(field, code="X"):  # noqa: ANN001, ANN202
        return detalhar({"field": field, "code": code, "message": "m", "severity": "error"}, R())["target"]

    assert alvo("customer_id") == {"kind": "customer", "customer_id": "c1"}
    assert alvo("customer.municipality_code") == {"kind": "customer", "customer_id": "c1"}
    assert alvo("service_profile_id") == {"kind": "service_profile", "profile_id": "p1"}
    assert alvo("company.special_regime") == {"kind": "company"}
    assert alvo("environment", "CERTIFICATE_NOT_CONFIGURED") == {"kind": "certificate"}
    assert alvo("tax_settings.ibs_cbs.class_code") == {"kind": "draft_field", "field": "tax_settings.ibs_cbs.class_code"}


def test_indice_de_regras_e_copia_fiel_da_fonte_oficial():
    fonte = json.loads((ROOT / "docs/oficial/regras-dps-anexo-i.json").read_text(encoding="utf-8"))
    idx = json.loads((ROOT / "services/shared/ctrl_common/dominios/regras_anexo_i.json").read_text(encoding="utf-8"))
    assert idx["fonte_sha256"] == fonte["fonte_sha256"] and len(idx["regras"]) == len(fonte["regras"])
    for r in fonte["regras"]:
        i = idx["regras"][r["codigo"]]
        assert (i["elemento"], i["regra"], i["mensagem"], i["efeito"]) == (r["elemento"], r["regra"], r["mensagem"], r["efeito"])
