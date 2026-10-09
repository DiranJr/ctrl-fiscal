"""Gerador de XML SIMULADO no leiaute NFS-e nacional (para o ambiente mock).

Os documentos são fictícios (xLocEmi = "SIMULACAO LOCAL", município 0000000)
e só existem no ambiente mock. A estrutura segue os nomes de elementos do XSD
v1.01 verificado, mas o XML não é validado contra o XSD completo.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

NS = "http://www.sped.fazenda.gov.br/nfse"
MOCK_MUN = "0000000"


def cnpj_dv(base12: str) -> str:
    def calc(chars: str, weights: list[int]) -> int:
        total = sum((ord(c) - 48) * w for c, w in zip(chars, weights))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    d1 = calc(base12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = calc(base12 + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return base12 + f"{d1}{d2}"


def _mod11(digits: str) -> str:
    total, weight = 0, 2
    for ch in reversed(digits):
        total += (ord(ch) - 48) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def mock_access_key(prest_cnpj: str, number: int, when: datetime, seed: str) -> str:
    cnum = str(int(hashlib.sha256(seed.encode()).hexdigest(), 16))[-9:]
    body = MOCK_MUN + "2" + "2" + prest_cnpj.zfill(14) + str(number).zfill(13) + when.strftime("%y%m") + cnum
    return body + _mod11(body)


def _party(tag: str, doc: str | None, name: str) -> str:
    ident = f"<CNPJ>{doc}</CNPJ>" if doc else "<cNaoNIF>0</cNaoNIF>"
    return f"<{tag}>{ident}<xNome>{escape(name)}</xNome></{tag}>"


def nfse_xml(
    *, key: str, number: int, prest: tuple[str, str], toma: tuple[str, str] | None, interm: tuple[str, str] | None,
    description: str, v_serv: str, issued_at: datetime, competence_day: str, tp_ret_iss: str = "1",
    v_issqn: str | None = None, fed: dict | None = None, v_total_ret: str | None = None, v_liq: str | None = None,
) -> bytes:
    fed = fed or {}
    dh = issued_at.strftime("%Y-%m-%dT%H:%M:%S-03:00")
    fed_xml = ""
    if fed:
        pc = ""
        if "tpRetPisCofins" in fed:
            pc = "<piscofins><CST>01</CST>"
            for k in ("vPis", "vCofins"):
                if k in fed:
                    pc += f"<{k}>{fed[k]}</{k}>"
            pc += f"<tpRetPisCofins>{fed['tpRetPisCofins']}</tpRetPisCofins></piscofins>"
        rets = "".join(f"<{k}>{fed[k]}</{k}>" for k in ("vRetCP", "vRetIRRF", "vRetCSLL") if k in fed)
        fed_xml = f"<tribFed>{pc}{rets}</tribFed>"
    valores_nfse = ""
    if v_issqn:
        valores_nfse += f"<vISSQN>{v_issqn}</vISSQN>"
    if v_total_ret:
        valores_nfse += f"<vTotalRet>{v_total_ret}</vTotalRet>"
    valores_nfse += f"<vLiq>{v_liq or v_serv}</vLiq>"
    dps_id = "DPS" + MOCK_MUN + "2" + prest[0].zfill(14) + "00001" + str(number).zfill(15)
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<NFSe xmlns="{NS}" versao="1.01"><infNFSe Id="NFS{key}">'
        f"<xLocEmi>SIMULACAO LOCAL</xLocEmi><xLocPrestacao>SIMULACAO LOCAL</xLocPrestacao>"
        f"<nNFSe>{number}</nNFSe><xTribNac>Simulado</xTribNac><verAplic>mock-0.1</verAplic>"
        f"<ambGer>2</ambGer><tpEmis>1</tpEmis><cStat>100</cStat><dhProc>{dh}</dhProc><nDFSe>{number}</nDFSe>"
        f"<emit><CNPJ>{prest[0]}</CNPJ><xNome>{escape(prest[1])}</xNome>"
        f"<enderNac><xLgr>Rua Simulada</xLgr><nro>0</nro><xBairro>Centro</xBairro><cMun>{MOCK_MUN}</cMun><UF>SP</UF><CEP>00000000</CEP></enderNac></emit>"
        f"<valores>{valores_nfse}</valores>"
        f'<DPS versao="1.01"><infDPS Id="{dps_id}"><tpAmb>2</tpAmb><dhEmi>{dh}</dhEmi><verAplic>mock-0.1</verAplic>'
        f"<serie>1</serie><nDPS>{number}</nDPS><dCompet>{competence_day}</dCompet><tpEmit>1</tpEmit><cLocEmi>{MOCK_MUN}</cLocEmi>"
        f"<prest><CNPJ>{prest[0]}</CNPJ><regTrib><opSimpNac>1</opSimpNac><regEspTrib>0</regEspTrib></regTrib></prest>"
        f"{_party('toma', *toma) if toma else ''}{_party('interm', *interm) if interm else ''}"
        f"<serv><locPrest><cLocPrestacao>{MOCK_MUN}</cLocPrestacao></locPrest><cServ><cTribNac>010101</cTribNac>"
        f"<xDescServ>{escape(description)}</xDescServ></cServ></serv>"
        f"<valores><vServPrest><vServ>{v_serv}</vServ></vServPrest><trib><tribMun><tribISSQN>1</tribISSQN>"
        f"<tpRetISSQN>{tp_ret_iss}</tpRetISSQN></tribMun>{fed_xml}<totTrib><indTotTrib>0</indTotTrib></totTrib></trib></valores>"
        f"</infDPS></DPS></infNFSe></NFSe>"
    )
    return xml.encode("utf-8")


def cancel_event_xml(key: str, author_cnpj: str, when: datetime) -> bytes:
    dh = when.strftime("%Y-%m-%dT%H:%M:%S-03:00")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<evento xmlns="{NS}" versao="1.01"><infEvento Id="EVT{key}101101001"><verAplic>mock-0.1</verAplic>'
        f"<ambGer>2</ambGer><nSeqEvento>001</nSeqEvento><dhProc>{dh}</dhProc><nDFSe>1</nDFSe>"
        f'<pedRegEvento versao="1.01"><infPedReg Id="PRE{key}101101"><tpAmb>2</tpAmb><verAplic>mock-0.1</verAplic>'
        f"<dhEvento>{dh}</dhEvento><CNPJAutor>{author_cnpj}</CNPJAutor><chNFSe>{key}</chNFSe>"
        f"<e101101><xDesc>Cancelamento de NFS-e</xDesc><cMotivo>1</cMotivo><xMotivo>Simulacao local</xMotivo></e101101>"
        f"</infPedReg></pedRegEvento></infEvento></evento>"
    ).encode("utf-8")


def demo_dataset(company_cnpj: str) -> list[dict]:
    """Sequência determinística de NSUs para a empresa (SIMULADA)."""
    base = datetime(2026, 9, 10, 9, 0, tzinfo=timezone(timedelta(hours=-3)))
    sup_a = (cnpj_dv("100000000001"), "Fornecedor Simulado A")
    sup_b = (cnpj_dv("200000000001"), "Fornecedor Simulado B")
    sup_d = (cnpj_dv("300000000001"), "Fornecedor Simulado D")
    other = (cnpj_dv("400000000001"), "Terceiro Simulado")
    customer = (cnpj_dv("445556660001"), "Cliente Demonstração 1 (LOCAL)")
    branch = (cnpj_dv(company_cnpj[:8] + "9999"), "Filial mesma raiz (SIMULADA)")
    me = (company_cnpj, "Empresa (SIMULADA)")
    k1 = mock_access_key(sup_a[0], 101, base, company_cnpj + "1")
    k2 = mock_access_key(sup_b[0], 202, base, company_cnpj + "2")
    k3 = mock_access_key(me[0], 303, base, company_cnpj + "3")
    k4 = mock_access_key(sup_d[0], 404, base, company_cnpj + "4")
    k5 = mock_access_key(other[0], 505, base, company_cnpj + "5")
    items = [
        # 1. Tomado com ISS retido, IRRF, INSS e PIS/COFINS/CSLL retidos (tpRetPisCofins=3).
        ("NFSE", k1, nfse_xml(key=k1, number=101, prest=sup_a, toma=me, interm=None,
                              description="Consultoria simulada", v_serv="10000.00", issued_at=base,
                              competence_day="2026-09-01", tp_ret_iss="2", v_issqn="500.00",
                              fed={"tpRetPisCofins": "3", "vPis": "65.00", "vCofins": "300.00", "vRetCP": "1100.00",
                                   "vRetIRRF": "150.00", "vRetCSLL": "100.00"},
                              v_total_ret="1850.00", v_liq="7785.00")),
        # 2. Tomado sem grupo tribFed: retenções federais ausentes (null, não zero).
        ("NFSE", k2, nfse_xml(key=k2, number=202, prest=sup_b, toma=me, interm=None,
                              description="Manutenção simulada", v_serv="2500.00", issued_at=base + timedelta(days=1),
                              competence_day="2026-09-01")),
        # 3. Emitido pela empresa (prestadora) para cliente de demonstração.
        ("NFSE", k3, nfse_xml(key=k3, number=303, prest=me, toma=customer, interm=None,
                              description="Serviço mensal simulado", v_serv="1000.00", issued_at=base + timedelta(days=2),
                              competence_day="2026-09-01")),
        # 4. Empresa como intermediária: fora da listagem (mantido no bruto).
        ("NFSE", k4, nfse_xml(key=k4, number=404, prest=sup_d, toma=other, interm=me,
                              description="Intermediação simulada", v_serv="800.00", issued_at=base + timedelta(days=3),
                              competence_day="2026-09-01")),
        # 5. Filial com mesma raiz como tomadora: NÃO pode ser misturada.
        ("NFSE", k5, nfse_xml(key=k5, number=505, prest=other, toma=branch, interm=None,
                              description="Serviço para filial simulada", v_serv="300.00", issued_at=base + timedelta(days=4),
                              competence_day="2026-09-01")),
        # 6. Evento de cancelamento da nota 2.
        ("EVENT", k2, cancel_event_xml(k2, sup_b[0], base + timedelta(days=5))),
        # 7. XML truncado: vai para quarentena durável.
        ("NFSE", None, b'<?xml version="1.0"?><NFSe xmlns="' + NS.encode() + b'"><infNFSe Id="NFS'),
    ]
    out = []
    for i, (kind, key, xml) in enumerate(items, start=1):
        out.append({
            "nsu": i, "access_key": key, "doc_kind": kind,
            "xml_gzip_b64": base64.b64encode(gzip.compress(xml)).decode(),
            "generated_at": (base + timedelta(days=i)).isoformat(),
        })
    return out
