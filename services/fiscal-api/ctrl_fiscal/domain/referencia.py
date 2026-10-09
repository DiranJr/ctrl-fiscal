"""Nota EMITIDA pelo workspace como referência para uma NOVA emissão, lida do XML oficial guardado.

- A referência precisa ser uma NFS-e emitida pela própria empresa: direção ISSUED, papel PRESTADOR, prestador no XML
  (DPS/infDPS/prest/CNPJ) igual ao CNPJ do workspace e tpEmit=1 (emitida pelo prestador). Nota recebida ou emitida
  por terceiro nunca fornece identidade nem enquadramento do prestador.
- O rascunho é NOVO: número da DPS/NFS-e, série, Id, chave, protocolo, assinatura, datas de emissão/processamento,
  eventos, situação, substituição e informações complementares próprias daquela nota (pedido, ART, docRef) NÃO são
  copiados; dCompet também não (depende da nova prestação).
- São preservadas as particularidades da operação: serviço (cTribNac, cTribMun, cNBS, descrição), local da
  prestação, obra, valores, descontos, deduções, ISSQN (tributação, retenção, alíquota, imunidade), PIS/COFINS,
  retenções federais, tributos aproximados e o grupo IBS/CBS.
- Nada é completado por maioria de notas: o que faltar, for inválido no leiaute/tabela vigente ou não puder ser
  reproduzido vira pendência específica, com a tag oficial e o motivo.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from ctrl_common import dominios

from .. import nfse_xml as nx

TRIB = {"1": "TAXABLE", "2": "IMMUNE", "3": "EXPORT", "4": "NOT_INCIDENT"}
RET_ISS = {"1": "NOT_RETAINED", "2": "RETAINED_BY_TOMADOR", "3": "RETAINED_BY_INTERMEDIARY"}
NAO_COPIADOS = (
    ("nNFSe / nDPS / serie / Id / chave", "Identidade fiscal da nota de origem: a nova emissão recebe número, série, Id "
                                          "e chave próprios."),
    ("protocolo / assinatura / cStat / dhProc", "Resultado do processamento da nota de origem (Sefin)."),
    ("dhEmi / dCompet", "Datas da nota de origem: a nova prestação define a sua (competência revisável)."),
    ("situação / eventos / substituição", "Estado e histórico da nota de origem não passam para o novo rascunho."),
    ("serv/infoCompl", "Pedido, ART/RRT, docRef e observações são próprios de cada nota."),
)


class ReferenciaInvalida(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _dec2(v: str | None) -> str | None:
    if v is None:
        return None
    try:
        return f"{Decimal(v).quantize(Decimal('0.01'))}"
    except InvalidOperation:
        return None


def _pend(field: str, code: str, message: str, severity: str = "warning", *, enquanto_vazio: bool = True,
          tag: str | None = None) -> dict:
    return {"field": field, "code": code, "message": message, "severity": severity, "while_empty": enquanto_vazio,
            "tag": tag}


def ler_referencia(xml: bytes, company_cnpj: str, competencia_nova: str) -> dict:
    """Lê a NFS-e de origem. Devolve dados do rascunho, origem de cada campo, o que não foi copiado e pendências."""
    root = nx.parse_xml_root(xml, nx.Limits())
    inf = nx._child(root, "infNFSe")
    dps = nx._path(inf, "DPS", "infDPS")
    if dps is None:
        raise ReferenciaInvalida("SOURCE_XML_INVALID", "O XML guardado não contém a DPS da nota de origem.")
    prest = nx._text(dps, "prest", "CNPJ")
    if prest != company_cnpj:
        raise ReferenciaInvalida("SOURCE_NOT_OWN_ISSUE", "O prestador no XML oficial não é a empresa deste workspace: "
                                                         "só uma nota emitida pela própria empresa serve de referência.")
    if nx._text(dps, "tpEmit") != "1":
        raise ReferenciaInvalida("SOURCE_NOT_OWN_ISSUE", "A nota de origem não foi emitida pelo prestador (tpEmit "
                                                         "diferente de 1): não serve de referência para emissão própria.")
    serv = nx._child(dps, "serv")
    cserv = nx._child(serv, "cServ")
    vals = nx._child(dps, "valores")
    trib = nx._child(vals, "trib")
    tm = nx._child(trib, "tribMun")
    fed = nx._child(trib, "tribFed")
    pc = nx._child(fed, "piscofins")
    campos: dict[str, dict] = {}
    pend: list[dict] = []

    def copia(campo: str, tag: str, valor):  # noqa: ANN001, ANN202
        if valor not in (None, "", {}):
            campos[campo] = {"origem": "xml", "tag": tag}
        return valor

    # ---------------------------------------------------------------- serviço
    ctn = nx._text(cserv, "cTribNac")
    if ctn and not dominios.servico_nacional(ctn):
        pend.append(_pend("service.national_code", "REFERENCE_CODE_INVALID",
                          f"cTribNac {ctn} da nota de origem não existe na lista nacional vigente (Anexo B): informe o "
                          "código da nova operação.", "error", tag="serv/cServ/cTribNac"))
        ctn = None
    elif not ctn:
        pend.append(_pend("service.national_code", "REFERENCE_FIELD_ABSENT", "A nota de origem não trazia cTribNac: "
                          "informe o código da nova operação.", "error", tag="serv/cServ/cTribNac"))
    nbs = nx._text(cserv, "cNBS")
    if nbs and not dominios.nbs(nbs):
        pend.append(_pend("service.nbs_code", "REFERENCE_CODE_INVALID", f"cNBS {nbs} da nota de origem não existe na "
                          "tabela NBS vigente: informe o código correto.", "error", tag="serv/cServ/cNBS"))
        nbs = None
    elif not nbs:
        pend.append(_pend("service.nbs_code", "REFERENCE_FIELD_ABSENT", "A nota de origem não trazia cNBS: obrigatório "
                          "quando o grupo IBS/CBS é informado (E0322).", tag="serv/cServ/cNBS"))
    ctm = nx._text(cserv, "cTribMun")
    if ctm and not (len(ctm) == 3 and ctm.isdigit()):
        pend.append(_pend("service.municipal_code", "REFERENCE_CODE_INVALID", f"cTribMun {ctm} fora do formato do "
                          "leiaute (3 dígitos): não copiado.", "info", tag="serv/cServ/cTribMun"))
        ctm = None
    loc = nx._text(serv, "locPrest", "cLocPrestacao")
    if nx._text(serv, "locPrest", "cPaisPrestacao"):
        pend.append(_pend("service.location_code", "REFERENCE_UNSUPPORTED", "A nota de origem foi prestada no exterior "
                          "(cPaisPrestacao): a plataforma não emite essa operação.", "error", enquanto_vazio=False,
                          tag="serv/locPrest/cPaisPrestacao"))
    elif loc and not dominios.municipio_ou_local_especial(loc):
        pend.append(_pend("service.location_code", "REFERENCE_CODE_INVALID", f"Município {loc} (cLocPrestacao) não "
                          "existe na tabela IBGE vigente: informe o local da prestação.", "error",
                          tag="serv/locPrest/cLocPrestacao"))
        loc = None
    extra = nx.servico_extra(serv) or {}
    obra_code = nx._text(serv, "obra", "cObra")
    service = {
        "national_code": copia("service.national_code", "serv/cServ/cTribNac", ctn),
        "municipal_code": copia("service.municipal_code", "serv/cServ/cTribMun", ctm),
        "description": copia("service.description", "serv/cServ/xDescServ", (nx._text(cserv, "xDescServ") or "")[:2000]),
        "nbs_code": copia("service.nbs_code", "serv/cServ/cNBS", nbs),
        "location_code": copia("service.location_code", "serv/locPrest/cLocPrestacao", loc),
        "obra_code": copia("service.obra_code", "serv/obra/cObra", obra_code),
        "obra_cib": copia("service.obra_cib", "serv/obra/cCIB", extra.get("obra_cib")),
        "obra_property_registration": copia("service.obra_property_registration", "serv/obra/inscImobFisc",
                                            extra.get("obra_property_registration")),
        "obra_address": copia("service.obra_address", "serv/obra/end", extra.get("obra_address")),
    }
    for grupo, tag in (("atvEvento", "serv/atvEvento"), ("explRod", "serv/explRod"), ("comExt", "serv/comExt"),
                       ("lsadppu", "serv/lsadppu")):
        if nx._child(serv, grupo) is not None:
            pend.append(_pend("service", "REFERENCE_UNSUPPORTED", f"A nota de origem tem o grupo {tag}, que a "
                              "plataforma não emite: para uma operação sem esse grupo use “Nova NFS-e do zero”.",
                              "error", enquanto_vazio=False, tag=tag))

    # ---------------------------------------------------------------- valores
    amount = _dec2(nx._text(vals, "vServPrest", "vServ"))
    d_inc = _dec2(nx._text(vals, "vDescCondIncond", "vDescIncond"))
    d_con = _dec2(nx._text(vals, "vDescCondIncond", "vDescCond"))
    ded = _dec2(nx._text(vals, "vDedRed", "vDR"))
    if nx._child(nx._child(vals, "vDedRed"), "pDR") is not None or nx._child(nx._child(vals, "vDedRed"), "documentos") \
            is not None:
        pend.append(_pend("deductions", "REFERENCE_UNSUPPORTED", "A nota de origem informou dedução/redução por "
                          "percentual ou por documentos (vDedRed/pDR ou documentos); a plataforma só emite o valor "
                          "(vDR): informe a dedução da nova nota.", "error", enquanto_vazio=False, tag="valores/vDedRed"))
    copia("amount", "valores/vServPrest/vServ", amount)
    copia("unconditional_discount", "valores/vDescCondIncond/vDescIncond", d_inc)
    copia("conditional_discount", "valores/vDescCondIncond/vDescCond", d_con)
    copia("deductions", "valores/vDedRed/vDR", ded)

    # ---------------------------------------------------------------- tributos
    tax: dict = {}
    trib_iss = nx._text(tm, "tribISSQN")
    tax["issqn_taxation"] = copia("tax_settings.issqn_taxation", "trib/tribMun/tribISSQN", TRIB.get(trib_iss or ""))
    tax["iss_retention"] = copia("tax_settings.iss_retention", "trib/tribMun/tpRetISSQN",
                                 RET_ISS.get(nx._text(tm, "tpRetISSQN") or ""))
    tax["iss_rate"] = copia("tax_settings.iss_rate", "trib/tribMun/pAliq", _dec2(nx._text(tm, "pAliq")))
    imun = nx._text(tm, "tpImunidade")
    tax["immunity_type"] = copia("tax_settings.immunity_type", "trib/tribMun/tpImunidade",
                                 imun if imun in ("1", "2", "3", "4", "5") else None)
    for grupo, tag in (("exigSusp", "trib/tribMun/exigSusp"), ("BM", "trib/tribMun/BM")):
        if nx._child(tm, grupo) is not None:
            pend.append(_pend("tax_settings", "REFERENCE_UNSUPPORTED", f"A nota de origem tem {tag} (suspensão da "
                              "exigibilidade ou benefício municipal), que a plataforma não emite: confira o ISSQN da "
                              "nova operação; sem esse tratamento use “Nova NFS-e do zero”.", "error",
                              enquanto_vazio=False, tag=tag))
    tax["pis_cofins_cst"] = copia("tax_settings.pis_cofins_cst", "trib/tribFed/piscofins/CST", nx._text(pc, "CST"))
    for chave_, tag in (("pis_cofins_base", "vBCPisCofins"), ("pis_amount", "vPis"), ("cofins_amount", "vCofins")):
        tax[chave_] = copia(f"tax_settings.{chave_}", f"trib/tribFed/piscofins/{tag}", _dec2(nx._text(pc, tag)))
    for chave_, tag in (("pis_rate", "pAliqPis"), ("cofins_rate", "pAliqCofins")):
        tax[chave_] = copia(f"tax_settings.{chave_}", f"trib/tribFed/piscofins/{tag}", _dec2(nx._text(pc, tag)))
    tax["pis_cofins_retention"] = copia("tax_settings.pis_cofins_retention", "trib/tribFed/piscofins/tpRetPisCofins",
                                        nx._text(pc, "tpRetPisCofins"))
    for chave_, tag in (("inss_withheld", "vRetCP"), ("irrf_withheld", "vRetIRRF"), ("csll_withheld", "vRetCSLL")):
        tax[chave_] = copia(f"tax_settings.{chave_}", f"trib/tribFed/{tag}", _dec2(nx._text(fed, tag)))
    tot = nx._child(trib, "totTrib")
    approx = None
    if nx._child(tot, "vTotTrib") is not None:
        g = nx._child(tot, "vTotTrib")
        approx = {"mode": "VALUES", "federal": _dec2(nx._text(g, "vTotTribFed")),
                  "state": _dec2(nx._text(g, "vTotTribEst")), "municipal": _dec2(nx._text(g, "vTotTribMun"))}
    elif nx._child(tot, "pTotTrib") is not None:
        g = nx._child(tot, "pTotTrib")
        approx = {"mode": "PERCENT", "federal": _dec2(nx._text(g, "pTotTribFed")),
                  "state": _dec2(nx._text(g, "pTotTribEst")), "municipal": _dec2(nx._text(g, "pTotTribMun"))}
    elif nx._text(tot, "pTotTribSN"):
        approx = {"mode": "SIMPLES_PERCENT", "simples": _dec2(nx._text(tot, "pTotTribSN"))}
    elif nx._text(tot, "indTotTrib"):
        approx = {"mode": "NOT_INFORMED"}
    tax["approx_tax"] = copia("tax_settings.approx_tax", "trib/totTrib", approx)
    if approx and approx["mode"] == "VALUES":
        pend.append(_pend("tax_settings.approx_tax", "REFERENCE_REVIEW_VALUES", "Tributos aproximados copiados em "
                          "valores (vTotTrib) da nota de origem: confira se correspondem ao valor da nova nota.", "info",
                          enquanto_vazio=False, tag="trib/totTrib/vTotTrib"))
    if any(tax.get(k) for k in ("inss_withheld", "irrf_withheld", "csll_withheld")) or \
            tax.get("iss_retention") in ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY"):
        pend.append(_pend("tax_settings", "REFERENCE_REVIEW_RETENTIONS", "Retenções copiadas da nota de origem: "
                          "confirme que valem para este tomador, valor e competência.", "info", enquanto_vazio=False))

    # ---------------------------------------------------------------- IBS/CBS
    g = nx._child(dps, "IBSCBS")
    gi = nx._path(g, "valores", "trib", "gIBSCBS")
    bruto = {"cIndOp": nx._text(g, "cIndOp"), "CST": nx._text(gi, "CST"), "cClassTrib": nx._text(gi, "cClassTrib"),
             "finNFSe": nx._text(g, "finNFSe")}
    ibs = nx._ibs_cbs(g) if g is not None else None
    if g is None:
        pend.append(_pend("tax_settings.ibs_cbs", "REFERENCE_IBS_CBS_ABSENT", "A nota de origem não tinha o grupo "
                          f"IBS/CBS. Para a competência {competencia_nova} defina cIndOp, CST e cClassTrib da nova "
                          "operação (nada é presumido a partir de outras notas).", tag="IBSCBS"))
    elif ibs is None:
        faltam = [k for k in ("cIndOp", "CST", "cClassTrib") if not bruto[k]]
        motivo = (f"incompleto na nota de origem (falta {', '.join(faltam)})" if faltam else
                  "com operação que a plataforma não emite (finNFSe diferente de 0, tpOper, gRefNFSe, tpEnteGov ou "
                  "reembolso)")
        pend.append(_pend("tax_settings.ibs_cbs", "REFERENCE_IBS_CBS_NOT_COPIED", f"Grupo IBS/CBS {motivo}: não "
                          "copiado. Informe a classificação da nova operação"
                          + (f" (a origem trazia: {', '.join(f'{k} {v}' for k, v in bruto.items() if v)})." if any(
                              bruto.values()) else "."), "error", tag="IBSCBS"))
    else:
        if not dominios.cindop_valido(ibs.get("operation_indicator")):
            pend.append(_pend("tax_settings.ibs_cbs", "REFERENCE_CODE_INVALID", f"cIndOp {ibs['operation_indicator']} "
                              "da nota de origem não existe no Anexo C vigente: revise.", "error", enquanto_vazio=False,
                              tag="IBSCBS/cIndOp"))
        tax["ibs_cbs"] = copia("tax_settings.ibs_cbs", "IBSCBS", ibs)
        campos["tax_settings.ibs_cbs"]["confirmar"] = True  # classificação da origem: evidência, não aprovação
    data = {
        "service": service,
        "amount": amount or "0.00",
        "deductions": ded or "0.00",
        "unconditional_discount": d_inc,
        "conditional_discount": d_con,
        "tax_settings": {k: v for k, v in tax.items() if v not in (None, "", {})},
    }
    _validar_formatos(data, campos, pend)
    pend.append(_pend("service_date", "REFERENCE_NEW_DATE", "Informe a data de início da nova prestação (dCompet): a "
                      "classificação e as regras oficiais são conferidas por ela, não pela data da nota de origem.",
                      tag="dCompet"))
    num = nx._text(inf, "nNFSe")
    return {
        "documento": {"number": num, "competence": (nx._text(dps, "dCompet") or "")[:7] or None,
                      "access_key": (inf.attrib.get("Id") or "")[3:] or None},
        "identidade": {"prest_cnpj": prest, "tpEmit": "1"},
        "dados": data, "campos": campos, "pendencias": pend,
        "nao_copiados": [{"campo": c, "motivo": m} for c, m in NAO_COPIADOS],
        "ibs_cbs_origem": {k: v for k, v in bruto.items() if v},
    }


def _validar_formatos(data: dict, campos: dict, pend: list[dict]) -> None:
    """Cada valor copiado precisa caber no leiaute ATUAL da plataforma (mesmos esquemas da edição manual). O que não
    cabe é removido do rascunho e vira pendência com o campo exato — nunca uma correção silenciosa."""
    from pydantic import ValidationError

    from ..api import schemas as sc

    svc = data["service"]
    for k in list(svc):
        if k == "description" or svc[k] in (None, ""):
            continue
        try:
            sc.ServicePatch.model_validate({k: svc[k]})
        except ValidationError:
            pend.append(_pend(f"service.{k}", "REFERENCE_FORMAT_INVALID", f"Valor de {campos.get(f'service.{k}', {}).get('tag', k)} "
                              "da nota de origem fora do formato aceito: não copiado.", "warning"))
            svc[k] = None
            campos.pop(f"service.{k}", None)
    if sum(bool(svc.get(k)) for k in ("obra_code", "obra_cib", "obra_address")) > 1:
        for k in ("obra_cib", "obra_address"):
            svc[k] = None
            campos.pop(f"service.{k}", None)
        pend.append(_pend("service.obra_code", "REFERENCE_OBRA_CHOICE", "A nota de origem identificava a obra de mais "
                          "de uma forma; foi mantido só o cObra: confira.", "warning", enquanto_vazio=False))
    tax = data["tax_settings"]
    for k in list(tax):
        try:
            sc.TaxSettings.model_validate({k: tax[k]})
        except ValidationError:
            pend.append(_pend(f"tax_settings.{k}", "REFERENCE_FORMAT_INVALID",
                              f"Valor de {campos.get(f'tax_settings.{k}', {}).get('tag', k)} da nota de origem fora do "
                              "formato aceito: não copiado; informe-o na nova nota.", "error"))
            tax.pop(k)
            campos.pop(f"tax_settings.{k}", None)


def valor_campo(d, campo: str):  # noqa: ANN001, ANN201
    """Valor ATUAL do rascunho no caminho usado pelas pendências da referência."""
    from .drafts import draft_out

    atual = draft_out(d)
    for parte in campo.split("."):
        if not isinstance(atual, dict):
            return None
        atual = atual.get(parte)
    return atual


def pendencias_atuais(d) -> list[dict]:  # noqa: ANN001
    """Pendências da referência que ainda valem para a versão atual do rascunho (as resolvidas somem)."""
    from .ibscbs import classificacao

    origem = d.origin or {}
    if origem.get("kind") != "document":
        return []
    out = []
    for p in origem.get("pending") or []:
        if p.get("while_empty") and valor_campo(d, p["field"]) not in (None, "", {}, []):
            continue
        out.append({"field": p["field"], "code": p["code"], "message": p["message"], "severity": p["severity"],
                    **({"rule": p["tag"]} if p.get("tag") else {})})
    # Revalidação pela competência NOVA: a classificação copiada precisa valer na data da nova prestação.
    ibs = (d.tax_settings or {}).get("ibs_cbs") or {}
    if ibs.get("class_code") and d.competence:
        em = date.fromisoformat(d.service_date) if d.service_date else date.fromisoformat(f"{d.competence}-01")
        if classificacao(ibs["class_code"], em) is None:
            out.append({"field": "tax_settings.ibs_cbs.class_code", "code": "REFERENCE_CLASS_NOT_VALID_AT_COMPETENCE",
                        "message": f"cClassTrib {ibs['class_code']} não está vigente para NFS-e em {em:%d/%m/%Y} "
                                   "(competência da nova emissão): informe a classificação aplicável.",
                        "severity": "error", "rule": "E0958"})
    return out
