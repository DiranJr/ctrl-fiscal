"""Emissão a partir de nota salva ("Usar nota como referência") e "Nova NFS-e do zero", com a configuração
tributária de cada OPERAÇÃO revisada e aprovada à parte.

Cobre: leitura do XML oficial preservando serviço, local, obra, valores e tributos; identidade fiscal nova; nota
recebida ou emitida por terceiro recusada; tratamentos tributários diferentes viram variantes sem sobrescrever o perfil
aprovado; reutilização da configuração aprovada por operação equivalente; códigos ausentes/incompletos/fora de
vigência na competência nova como pendências específicas; isolamento entre workspaces; fluxo manual com aprovação;
salvar nunca emite; revisão e agendamento só depois da aprovação. Nada é transmitido a ambiente oficial."""
from __future__ import annotations

import uuid
from datetime import timedelta

from conftest import cnpj
from fixtures_nfse import MUN, NS, chave

F = "/api/fiscal/v1/companies"
C = "/api/core/v1/companies"
TOMA = cnpj("445556660001")  # mesmo documento do cliente já cadastrado em world["customer"]


def nota(prest: str, numero: int, *, toma: str = TOMA, ctn: str = "071601", nbs: str | None = "101032000",
         ibs: tuple | None = ("100301", "000", "000001"), tp_emit: str = "1", dcompet: str = "2026-10-08",
         extras: str = "", valores_extra: str = "", trib_mun: str = "<tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN>",
         trib_fed: str = "<piscofins><CST>01</CST><tpRetPisCofins>0</tpRetPisCofins></piscofins>",
         tot: str = "<vTotTrib><vTotTribFed>1.00</vTotTribFed><vTotTribEst>0.00</vTotTribEst>"
                    "<vTotTribMun>2.00</vTotTribMun></vTotTrib>") -> bytes:
    """NFS-e fictícia com a estrutura do XSD v1.01; ibs=(cIndOp, CST, cClassTrib), None (sem grupo) ou parcial."""
    key = chave(prest, numero)
    dh = "2026-10-08T13:46:54-03:00"
    g_ibs = ""
    if ibs is not None:
        op_, cst, cls = ibs
        g_ibs = ("<IBSCBS><finNFSe>0</finNFSe><indFinal>0</indFinal>" + (f"<cIndOp>{op_}</cIndOp>" if op_ else "")
                 + "<indDest>0</indDest><valores><trib><gIBSCBS>" + (f"<CST>{cst}</CST>" if cst else "")
                 + (f"<cClassTrib>{cls}</cClassTrib>" if cls else "") + "</gIBSCBS></trib></valores></IBSCBS>")
    dps_id = "DPS" + MUN + "2" + prest + "00900" + str(numero).zfill(15)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><NFSe xmlns="{NS}" versao="1.01"><infNFSe Id="NFS{key}">'
        f"<xLocEmi>Parauapebas</xLocEmi><xLocPrestacao>Parauapebas</xLocPrestacao><nNFSe>{numero}</nNFSe>"
        f"<cLocIncid>{MUN}</cLocIncid><xLocIncid>Parauapebas</xLocIncid><xTribNac>Serviço (fictício)</xTribNac>"
        f"<verAplic>EmissorWeb_1.6.0.0</verAplic><ambGer>2</ambGer><tpEmis>1</tpEmis>"
        f"<procEmi>2</procEmi><cStat>100</cStat><dhProc>{dh}</dhProc><nDFSe>1234567</nDFSe>"
        f"<emit><CNPJ>{prest}</CNPJ><xNome>EMPRESA FICTICIA</xNome><enderNac><xLgr>Rua Ficticia</xLgr><nro>100</nro>"
        f"<xBairro>Centro</xBairro><cMun>{MUN}</cMun><UF>PA</UF><CEP>68515000</CEP></enderNac></emit>"
        f"<valores><vLiq>15000.00</vLiq></valores>"
        f'<DPS versao="1.01"><infDPS Id="{dps_id}"><tpAmb>1</tpAmb><dhEmi>{dh}</dhEmi>'
        f"<verAplic>EmissorWeb_1.6.0.0</verAplic><serie>00900</serie><nDPS>{numero}</nDPS><dCompet>{dcompet}</dCompet>"
        f"<tpEmit>{tp_emit}</tpEmit><cLocEmi>{MUN}</cLocEmi>"
        f"<prest><CNPJ>{prest}</CNPJ><regTrib><opSimpNac>1</opSimpNac><regEspTrib>0</regEspTrib></regTrib></prest>"
        f"<toma><CNPJ>{toma}</CNPJ><xNome>TOMADOR FICTICIO</xNome></toma>"
        f"<serv><locPrest><cLocPrestacao>{MUN}</cLocPrestacao></locPrest><cServ><cTribNac>{ctn}</cTribNac>"
        f"<xDescServ>Servico fixture {numero}</xDescServ>" + (f"<cNBS>{nbs}</cNBS>" if nbs else "") + f"</cServ>{extras}</serv>"
        f"<valores><vServPrest><vServ>15000.00</vServ></vServPrest>{valores_extra}<trib><tribMun>{trib_mun}</tribMun>"
        f"<tribFed>{trib_fed}</tribFed><totTrib>{tot}</totTrib></trib></valores>{g_ibs}</infDPS></DPS></infNFSe></NFSe>"
    ).encode("utf-8")


