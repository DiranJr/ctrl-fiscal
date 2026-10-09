"""Fixture com a MESMA estrutura da NFS-e nº 187 (Emissor Web, tomador com endereço nacional), valores fictícios.
Sem dependência de pytest: usada pelos testes e pelo script de navegador do ambiente temporário."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

SP = ZoneInfo("America/Sao_Paulo")
NS = "http://www.sped.fazenda.gov.br/nfse"
MUN = "1505536"


def chave(prest: str, numero: int) -> str:
    from nfse_api.providers.mock_data import mock_access_key

    return mock_access_key(prest, numero, datetime(2026, 10, 8, 13, 46, tzinfo=SP), f"ref-{numero}")


def nota_como_187(prest: str, toma: str, numero: int, *, retencoes: bool = False, sem_endereco: bool = False,
                  ibscbs: bool = False) -> bytes:
    """Estrutura da NFS-e 187 (Emissor Web, ambGer=2): emit/prest com IM, tomador com endereço nacional, cTribNac
    072101 com cNBS, ISS não retido, PIS/COFINS CST 01 sem retenção e totTrib por valores."""
    key = chave(prest, numero)
    dh = "2026-10-08T13:46:54-03:00"
    end = ("" if sem_endereco else
           f"<end><endNac><cMun>{MUN}</cMun><CEP>68515000</CEP></endNac><xLgr>Rua Um</xLgr><nro>123</nro>"
           f"<xCpl>Sala 2</xCpl><xBairro>Centro</xBairro></end>")
    trib_mun = ("<tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>2</tpRetISSQN><pAliq>2.00</pAliq></tribMun>" if retencoes
                else "<tribMun><tribISSQN>1</tribISSQN><tpRetISSQN>1</tpRetISSQN></tribMun>")
    trib_fed = ("<tribFed><piscofins><CST>01</CST><vBCPisCofins>15000.00</vBCPisCofins><pAliqPis>0.65</pAliqPis>"
                "<pAliqCofins>3.00</pAliqCofins><vPis>97.50</vPis><vCofins>450.00</vCofins><tpRetPisCofins>4</tpRetPisCofins>"
                "</piscofins><vRetIRRF>225.00</vRetIRRF><vRetCSLL>150.00</vRetCSLL></tribFed>" if retencoes
                else "<tribFed><piscofins><CST>01</CST><tpRetPisCofins>0</tpRetPisCofins></piscofins></tribFed>")
    valores_nfse = ("<vBC>15000.00</vBC><pAliqAplic>2.00</pAliqAplic><vISSQN>300.00</vISSQN>"
                    + ("<vTotalRet>1222.50</vTotalRet><vLiq>13777.50</vLiq>" if retencoes else "<vLiq>15000.00</vLiq>"))
    # ibscbs: grupo infNFSe/IBSCBS como a Sefin devolve (valores FICTÍCIOS, estrutura do XSD TCRTCIBSCBS) e o grupo
    # IBSCBS da DPS com os códigos informados.
    ibs_nfse = ("<IBSCBS><cLocalidadeIncid>" + MUN + "</cLocalidadeIncid><xLocalidadeIncid>Parauapebas</xLocalidadeIncid>"
                "<valores><vBC>14700.00</vBC><uf><pIBSUF>0.10</pIBSUF><pAliqEfetUF>0.10</pAliqEfetUF></uf>"
                "<mun><pIBSMun>0.00</pIBSMun><pAliqEfetMun>0.00</pAliqEfetMun></mun>"
                "<fed><pCBS>0.90</pCBS><pAliqEfetCBS>0.90</pAliqEfetCBS></fed></valores>"
                "<totCIBS><vTotNF>15147.00</vTotNF><gIBS><vIBSTot>14.70</vIBSTot><gIBSUFTot><vIBSUF>14.70</vIBSUF>"
                "</gIBSUFTot><gIBSMunTot><vIBSMun>0.00</vIBSMun></gIBSMunTot></gIBS><gCBS><vCBS>132.30</vCBS></gCBS>"
                "</totCIBS></IBSCBS>") if ibscbs else ""
    ibs_dps = ("<IBSCBS><finNFSe>0</finNFSe><indFinal>0</indFinal><cIndOp>100301</cIndOp><indDest>0</indDest>"
               "<valores><trib><gIBSCBS><CST>000</CST><cClassTrib>000001</cClassTrib></gIBSCBS></trib></valores>"
               "</IBSCBS>") if ibscbs else ""
    dps_id = "DPS" + MUN + "2" + prest + "00900" + str(numero).zfill(15)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><NFSe xmlns="{NS}" versao="1.01"><infNFSe Id="NFS{key}">'
        f"<xLocEmi>Parauapebas</xLocEmi><xLocPrestacao>Parauapebas</xLocPrestacao><nNFSe>{numero}</nNFSe>"
        f"<cLocIncid>{MUN}</cLocIncid><xLocIncid>Parauapebas</xLocIncid><xTribNac>Serviço de construção (fictício)</xTribNac>"
        f"<xNBS>Serviço (fictício)</xNBS><verAplic>EmissorWeb_1.6.0.0</verAplic><ambGer>2</ambGer><tpEmis>1</tpEmis>"
        f"<procEmi>2</procEmi><cStat>100</cStat><dhProc>{dh}</dhProc><nDFSe>1234567</nDFSe>"
        f"<emit><CNPJ>{prest}</CNPJ><IM>123456789012345</IM><xNome>EMPRESA FICTICIA PRESTADORA</xNome>"
        f"<enderNac><xLgr>Rua Ficticia</xLgr><nro>100</nro><xBairro>Centro</xBairro><cMun>{MUN}</cMun><UF>PA</UF>"
        f"<CEP>68515000</CEP></enderNac><fone>9433330000</fone><email>contato@ficticia.example</email></emit>"
        f"<valores>{valores_nfse}</valores>{ibs_nfse}"
        f'<DPS versao="1.01"><infDPS Id="{dps_id}"><tpAmb>1</tpAmb><dhEmi>{dh}</dhEmi>'
        f"<verAplic>EmissorWeb_1.6.0.0</verAplic><serie>00900</serie><nDPS>{numero}</nDPS><dCompet>2026-10-08</dCompet>"
        f"<tpEmit>1</tpEmit><cLocEmi>{MUN}</cLocEmi>"
        f"<prest><CNPJ>{prest}</CNPJ><IM>123456789012345</IM><fone>9433330000</fone><email>contato@ficticia.example</email>"
        f"<regTrib><opSimpNac>1</opSimpNac><regEspTrib>0</regEspTrib></regTrib></prest>"
        f"<toma><CNPJ>{toma}</CNPJ><xNome>TOMADOR FICTICIO</xNome>{end}</toma>"
        f"<serv><locPrest><cLocPrestacao>{MUN}</cLocPrestacao></locPrest><cServ><cTribNac>072101</cTribNac>"
        f"<xDescServ>Servico de manutencao predial conforme contrato (fixture)</xDescServ><cNBS>101010100</cNBS></cServ></serv>"
        f"<valores><vServPrest><vServ>15000.00</vServ></vServPrest><trib>{trib_mun}{trib_fed}"
        f"<totTrib><vTotTrib><vTotTribFed>1.00</vTotTribFed><vTotTribEst>0.00</vTotTribEst><vTotTribMun>2.00</vTotTribMun>"
        f"</vTotTrib></totTrib></trib></valores>{ibs_dps}</infDPS></DPS></infNFSe></NFSe>"
    ).encode("utf-8")
