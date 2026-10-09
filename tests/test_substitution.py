"""cStat 101 (NFS-e de Substituição Gerada): estado atual derivado de cStat + eventos, vínculos de substituição
nos dois sentidos, código original preservado. Sem XSD: só o parser seguro e a ingestão."""
from __future__ import annotations

from datetime import datetime, timedelta

from conftest import SP, cnpj

F = "/api/fiscal/v1/companies"


def _xmls(prest: str):  # noqa: ANN202
    from nfse_api.providers.mock_data import mock_access_key, nfse_xml

    when = datetime(2026, 9, 10, 9, 0, tzinfo=SP)
    toma = (cnpj("445556660001"), "Tomador")
    k_old = mock_access_key(prest, 10, when, "old")
    k_new = mock_access_key(prest, 11, when + timedelta(days=2), "new")
    old = nfse_xml(key=k_old, number=10, prest=(prest, "Empresa"), toma=toma, interm=None, description="Original",
                   v_serv="100.00", issued_at=when, competence_day="2026-09-01")
    new = nfse_xml(key=k_new, number=11, prest=(prest, "Empresa"), toma=toma, interm=None, description="Substituta",
                   v_serv="90.00", issued_at=when + timedelta(days=2), competence_day="2026-09-01")
    new = new.replace(b"<cStat>100</cStat>", b"<cStat>101</cStat>", 1).replace(
        b"</cLocEmi>", b"</cLocEmi><subst><chSubstda>" + k_old.encode() + b"</chSubstda><cMotivo>99</cMotivo>"
                       b"<xMotivo>Correcao de valores do servico</xMotivo></subst>", 1)
    return k_old, k_new, old, new


def _event(key: str, code: str, body: str, author: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><evento xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">'
        f'<infEvento Id="EVT{key}{code}001"><verAplic>t</verAplic><ambGer>2</ambGer><nSeqEvento>001</nSeqEvento>'
        f'<dhProc>2026-09-12T10:00:00-03:00</dhProc><nDFSe>1</nDFSe><pedRegEvento versao="1.01">'
        f'<infPedReg Id="PRE{key}{code}"><tpAmb>1</tpAmb><verAplic>t</verAplic><dhEvento>2026-09-12T10:00:00-03:00</dhEvento>'
        f'<CNPJAutor>{author}</CNPJAutor><chNFSe>{key}</chNFSe><e{code}>{body}</e{code}></infPedReg></pedRegEvento>'
        '</infEvento></evento>').encode()


def _ingest(stack, company_id: str, company_cnpj: str, raw: bytes):  # noqa: ANN001, ANN202
    from ctrl_fiscal.clients import CompanyInfo
    from ctrl_fiscal.domain.ingest import ingest_xml
    from ctrl_fiscal.nfse_xml import Limits

    comp = CompanyInfo(id=company_id, name="E", cnpj=company_cnpj, timezone="America/Sao_Paulo",
                       environment="mock", municipality_code=None)
    with stack.fiscal_sessions() as s, s.begin():
        return ingest_xml(s, company=comp, environment="mock", raw=raw, source="IMPORT", simulated=False,
                          now=stack.clock.now(), limits=Limits())


def _docs(stack, world) -> dict:  # noqa: ANN001
    r = stack.fiscal.get(f"{F}/{world['a']}/documents", headers=world["ha"], params={"page_size": 50})
    return {d["access_key"]: d for d in r.json()["items"]}


def test_substitution_in_order_note_then_substitute_then_event(stack, world):
    k_old, k_new, old, new = _xmls(world["a_cnpj"])
    _ingest(stack, world["a"], world["a_cnpj"], old)
    _ingest(stack, world["a"], world["a_cnpj"], new)
    d = _docs(stack, world)
    # Antes do evento: a original continua AUTORIZADA (a substituta não a cancela sozinha), mas já vinculada.
    assert d[k_old]["status"] == "AUTHORIZED" and d[k_old]["substitution"]["substituted_by_access_key"] == k_new
    assert d[k_new]["status"] == "AUTHORIZED" and d[k_new]["status_code"] == "101"
    assert d[k_new]["status_code_label"] == "NFS-e de Substituição Gerada"
    assert d[k_new]["substitution"] == {"substitutes_access_key": k_old, "reason_code": "99", "substituted_by_access_key": None}
    _ingest(stack, world["a"], world["a_cnpj"], _event(
        k_old, "105102", f"<xDesc>Cancelamento de NFS-e por Substituicao</xDesc><cMotivo>01</cMotivo><chSubstituta>{k_new}</chSubstituta>",
        world["a_cnpj"]))
    d = _docs(stack, world)
    assert d[k_old]["status"] == "CANCELLED" and d[k_old]["cancellation_event_type"] == "e105102"
    assert d[k_old]["status_code"] == "100"  # código original preservado
    assert d[k_new]["status"] == "AUTHORIZED"
    detail = stack.fiscal.get(f"{F}/{world['a']}/documents/{d[k_old]['id']}", headers=world["ha"]).json()
    assert detail["events"][0]["substitute_access_key"] == k_new


def test_substitution_out_of_order_event_before_note(stack, world):
    k_old, k_new, old, new = _xmls(world["a_cnpj"])
    _ingest(stack, world["a"], world["a_cnpj"], new)
    _ingest(stack, world["a"], world["a_cnpj"], _event(
        k_old, "105102", f"<xDesc>x</xDesc><cMotivo>01</cMotivo><chSubstituta>{k_new}</chSubstituta>", world["a_cnpj"]))
    _ingest(stack, world["a"], world["a_cnpj"], old)
    d = _docs(stack, world)
    assert d[k_old]["status"] == "CANCELLED" and d[k_old]["cancellation_event_type"] == "e105102"
    assert d[k_old]["substitution"]["substituted_by_access_key"] == k_new
    assert d[k_new]["status"] == "AUTHORIZED" and d[k_new]["substitution"]["substitutes_access_key"] == k_old


def test_cstat_101_cancelled_by_own_event_is_not_active(stack, world):
    _k_old, k_new, _old, new = _xmls(world["a_cnpj"])
    _ingest(stack, world["a"], world["a_cnpj"], new)
    _ingest(stack, world["a"], world["a_cnpj"], _event(
        k_new, "101101", "<xDesc>Cancelamento de NFS-e</xDesc><cMotivo>1</cMotivo><xMotivo>teste</xMotivo>", world["a_cnpj"]))
    d = _docs(stack, world)
    assert d[k_new]["status"] == "CANCELLED" and d[k_new]["status_code"] == "101"
    assert d[k_new]["cancellation_event_type"] == "e101101"


def test_unknown_cstat_stays_unknown(stack, world):
    k_old, _k_new, old, _new = _xmls(world["a_cnpj"])
    _ingest(stack, world["a"], world["a_cnpj"], old.replace(b"<cStat>100</cStat>", b"<cStat>199</cStat>", 1))
    d = _docs(stack, world)
    assert d[k_old]["status"] == "UNKNOWN" and d[k_old]["status_code"] == "199" and d[k_old]["status_code_label"] is None
