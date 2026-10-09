"""Inventário dos serviços PRESTADOS pelo workspace a partir dos XMLs oficiais das notas EMITIDAS.

Somente notas emitidas pela própria empresa (direction=ISSUED, papel PRESTADOR e CNPJ do prestador no XML igual ao do
workspace). Notas recebidas nunca são evidência dos serviços prestados.

Agrupamento: identidade do serviço = cTribNac + cNBS (códigos oficiais, nunca texto livre ou nome de cliente). Dentro
de cada grupo, as variações de operação/tratamento tributário (ISS, retenções, PIS/COFINS, obra, IBS/CBS, municípios)
são contadas e as que afetam o perfil viram DIVERGÊNCIAS para decisão do usuário — nada é escolhido por maioria.

A classificação IBS/CBS encontrada em notas anteriores é EVIDÊNCIA HISTÓRICA (origem "xml_historico"): não garante o
enquadramento de uma nova operação e precisa de confirmação na revisão.
"""
from __future__ import annotations

import gzip
from collections import Counter, defaultdict

from defusedxml import ElementTree as SafeET
from sqlalchemy import select
from sqlalchemy.orm import Session

from ctrl_common import dominios

from ..clients import CompanyInfo
from ..models import Document, StoredBlob

NS = "{http://www.sped.fazenda.gov.br/nfse}"
TRIB = {"1": "TAXABLE", "2": "IMMUNE", "3": "EXPORT", "4": "NOT_INCIDENT"}
TRIB_TXT = {"1": "Tributável", "2": "Imunidade", "3": "Exportação", "4": "Não incidência"}
RET_TXT = {"1": "Não retido", "2": "Retido pelo tomador", "3": "Retido pelo intermediário"}
INC_TXT = {"EP": "estabelecimento do prestador", "LP": "local da prestação", "ET": "estabelecimento do tomador"}
MAX_EXEMPLOS = 6


def _t(el, path: str) -> str | None:  # noqa: ANN001
    if el is None:
        return None
    x = el.find("/".join(NS + p for p in path.split("/")))
    return x.text.strip() if x is not None and x.text and x.text.strip() else None


def _fmt_ctn(c: str | None) -> str:
    return f"{c[:2]}.{c[2:4]}.{c[4:]}" if c and len(c) == 6 else (c or "—")


def _mun(code: str | None) -> str | None:
    m = dominios.municipio(code)
    if not m:
        return None
    return f"{m['nome']}/{m['uf']}" if m.get("uf") else m["nome"]


