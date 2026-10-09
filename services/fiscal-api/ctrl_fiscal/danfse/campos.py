"""Extração dos campos do DANFSe a partir do XML OFICIAL da NFS-e (NT SE/CGNFS-e nº 008, versão 1.02 de 14/07/2026).

Regras aplicadas (itens da NT):
- 2.1: só se imprime o que consta do arquivo da NFS-e. Nada vem do cadastro da plataforma (cliente, empresa, perfil).
  Códigos são descritos por tabelas oficiais: domínios do leiaute (XSD v1.01) e tabela IBGE de municípios (Anexo A).
- Nota 12: campo sem informação no XML é impresso como traço (-). Nenhum valor é presumido (ex.: 0,00).
- Prestador: os campos vêm de DPS/infDPS/prest. Quando o grupo prest da DPS não traz nome/endereço (o leiaute os
  dispensa quando o prestador é o emitente, tpEmit=1), usa-se infNFSe/emit, que nesse caso identifica o MESMO
  contribuinte (mesmo CNPJ/CPF, conferido) — continua sendo conteúdo do XML oficial.
- tpRetPisCofins = 1 (v1.02): "Contribuições Sociais – Retidas" = vRetCSLL + vPis + vCofins; PIS e COFINS de
  apuração própria impressos como 0,00. Nos demais códigos, vRetCSLL / vPis / vCofins como informados.
- Linha PIS/COFINS de apuração própria (nota 6) só para competência até 31/12/2026.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from defusedxml import ElementTree as SafeET

from ctrl_common import dominios

NS = "{http://www.sped.fazenda.gov.br/nfse}"
DASH = "-"
QR_BASE = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave="

# Domínios do leiaute NFS-e/DPS v1.01 (tiposSimples_v1.01.xsd) — descrições oficiais.
TP_EMIT = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}
C_STAT = {"100": "NFS-e Gerada", "101": "NFS-e de Substituição Gerada",
          "102": "NFS-e de Decisão Judicial ou Administrativa", "103": "NFS-e Avulsa", "107": "NFS-e MEI"}
FIN_NFSE = {"0": "NFS-e regular"}
AMB_GER = {"1": "Prefeitura", "2": "Sistema Nacional da NFS-e"}
TP_AMB = {"1": "Produção", "2": "Homologação"}
OP_SIMP_NAC = {"1": "Não Optante", "2": "Optante - Microempreendedor Individual (MEI)",
               "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)"}
REG_AP_TRIB_SN = {
    "1": "Regime de apuração dos tributos federais e municipal pelo SN",
    "2": "Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN conforme respectiva legislação "
         "municipal do tributo",
    "3": "Regime de apuração dos tributos federais e municipal por fora do SN conforme respectivas legislações "
         "federal e municipal de cada tributo",
}
TRIB_ISSQN = {"1": "Operação Tributável", "2": "Imunidade", "3": "Exportação de Serviço", "4": "Não Incidência"}
REG_ESP_TRIB = {"0": "Nenhum", "1": "Ato Cooperado (Cooperativa)", "2": "Estimativa", "3": "Microempresa Municipal",
                "4": "Notário ou Registrador", "5": "Profissional Autônomo", "6": "Sociedade de Profissionais",
                "9": "Outros"}
TP_IMUNIDADE = {
    "0": "Imunidade (tipo não informado na nota de origem)",
    "1": "Patrimônio, renda ou serviços, uns dos outros (CF88, Art 150, VI, a)",
    "2": "Templos de qualquer culto (CF88, Art 150, VI, b)",
    "3": "Patrimônio, renda ou serviços dos partidos políticos, inclusive suas fundações, das entidades sindicais "
         "dos trabalhadores, das instituições de educação e de assistência social, sem fins lucrativos, atendidos "
         "os requisitos da lei (CF88, Art 150, VI, c)",
    "4": "Livros, jornais, periódicos e o papel destinado a sua impressão (CF88, Art 150, VI, d)",
    "5": "Fonogramas e videofonogramas musicais produzidos no Brasil contendo obras musicais ou literomusicais de "
         "autores brasileiros e/ou obras em geral interpretadas por artistas brasileiros bem como os suportes "
         "materiais ou arquivos digitais que os contenham (CF88, Art 150, VI, e)",
}
TP_SUSP = {"1": "Exigibilidade Suspensa por Decisão Judicial", "2": "Exigibilidade Suspensa por Processo Administrativo"}
TP_BM = {"1": "Isenção", "2": "Redução da BC em ppBM %", "3": "Redução da BC em R$ vInfoBM",
         "4": "Alíquota Diferenciada de aliqDifBM %"}
TP_RET_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
TP_RET_PIS_COFINS = {
    "0": "PIS/COFINS/CSLL Não Retidos", "1": "PIS/COFINS Retidos", "2": "PIS/COFINS Não Retidos",
    "3": "PIS/COFINS/CSLL Retidos", "4": "PIS/COFINS Retidos, CSLL Não Retido", "5": "PIS Retido, COFINS/CSLL Não Retido",
    "6": "COFINS Retido, PIS/CSLL Não Retido", "7": "PIS Não Retido, COFINS/CSLL Retidos",
    "8": "PIS/COFINS Não Retidos, CSLL Retido", "9": "COFINS Não Retido, PIS/CSLL Retidos",
}

TOMADOR_NAO_IDENT = "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"
DEST_NAO_IDENT = "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"
DEST_PROPRIO_TOMADOR = "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO"
INTERM_NAO_IDENT = "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"


class XmlNaoNfse(ValueError):
    """O arquivo não é uma NFS-e (raiz NFSe/infNFSe ausente)."""


def _t(el, path: str) -> str | None:  # noqa: ANN001
    if el is None:
        return None
    v = el.findtext("/".join(f"{NS}{p}" for p in path.split("/")))
    v = v.strip() if v is not None else None
    return v or None


def _f(el, path: str):  # noqa: ANN001, ANN202
    return None if el is None else el.find("/".join(f"{NS}{p}" for p in path.split("/")))


def _dec(v: str | None) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def brl(v: Decimal | str | None) -> str:
    d = _dec(v) if isinstance(v, str) else v
    if d is None:
        return DASH
    s = f"{d.quantize(Decimal('0.01')):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def pct(v: str | None) -> str:
    d = _dec(v)
    if d is None:
        return DASH
    return f"{d.quantize(Decimal('0.01')):.2f}".replace(".", ",") + "%"


def doc_fmt(cnpj: str | None, cpf: str | None, nif: str | None) -> str:
    if cnpj:
        c = cnpj
        return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}" if len(c) == 14 else c
    if cpf:
        c = cpf
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:11]}" if len(c) == 11 else c
    return nif or DASH


def cep_fmt(cep: str | None) -> str | None:
    if not cep:
        return None
    return f"{cep[:2]}.{cep[2:5]}-{cep[5:8]}" if len(cep) == 8 and cep.isdigit() else cep


def data_fmt(v: str | None) -> str:
    """AAAA-MM-DD → DD/MM/AAAA, sem conversão."""
    if not v or len(v) < 10:
        return v or DASH
    return f"{v[8:10]}/{v[5:7]}/{v[0:4]}"


def data_hora_fmt(v: str | None) -> str:
    """AAAA-MM-DDThh:mm:ss±hh:mm → DD/MM/AAAA hh:mm:ss, na hora registrada no próprio XML (sem conversão de fuso)."""
    if not v or len(v) < 19:
        return v or DASH
    return f"{data_fmt(v[:10])} {v[11:19]}"


def desc(table: dict, code: str | None) -> str:
    if code is None:
        return DASH
    return table.get(code, code)


def _mun(code: str | None) -> dict | None:
    return dominios.municipio(code) if code else None


def mun_uf(code: str | None, xcidade: str | None = None) -> str:
    m = _mun(code)
    if m:
        return f"{m['nome']} / {m['uf']}"
    return xcidade or DASH


def _join(*parts: str | None, sep: str = " / ") -> str:
    vals = [p for p in parts if p]
    return sep.join(vals) if vals else DASH


@dataclass
class Pessoa:
    doc: str = DASH
    im: str = DASH
    fone: str = DASH
    nome: str = DASH
    municipio: str = DASH
    ibge_cep: str = DASH
    endereco: str = DASH
    email: str = DASH


@dataclass
class Danfse:
    homologacao: bool
    cabecalho_municipio: str | None
    ambiente_gerador: str
    tipo_ambiente: str
    chave: str
    qr_url: str
    numero: str
    competencia: str
    dh_nfse: str
    ndps: str
    serie: str
    dh_dps: str
    emitente: str
    situacao: str
    finalidade: str
    prestador: Pessoa
    simples: str
    regime_sn: str
    tomador: Pessoa | None
    tomador_msg: str | None
    destinatario: Pessoa | None
    destinatario_msg: str | None
    intermediario: Pessoa | None
    intermediario_msg: str | None
    cod_trib: str
    nbs: str
    local_prestacao: str
    desc_cod_trib: str
    descricao: str
    issqn: dict[str, str]
    federal: dict[str, str]
    imprime_pis_cofins_proprio: bool
    ibscbs: dict[str, str]
    totais: dict[str, str]
    info_complementar: str
    totais_aproximados: str
    competencia_iso: str | None = None
    fontes: dict = field(default_factory=dict)


def _pessoa(g, endereco_tag: str = "end") -> Pessoa:  # noqa: ANN001
    end = _f(g, endereco_tag)
    nac = _f(end, "endNac") if end is not None else None
    ext = _f(end, "endExt") if end is not None else None
    if nac is not None:
        municipio = mun_uf(_t(nac, "cMun"))
        ibge_cep = _join(_t(nac, "cMun"), cep_fmt(_t(nac, "CEP")))
    elif ext is not None:
        municipio = _join(_t(ext, "xCidade"), _t(ext, "xEstProvReg"), _t(ext, "cPais"))
        ibge_cep = _join(_t(ext, "cEndPost"))
    else:
        municipio = ibge_cep = DASH
    endereco = _join(_t(end, "xLgr"), _t(end, "nro"), _t(end, "xCpl"), _t(end, "xBairro"), sep=", ") if end is not None else DASH
    return Pessoa(doc=doc_fmt(_t(g, "CNPJ"), _t(g, "CPF"), _t(g, "NIF")), im=_t(g, "IM") or DASH, fone=_t(g, "fone") or DASH,
                  nome=_t(g, "xNome") or DASH, municipio=municipio, ibge_cep=ibge_cep, endereco=endereco,
                  email=_t(g, "email") or DASH)


def _prestador(inf_nfse, dps, tp_emit: str | None) -> Pessoa:  # noqa: ANN001
    prest = _f(dps, "prest")
    p = _pessoa(prest)
    emit = _f(inf_nfse, "emit")
    mesmo = (emit is not None and tp_emit == "1"
             and (_t(emit, "CNPJ") or _t(emit, "CPF")) == (_t(prest, "CNPJ") or _t(prest, "CPF")))
    if mesmo:
        # Prestador é o emitente (tpEmit=1, mesmo documento): completar com infNFSe/emit (conteúdo do XML oficial).
        e = _pessoa(emit, "enderNac")
        en = _f(emit, "enderNac")
        if en is not None:
            e.municipio = mun_uf(_t(en, "cMun"))
            e.ibge_cep = _join(_t(en, "cMun"), cep_fmt(_t(en, "CEP")))
            e.endereco = _join(_t(en, "xLgr"), _t(en, "nro"), _t(en, "xCpl"), _t(en, "xBairro"), sep=", ")
        for k in ("im", "fone", "nome", "municipio", "ibge_cep", "endereco", "email"):
            if getattr(p, k) == DASH:
                setattr(p, k, getattr(e, k))
    return p


def _cod_trib(c_trib_nac: str | None, c_trib_mun: str | None) -> str:
    nac = f"{c_trib_nac[:2]}.{c_trib_nac[2:4]}.{c_trib_nac[4:6]}" if c_trib_nac and len(c_trib_nac) == 6 else c_trib_nac
    return _join(nac, c_trib_mun)


def _nbs(v: str | None) -> str:
    if not v:
        return DASH
    return f"{v[0]}.{v[1:5]}.{v[5:7]}.{v[7:9]}" if len(v) == 9 else v


def _soma(*vals: str | None) -> Decimal | None:
    ds = [d for d in (_dec(v) for v in vals) if d is not None]
    return sum(ds, Decimal("0")) if ds else None


def extrair(xml: bytes) -> Danfse:
    root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag != f"{NS}NFSe" or _f(root, "infNFSe") is None:
        raise XmlNaoNfse("XML não é uma NFS-e (NFSe/infNFSe).")
    inf = _f(root, "infNFSe")
    dps = _f(inf, "DPS/infDPS")
    if dps is None:
        raise XmlNaoNfse("NFS-e sem o grupo DPS/infDPS.")
    nfse_id = inf.get("Id") or ""
    chave = nfse_id[3:] if nfse_id.startswith("NFS") else nfse_id
    tp_amb = _t(dps, "tpAmb")
    tp_emit = _t(dps, "tpEmit")
    c_trib_nac = _t(dps, "serv/cServ/cTribNac")
    emit_uf = _t(inf, "emit/enderNac/UF")
    cab_mun = None
    if not (c_trib_nac or "").startswith("99"):  # NT: não exibir quando o item do cTribNac for 99
        x_loc_emi = _t(inf, "xLocEmi")
        cab_mun = f"Município: {_join(x_loc_emi, emit_uf)}" if x_loc_emi else None
    rt = _f(dps, "prest/regTrib")

    # Tomador / destinatário / intermediário (notas 2 e 3)
    toma = _f(dps, "toma")
    interm = _f(dps, "interm")
    ibscbs_dps = _f(dps, "IBSCBS")
    dest = _f(ibscbs_dps, "dest")
    if dest is not None:
        destinatario, dest_msg = _pessoa(dest), None
    elif ibscbs_dps is not None and _t(ibscbs_dps, "indDest") == "0":
        destinatario, dest_msg = None, DEST_PROPRIO_TOMADOR
    else:
        destinatario, dest_msg = None, DEST_NAO_IDENT

    # Serviço
    x_trib_mun = _t(inf, "xTribMun")
    loc_prest = _t(dps, "serv/locPrest/cLocPrestacao")
    pais_prest = _t(dps, "serv/locPrest/cPaisPrestacao")
    m = _mun(loc_prest)
    local_prest = _join(_t(inf, "xLocPrestacao"), m["uf"] if m else None, pais_prest or ("BR" if loc_prest else None))

    # ISSQN
    tm = _f(dps, "valores/trib/tribMun")
    vals = _f(inf, "valores")
    loc_incid = _t(inf, "cLocIncid")
    mi = _mun(loc_incid)
    incid = _join(_t(inf, "xLocIncid"), mi["uf"] if mi else None,
                  _t(tm, "cPaisResult") or ("BR" if loc_incid else None))
    total_ded = _dec(_t(dps, "valores/vDedRed/vDR"))
    if total_ded is None:
        total_ded = _soma(_t(vals, "vCalcDR"), _t(inf, "IBSCBS/valores/vCalcReeRepRes"))
    issqn = {
        "tributacao": desc(TRIB_ISSQN, _t(tm, "tribISSQN")),
        "incidencia": incid,
        "regime_especial": desc(REG_ESP_TRIB, _t(rt, "regEspTrib")),
        "imunidade": desc(TP_IMUNIDADE, _t(tm, "tpImunidade")),
        "suspensao": desc(TP_SUSP, _t(tm, "exigSusp/tpSusp")),
        "processo": _t(tm, "exigSusp/nProcesso") or DASH,
        "beneficio": desc(TP_BM, _t(vals, "tpBM")),
        "calculo_bm": brl(_t(vals, "vCalcBM") or _t(tm, "BM/vRedBCBM")),
        "deducoes": brl(total_ded),
        "desc_incond": brl(_t(dps, "valores/vDescCondIncond/vDescIncond")),
        "bc": brl(_t(vals, "vBC")),
        "aliquota": pct(_t(vals, "pAliqAplic")),
        "retencao": desc(TP_RET_ISSQN, _t(tm, "tpRetISSQN")),
        "apurado": brl(_t(vals, "vISSQN")),
    }

    # Federais (exceto CBS) — NT v1.02
    tf = _f(dps, "valores/trib/tribFed")
    tp_pc = _t(tf, "piscofins/tpRetPisCofins")
    v_pis, v_cofins, v_csll = _t(tf, "piscofins/vPis"), _t(tf, "piscofins/vCofins"), _t(tf, "vRetCSLL")
    if tp_pc == "1":
        contrib = brl(_soma(v_csll, v_pis, v_cofins))
        pis_proprio = cofins_proprio = brl(Decimal("0"))
    else:
        contrib, pis_proprio, cofins_proprio = brl(v_csll), brl(v_pis), brl(v_cofins)
    federal = {"irrf": brl(_t(tf, "vRetIRRF")), "cp": brl(_t(tf, "vRetCP")), "contrib": contrib,
               "pis": pis_proprio, "cofins": cofins_proprio, "desc_contrib": desc(TP_RET_PIS_COFINS, tp_pc)}
    d_compet = _t(dps, "dCompet")
    imprime_pc = bool(d_compet) and d_compet[:4] <= "2026"

    # IBS / CBS
    ib = _f(inf, "IBSCBS")
    gib = _f(ibscbs_dps, "valores/trib/gIBSCBS")
    if ib is None and ibscbs_dps is None:
        ibscbs = {k: DASH for k in ("cst", "indicador", "exclusoes", "bc", "red_aliq", "aliq_uf_mun", "aliq_efet_mun",
                                    "v_mun", "aliq_efet_uf", "v_uf", "v_ibs", "aliq_cbs", "aliq_efet_cbs", "v_cbs")}
    else:
        loc = _t(ib, "cLocalidadeIncid")
        ml = _mun(loc)
        excl = _soma(_t(dps, "valores/vDescCondIncond/vDescIncond"), _t(ib, "valores/vCalcReeRepRes"),
                     _t(vals, "vISSQN"), v_pis, v_cofins)
        ibscbs = {
            "cst": _join(_t(gib, "CST"), _t(gib, "cClassTrib")),
            "indicador": _join(_t(ibscbs_dps, "cIndOp"), loc, _t(ib, "xLocalidadeIncid"), ml["uf"] if ml else None),
            "exclusoes": brl(excl),
            "bc": brl(_t(ib, "valores/vBC")),
            "red_aliq": _join(pct(_t(ib, "valores/uf/pRedAliqUF")), pct(_t(ib, "valores/mun/pRedAliqMun")),
                              pct(_t(ib, "valores/fed/pRedAliqCBS"))),
            "aliq_uf_mun": _join(pct(_t(ib, "valores/uf/pIBSUF")), pct(_t(ib, "valores/mun/pIBSMun"))),
            "aliq_efet_mun": pct(_t(ib, "valores/mun/pAliqEfetMun")),
            "v_mun": brl(_t(ib, "totCIBS/gIBS/gIBSMunTot/vIBSMun")),
            "aliq_efet_uf": pct(_t(ib, "valores/uf/pAliqEfetUF")),
            "v_uf": brl(_t(ib, "totCIBS/gIBS/gIBSUFTot/vIBSUF")),
            "v_ibs": brl(_t(ib, "totCIBS/gIBS/vIBSTot")),
            "aliq_cbs": pct(_t(ib, "valores/fed/pCBS")),
            "aliq_efet_cbs": pct(_t(ib, "valores/fed/pAliqEfetCBS")),
            "v_cbs": brl(_t(ib, "totCIBS/gCBS/vCBS")),
        }

    totais = {
        "servico": brl(_t(dps, "valores/vServPrest/vServ")),
        "desc_incond": brl(_t(dps, "valores/vDescCondIncond/vDescIncond")),
        "desc_cond": brl(_t(dps, "valores/vDescCondIncond/vDescCond")),
        "retencoes": brl(_t(vals, "vTotalRet")),
        "liquido": brl(_t(vals, "vLiq")),
        "ibscbs": brl(_soma(_t(ib, "totCIBS/gIBS/vIBSTot"), _t(ib, "totCIBS/gCBS/vCBS"))),
        "liquido_ibscbs": brl(_t(ib, "totCIBS/vTotNF")),
    }

    # Informações complementares — ordem da NT (item 2.4.5), separadas por pipes.
    ic = _f(dps, "serv/infoCompl")
    obra = _f(dps, "serv/obra")
    evt = _f(dps, "serv/atvEvento")
    partes: list[str] = []

    def add(rotulo: str, valor: str | None) -> None:
        if valor:
            partes.append(f"{rotulo} {valor}")

    add("Inf. Cont.:", _t(ic, "xInfComp"))
    add("NFS-e Subst.:", _t(dps, "subst/chSubstda"))
    add("Doc. Ref.:", _t(ic, "docRef"))
    add("Cod. Obra:", _t(obra, "cObra"))
    if obra is not None and not _t(obra, "cObra") and _f(obra, "end") is not None:
        oe = _f(obra, "end")
        add("Obra:", _join(cep_fmt(_t(oe, "CEP") or _t(oe, "endExt/cEndPost")), _t(oe, "xLgr"), _t(oe, "nro"),
                           _t(oe, "xCpl"), _t(oe, "xBairro"), sep=", "))
    add("Insc. Imob.:", _t(obra, "inscImobFisc") or _t(ibscbs_dps, "imovel/inscImobFisc"))
    add("Cod. Evt.:", _t(evt, "idAtvEvt"))
    if evt is not None and not _t(evt, "idAtvEvt"):
        add("Evento:", _join(_t(evt, "xNome"), data_fmt(_t(evt, "dtIni")) if _t(evt, "dtIni") else None,
                             data_fmt(_t(evt, "dtFim")) if _t(evt, "dtFim") else None, sep=" - "))
    add("Doc. Tec.:", _t(ic, "idDocTec"))
    add("Núm. Ped.:", _t(ic, "xPed"))
    itens = [x.text.strip() for x in (ic.findall(f"{NS}gItemPed/{NS}xItemPed") if ic is not None else []) if x.text]
    add("Item Ped.:", ", ".join(itens) if itens else None)
    out_inf = [v for v in (_t(inf, "xOutInf"), _t(vals, "xOutInf")) if v]
    add("Inf. A. T. Mun.:", " ".join(out_inf) if out_inf else None)

    tt = _f(dps, "valores/trib/totTrib")
    if _f(tt, "vTotTrib") is not None:
        aprox = (f"Federais: {brl(_t(tt, 'vTotTrib/vTotTribFed'))} ; Estaduais: {brl(_t(tt, 'vTotTrib/vTotTribEst'))} ; "
                 f"Municipais: {brl(_t(tt, 'vTotTrib/vTotTribMun'))}")
    elif _f(tt, "pTotTrib") is not None:
        aprox = (f"Federais: {pct(_t(tt, 'pTotTrib/pTotTribFed'))} ; Estaduais: {pct(_t(tt, 'pTotTrib/pTotTribEst'))} ; "
                 f"Municipais: {pct(_t(tt, 'pTotTrib/pTotTribMun'))}")
    elif _t(tt, "pTotTribSN"):
        # O XML traz só o percentual do Simples Nacional (sem separação federal/estadual/municipal): impresso como está.
        aprox = f"Simples Nacional: {pct(_t(tt, 'pTotTribSN'))}"
    else:
        aprox = f"Federais: {DASH} ; Estaduais: {DASH} ; Municipais: {DASH}"

    return Danfse(
        homologacao=tp_amb == "2",
        cabecalho_municipio=cab_mun,
        ambiente_gerador=f"Ambiente Gerador: {desc(AMB_GER, _t(inf, 'ambGer'))}",
        tipo_ambiente=f"Tipo de Ambiente: {desc(TP_AMB, tp_amb)}",
        chave=chave or DASH,
        qr_url=QR_BASE + chave,
        numero=_t(inf, "nNFSe") or DASH,
        competencia=data_fmt(d_compet),
        dh_nfse=data_hora_fmt(_t(inf, "dhProc")),
        ndps=_t(dps, "nDPS") or DASH,
        serie=_t(dps, "serie") or DASH,
        dh_dps=data_hora_fmt(_t(dps, "dhEmi")),
        emitente=desc(TP_EMIT, tp_emit),
        situacao=desc(C_STAT, _t(inf, "cStat")),
        finalidade=desc(FIN_NFSE, _t(ibscbs_dps, "finNFSe")),
        prestador=_prestador(inf, dps, tp_emit),
        simples=desc(OP_SIMP_NAC, _t(rt, "opSimpNac")),
        regime_sn=desc(REG_AP_TRIB_SN, _t(rt, "regApTribSN")),
        tomador=_pessoa(toma) if toma is not None else None,
        tomador_msg=None if toma is not None else TOMADOR_NAO_IDENT,
        destinatario=destinatario,
        destinatario_msg=dest_msg,
        intermediario=_pessoa(interm) if interm is not None else None,
        intermediario_msg=None if interm is not None else INTERM_NAO_IDENT,
        cod_trib=_cod_trib(c_trib_nac, _t(dps, "serv/cServ/cTribMun")),
        nbs=_nbs(_t(dps, "serv/cServ/cNBS")),
        local_prestacao=local_prest,
        desc_cod_trib=x_trib_mun or _t(inf, "xTribNac") or DASH,
        descricao=_t(dps, "serv/cServ/xDescServ") or DASH,
        issqn=issqn,
        federal=federal,
        imprime_pis_cofins_proprio=imprime_pc,
        ibscbs=ibscbs,
        totais=totais,
        info_complementar=" | ".join(partes),
        totais_aproximados=f"Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: {aprox}",
        competencia_iso=d_compet,
    )
