"""Investigação do suposto defeito de série no XSD oficial de PRODUÇÃO v1.01-20260209 — reprodução mínima e
tratamento explícito (o arquivo oficial não é alterado e a validação não é desligada).

TSSerieDPS (produção) = "^0{0,4}\\d{1,5}$". Em XML Schema (W3C XSD 1.0 Parte 2, Apêndice F) "^" e "$" são
caracteres literais: nenhuma série numérica passa; só a cadeia "^1$" passaria. O pacote PRODREST v1.01-20260727
corrigiu para "[0-9]{1,4}|[0-8][0-9]{4}"."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest
from lxml import etree

from conftest import ROOT, SP, cnpj

XSD = ROOT / "services/nfse-api/nfse_api/xsd"
PROD, REST = "producao-v1.01-20260209", "prodrest-v1.01-20260727"
NSU = "http://www.sped.fazenda.gov.br/nfse"
MUN = "1505536"


def _schema_serie(pkg: str) -> etree.XMLSchema:
    """Esquema mínimo: inclui o tiposSimples OFICIAL do pacote e declara só <serie> do tipo TSSerieDPS."""
    inc = (XSD / pkg / "tiposSimples_v1.01.xsd").resolve().as_uri()
    w = (f'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns="{NSU}" targetNamespace="{NSU}" '
         f'elementFormDefault="qualified"><xs:include schemaLocation="{inc}"/>'
         '<xs:element name="serie" type="TSSerieDPS"/></xs:schema>')
    return etree.XMLSchema(etree.fromstring(w.encode()))


def _ok(sc: etree.XMLSchema, valor: str) -> bool:
    return sc.validate(etree.fromstring(f'<serie xmlns="{NSU}">{valor}</serie>'.encode()))


def test_reproducao_minima_ancoras_literais_no_xsd_de_producao():
    prod, rest = _schema_serie(PROD), _schema_serie(REST)
    for serie in ("1", "00001", "900", "49999"):
        assert not _ok(prod, serie), serie  # produção recusa TODA série numérica
        assert _ok(rest, serie), serie
    assert _ok(prod, "^1$")  # prova de que ^ e $ são literais no padrão de produção
    assert not _ok(rest, "^1$")
    assert not _ok(rest, "90000") and not _ok(rest, "123456")


def test_arquivos_oficiais_intactos():
    """O tratamento não edita o XSD: os hashes conferem com o SHA256SUMS empacotado."""
    linhas = (XSD / "SHA256SUMS.txt").read_text(encoding="utf-8").split("\n")
    conferidos = 0
    for ln in linhas:
        if not ln.strip().endswith(".xsd"):
            continue  # cabeçalho/comentários do arquivo de somas
        h, nome = ln.split(maxsplit=1)
        p = XSD / nome.strip().lstrip("*")
        assert hashlib.sha256(p.read_bytes()).hexdigest() == h, nome
        conferidos += 1
    assert conferidos >= 20
    assert 'value="^0{0,4}\\d{1,5}$"' in (XSD / PROD / "tiposSimples_v1.01.xsd").read_text(encoding="utf-8")


def _snap(serie_ok: bool = True) -> dict:
    return {
        "company": {"cnpj": cnpj("123456780001"), "municipality_code": MUN, "timezone": "America/Sao_Paulo",
                    "fiscal_profile": {"simples_option": "1", "special_regime": "0", "municipal_registration": "123456"}},
        "customer": {"document": cnpj("445556660001"), "name": "Tomador Teste", "municipality_code": MUN,
                     "postal_code": "68500000", "address": {"street": "Rua A", "number": "10", "district": "Centro"}},
        "competence": "2026-10", "service_date": "2026-10-09", "amount": "1000.00", "deductions": "0.00",
        "service": {"national_code": "010101", "municipal_code": "001", "description": "Serviço", "location_code": MUN,
                    "nbs_code": "101011100"},
        "tax_settings": {"issqn_taxation": "TAXABLE", "iss_retention": "NOT_RETAINED",
                         "approx_tax": {"mode": "VALUES", "federal": "10.00", "state": "0.00", "municipal": "20.00"},
                         "ibs_cbs": {"operation_indicator": "100301", "cst": "000", "class_code": "000001"}},
    }


def test_producao_aceita_serie_valida_e_continua_recusando_o_resto(caplog):
    import logging

    from nfse_api import dps

    caplog.set_level(logging.WARNING, logger="ctrl.nfse.dps")

    agora = datetime(2026, 10, 9, 10, 0, tzinfo=SP)
    m = dps.montar(_snap(), environment="production", serie="1", numero=1, agora=agora)
    assert etree.fromstring(m.xml).findtext(f"{{{NSU}}}infDPS/{{{NSU}}}serie") == "1"
    usos = [r.getMessage() for r in caplog.records if "compatibilidade TSSerieDPS aplicada" in r.getMessage()]
    assert len(usos) == 1 and "serie=1" in usos[0] and m.dps_id in usos[0]  # uso da compatibilidade registrado
    # Sem o tratamento, o XSD de produção recusaria SOMENTE a série (evidência do defeito na DPS completa).
    sc = dps._schema("production")
    assert not sc.validate(etree.ElementTree(etree.fromstring(m.xml)))
    msgs = [e.message for e in sc.error_log]
    assert len(msgs) == 1 and "nfse}serie'" in msgs[0] and dps.SERIE_PADRAO_DEFEITUOSO in msgs[0]
    # Série fora da TSSerieDPS corrigida continua recusada; e qualquer outro erro continua bloqueando.
    doc = etree.fromstring(m.xml)
    doc.find(f"{{{NSU}}}infDPS/{{{NSU}}}serie").text = "90000"
    with pytest.raises(dps.DpsInvalidaXsd):
        dps.validar_xsd(etree.tostring(doc), "production")
    doc = etree.fromstring(m.xml)
    doc.find(f"{{{NSU}}}infDPS/{{{NSU}}}serie").text = "1"
    doc.find(f"{{{NSU}}}infDPS/{{{NSU}}}tpAmb").text = "9"
    with pytest.raises(dps.DpsInvalidaXsd) as e:
        dps.validar_xsd(etree.tostring(doc), "production")
    assert all("nfse}serie'" not in x for x in e.value.erros)
    # Série com caractere não numérico também é recusada (a conferência substituta exige só dígitos).
    doc = etree.fromstring(m.xml)
    doc.find(f"{{{NSU}}}infDPS/{{{NSU}}}serie").text = "1A"
    with pytest.raises(dps.DpsInvalidaXsd):
        dps.validar_xsd(etree.tostring(doc), "production")
    # Restrita segue validando com o próprio pacote, sem tratamento algum (e sem registro de compatibilidade).
    caplog.clear()
    dps.validar_xsd(dps.montar(_snap(), environment="restricted", serie="1", numero=1, agora=agora).xml, "restricted")
    assert not [r for r in caplog.records if "compatibilidade" in r.getMessage()]


def test_eventos_de_producao_nao_usam_tsseriedps():
    for nome in ("pedRegEvento_v1.01.xsd", "tiposEventos_v1.01.xsd", "evento_v1.01.xsd"):
        assert "TSSerieDPS" not in (XSD / PROD / nome).read_text(encoding="utf-8"), nome
