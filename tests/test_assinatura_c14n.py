"""Assinatura XMLDSig: canonicalização C14N 1.0 de sub-elemento com namespace padrão herdado.

Defeito real (Produção Restrita, 09/10/2026, E0714 "Arquivo enviado com erro na assinatura"): a c14n do libxml2 aplicada
ao infDPS NO LUGAR emitia `xmlns=""` espúrios em descendentes (ex.: <CNPJ xmlns="">). O digest e a assinatura
divergiam do que a Sefin (.NET SignedXml) calcula. Os valores esperados aqui foram escritos à mão pela especificação
C14N 1.0 (e conferidos com System.Security.Cryptography.Xml.XmlDsigC14NTransform), não pelo próprio lxml."""
from __future__ import annotations

import base64
import hashlib
from lxml import etree

NS = "http://www.sped.fazenda.gov.br/nfse"


def _dps_teste() -> bytes:
    """Estrutura da DPS com grupos aninhados (prest/regTrib, toma/end/endNac, serv/obra/end) — onde o defeito aparecia."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><DPS xmlns="{NS}" versao="1.01"><infDPS Id="DPS1">'
        "<tpAmb>2</tpAmb><prest><CNPJ>11222333000181</CNPJ><IM>1</IM><regTrib><opSimpNac>1</opSimpNac></regTrib></prest>"
        "<toma><CNPJ>11444777000161</CNPJ><xNome>A &amp; B</xNome><end><endNac><cMun>1505536</cMun><CEP>68515000</CEP>"
        "</endNac><xLgr>Rua Teste</xLgr><nro>S/N</nro><xCpl>KM    1 2</xCpl><xBairro>Bairro Teste</xBairro></end></toma>"
        "</infDPS></DPS>"
    ).encode()


ESPERADO_INFDPS = (
    f'<infDPS xmlns="{NS}" Id="DPS1"><tpAmb>2</tpAmb><prest><CNPJ>11222333000181</CNPJ><IM>1</IM><regTrib>'
    "<opSimpNac>1</opSimpNac></regTrib></prest><toma><CNPJ>11444777000161</CNPJ><xNome>A &amp; B</xNome><end><endNac>"
    "<cMun>1505536</cMun><CEP>68515000</CEP></endNac><xLgr>Rua Teste</xLgr><nro>S/N</nro><xCpl>KM    1 2</xCpl>"
    "<xBairro>Bairro Teste</xBairro></end></toma></infDPS>"
).encode()


def test_c14n_do_infdps_tem_o_namespace_herdado_e_nenhum_xmlns_vazio():
    from nfse_api.signer import _c14n

    root = etree.fromstring(_dps_teste())
    assert _c14n(root[0]) == ESPERADO_INFDPS


def test_assinatura_usa_o_digest_da_forma_canonica_correta():
    from cryptography.hazmat.primitives import serialization

    from test_nfse_connector import make_cert  # noqa: PLC0415
    from nfse_api.signer import DS, sign_enveloped, verify_enveloped

    key, cert = make_cert(cnpj_value="11222333000181")
    assinado = sign_enveloped(_dps_teste(), key, cert.public_bytes(serialization.Encoding.DER))
    assert b'xmlns=""' not in assinado
    root = etree.fromstring(assinado)
    digest = root.find(f"{{{DS}}}Signature/{{{DS}}}SignedInfo/{{{DS}}}Reference/{{{DS}}}DigestValue").text
    assert digest == base64.b64encode(hashlib.sha1(ESPERADO_INFDPS).digest()).decode()  # noqa: S324
    si = root.find(f"{{{DS}}}Signature/{{{DS}}}SignedInfo")
    from nfse_api.signer import _c14n

    assert b'xmlns=""' not in _c14n(si) and _c14n(si).startswith(f'<SignedInfo xmlns="{DS}">'.encode())
    assert verify_enveloped(assinado)