def importar(stack, cid: str, h, xml: bytes, nome: str) -> None:  # noqa: ANN001
    r = stack.fiscal.post(f"{F}/{cid}/documents/import", headers=h, files=[("files", (nome, xml, "application/xml"))])
    assert r.status_code == 202, r.text
    w = stack.worker()
    while w.run_jobs():
        pass


def doc(stack, cid: str, h, numero: int, direction: str = "ISSUED") -> dict:  # noqa: ANN001
    items = stack.fiscal.get(f"{F}/{cid}/documents", headers=h, params={"direction": direction, "environment": "mock",
                                                                       "page_size": 100}).json()["items"]
    return next(d for d in items if d["number"] == str(numero))


def referencia(stack, world, numero: int, cid: str | None = None, h=None):  # noqa: ANN001, ANN201
    cid, h = cid or world["a"], h or world["ha"]
    return stack.fiscal.post(f"{F}/{cid}/drafts/from-document", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                             json={"source_document_id": doc(stack, cid, h, numero)["id"]})


def perfis(stack, cid: str, h) -> dict:  # noqa: ANN001
    return {p["id"]: p for p in stack.core.get(f"{C}/{cid}/service-profiles", headers=h).json()}


def aprovar(stack, cid: str, h, pid: str) -> dict:  # noqa: ANN001
    p = perfis(stack, cid, h)[pid]
    r = stack.core.post(f"{C}/{cid}/service-profiles/{pid}/approve", headers=h,
                        json={"expected_version": p["version"], "valid_from": "2026-10-01"})
    assert r.status_code == 200, r.text
    return r.json()


def validar(stack, cid: str, h, did: str) -> dict:  # noqa: ANN001
    v = stack.fiscal.post(f"{F}/{cid}/drafts/{did}/validate", headers=h)
    assert v.status_code == 200, v.text
    return {i["code"]: i for i in v.json()["issues"]}


def registrar(stack, cid: str, h, d: dict) -> dict:  # noqa: ANN001
    r = stack.fiscal.post(f"{F}/{cid}/drafts/{d['id']}/operation-config", headers=h,
                          json={"expected_version": d["version"]})
    assert r.status_code == 200, r.text
    return r.json()


def agendar(stack, cid: str, h, d: dict):  # noqa: ANN001, ANN201
    return stack.fiscal.post(f"{F}/{cid}/batches", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                             json={"drafts": [{"id": d["id"], "expected_version": d["version"]}],
                                   "scheduled_at": (stack.clock.now() + timedelta(minutes=1)).isoformat(),
                                   "timezone": "America/Sao_Paulo"})


def patch(stack, cid: str, h, d: dict, **campos) -> dict:  # noqa: ANN001, ANN003
    r = stack.fiscal.patch(f"{F}/{cid}/drafts/{d['id']}", headers=h, json={"expected_version": d["version"], **campos})
    assert r.status_code == 200, r.text
    return r.json()


