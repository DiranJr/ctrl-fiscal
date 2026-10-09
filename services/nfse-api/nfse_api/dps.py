"""Montagem da DPS (leiaute v1.01) a partir do snapshot congelado do lote, com validação XSD local.

Fontes: XSD oficial PRODREST v1.01-20260727 (TCInfDPS e tipos) empacotado em nfse_api/xsd, e a estrutura de
DPS reais aceitas pela Sefin (conferência em tests/test_official_integration.py e scripts/comparar_dps_reais.py). Regras:
- Nenhum valor fiscal é presumido: dado obrigatório ausente => DpsIncompleta (nada é transmitido).
- prest sem xNome (emitente = prestador, como nas DPS aceitas); regTrib do perfil fiscal da empresa.
- Valores monetários com 2 casas (TSDec15V2); alíquotas no formato do XSD (TSDec1V2/TSDec2V2/TSDec3V2).
- Ordem dos elementos conforme as sequências do XSD.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import logging

from lxml import etree

log = logging.getLogger("ctrl.nfse.dps")

NS = "http://www.sped.fazenda.gov.br/nfse"
VERSAO = "1.01"
VER_APLIC = "CTRLFiscal-0.2"
XSD_DIR = Path(__file__).resolve().parent / "xsd"
XSD_BY_ENV = {
    "restricted": XSD_DIR / "prodrest-v1.01-20260727" / "DPS_v1.01.xsd",
    "production": XSD_DIR / "producao-v1.01-20260209" / "DPS_v1.01.xsd",
}
TP_AMB = {"restricted": "2", "production": "1"}
TRIB_ISSQN = {"TAXABLE": "1", "IMMUNE": "2", "EXPORT": "3", "NOT_INCIDENT": "4"}
RET_ISSQN = {"NOT_RETAINED": "1", "RETAINED_BY_TOMADOR": "2", "RETAINED_BY_INTERMEDIARY": "3"}


class DpsIncompleta(Exception):
    def __init__(self, faltas: list[dict[str, str]]) -> None:
        super().__init__("DPS incompleta")
        self.faltas = faltas


class DpsInvalidaXsd(Exception):
    def __init__(self, erros: list[str]) -> None:
        super().__init__("DPS inválida no XSD")
        self.erros = erros


@dataclass
class DpsMontada:
    dps_id: str
    xml: bytes  # sem assinatura
    dh_emi: str


def _money(v: object, field: str, faltas: list) -> str | None:
    if v is None or v == "":
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        faltas.append({"campo": field, "codigo": "VALOR_INVALIDO", "mensagem": "Valor monetário inválido."})
        return None
    if d < 0:
        faltas.append({"campo": field, "codigo": "VALOR_NEGATIVO", "mensagem": "Valor não pode ser negativo."})
        return None
    return f"{d.quantize(Decimal('0.01'))}"


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        el.text = text
    return el


def _digits(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


def _digits_alnum(v: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", v or "").upper()


def _end_obra(parent: etree._Element, e: dict) -> None:
    """TCEnderObraEvento nacional: CEP xLgr nro xCpl? xBairro (endExt não suportado)."""
    end = _sub(parent, "end")
    _sub(end, "CEP", _digits(e["postal_code"]))
    _sub(end, "xLgr", e["street"])
    _sub(end, "nro", e["number"])
    if e.get("complement"):
        _sub(end, "xCpl", e["complement"])
    _sub(end, "xBairro", e["district"])


# Anexo I, E0931/E0932/E0928: subitens de obra em que o grupo IBSCBS/imovel NÃO é permitido; cIndOp de imóveis.
IMOVEL_PROIBIDO = ("070201", "070202", "070401", "070501", "070502", "070601", "070602", "070701", "070801",
                   "071701", "071901")
IMOVEL_CINDOP = ("020101", "020201", "020301")


def dps_id(municipio: str, cnpj: str, serie: str, numero: int) -> str:
    return "DPS" + municipio.zfill(7) + "2" + cnpj.zfill(14) + str(int(serie)).zfill(5) + str(numero).zfill(15)


def montar(snapshot: dict, *, environment: str, serie: str, numero: int, agora: datetime) -> DpsMontada:
    """Monta a DPS sem assinatura. `agora` (com fuso) vira dhEmi no fuso da empresa."""
    if environment not in TP_AMB:
        raise ValueError("ambiente oficial inválido")
    faltas: list[dict[str, str]] = []

    def need(value: object, campo: str, mensagem: str) -> object:
        if value in (None, "", []):
            faltas.append({"campo": campo, "codigo": "OBRIGATORIO", "mensagem": mensagem})
        return value

    # Anexo I (LEIAUTE linha 105; RN 144/E0010): faixa 00001–49999 = emissão com aplicativo próprio (esta plataforma).
    if not str(serie).isdigit() or not 1 <= int(serie) <= 49999:
        faltas.append({"campo": "dps.series", "codigo": "SERIE_FORA_DA_FAIXA",
                       "mensagem": "Série de aplicativo próprio deve estar entre 1 e 49999 (regra E0010)."})
    company = snapshot.get("company") or {}
    perfil = company.get("fiscal_profile") or {}
    customer = snapshot.get("customer") or {}
    service = snapshot.get("service") or {}
    tax = snapshot.get("tax_settings") or {}
    cnpj = _digits(company.get("cnpj"))
    municipio = need(company.get("municipality_code"), "company.municipality_code", "Município do emitente (cLocEmi).")
    need(cnpj, "company.cnpj", "CNPJ do prestador.")
    op_simp = need(perfil.get("simples_option"), "company.fiscal_profile.simples_option", "opSimpNac do prestador.")
    reg_esp = need(perfil.get("special_regime"), "company.fiscal_profile.special_regime", "regEspTrib do prestador.")
    service_date = need(snapshot.get("service_date"), "service_date", "dCompet: data de início da prestação (AAAA-MM-DD).")
    if service_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(service_date)):
        faltas.append({"campo": "service_date", "codigo": "FORMATO", "mensagem": "dCompet deve ser AAAA-MM-DD."})
    elif service_date:
        try:
            date.fromisoformat(service_date)
        except ValueError:
            faltas.append({"campo": "service_date", "codigo": "DATA_INVALIDA", "mensagem": "dCompet inexistente."})
        if snapshot.get("competence") and str(service_date)[:7] != snapshot["competence"]:
            faltas.append({"campo": "service_date", "codigo": "COMPETENCIA_DIVERGENTE",
                           "mensagem": "dCompet deve pertencer à competência do rascunho."})
    doc_toma = _digits(customer.get("document"))
    need(doc_toma, "customer.document", "CNPJ/CPF do tomador.")
    need(customer.get("name"), "customer.name", "Nome do tomador (toma/xNome).")
    loc = need(service.get("location_code"), "service.location_code", "Município da prestação (cLocPrestacao).")
    c_trib_nac = need(service.get("national_code"), "service.national_code", "cTribNac (6 dígitos).")
    desc = need((service.get("description") or "").strip(), "service.description", "xDescServ.")
    v_serv = need(_money(snapshot.get("amount"), "amount", faltas), "amount", "vServ.")
    trib_issqn = need(TRIB_ISSQN.get(tax.get("issqn_taxation") or ""), "tax_settings.issqn_taxation", "tribISSQN.")
    ret_issqn = need(RET_ISSQN.get(tax.get("iss_retention") or ""), "tax_settings.iss_retention", "tpRetISSQN.")
    approx = tax.get("approx_tax") or {}
    need(approx.get("mode"), "tax_settings.approx_tax.mode",
         "totTrib: informe VALUES, PERCENT, SIMPLES_PERCENT ou NOT_INFORMED (Lei 12.741/2012).")
    # Defesa em profundidade (as mesmas regras bloqueiam o agendamento na fiscal-api): domínios oficiais e
    # combinações estruturais do Anexo I que nunca devem chegar à Sefin.
    from ctrl_common import dominios

    if c_trib_nac and not dominios.servico_nacional(c_trib_nac):
        faltas.append({"campo": "service.national_code", "codigo": "E0310", "mensagem": "cTribNac fora da lista nacional."})
    if service.get("nbs_code") and not dominios.nbs(service["nbs_code"]):
        faltas.append({"campo": "service.nbs_code", "codigo": "E0316", "mensagem": "cNBS fora da tabela NBS."})
    if loc and not dominios.municipio_ou_local_especial(loc):
        faltas.append({"campo": "service.location_code", "codigo": "E0302", "mensagem": "Município fora da tabela IBGE."})
    grupo = dominios.grupo_obrigatorio(c_trib_nac)
    tem_obra = bool(service.get("obra_code") or service.get("obra_address") or service.get("obra_cib"))
    if grupo == "obra" and not tem_obra:
        faltas.append({"campo": "service.obra_code", "codigo": "E0370", "mensagem": "Subitem exige o grupo obra."})
    if tem_obra and grupo != "obra" and c_trib_nac != dominios.OBRA_EXCECAO:
        faltas.append({"campo": "service.obra_code", "codigo": "E0372", "mensagem": "Grupo obra não permitido para o subitem."})
    if grupo == "atvEvento":
        faltas.append({"campo": "service.national_code", "codigo": "ATV_EVENTO", "mensagem": "Grupo atvEvento não suportado."})
    if op_simp == "1" and (approx.get("mode") in ("NOT_INFORMED", "SIMPLES_PERCENT")):
        faltas.append({"campo": "tax_settings.approx_tax.mode", "codigo": "E0713",
                       "mensagem": "Não optante informa totTrib por valores ou percentuais."})
    obra_end = service.get("obra_address") or None
    if sum(bool(x) for x in (service.get("obra_code"), service.get("obra_cib"), obra_end)) > 1:
        faltas.append({"campo": "service.obra_address", "codigo": "ESCOLHA",
                       "mensagem": "obra: informe cObra OU cCIB OU endereço, não mais de um."})
    if service.get("obra_property_registration") and not tem_obra:
        faltas.append({"campo": "service.obra_property_registration", "codigo": "ESCOLHA",
                       "mensagem": "obra/inscImobFisc exige cObra, cCIB ou endereço da obra."})
    if obra_end:
        for k, tag in (("postal_code", "CEP"), ("street", "xLgr"), ("number", "nro"), ("district", "xBairro")):
            need(obra_end.get(k), f"service.obra_address.{k}", f"obra/end/{tag}.")
    ibs = tax.get("ibs_cbs") or None
    if ibs:
        need(service.get("nbs_code"), "service.nbs_code", "cNBS obrigatório com IBS/CBS (regra E0322).")
        need(ibs.get("operation_indicator"), "tax_settings.ibs_cbs.operation_indicator", "IBSCBS/cIndOp.")
        need(ibs.get("cst"), "tax_settings.ibs_cbs.cst", "IBSCBS/gIBSCBS/CST.")
        need(ibs.get("class_code"), "tax_settings.ibs_cbs.class_code", "IBSCBS/gIBSCBS/cClassTrib.")
        im = ibs.get("real_estate") or None
        obra_item = c_trib_nac in IMOVEL_PROIBIDO
        if im and obra_item:
            faltas.append({"campo": "tax_settings.ibs_cbs.real_estate", "codigo": "E0931",
                           "mensagem": "Subitem de obra: grupo imovel não permitido."})
        if not obra_item and ibs.get("operation_indicator") in IMOVEL_CINDOP and not im:
            faltas.append({"campo": "tax_settings.ibs_cbs.real_estate", "codigo": "E0932",
                           "mensagem": "Operação com bem imóvel exige o grupo imovel."})
        if im and not obra_item and ibs.get("operation_indicator") not in IMOVEL_CINDOP:
            faltas.append({"campo": "tax_settings.ibs_cbs.real_estate", "codigo": "E0928",
                           "mensagem": "Grupo imovel só para operações com bem imóvel."})
        if im and bool(im.get("cib")) == bool(im.get("address")):
            faltas.append({"campo": "tax_settings.ibs_cbs.real_estate", "codigo": "ESCOLHA",
                           "mensagem": "imovel: informe cCIB OU endereço."})
    if perfil.get("simples_regime") and op_simp != "3":
        faltas.append({"campo": "company.fiscal_profile.simples_regime", "codigo": "INCOMPATIVEL",
                       "mensagem": "regApTribSN só para opSimpNac=3."})
    if faltas:
        raise DpsIncompleta(faltas)

    tz = ZoneInfo(company.get("timezone") or "America/Sao_Paulo")
    dh_emi = agora.astimezone(tz).replace(microsecond=0).isoformat()
    ident = dps_id(municipio, cnpj, serie, numero)
    root = etree.Element(f"{{{NS}}}DPS", nsmap={None: NS})
    root.set("versao", VERSAO)
    inf = _sub(root, "infDPS")
    inf.set("Id", ident)
    _sub(inf, "tpAmb", TP_AMB[environment])
    _sub(inf, "dhEmi", dh_emi)
    _sub(inf, "verAplic", VER_APLIC)
    _sub(inf, "serie", str(int(serie)))
    _sub(inf, "nDPS", str(numero))
    _sub(inf, "dCompet", service_date)
    _sub(inf, "tpEmit", "1")
    _sub(inf, "cLocEmi", municipio)

    prest = _sub(inf, "prest")
    _sub(prest, "CNPJ", cnpj)
    if perfil.get("municipal_registration"):
        _sub(prest, "IM", perfil["municipal_registration"])
    if perfil.get("phone"):
        _sub(prest, "fone", _digits(perfil["phone"]))
    if perfil.get("email"):
        _sub(prest, "email", perfil["email"])
    reg = _sub(prest, "regTrib")
    _sub(reg, "opSimpNac", op_simp)
    if perfil.get("simples_regime"):
        _sub(reg, "regApTribSN", perfil["simples_regime"])
    _sub(reg, "regEspTrib", reg_esp)

    toma = _sub(inf, "toma")
    _sub(toma, "CNPJ" if len(doc_toma) == 14 else "CPF", doc_toma)
    if customer.get("municipal_registration"):
        _sub(toma, "IM", customer["municipal_registration"])
    _sub(toma, "xNome", customer["name"].strip())
    addr = customer.get("address") or {}
    if customer.get("municipality_code") and customer.get("postal_code") and addr.get("street") and addr.get("number") \
            and addr.get("district"):
        end = _sub(toma, "end")
        en = _sub(end, "endNac")
        _sub(en, "cMun", customer["municipality_code"])
        _sub(en, "CEP", _digits(customer["postal_code"]))
        _sub(end, "xLgr", addr["street"])
        _sub(end, "nro", addr["number"])
        if addr.get("complement"):
            _sub(end, "xCpl", addr["complement"])
        _sub(end, "xBairro", addr["district"])
    if customer.get("phone"):
        _sub(toma, "fone", _digits(customer["phone"]))
    if customer.get("email"):
        _sub(toma, "email", customer["email"])

    serv = _sub(inf, "serv")
    _sub(_sub(serv, "locPrest"), "cLocPrestacao", loc)
    cserv = _sub(serv, "cServ")
    _sub(cserv, "cTribNac", c_trib_nac)
    if service.get("municipal_code"):
        _sub(cserv, "cTribMun", service["municipal_code"])
    _sub(cserv, "xDescServ", desc)
    if service.get("nbs_code"):
        _sub(cserv, "cNBS", service["nbs_code"])
    if tem_obra:
        # TCInfoObra: inscImobFisc? + escolha (cObra | cCIB | end)
        obra = _sub(serv, "obra")
        if service.get("obra_property_registration"):
            _sub(obra, "inscImobFisc", service["obra_property_registration"])
        if service.get("obra_code"):
            _sub(obra, "cObra", service["obra_code"])
        elif service.get("obra_cib"):
            _sub(obra, "cCIB", service["obra_cib"])
        else:
            _end_obra(obra, obra_end)
    info = service.get("additional_info") or {}
    if any(info.get(k) for k in ("technical_doc", "document_reference", "order_number", "order_items", "notes")):
        # TCInfoCompl: idDocTec? docRef? xPed? gItemPed? xInfComp?
        ic = _sub(serv, "infoCompl")
        for tag, key in (("idDocTec", "technical_doc"), ("docRef", "document_reference"), ("xPed", "order_number")):
            if info.get(key):
                _sub(ic, tag, info[key])
        if info.get("order_items"):
            gi = _sub(ic, "gItemPed")
            for item in info["order_items"][:99]:
                _sub(gi, "xItemPed", item)
        if info.get("notes"):
            _sub(ic, "xInfComp", info["notes"])

    valores = _sub(inf, "valores")
    _sub(_sub(valores, "vServPrest"), "vServ", v_serv)
    d_inc = _money(snapshot.get("unconditional_discount"), "unconditional_discount", faltas)
    d_con = _money(snapshot.get("conditional_discount"), "conditional_discount", faltas)
    if (d_inc and Decimal(d_inc) > 0) or (d_con and Decimal(d_con) > 0):
        desc = _sub(valores, "vDescCondIncond")
        if d_inc and Decimal(d_inc) > 0:
            _sub(desc, "vDescIncond", d_inc)
        if d_con and Decimal(d_con) > 0:
            _sub(desc, "vDescCond", d_con)
    deducoes = _money(snapshot.get("deductions"), "deductions", faltas)
    if deducoes and Decimal(deducoes) > 0:
        _sub(_sub(valores, "vDedRed"), "vDR", deducoes)
    trib = _sub(valores, "trib")
    tm = _sub(trib, "tribMun")
    _sub(tm, "tribISSQN", trib_issqn)
    if tax.get("immunity_type"):
        _sub(tm, "tpImunidade", str(tax["immunity_type"]))
    _sub(tm, "tpRetISSQN", ret_issqn)
    if tax.get("iss_rate"):
        _sub(tm, "pAliq", tax["iss_rate"])
    fed_vals = {k: _money(tax.get(f), f"tax_settings.{f}", faltas)
                for k, f in (("vRetCP", "inss_withheld"), ("vRetIRRF", "irrf_withheld"), ("vRetCSLL", "csll_withheld"))}
    if tax.get("pis_cofins_cst") or any(fed_vals.values()):
        fed = _sub(trib, "tribFed")
        if tax.get("pis_cofins_cst"):
            pc = _sub(fed, "piscofins")
            _sub(pc, "CST", tax["pis_cofins_cst"])
            for tag, key, kind in (("vBCPisCofins", "pis_cofins_base", "money"), ("pAliqPis", "pis_rate", "rate"),
                                   ("pAliqCofins", "cofins_rate", "rate"), ("vPis", "pis_amount", "money"),
                                   ("vCofins", "cofins_amount", "money")):
                if tax.get(key) not in (None, ""):
                    _sub(pc, tag, _money(tax[key], f"tax_settings.{key}", faltas) if kind == "money" else str(tax[key]))
            if tax.get("pis_cofins_retention") is not None:
                _sub(pc, "tpRetPisCofins", tax["pis_cofins_retention"])
        for k in ("vRetCP", "vRetIRRF", "vRetCSLL"):
            if fed_vals[k]:
                _sub(fed, k, fed_vals[k])
    tot = _sub(trib, "totTrib")
    mode = approx["mode"]
    if mode == "VALUES":
        g = _sub(tot, "vTotTrib")
        for k, f in (("vTotTribFed", "federal"), ("vTotTribEst", "state"), ("vTotTribMun", "municipal")):
            _sub(g, k, need(_money(approx.get(f), f"tax_settings.approx_tax.{f}", faltas), f"tax_settings.approx_tax.{f}", k))
    elif mode == "PERCENT":
        g = _sub(tot, "pTotTrib")
        for k, f in (("pTotTribFed", "federal"), ("pTotTribEst", "state"), ("pTotTribMun", "municipal")):
            _sub(g, k, need(approx.get(f), f"tax_settings.approx_tax.{f}", k))
    elif mode == "SIMPLES_PERCENT":
        _sub(tot, "pTotTribSN", need(approx.get("simples"), "tax_settings.approx_tax.simples", "pTotTribSN"))
    elif mode == "NOT_INFORMED":
        _sub(tot, "indTotTrib", "0")
    else:
        faltas.append({"campo": "tax_settings.approx_tax.mode", "codigo": "INVALIDO", "mensagem": "Modo de totTrib inválido."})
    if ibs:
        # Mesma estrutura das DPS reais aceitas: NFS-e regular, tomador = destinatário.
        g = _sub(inf, "IBSCBS")
        _sub(g, "finNFSe", "0")
        if ibs.get("personal_use") in ("0", "1"):
            _sub(g, "indFinal", ibs["personal_use"])
        _sub(g, "cIndOp", ibs["operation_indicator"])
        dest = ibs.get("recipient") or None
        _sub(g, "indDest", "1" if dest else "0")
        if dest:
            # TCRTCInfoDest: (CNPJ|CPF|NIF|cNaoNIF) xNome end? fone? email?
            gd = _sub(g, "dest")
            if dest["document_type"] == "NO_NIF":
                _sub(gd, "cNaoNIF", dest["no_nif_reason"])
            else:
                doc = dest["document"] if dest["document_type"] == "NIF" else _digits_alnum(dest["document"])
                _sub(gd, dest["document_type"], doc)
            _sub(gd, "xNome", dest["name"].strip())
            de = dest.get("address") or None
            if de:
                end = _sub(gd, "end")
                en = _sub(end, "endNac")
                _sub(en, "cMun", de["municipality_code"])
                _sub(en, "CEP", _digits(de["postal_code"]))
                _sub(end, "xLgr", de["street"])
                _sub(end, "nro", de["number"])
                if de.get("complement"):
                    _sub(end, "xCpl", de["complement"])
                _sub(end, "xBairro", de["district"])
            if dest.get("phone"):
                _sub(gd, "fone", _digits(dest["phone"]))
            if dest.get("email"):
                _sub(gd, "email", dest["email"])
        im = ibs.get("real_estate") or None
        if im:
            # TCRTCInfoImovel: inscImobFisc? + escolha (cCIB | end)
            gim = _sub(g, "imovel")
            if im.get("registration"):
                _sub(gim, "inscImobFisc", im["registration"])
            if im.get("cib"):
                _sub(gim, "cCIB", im["cib"])
            else:
                _end_obra(gim, im["address"])
        gi = _sub(_sub(_sub(g, "valores"), "trib"), "gIBSCBS")
        # TCRTCInfoTributosSitClas: CST cClassTrib cCredPres? gTribRegular? gDif?
        _sub(gi, "CST", ibs["cst"])
        _sub(gi, "cClassTrib", ibs["class_code"])
        if ibs.get("presumed_credit"):
            _sub(gi, "cCredPres", ibs["presumed_credit"])
        reg = ibs.get("regular") or None
        if reg:
            gr = _sub(gi, "gTribRegular")
            _sub(gr, "CSTReg", reg["cst"])
            _sub(gr, "cClassTribReg", reg["class_code"])
        dif = ibs.get("deferral") or None
        if dif:
            gdf = _sub(gi, "gDif")
            _sub(gdf, "pDifUF", dif["uf"])
            _sub(gdf, "pDifMun", dif["mun"])
            _sub(gdf, "pDifCBS", dif["cbs"])
    if faltas:
        raise DpsIncompleta(faltas)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    validar_xsd(xml, environment)
    return DpsMontada(dps_id=ident, xml=xml, dh_emi=dh_emi)


class _NoNet(etree.Resolver):
    def resolve(self, url, pubid, context):  # noqa: ANN001, ANN201
        if url.startswith(("http://", "https://")):
            raise ValueError("resolução remota de schema bloqueada")
        return None


@lru_cache(maxsize=4)
def _schema(environment: str) -> etree.XMLSchema:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    parser.resolvers.add(_NoNet())
    return etree.XMLSchema(etree.parse(str(XSD_BY_ENV[environment]), parser))


# DEFEITO DOCUMENTADO do pacote XSD de PRODUÇÃO v1.01-20260209 (arquivo oficial mantido INTACTO, SHA256SUMS):
# TSSerieDPS usa o padrão "^0{0,4}\d{1,5}$". Em expressões regulares de XML Schema (W3C XSD 1.0 Parte 2, Apêndice F)
# não existem âncoras: "^" e "$" são caracteres LITERAIS e o padrão já casa a cadeia inteira. Logo NENHUMA série
# numérica é aceita ("1" falha; só "^1$" passaria). Reprodução mínima: tests/test_serie_xsd.py; 2259 de 2259 DPS reais
# de produção (importadas do ADN) falham SOMENTE nesta faceta e passam no pacote PRODREST v1.01-20260727, cuja
# TSSerieDPS corrigida é "[0-9]{1,4}|[0-8][0-9]{4}". Tratamento explícito (sem desligar validação): em produção, um
# erro de faceta 'pattern' do elemento serie com ESTE padrão defeituoso é substituído pela conferência da série
# contra a TSSerieDPS do pacote PRODREST (mesmo tamanho máximo 5); qualquer outro erro continua bloqueando.
SERIE_PADRAO_DEFEITUOSO = r"^0{0,4}\d{1,5}$"
SERIE_PADRAO_PRODREST = re.compile(r"[0-9]{1,4}|[0-8][0-9]{4}")


def _erro_serie_defeituosa(e, doc: etree._Element) -> bool:  # noqa: ANN001
    if "nfse}serie'" not in e.message or "[facet 'pattern']" not in e.message or SERIE_PADRAO_DEFEITUOSO not in e.message:
        return False
    serie = doc.find(f".//{{{NS}}}infDPS/{{{NS}}}serie")
    return serie is not None and bool(SERIE_PADRAO_PRODREST.fullmatch(serie.text or ""))


def validar_xsd(xml: bytes, environment: str) -> None:
    schema = _schema(environment)
    doc = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    if schema.validate(etree.ElementTree(doc)):
        return
    erros = list(schema.error_log)
    if environment == "production":
        todos = len(erros)
        erros = [e for e in erros if not _erro_serie_defeituosa(e, doc)]
        if len(erros) < todos:
            # Registro de USO da compatibilidade (auditável no log do conector): Id da DPS e série, sem dados pessoais.
            inf = doc.find(f"{{{NS}}}infDPS")
            log.warning("compatibilidade TSSerieDPS aplicada (XSD produção 20260209, padrão com âncoras literais): "
                        "dps=%s serie=%s conferida contra TSSerieDPS PRODREST 20260727; demais erros=%d",
                        inf.get("Id") if inf is not None else "?", doc.findtext(f".//{{{NS}}}serie"), len(erros))
    if erros:
        raise DpsInvalidaXsd([f"linha {e.line}: {e.message}"[:300] for e in erros[:20]])
