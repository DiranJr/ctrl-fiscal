"""Perfis de serviço do workspace: revisão, compatibilidade, versões e preparação a partir das notas emitidas.

Princípios:
- Um perfil preparado a partir das notas fica PENDENTE DE REVISÃO; nada é aprovado automaticamente e um perfil
  existente nunca é sobrescrito pela preparação (idempotência por origin_key = cTribNac:cNBS).
- Cada campo guarda a ORIGEM: "xml" (lido de forma consistente das notas oficiais emitidas), "xml_historico"
  (classificação presente em notas anteriores — evidência histórica, não garante o enquadramento de nova operação),
  "tabela" (sugestão a partir de tabela oficial, com fonte) e "usuario" (preenchido/confirmado por pessoa).
- Pendências dizem EXATAMENTE o que falta e por quê (regra oficial), nunca "aguardando contabilidade".
- Toda alteração gera nova versão com o conteúdo completo (service_profile_versions) e auditoria.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from ctrl_common import dominios

from .models import Company, ServiceProfile, ServiceProfileVersion

CAMPOS = ("name", "national_code", "municipal_code", "nbs_code", "description", "issqn_taxation", "pis_cofins_cst",
          "ibs_cbs", "source", "valid_from", "valid_to")
SIMPLES = ("2", "3")
IBSCBS_OBRIGATORIO_DESDE = date(2026, 10, 1)  # Ato Conjunto RFB/CGIBS 4/2026 (não optantes)
OBRA_SUBITENS_IMOVEL_PROIBIDO = ("070201", "070202", "070401", "070501", "070502", "070601", "070602", "070701",
                                 "070801", "071701", "071901")  # Anexo I, E0931
CINDOP_IMOVEL = ("020101", "020201", "020301")
ISSQN = {"TAXABLE": "1 — Operação tributável", "IMMUNE": "2 — Imunidade", "EXPORT": "3 — Exportação de serviço",
         "NOT_INCIDENT": "4 — Não incidência"}
ROTULOS = {"name": "Nome do perfil", "national_code": "Código de tributação nacional (cTribNac)",
           "municipal_code": "Código de tributação municipal (cTribMun)", "nbs_code": "Código NBS (cNBS)",
           "description": "Descrição padrão do serviço", "issqn_taxation": "Tributação do ISSQN (tribISSQN)",
           "pis_cofins_cst": "CST do PIS/COFINS", "ibs_cbs": "Classificação IBS/CBS",
           "ibs_cbs.operation_indicator": "Indicador da operação (cIndOp)",
           "ibs_cbs.class_code": "Classificação tributária (cClassTrib)", "ibs_cbs.cst": "CST do IBS/CBS",
           "ibs_cbs.regular": "Tributação regular (gTribRegular)", "valid_from": "Início da vigência",
           "proposta": "Proposta de revisão"}
# Proposta de revisão (em `suggestions`): análise das notas e das tabelas com alternativas CONDICIONAIS. Não altera
# nenhum campo do perfil; enquanto ABERTA bloqueia a aprovação. Só uma pessoa autorizada a resolve: APLICADA (os
# campos da alternativa escolhida passam ao perfil, que continua pendente de revisão) ou DESCARTADA.
PROPOSTA = "proposta_revisao"
PROPOSTA_STATUS = ("ABERTA", "APLICADA", "DESCARTADA")


def propostas(p: ServiceProfile, status: str | None = None) -> list[dict]:
    return [x for x in (p.suggestions or []) if isinstance(x, dict) and x.get("tipo") == PROPOSTA
            and (status is None or x.get("status") == status)]


@lru_cache(maxsize=1)
def _classes() -> dict:
    import ctrl_common

    data = json.loads((Path(ctrl_common.__file__).parent / "dominios" / "cclasstrib_nfse.json").read_text(encoding="utf-8"))
    return {"meta": data["meta"], "por_codigo": {c["codigo"]: c for c in data["classificacoes"]}}


def classificacao(codigo: str | None, em: date | None = None) -> dict | None:
    c = _classes()["por_codigo"].get(codigo or "")
    if c is None or em is None:
        return c
    ini = date.fromisoformat(c["inicio_vigencia"]) if c.get("inicio_vigencia") else None
    fim = date.fromisoformat(c["fim_vigencia"]) if c.get("fim_vigencia") else None
    return c if (ini is None or ini <= em) and (fim is None or em <= fim) else None


def descricoes(p: ServiceProfile) -> dict:
    """Descrições compreensíveis dos códigos, com a tabela oficial de origem."""
    ibs = p.ibs_cbs or {}
    c = classificacao(ibs.get("class_code"))
    reg = classificacao((ibs.get("regular") or {}).get("class_code"))
    info = dominios.cindop_info(ibs.get("operation_indicator"))
    inc = dominios.incidencia(p.national_code)
    return {
        "national_code": {"texto": dominios.servico_nacional(p.national_code), "fonte": "Anexo B — Lista de serviços nacional"},
        "nbs_code": {"texto": dominios.nbs(p.nbs_code), "fonte": "Anexo B — NBS 2.0"},
        "incidencia": {"codigo": inc, "texto": {"EP": "Estabelecimento do prestador", "LP": "Local da prestação",
                                                 "ET": "Estabelecimento do tomador"}.get(inc or ""),
                       "fonte": "Anexo I — MUN.INCID_INFO.SERV."},
        "grupo_obrigatorio": {"codigo": dominios.grupo_obrigatorio(p.national_code), "fonte": "Anexo I (E0370/E0372)"},
        "issqn_taxation": {"texto": ISSQN.get(p.issqn_taxation or "")},
        "operation_indicator": ({"texto": info["tipo"], "local_operacao": info["local_operacao"],
                                 "local_dfe": info["local_dfe"], "artigo": info["artigo"],
                                 "fonte": "Anexo C — cIndOp (art. 11 da LC 214/2025)"} if info else None),
        "class_code": ({"texto": c["nome"], "cst": c["cst"], "cst_nome": c["cst_nome"],
                        "reducao_ibs": c["perc_red_ibs"], "reducao_cbs": c["perc_red_cbs"],
                        "exige_tributacao_regular": c["exige_tributacao_regular"],
                        "permite_diferimento": c["cst_diferimento"], "legislacao": c.get("legislacao"),
                        "fonte": "Tabela cClassTrib para NFS-e"} if c else None),
        "regular_class_code": {"texto": reg["nome"]} if reg else None,
    }


def _issue(field: str, code: str, message: str, severity: str = "error") -> dict:
    return {"field": field, "label": ROTULOS.get(field, field), "code": code, "message": message, "severity": severity}


def pendencias(p: ServiceProfile, company: Company | None, hoje: date) -> list[dict]:
    """O que impede a aprovação (error), o que precisa de atenção (warning) e o que cada nota exigirá (info)."""
    out: list[dict] = []
    nao_optante = company is None or company.simples_option not in SIMPLES
    if not p.national_code:
        out.append(_issue("national_code", "REQUIRED", "Informe o código de tributação nacional (6 dígitos)."))
    elif not dominios.servico_nacional(p.national_code):
        out.append(_issue("national_code", "E0310", "cTribNac inexistente na lista nacional (Anexo B)."))
    if not p.issqn_taxation:
        out.append(_issue("issqn_taxation", "REQUIRED", "Defina a tributação do ISSQN (tribISSQN): tributável, "
                                                        "imunidade, exportação ou não incidência."))
    ibs = p.ibs_cbs or {}
    if p.nbs_code and not dominios.nbs(p.nbs_code):
        out.append(_issue("nbs_code", "E0316", "cNBS inexistente na tabela NBS (Anexo B)."))
    elif not p.nbs_code and (ibs or nao_optante):
        out.append(_issue("nbs_code", "E0322", "Informe o código NBS: obrigatório quando o grupo IBS/CBS é informado "
                                               "(regra E0322)."))
    if not ibs:
        if nao_optante:
            out.append(_issue("ibs_cbs", "IBS_CBS_REQUIRED",
                              "Defina cIndOp (Anexo C) e cClassTrib (tabela oficial): o grupo IBS/CBS é obrigatório "
                              f"para fatos geradores desde {IBSCBS_OBRIGATORIO_DESDE:%d/%m/%Y} para empresas não "
                              "optantes pelo Simples Nacional (Ato Conjunto RFB/CGIBS 4/2026)."))
    else:
        op_ = ibs.get("operation_indicator")
        if not op_:
            out.append(_issue("ibs_cbs.operation_indicator", "REQUIRED", "Informe o indicador da operação (cIndOp)."))
        elif not dominios.cindop_valido(op_):
            out.append(_issue("ibs_cbs.operation_indicator", "E0901", "cIndOp inexistente no Anexo C."))
        c = classificacao(ibs.get("class_code"), hoje)
        if not ibs.get("class_code"):
            out.append(_issue("ibs_cbs.class_code", "REQUIRED", "Informe a classificação tributária (cClassTrib)."))
        elif c is None:
            out.append(_issue("ibs_cbs.class_code", "E0958", "cClassTrib inexistente na tabela oficial para NFS-e ou "
                                                             "fora de vigência hoje."))
        else:
            if ibs.get("cst") != c["cst"]:
                out.append(_issue("ibs_cbs.cst", "E0959", f"O CST deve ser {c['cst']} (3 primeiros dígitos do "
                                                          "cClassTrib)."))
            reg = ibs.get("regular") or None
            if c["exige_tributacao_regular"] and not reg:
                out.append(_issue("ibs_cbs.regular", "E0965", "Esta classificação exige o grupo de tributação regular: "
                                                              "informe CSTReg e cClassTribReg."))
            elif reg and not c["exige_tributacao_regular"]:
                out.append(_issue("ibs_cbs.regular", "E0964", "Remova a tributação regular: esta classificação não a "
                                                              "admite."))
            elif reg:
                cr = classificacao(reg.get("class_code"), hoje)
                if cr is None:
                    out.append(_issue("ibs_cbs.regular", "E0969", "cClassTribReg inexistente na tabela oficial."))
                elif reg.get("cst") != cr["cst"]:
                    out.append(_issue("ibs_cbs.regular", "E0970", f"CSTReg deve ser {cr['cst']}."))
            if c["cst_diferimento"]:
                out.append(_issue("ibs_cbs.class_code", "E0972", "Cada nota exigirá os percentuais de diferimento "
                                                                 "(pDifUF, pDifMun, pDifCBS).", "info"))
            if not (c["cst"] == "000" and float(c["perc_red_ibs"]) == 0 and float(c["perc_red_cbs"]) == 0):
                out.append(_issue("ibs_cbs.class_code", "SPECIAL_TREATMENT",
                                  f"Classificação com redução ou regime específico ({c['nome']}): confirme o "
                                  "enquadramento.", "warning"))
        if op_ in CINDOP_IMOVEL and p.national_code in OBRA_SUBITENS_IMOVEL_PROIBIDO:
            out.append(_issue("ibs_cbs.operation_indicator", "OBRA_SEM_IMOVEL",
                              "Subitem de obra: o grupo 'imóvel' do IBS/CBS não é permitido (E0931); a nota usa o "
                              "grupo 'obra' do serviço.", "info"))
        elif op_ in CINDOP_IMOVEL:
            out.append(_issue("ibs_cbs.operation_indicator", "E0932", "Cada nota exigirá o grupo 'imóvel' (cCIB ou "
                                                                      "endereço do imóvel).", "info"))
        info = dominios.cindop_info(op_)
        if info and "destinatário" in (info.get("local_dfe") or "").lower():
            out.append(_issue("ibs_cbs.operation_indicator", "DEST_REQUIRED", "Este cIndOp localiza a operação no "
                                                                              "endereço do destinatário: a nota exigirá "
                                                                              "o grupo 'destinatário'.", "info"))
    if dominios.grupo_obrigatorio(p.national_code) == "obra":
        out.append(_issue("national_code", "E0370", "Cada nota exigirá o grupo 'obra' (CNO/CEI, CIB ou endereço da "
                                                    "obra).", "info"))
    for campo, src in (p.field_sources or {}).items():
        if isinstance(src, dict) and src.get("origem") in ("xml_historico", "tabela") and not src.get("confirmado_por"):
            out.append(_issue(campo, "CONFIRMAR",
                              "Valor de nota anterior (evidência histórica): confirme se vale para novas operações."
                              if src["origem"] == "xml_historico" else
                              "Sugestão baseada em tabela oficial: confirme.", "warning"))
    for prop in propostas(p, "ABERTA"):
        perguntas = ", ".join(q["id"] for q in prop.get("perguntas") or [])
        out.append(_issue("proposta", "PROPOSTA_EM_ABERTO",
                          f"Proposta de revisão {prop['id']} em aberto"
                          + (f" (perguntas {perguntas})" if perguntas else "")
                          + ": aplique uma alternativa confirmada ou descarte a proposta antes de aprovar."))
    if p.valid_from and p.valid_to and p.valid_to < p.valid_from:
        out.append(_issue("valid_to", "VIGENCIA", "Fim da vigência anterior ao início."))
    return out


def bloqueios(p: ServiceProfile, company: Company | None, hoje: date) -> list[dict]:
    return [i for i in pendencias(p, company, hoje) if i["severity"] == "error"]


def snapshot(p: ServiceProfile) -> dict:
    out = {k: getattr(p, k) for k in CAMPOS}
    for k in ("valid_from", "valid_to"):
        out[k] = out[k].isoformat() if out[k] else None
    out.update(status=p.status, is_active=p.is_active, field_sources=p.field_sources, origin_key=p.origin_key,
               approved_by=p.approved_by, approved_at=p.approved_at.isoformat() if p.approved_at else None)
    return out


def registrar_versao(s: Session, p: ServiceProfile, action: str, now: datetime, actor_id: str | None,
                     actor_type: str = "user") -> None:
    s.add(ServiceProfileVersion(profile_id=p.id, company_id=p.company_id, version=p.version, action=action,
                                snapshot=snapshot(p), actor_type=actor_type, actor_id=actor_id, created_at=now))


def fontes_usuario(campos: list[str], user_id: str, now: datetime, base: dict | None = None) -> dict:
    out = dict(base or {})
    for k in campos:
        out[k] = {"origem": "usuario", "confirmado_por": user_id, "em": now.isoformat()}
    return out