def test_referencia_le_xml_oficial_preserva_particularidades_e_descarta_identidade(stack, world):
    xml = nota(world["a_cnpj"], 501, extras="<obra><cObra>CNO000123</cObra></obra><infoCompl><xPed>PED-77</xPed>"
                                           "</infoCompl>",
               valores_extra="<vDescCondIncond><vDescIncond>100.00</vDescIncond></vDescCondIncond>",
               trib_mun="<tribISSQN>1</tribISSQN><tpRetISSQN>2</tpRetISSQN><pAliq>3.00</pAliq>",
               trib_fed="<piscofins><CST>01</CST><vBCPisCofins>15000.00</vBCPisCofins><pAliqPis>0.65</pAliqPis>"
                        "<pAliqCofins>3.00</pAliqCofins><vPis>97.50</vPis><vCofins>450.00</vCofins>"
                        "<tpRetPisCofins>4</tpRetPisCofins></piscofins><vRetIRRF>225.00</vRetIRRF>"
                        "<vRetCSLL>150.00</vRetCSLL>",
               tot="<pTotTrib><pTotTribFed>13.45</pTotTribFed><pTotTribEst>0.00</pTotTribEst>"
                   "<pTotTribMun>3.00</pTotTribMun></pTotTrib>")
    importar(stack, world["a"], world["ha"], xml, "n501.xml")
    r = referencia(stack, world, 501)
    assert r.status_code == 201, r.text
    d = r.json()
    for proibido in ("number", "access_key", "dps_id", "protocol", "signature", "events", "status_code"):
        assert proibido not in d
    assert d["status"] == "DRAFT" and d["competence"] == "2026-10" and d["service_date"] is None
    s, t = d["service"], d["tax_settings"]
    assert (s["national_code"], s["nbs_code"], s["location_code"], s["obra_code"]) == ("071601", "101032000", MUN,
                                                                                      "CNO000123")
    assert s["additional_info"] is None  # infoCompl (pedido) é da nota de origem
    assert d["amount"] == "15000.00" and d["unconditional_discount"] == "100.00"
    assert t["issqn_taxation"] == "TAXABLE" and t["iss_retention"] == "RETAINED_BY_TOMADOR" and t["iss_rate"] == "3.00"
    assert (t["pis_cofins_cst"], t["pis_cofins_base"], t["pis_rate"], t["cofins_rate"], t["pis_amount"],
            t["cofins_amount"], t["pis_cofins_retention"]) == ("01", "15000.00", "0.65", "3.00", "97.50", "450.00", "4")
    assert t["irrf_withheld"] == "225.00" and t["csll_withheld"] == "150.00"
    assert t["approx_tax"] == {"mode": "PERCENT", "federal": "13.45", "state": "0.00", "municipal": "3.00"}
    assert t["ibs_cbs"]["operation_indicator"] == "100301" and t["ibs_cbs"]["class_code"] == "000001"
    o = d["origin"]
    assert o["kind"] == "document" and o["document"]["number"] == "501"
    assert o["fields"]["tax_settings.iss_rate"] == {"origem": "xml", "tag": "trib/tribMun/pAliq"}
    assert o["fields"]["tax_settings.ibs_cbs"]["confirmar"] is True
    assert any(n["campo"] == "serv/infoCompl" for n in o["not_copied"])
    # Configuração da operação: variante PENDENTE (escolher a nota não aprova o tratamento tributário).
    op_ = o["operation"]
    assert op_["resolution"] == "CREATED_VARIANT" and op_["profile_status"] == "PENDING_REVIEW"
    assert d["service_profile_id"] == op_["profile_id"]
    p = perfis(stack, world["a"], world["ha"])[op_["profile_id"]]
    assert p["field_sources"]["national_code"]["origem"] == "xml"
    assert p["field_sources"]["ibs_cbs"]["origem"] == "xml_historico"
    assert p["evidence"]["origem"]["document"]["number"] == "501"
    v = validar(stack, world["a"], world["ha"], d["id"])
    assert v["SERVICE_PROFILE_NOT_APPROVED"]["severity"] == "error"
    assert v["REFERENCE_NEW_DATE"]["field"] == "service_date"
    assert v["REFERENCE_REVIEW_RETENTIONS"]["severity"] == "info"
    r = agendar(stack, world["a"], world["ha"], d)
    assert r.status_code == 422 and "SERVICE_PROFILE_NOT_APPROVED" in r.text
    # Nota original intacta.
    assert stack.fiscal.get(f"{F}/{world['a']}/documents/{o['document']['id']}",
                            headers=world["ha"]).json()["number"] == "501"