def ler_nota(xml: bytes) -> dict | None:
    """Campos usados no inventário, lidos do XML oficial (NFS-e autorizada)."""
    root = SafeET.fromstring(xml, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    inf = root.find(NS + "infNFSe")
    dps = inf.find(f"{NS}DPS/{NS}infDPS") if inf is not None else None
    if dps is None:
        return None
    serv = dps.find(NS + "serv")
    trib = dps.find(f"{NS}valores/{NS}trib")
    ibs = dps.find(NS + "IBSCBS")
    obra = serv.find(NS + "obra") if serv is not None else None
    emit = inf.find(NS + "emit")
    end = emit.find(f"{NS}enderNac") if emit is not None else None
    tipo_obra = None
    if obra is not None:
        tipo_obra = next((k for k in ("cObra", "cCIB", "end") if obra.find(NS + k) is not None), "outro")
    return {
        "prest_cnpj": _t(dps, "prest/CNPJ"), "tpEmit": _t(dps, "tpEmit"),
        "cTribNac": _t(serv, "cServ/cTribNac"), "cTribMun": _t(serv, "cServ/cTribMun"), "cNBS": _t(serv, "cServ/cNBS"),
        "xTribNac": _t(inf, "xTribNac"), "xNBS": _t(inf, "xNBS"), "xDescServ": _t(serv, "cServ/xDescServ"),
        "cLocPrestacao": _t(serv, "locPrest/cLocPrestacao"), "cPaisPrestacao": _t(serv, "locPrest/cPaisPrestacao"),
        "cLocIncid": _t(inf, "cLocIncid"),
        "tribISSQN": _t(trib, "tribMun/tribISSQN"), "tpRetISSQN": _t(trib, "tribMun/tpRetISSQN"),
        "pAliqAplic": _t(inf, "valores/pAliqAplic"),
        "cstPisCofins": _t(trib, "tribFed/piscofins/CST"), "tpRetPisCofins": _t(trib, "tribFed/piscofins/tpRetPisCofins"),
        "retINSS": _t(trib, "tribFed/vRetCP") is not None, "retIRRF": _t(trib, "tribFed/vRetIRRF") is not None,
        "retCSLL": _t(trib, "tribFed/vRetCSLL") is not None,
        "obra": tipo_obra, "infoCompl": serv is not None and serv.find(NS + "infoCompl") is not None,
        "ibs": None if ibs is None else {
            "cIndOp": _t(ibs, "cIndOp"), "CST": _t(ibs, "valores/trib/gIBSCBS/CST"),
            "cClassTrib": _t(ibs, "valores/trib/gIBSCBS/cClassTrib"), "indFinal": _t(ibs, "indFinal"),
            "indDest": _t(ibs, "indDest"), "finNFSe": _t(ibs, "finNFSe")},
        "imovel": ibs is not None and ibs.find(NS + "imovel") is not None,
        "dest": ibs is not None and _t(ibs, "indDest") == "1",
        "emit": {"IM": _t(emit, "IM"), "fone": _t(emit, "fone"), "email": _t(emit, "email"),
                 "xLgr": _t(end, "xLgr"), "nro": _t(end, "nro"), "xCpl": _t(end, "xCpl"), "xBairro": _t(end, "xBairro"),
                 "cMun": _t(end, "cMun"), "UF": _t(end, "UF"), "CEP": _t(end, "CEP")},
    }


def _notas(s: Session, company: CompanyInfo) -> list[tuple[Document, dict]]:
    rows = s.execute(select(Document, StoredBlob.content_gz).join(StoredBlob, StoredBlob.id == Document.xml_blob_id)
                     .where(Document.company_id == company.id, Document.direction == "ISSUED",
                            Document.company_role == "PRESTADOR", StoredBlob.company_id == company.id)
                     .order_by(Document.issued_at)).all()
    out = []
    for d, gz in rows:
        try:
            n = ler_nota(gzip.decompress(gz))
        except Exception:  # noqa: BLE001 - XML já aceito na ingestão; ilegível aqui não entra no inventário
            continue
        if n and n["prest_cnpj"] == company.cnpj:
            out.append((d, n))
    return out


def _cont(itens, chave) -> list[dict]:  # noqa: ANN001
    c = Counter(chave(n) for n in itens)
    return [{"valor": k, "notas": v} for k, v in c.most_common()]


def inventario(s: Session, company: CompanyInfo) -> dict:
    notas = _notas(s, company)
    grupos: dict[str, list[tuple[Document, dict]]] = defaultdict(list)
    for d, n in notas:
        grupos[f"{n['cTribNac'] or '-'}:{n['cNBS'] or '-'}"].append((d, n))
    # Capítulo NBS predominante por cTribNac (para apontar NBS fora do padrão — conferência, não regra).
    cap: dict[str, Counter] = defaultdict(Counter)
    for d, n in notas:
        if n["cNBS"]:
            cap[n["cTribNac"]][n["cNBS"][:3]] += 1
    out = [_grupo(k, v, cap) for k, v in grupos.items()]
    out.sort(key=lambda g: (-g["total_notas"], g["origin_key"]))
    comps = [d.competence for d, _ in notas if d.competence]
    return {"workspace_id": company.id, "fonte": "XML oficial das NFS-e EMITIDAS pelo workspace (notas recebidas não "
                                                 "entram)",
            "total_notas": len(notas), "periodo": {"de": min(comps) if comps else None, "ate": max(comps) if comps else None},
            "grupos": out}


def _grupo(key: str, itens: list[tuple[Document, dict]], cap: dict) -> dict:
    ns = [n for _, n in itens]
    docs = [d for d, _ in itens]
    ctn, nbs = ns[0]["cTribNac"], ns[0]["cNBS"]
    comps = sorted(d.competence for d in docs if d.competence)
    ibs_var = Counter((n["ibs"]["cIndOp"], n["ibs"]["CST"], n["ibs"]["cClassTrib"], n["ibs"]["indFinal"])
                      for n in ns if n["ibs"])
    ibs_lista = []
    for (op, cst, cls, fin), qtd in ibs_var.most_common():
        cs = sorted(d.competence for d, n in itens if n["ibs"] and n["ibs"]["cIndOp"] == op and n["ibs"]["cClassTrib"] == cls)
        info = dominios.cindop_info(op)
        ibs_lista.append({"cIndOp": op, "CST": cst, "cClassTrib": cls, "indFinal": fin, "notas": qtd,
                          "de": cs[0] if cs else None, "ate": cs[-1] if cs else None,
                          "cIndOp_descricao": info["tipo"] if info else None,
                          "cIndOp_local": info["local_dfe"] if info else None})
    pc = Counter((n["cstPisCofins"], n["tpRetPisCofins"]) for n in ns)
    trib = Counter(n["tribISSQN"] for n in ns)
    locais = Counter((n["cLocPrestacao"], n["cLocIncid"]) for n in ns)
    inc_tab = dominios.incidencia(ctn)
    exemplos = sorted(itens, key=lambda x: (x[0].status != "AUTHORIZED", x[0].competence or ""))
    passo = max(1, len(exemplos) // MAX_EXEMPLOS)
    exemplos = exemplos[::passo][:MAX_EXEMPLOS]
    div: list[dict] = []
    obs: list[dict] = []
    if len({n["cTribMun"] for n in ns}) > 1:
        div.append({"code": "CTRIBMUN_VARIA", "message": "cTribMun varia entre as notas (código municipal depende do "
                                                         "município de incidência): defina por nota ou deixe em branco."})
    cst_pc = {c for c, _ in pc if c}
    if len(cst_pc) > 1:
        div.append({"code": "PIS_COFINS_CST_VARIA", "message": "CST do PIS/COFINS diferente entre as notas: "
                                                               + ", ".join(sorted(cst_pc)) + " — escolha o aplicável."})
    if any(c is None for c, _ in pc) and cst_pc:
        div.append({"code": "PIS_COFINS_AUSENTE_EM_PARTE", "message": "Parte das notas não informou o grupo PIS/COFINS."})
    if len(ibs_var) > 1:
        div.append({"code": "IBS_CBS_VARIA", "message": "Notas com classificações IBS/CBS diferentes para o mesmo "
                                                        "serviço: confirme qual se aplica (ou crie perfis distintos)."})
    if len(trib) > 1:
        div.append({"code": "TRIB_ISSQN_VARIA", "message": "Tributação do ISSQN diferente entre as notas."})
    if len({n["tpRetISSQN"] for n in ns}) > 1:
        obs.append({"code": "RETENCAO_ISS_VARIA", "message": "A retenção do ISS varia entre as notas (depende do "
                                                             "tomador/município): é dado de cada nota, não do perfil.",
                    "fonte": "XML (tpRetISSQN)"})
    fora = [(lp, li) for (lp, li) in locais if lp and li and lp != li]
    if inc_tab == "LP" and fora:
        div.append({"code": "INCIDENCIA_DIVERGE", "message": f"{sum(locais[x] for x in fora)} nota(s) com município de "
                                                             "incidência diferente do local da prestação, embora o "
                                                             "Anexo I indique incidência no local da prestação."})
    if inc_tab:
        obs.append({"code": "INCIDENCIA_TABELA", "message": f"Anexo I: incidência do ISS no {INC_TXT[inc_tab]}.",
                    "fonte": "Anexo I — MUN.INCID_INFO.SERV."})
    grupo_obr = dominios.grupo_obrigatorio(ctn)
    sem_obra = sum(1 for n in ns if not n["obra"])
    if grupo_obr == "obra" and sem_obra:
        div.append({"code": "OBRA_AUSENTE", "message": f"{sem_obra} nota(s) sem o grupo obra, exigido para este "
                                                       "subitem (E0370)."})
    if grupo_obr == "obra":
        obs.append({"code": "OBRA_EXIGIDA", "message": "Cada nota deste subitem exige o grupo obra (CNO/CEI, CIB ou "
                                                       "endereço).", "fonte": "Anexo I (E0370)"})
    if nbs and ctn in cap:
        principal, qtd = cap[ctn].most_common(1)[0]
        total = sum(cap[ctn].values())
        if nbs[:3] != principal and qtd > total / 2:
            div.append({"code": "NBS_FORA_DO_PADRAO", "message": f"NBS {nbs} ({dominios.nbs(nbs) or 'sem descrição'}) "
                                                                 "difere do capítulo NBS usado na maioria das notas "
                                                                 f"deste cTribNac ({principal}…): confira o código."})
    if not nbs:
        div.append({"code": "NBS_AUSENTE", "message": "Notas sem cNBS: o NBS é obrigatório com o grupo IBS/CBS (E0322)."})
    for v in ibs_lista:
        if v["cIndOp_descricao"]:
            obs.append({"code": "CINDOP_ANEXO_C",
                        "message": f"cIndOp {v['cIndOp']} usado em {v['notas']} nota(s): “{v['cIndOp_descricao']}” "
                                   f"(local no DF-e: {v['cIndOp_local']}). Confirme se descreve esta operação.",
                        "fonte": "Anexo C — cIndOp"})
    sem_ibs = sum(1 for n in ns if not n["ibs"])
    return {
        "origin_key": key, "cTribNac": ctn, "cNBS": nbs,
        "descricao_cTribNac": next((n["xTribNac"] for n in ns if n["xTribNac"]), None) or dominios.servico_nacional(ctn),
        "descricao_cNBS": next((n["xNBS"] for n in ns if n["xNBS"]), None) or dominios.nbs(nbs),
        "total_notas": len(ns),
        "situacao": dict(Counter(d.status for d in docs)),
        "periodo": {"de": comps[0] if comps else None, "ate": comps[-1] if comps else None},
        "descricoes_usadas": [{"texto": (k or "")[:120], "notas": v} for k, v in
                              Counter((n["xDescServ"] or "")[:120] for n in ns).most_common(5)],
        "cTribMun": _cont(ns, lambda n: n["cTribMun"]),
        "locais": [{"cLocPrestacao": lp, "prestacao": _mun(lp), "cLocIncid": li, "incidencia": _mun(li), "notas": q}
                   for (lp, li), q in locais.most_common()],
        "iss": [{"tribISSQN": t, "tributacao": TRIB_TXT.get(t or ""), "tpRetISSQN": r, "retencao": RET_TXT.get(r or ""),
                 "aliquota_aplicada": a, "notas": q}
                for (t, r, a), q in Counter((n["tribISSQN"], n["tpRetISSQN"], n["pAliqAplic"]) for n in ns).most_common()],
        "pis_cofins": [{"CST": c, "tpRetPisCofins": r, "notas": q} for (c, r), q in pc.most_common()],
        "retencoes_federais": {"INSS": sum(n["retINSS"] for n in ns), "IRRF": sum(n["retIRRF"] for n in ns),
                               "CSLL_PIS_COFINS": sum(n["retCSLL"] for n in ns)},
        "obra": dict(Counter(n["obra"] or "ausente" for n in ns)),
        "imovel": sum(n["imovel"] for n in ns), "destinatario": sum(n["dest"] for n in ns),
        "info_complementar": sum(n["infoCompl"] for n in ns),
        "ibs_cbs": ibs_lista, "ibs_cbs_ausente": sem_ibs,
        "exemplos": [{"numero": d.number, "competencia": d.competence, "chave": d.access_key, "situacao": d.status,
                      "document_id": d.id} for d, _ in exemplos],
        "divergencias": div, "observacoes": obs,
    }


def proposta(g: dict) -> dict:
    """Perfil PENDENTE a partir de um grupo: só leva o que é consistente nas notas; o resto fica como pendência."""
    fields: dict = {"national_code": g["cTribNac"], "nbs_code": g["cNBS"]}
    fontes: dict = {"national_code": {"origem": "xml"}}
    if g["cNBS"]:
        fontes["nbs_code"] = {"origem": "xml"}
    tribs = {i["tribISSQN"] for i in g["iss"] if i["tribISSQN"]}
    if len(tribs) == 1:
        fields["issqn_taxation"] = TRIB[tribs.pop()]
        fontes["issqn_taxation"] = {"origem": "xml"}
    csts = {i["CST"] for i in g["pis_cofins"] if i["CST"]}
    if len(csts) == 1:
        fields["pis_cofins_cst"] = csts.pop()
        fontes["pis_cofins_cst"] = {"origem": "xml"}
    muns = {i["valor"] for i in g["cTribMun"]}
    if len(muns) == 1 and None not in muns:
        fields["municipal_code"] = muns.pop()
        fontes["municipal_code"] = {"origem": "xml"}
    if len(g["ibs_cbs"]) == 1:
        v = g["ibs_cbs"][0]
        fields["ibs_cbs"] = {"operation_indicator": v["cIndOp"], "cst": v["CST"], "class_code": v["cClassTrib"],
                             **({"personal_use": v["indFinal"]} if v["indFinal"] in ("0", "1") else {})}
        fontes["ibs_cbs"] = {"origem": "xml_historico",
                             "nota": f"Usado em {v['notas']} nota(s) de {v['de']} a {v['ate']} — evidência histórica."}
    sugestoes = [{"campo": "observacao", "codigo": o["code"], "texto": o["message"], "fonte": o["fonte"],
                  "tipo": "tabela_oficial" if "Anexo" in o["fonte"] else "dados_xml"} for o in g["observacoes"]]
    nome_nbs = (g["descricao_cNBS"] or "sem NBS")
    nome = f"{_fmt_ctn(g['cTribNac'])} · {nome_nbs}"[:120]
    return {"origin_key": g["origin_key"], "name": nome, "fields": fields, "field_sources": fontes,
            "evidence": {k: g[k] for k in ("total_notas", "situacao", "periodo", "descricao_cTribNac", "descricao_cNBS",
                                           "descricoes_usadas", "cTribMun", "locais", "iss", "pis_cofins",
                                           "retencoes_federais", "obra", "imovel", "destinatario", "info_complementar",
                                           "ibs_cbs", "ibs_cbs_ausente", "exemplos", "divergencias")},
            "suggestions": sugestoes}


def dados_prestador(s: Session, company: CompanyInfo) -> dict:
    """Dados do prestador no grupo emit das notas emitidas. Seguro = todas as notas que trazem o campo concordam."""
    notas = _notas(s, company)
    campos = {"municipal_registration": "IM", "phone": "fone", "email": "email", "postal_code": "CEP",
              "municipality_code": "cMun", "address.street": "xLgr", "address.number": "nro",
              "address.complement": "xCpl", "address.district": "xBairro", "address.state": "UF"}
    out = {}
    for campo, tag in campos.items():
        vals = [(n["emit"].get(tag), d) for d, n in notas if n["emit"].get(tag)]
        dist = Counter(v for v, _ in vals)
        ult = sorted(vals, key=lambda x: x[1].issued_at.isoformat() if x[1].issued_at else "")[-1][1] if vals else None
        out[campo] = {"tag": f"emit/{tag}", "valores": [{"valor": v, "notas": q} for v, q in dist.most_common()],
                      "consistente": len(dist) == 1, "notas_com_campo": len(vals),
                      "referencia": {"numero": ult.number, "chave": ult.access_key} if ult else None}
    cmun = out["municipality_code"]
    if cmun["consistente"]:
        m = dominios.municipio(cmun["valores"][0]["valor"])
        if m:
            out["address.city"] = {"tag": "Anexo A (município IBGE)", "valores": [{"valor": m["nome"], "notas": cmun["notas_com_campo"]}],
                                   "consistente": True, "notas_com_campo": cmun["notas_com_campo"],
                                   "referencia": cmun["referencia"], "origem": "tabela"}
    return {"total_notas": len(notas), "campos": out}


def valores_seguros(dados: dict) -> tuple[dict, dict]:
    """Só campos CONSISTENTES em todas as notas; o cadastro decide se estão vazios (núcleo nunca sobrescreve)."""
    values: dict = {}
    sources: dict = {}
    for campo, info in dados["campos"].items():
        if not info["consistente"] or not info["valores"]:
            continue
        v = info["valores"][0]["valor"]
        if campo == "phone":
            v = "".join(ch for ch in v if ch.isdigit())[:20]
        if campo.startswith("address."):
            values.setdefault("address", {})[campo.split(".", 1)[1]] = v
        else:
            values[campo] = v
        sources[campo] = info["referencia"]
    return values, sources
