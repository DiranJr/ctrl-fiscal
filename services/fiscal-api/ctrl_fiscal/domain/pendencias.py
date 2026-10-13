"""Detalhamento das pendências da validação do rascunho para apresentação (não muda nenhuma regra nem severidade).

Cada pendência devolvida pela validação (campo, código, mensagem, severidade e, quando houver, regra oficial) ganha:
- `section`/`label`: seção e campo do rascunho em português;
- `blocking`: true para erro (impede agendar/emitir); avisos e informações não bloqueiam;
- `reason` + `reason_source`: o TEXTO OFICIAL da regra do Anexo I quando a pendência tem código oficial (cópia sem
  alteração do índice gerado por scripts/gerar_indice_regras_anexo_i.py); sem código oficial, `reason` fica vazio e a
  origem diz que é validação local — nada é inventado nem resumido;
- `official_message`: a mensagem oficial da Sefin para a regra (Anexo I), quando houver;
- `xml_path`: elemento da DPS envolvido (o do Anexo I, quando houver regra; senão o do leiaute para o campo);
- `action` + `target`: onde corrigir — campo do rascunho, cadastro do cliente, perfil de serviço, cadastro da empresa
  ou certificado.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

PREFIXO_XML = "DPS/infDPS/"

# prefixo do campo → (seção, rótulo, caminho relativo a DPS/infDPS ou None, destino)
CAMPOS: list[tuple[str, str, str, str | None, str | None]] = [
    ("customer.municipality_code", "Tomador", "Município do tomador", "toma/end/endNac/cMun", "customer"),
    ("customer.address", "Tomador", "Endereço do tomador", "toma/end", "customer"),
    ("customer_id", "Tomador", "Tomador (cliente)", "toma", "customer"),
    ("customer", "Tomador", "Cadastro do tomador", "toma", "customer"),
    ("competence", "Datas", "Competência", "dCompet", "draft"),
    ("service_date", "Datas", "Data da prestação (dCompet)", "dCompet", "draft"),
    ("amount", "Valores", "Valor do serviço", "valores/vServPrest/vServ", "draft"),
    ("deductions", "Valores", "Deduções/reduções", "valores/vDedRed", "draft"),
    ("unconditional_discount", "Valores", "Desconto incondicionado", "valores/vDescCondIncond/vDescIncond", "draft"),
    ("conditional_discount", "Valores", "Desconto condicionado", "valores/vDescCondIncond/vDescCond", "draft"),
    ("service.description", "Serviço", "Descrição do serviço", "serv/cServ/xDescServ", "draft"),
    ("service.national_code", "Serviço", "Código de tributação nacional (cTribNac)", "serv/cServ/cTribNac", "draft"),
    ("service.municipal_code", "Serviço", "Código de tributação municipal (cTribMun)", "serv/cServ/cTribMun", "draft"),
    ("service.nbs_code", "Serviço", "Código NBS (cNBS)", "serv/cServ/cNBS", "draft"),
    ("service.location_code", "Serviço", "Município da prestação", "serv/locPrest/cLocPrestacao", "draft"),
    ("service.obra_code", "Obra", "Código da obra (CNO/CEI)", "serv/obra/cObra", "draft"),
    ("service.obra_property_registration", "Obra", "Inscrição imobiliária da obra", "serv/obra/inscImobFisc", "draft"),
    ("service.obra", "Obra", "Identificação da obra", "serv/obra", "draft"),
    ("service.additional_info", "Serviço", "Informações complementares", "serv/infoCompl", "draft"),
    ("service", "Serviço", "Serviço", "serv", "draft"),
    ("tax_settings.issqn_taxation", "ISSQN", "Tributação do ISSQN (tribISSQN)", "valores/trib/tribMun/tribISSQN", "draft"),
    ("tax_settings.iss_retention", "ISSQN", "Retenção do ISSQN (tpRetISSQN)", "valores/trib/tribMun/tpRetISSQN", "draft"),
    ("tax_settings.iss_rate", "ISSQN", "Alíquota do ISSQN (pAliq)", "valores/trib/tribMun/pAliq", "draft"),
    ("tax_settings.immunity_type", "ISSQN", "Tipo de imunidade (tpImunidade)", "valores/trib/tribMun/tpImunidade", "draft"),
    ("tax_settings.pis_cofins", "Tributos federais", "PIS/COFINS", "valores/trib/tribFed/piscofins", "draft"),
    ("tax_settings.csll_withheld", "Tributos federais", "CSLL retida", "valores/trib/tribFed/vRetCSLL", "draft"),
    ("tax_settings.irrf_withheld", "Tributos federais", "IRRF retido", "valores/trib/tribFed/vRetIRRF", "draft"),
    ("tax_settings.inss_withheld", "Tributos federais", "Contribuição previdenciária retida", "valores/trib/tribFed/vRetCP",
     "draft"),
    ("tax_settings.approx_tax", "Tributos aproximados", "Total aproximado de tributos (totTrib)", "valores/trib/totTrib",
     "draft"),
    ("tax_settings.ibs_cbs.operation_indicator", "IBS/CBS", "Indicador da operação (cIndOp)", "IBSCBS/cIndOp", "draft"),
    ("tax_settings.ibs_cbs.regular", "IBS/CBS", "Tributação regular (gTribRegular)",
     "IBSCBS/valores/trib/gIBSCBS/gTribRegular", "draft"),
    ("tax_settings.ibs_cbs.cst", "IBS/CBS", "CST do IBS/CBS", "IBSCBS/valores/trib/gIBSCBS/CST", "draft"),
    ("tax_settings.ibs_cbs.class_code", "IBS/CBS", "Classificação tributária (cClassTrib)",
     "IBSCBS/valores/trib/gIBSCBS/cClassTrib", "draft"),
    ("tax_settings.ibs_cbs.deferral", "IBS/CBS", "Diferimento (gDif)", "IBSCBS/valores/trib/gIBSCBS/gDif", "draft"),
    ("tax_settings.ibs_cbs.real_estate", "IBS/CBS", "Imóvel (grupo imovel)", "IBSCBS/imovel", "draft"),
    ("tax_settings.ibs_cbs", "IBS/CBS", "Grupo IBS/CBS", "IBSCBS", "draft"),
    ("tax_settings", "Tributos", "Tributos", "valores/trib", "draft"),
    ("service_profile_id", "Configuração da operação", "Perfil de serviço / configuração tributária", None,
     "service_profile"),
    ("company", "Empresa (prestador)", "Cadastro da empresa", "prest", "company"),
    ("environment", "Ambiente de emissão", "Ambiente", "tpAmb", None),
]
ACOES = {
    "draft": "Corrija “{label}” no editor do rascunho e valide de novo.",
    "customer": "Atualize o cadastro do tomador em Clientes e valide o rascunho de novo.",
    "service_profile": "Revise o perfil de serviço em Configurações → Perfis de serviço (ou na aba “Configuração da "
                       "operação” do rascunho) e valide de novo.",
    "company": "Ajuste o cadastro da empresa em Configurações e valide o rascunho de novo.",
    "certificate": "Configure o certificado digital A1 da empresa em Configurações.",
}


@lru_cache(maxsize=1)
def regras() -> dict:
    import ctrl_common

    p = Path(ctrl_common.__file__).parent / "dominios" / "regras_anexo_i.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _campo(field: str) -> tuple[str, str, str | None, str | None]:
    for prefixo, secao, rotulo, xml, destino in CAMPOS:
        if field == prefixo or field.startswith(prefixo + ".") or field.startswith(prefixo + "["):
            return secao, rotulo, xml, destino
    return "Rascunho", field, None, "draft"


def detalhar(issue: dict, draft=None) -> dict:  # noqa: ANN001
    """Acrescenta os detalhes de apresentação a UMA pendência (função pura; reaplicar dá o mesmo resultado)."""
    field, code = issue.get("field") or "", issue.get("code") or ""
    secao, rotulo, xml, destino = _campo(field)
    if "CERTIFICATE" in code:
        secao, rotulo, destino = "Certificado digital", "Certificado A1 da empresa", "certificate"
    out = {**issue, "section": secao, "label": rotulo, "blocking": issue.get("severity") == "error"}
    regra = issue.get("rule")
    oficial = regras()["regras"].get(regra) if regra and re.fullmatch(r"E\d{4}", regra) else None
    if oficial:
        out.update(reason=oficial["regra"], official_message=oficial["mensagem"],
                   reason_source=f"Anexo I (RN DPS/NFS-e v1.01) — regra {regra} ({oficial['efeito']})",
                   xml_path=oficial["elemento"].removeprefix("NFSe/infNFSe/"))
    else:
        out.update(reason=None, official_message=None,
                   reason_source=("Nota de referência (XML oficial)" if regra else
                                  "Validação local do CTRL Fiscal, antes da transmissão (sem código oficial)"),
                   xml_path=(PREFIXO_XML + xml) if xml else None)
    alvo = None
    if destino == "draft":
        alvo = {"kind": "draft_field", "field": field}
    elif destino == "customer":
        alvo = {"kind": "customer", "customer_id": getattr(draft, "customer_id", None)}
    elif destino == "service_profile":
        alvo = {"kind": "service_profile", "profile_id": getattr(draft, "service_profile_id", None)}
    elif destino in ("company", "certificate"):
        alvo = {"kind": destino}
    out["target"] = alvo
    out["action"] = (ACOES[destino].format(label=rotulo) if destino and issue.get("severity") != "info" else None)
    return out


def detalhar_todas(issues: list[dict] | None, draft=None) -> list[dict]:  # noqa: ANN001
    """Bloqueios primeiro, depois avisos e informações; a ordem original é mantida dentro de cada grupo."""
    peso = {"error": 0, "warning": 1, "info": 2}
    lista = [detalhar(i, draft) for i in (issues or [])]
    return sorted(lista, key=lambda i: peso.get(i.get("severity"), 3))