def test_tratamentos_diferentes_viram_variantes_sem_sobrescrever_o_aprovado(stack, world):
    a, h = world["a"], world["ha"]
    importar(stack, a, h, nota(world["a_cnpj"], 601), "n601.xml")
    importar(stack, a, h, nota(world["a_cnpj"], 602), "n602.xml")
    importar(stack, a, h, nota(world["a_cnpj"], 603, ibs=("100301", "200", "200052")), "n603.xml")
    d1 = referencia(stack, world, 601).json()
    va = d1["origin"]["operation"]["profile_id"]
    aprovado = aprovar(stack, a, h, va)
    # Operação equivalente: reutiliza a configuração APROVADA (sem criar outra).
    d2 = referencia(stack, world, 602).json()
    assert d2["origin"]["operation"]["resolution"] == "REUSED_APPROVED" and d2["service_profile_id"] == va
    assert d2["service_profile_version"] == aprovado["version"]
    antes = len(perfis(stack, a, h))
    # Tratamento diferente: nova variante pendente; o aprovado não muda e o rascunho mantém os dados da nota.
    d3 = referencia(stack, world, 603).json()
    op3 = d3["origin"]["operation"]
    assert op3["resolution"] == "CREATED_VARIANT" and op3["profile_id"] != va
    assert op3["divergent_from"][0]["id"] == va
    assert set(op3["divergent_from"][0]["fields"]) == {"CST do IBS/CBS", "cClassTrib"}
    assert d3["tax_settings"]["ibs_cbs"]["class_code"] == "200052"
    ps_ = perfis(stack, a, h)
    assert len(ps_) == antes + 1
    assert ps_[va]["status"] == "APPROVED" and ps_[va]["version"] == aprovado["version"]
    assert ps_[va]["ibs_cbs"]["class_code"] == "000001"
    v = validar(stack, a, h, d3["id"])
    assert v["SERVICE_PROFILE_NOT_APPROVED"]["severity"] == "error" and "OPERATION_VARIANT" in v
    # Rascunho ligado ao aprovado e depois editado para outra classificação: divergência apontada, dados mantidos;
    # registrar a operação vincula a variante pendente já existente (sem duplicar).
    d2 = patch(stack, a, h, d2, tax_settings={"ibs_cbs": {"operation_indicator": "100301", "cst": "200",
                                                          "class_code": "200052"}})
    v = validar(stack, a, h, d2["id"])
    assert "SERVICE_PROFILE_DIVERGENT" in v and d2["tax_settings"]["ibs_cbs"]["class_code"] == "200052"
    d2 = registrar(stack, a, h, d2)
    assert d2["origin"]["operation"]["resolution"] == "LINKED_PENDING"
    assert d2["service_profile_id"] == op3["profile_id"] and len(perfis(stack, a, h)) == antes + 1
    assert perfis(stack, a, h)[va]["version"] == aprovado["version"]


def test_codigos_ausentes_incompletos_e_competencia_nova(stack, world, monkeypatch):
    a, h = world["a"], world["ha"]
    # Nota de setembro, antes da obrigatoriedade do IBS/CBS, sem cNBS e sem o grupo IBS/CBS.
    importar(stack, a, h, nota(world["a_cnpj"], 701, nbs=None, ibs=None, dcompet="2026-09-10"), "n701.xml")
    d = referencia(stack, world, 701).json()
    assert d["competence"] == "2026-10"  # competência NOVA (mês atual), não a da nota de origem
    pend = {p["code"]: p for p in d["origin"]["pending"]}
    assert pend["REFERENCE_IBS_CBS_ABSENT"]["field"] == "tax_settings.ibs_cbs" and "2026-10" in \
        pend["REFERENCE_IBS_CBS_ABSENT"]["message"]
    assert pend["REFERENCE_FIELD_ABSENT"]["field"] == "service.nbs_code"
    assert d["tax_settings"]["ibs_cbs"] is None and d["service"]["nbs_code"] is None  # nada presumido
    d = patch(stack, a, h, d, service_date="2026-10-15")
    v = validar(stack, a, h, d["id"])
    assert {"REFERENCE_IBS_CBS_ABSENT", "IBS_CBS_REQUIRED", "REFERENCE_FIELD_ABSENT"} <= set(v)
    # A configuração criada fica com o que falta como pendência bloqueante (não pode ser aprovada assim).
    p = perfis(stack, a, h)[d["service_profile_id"]]
    assert p["status"] == "PENDING_REVIEW" and not p["can_approve"]
    assert {i["field"] for i in p["pending"] if i["severity"] == "error"} >= {"ibs_cbs"}
    # Preenchidos pelo usuário: a pendência da referência some; a origem do campo passa a ser "usuário".
    d = patch(stack, a, h, d, service={"description": d["service"]["description"], "nbs_code": "101032000"},
              tax_settings={"ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}})
    v = validar(stack, a, h, d["id"])
    assert not {"REFERENCE_IBS_CBS_ABSENT", "REFERENCE_FIELD_ABSENT", "IBS_CBS_REQUIRED"} & set(v)
    d = registrar(stack, a, h, d)
    p = perfis(stack, a, h)[d["service_profile_id"]]
    assert p["field_sources"]["ibs_cbs"]["origem"] == "usuario" and p["field_sources"]["national_code"]["origem"] == "xml"
    # Grupo IBS/CBS incompleto na origem (sem cClassTrib): não copiado, com o que a origem trazia.
    importar(stack, a, h, nota(world["a_cnpj"], 702, ibs=("100301", None, None)), "n702.xml")
    d2 = referencia(stack, world, 702).json()
    p2 = {p["code"]: p for p in d2["origin"]["pending"]}["REFERENCE_IBS_CBS_NOT_COPIED"]
    assert p2["severity"] == "error" and "cIndOp 100301" in p2["message"] and d2["tax_settings"]["ibs_cbs"] is None
    # Classificação válida na nota de origem, mas fora de vigência na competência da nova emissão.
    from ctrl_fiscal.domain import ibscbs

    original = ibscbs.classificacao
    monkeypatch.setattr(ibscbs, "classificacao", lambda c, em: None if c == "000001" and em and str(em) >= "2026-10-01"
                        else original(c, em))
    importar(stack, a, h, nota(world["a_cnpj"], 703), "n703.xml")
    d3 = referencia(stack, world, 703).json()
    v = validar(stack, a, h, d3["id"])
    assert v["REFERENCE_CLASS_NOT_VALID_AT_COMPETENCE"]["severity"] == "error"


def test_referencia_so_de_emissao_propria_e_isolada_entre_workspaces(stack, world):
    a, h, b, hb = world["a"], world["ha"], world["b"], world["hb"]
    # Nota RECEBIDA pela empresa A (A é a tomadora): não fornece identidade nem enquadramento do prestador.
    importar(stack, a, h, nota(cnpj("334455660001"), 801, toma=world["a_cnpj"]), "n801.xml")
    r = stack.fiscal.post(f"{F}/{a}/drafts/from-document", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                          json={"source_document_id": doc(stack, a, h, 801, "RECEIVED")["id"]})
    assert r.status_code == 422 and r.json()["code"] == "SOURCE_NOT_ISSUED"
    # Emitida com o CNPJ de A como prestador, mas por outro emitente (tpEmit=2): também recusada.
    importar(stack, a, h, nota(world["a_cnpj"], 802, tp_emit="2"), "n802.xml")
    r = referencia(stack, world, 802)
    assert r.status_code == 422 and r.json()["code"] == "SOURCE_NOT_OWN_ISSUE", r.text
    # Configuração aprovada em A não é reutilizada por B; B não vincula perfil de A nem usa nota de A.
    importar(stack, a, h, nota(world["a_cnpj"], 803), "n803.xml")
    da = referencia(stack, world, 803).json()
    aprovar(stack, a, h, da["service_profile_id"])
    rb = stack.fiscal.post(f"{F}/{b}/drafts/from-document", headers={**hb, "Idempotency-Key": uuid.uuid4().hex},
                           json={"source_document_id": da["origin"]["document"]["id"]})
    assert rb.status_code == 404
    cb = stack.core.post(f"{C}/{b}/customers", headers=hb, json={"name": "Cliente B", "document_type": "CNPJ",
                                                                  "document": cnpj("556600110001")}).json()
    db_ = stack.fiscal.post(f"{F}/{b}/drafts", headers=hb, json={
        "customer_id": cb["id"], "competence": "2026-10", "amount": "100.00",
        "service": {"national_code": "071601", "nbs_code": "101032000", "description": "Serviço B"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01",
                         "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}}}).json()
    assert stack.fiscal.patch(f"{F}/{b}/drafts/{db_['id']}", headers=hb, json={
        "expected_version": db_["version"], "service_profile_id": da["service_profile_id"]}).status_code == 422
    db_ = registrar(stack, b, hb, db_)
    assert db_["origin"]["operation"]["resolution"] == "CREATED_VARIANT"
    assert db_["service_profile_id"] != da["service_profile_id"] and db_["origin"]["operation"]["divergent_from"] == []
    assert da["service_profile_id"] not in perfis(stack, b, hb)


def test_referencia_entre_ambientes_so_de_producao_para_producao_restrita(stack, world):
    """Nota real de produção pode ser referência de um TESTE em Produção Restrita; o inverso, nunca."""
    from core_api.models import Company
    from ctrl_fiscal.models import Document

    a, h = world["a"], world["ha"]
    importar(stack, a, h, nota(world["a_cnpj"], 901), "n901.xml")
    did = doc(stack, a, h, 901)["id"]

    def env_empresa(env: str) -> None:
        with stack.core_sessions() as s:
            s.get(Company, a).nfse_environment = env
            s.commit()

    def env_nota(env: str) -> None:
        with stack.fiscal_sessions() as s:
            s.get(Document, did).environment = env
            s.commit()

    def ref():  # noqa: ANN202
        return stack.fiscal.post(f"{F}/{a}/drafts/from-document", headers={**h, "Idempotency-Key": uuid.uuid4().hex},
                                 json={"source_document_id": did})

    env_nota("production")
    env_empresa("restricted")
    r = ref()
    assert r.status_code == 201, r.text
    assert r.json()["environment"] == "restricted"
    assert "REFERENCE_FROM_PRODUCTION" in {p["code"] for p in r.json()["origin"]["pending"]}
    env_nota("restricted")
    env_empresa("production")
    r = ref()
    assert r.status_code == 422 and r.json()["code"] == "SOURCE_OTHER_ENVIRONMENT"


def test_nova_nfse_do_zero_configura_aprova_revisa_e_so_emite_ao_agendar(stack, world):
    a, h = world["a"], world["ha"]
    corpo = {"customer_id": world["customer"], "competence": "2026-10", "service_date": "2026-10-15",
             "amount": "1000.00",
             "service": {"national_code": "071601", "nbs_code": "101032000", "location_code": MUN,
                         "description": "Serviço preenchido do zero"},
             "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED", "pis_cofins_cst": "01",
                              "approx_tax": {"mode": "NOT_INFORMED"},
                              "ibs_cbs": {"operation_indicator": "100301", "cst": "200", "class_code": "200052"}}}
    d = stack.fiscal.post(f"{F}/{a}/drafts", headers=h, json=corpo).json()
    assert d["origin"] == {"kind": "manual"} and d["service_profile_id"] is None
    d = patch(stack, a, h, d, amount="1200.00")
    d = registrar(stack, a, h, d)  # registra a configuração desta operação para revisão
    op_ = d["origin"]["operation"]
    assert op_["resolution"] == "CREATED_VARIANT" and op_["profile_status"] == "PENDING_REVIEW"
    p = perfis(stack, a, h)[op_["profile_id"]]
    assert p["field_sources"]["ibs_cbs"]["origem"] == "usuario" and "informado_por" in p["field_sources"]["ibs_cbs"]
    assert p["evidence"]["origem"]["kind"] == "manual" and p["source"] == "Operação preenchida manualmente"
    # Salvar e registrar NUNCA emitem: nada agendado, nada enviado.
    assert stack.fiscal.get(f"{F}/{a}/batches", headers=h).json()["total"] == 0
    r = agendar(stack, a, h, d)
    assert r.status_code == 422 and "SERVICE_PROFILE_NOT_APPROVED" in r.text
    aprovado = aprovar(stack, a, h, op_["profile_id"])
    hist = stack.core.get(f"{C}/{a}/service-profiles/{op_['profile_id']}/versions", headers=h).json()
    assert [x["action"] for x in hist][:2] == ["approved", "variant_prepared"]
    assert hist[0]["snapshot"]["field_sources"]["ibs_cbs"]["confirmado_por"]
    # A aprovação muda a versão: o rascunho precisa ser conferido de novo antes de agendar.
    v = validar(stack, a, h, d["id"])
    assert "SERVICE_PROFILE_CHANGED" in v
    d = registrar(stack, a, h, d)
    assert d["origin"]["operation"]["resolution"] == "REUSED_APPROVED"
    assert d["service_profile_version"] == aprovado["version"]
    v = validar(stack, a, h, d["id"])
    assert not [k for k, i in v.items() if i["severity"] == "error"], v
    r = agendar(stack, a, h, d)
    assert r.status_code == 202, r.text
    stack.clock.advance(minutes=2)
    w = stack.worker()
    while w.run_emissions():
        pass
    item = stack.fiscal.get(f"{F}/{a}/batches/{r.json()['batch_id']}", headers=h).json()["items"][0]
    assert item["status"] == "AUTHORIZED" and item["simulated"] is True
