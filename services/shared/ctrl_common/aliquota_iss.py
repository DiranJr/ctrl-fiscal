"""Alíquota do ISSQN: pAliq da DPS × alíquota de REFERÊNCIA interna — mesma regra na fiscal-api e na nfse-api.

Dois campos distintos em tax_settings (nenhum converte o outro):
- `iss_rate`: pAliq. Vai na DPS exatamente como informado (valores/trib/tribMun/pAliq).
- `iss_reference_rate`: alíquota de REFERÊNCIA. Serve só para estimar ISS, retenção e líquido antes da autorização;
  nunca vai na DPS.

Se o pAliq é permitido, obrigatório ou proibido depende do Anexo I (RN DPS_NFS-e linhas 502–513): regime do
prestador (opSimpNac, regApTribSN, regEspTrib), tribISSQN, retenção e situação do CONVÊNIO do município de incidência
no ambiente (Parametrização). Sem a situação do convênio, o resultado é UNKNOWN: nada é presumido.
Benefício municipal (E0612) não é enviado pela plataforma e por isso não é avaliado aqui.
"""
from __future__ import annotations

import re

PROHIBITED, REQUIRED, OPTIONAL, UNKNOWN = "PROHIBITED", "REQUIRED", "OPTIONAL", "UNKNOWN"
SEM_ALIQUOTA_TRIB = ("IMMUNE", "EXPORT", "NOT_INCIDENT")  # tribISSQN 2, 3 e 4 (E0602)
ISS_RETIDO = ("RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY")
# Regras que dependem do convênio: sem a Parametrização, o status fica UNKNOWN com estes códigos possíveis.
DEPENDEM_DO_CONVENIO = {"1": ("E0617", "E0619"), "3:1:ret": ("E0621", "E0628"), "3:1": ("E0625", "E0631"),
                        "3:2": ("E0635", "E0640"), "3:3": ("E0635", "E0640")}


def informado(v: object) -> bool:
    return v not in (None, "")


def situacao_paliq(perfil: dict | None, tax: dict | None, convenio_ativo: bool | None) -> dict:
    """{status, rule, possible_rules}: PROHIBITED/REQUIRED/OPTIONAL/UNKNOWN pelo Anexo I.
    convenio_ativo: True/False pela Parametrização; None = não consultado ou indisponível."""
    perfil, tax = perfil or {}, tax or {}
    op, reg_sn, esp = perfil.get("simples_option"), perfil.get("simples_regime"), perfil.get("special_regime")
    if op == "2":
        return {"status": PROHIBITED, "rule": "E0600", "possible_rules": []}
    if tax.get("issqn_taxation") in SEM_ALIQUOTA_TRIB:
        return {"status": PROHIBITED, "rule": "E0602", "possible_rules": []}
    if esp not in (None, "0"):
        return {"status": PROHIBITED, "rule": "E0604", "possible_rules": []}
    retido = tax.get("iss_retention") in ISS_RETIDO
    if op == "1":
        chave = "1"
        if convenio_ativo is True:
            return {"status": PROHIBITED, "rule": "E0617", "possible_rules": []}
        if convenio_ativo is False:
            return ({"status": REQUIRED, "rule": "E0619", "possible_rules": []} if esp == "0" else
                    {"status": UNKNOWN, "rule": None, "possible_rules": ["E0619"]})  # regEspTrib não informado
    elif op == "3" and reg_sn == "1":
        chave = "3:1:ret" if retido else "3:1"
        ativo_regra, inativo_regra = DEPENDEM_DO_CONVENIO[chave]
        if convenio_ativo is True:
            return {"status": REQUIRED if retido else PROHIBITED, "rule": ativo_regra, "possible_rules": []}
        if convenio_ativo is False:
            return {"status": REQUIRED if retido else PROHIBITED, "rule": inativo_regra, "possible_rules": []}
    elif op == "3" and reg_sn in ("2", "3"):
        chave = f"3:{reg_sn}"
        if convenio_ativo is True:
            return {"status": PROHIBITED, "rule": "E0635", "possible_rules": []}
        if convenio_ativo is False:
            return {"status": REQUIRED, "rule": "E0640", "possible_rules": []}
    else:
        # Regime do prestador incompleto (sem opSimpNac, ou ME/EPP sem regApTribSN): não há como decidir.
        return {"status": UNKNOWN, "rule": None, "possible_rules": []}
    if convenio_ativo is None:
        return {"status": UNKNOWN, "rule": None, "possible_rules": list(DEPENDEM_DO_CONVENIO[chave])}
    return {"status": OPTIONAL, "rule": None, "possible_rules": []}


def codigo_servico_parametrizacao(c_trib_nac: str | None, c_trib_mun: str | None) -> str | None:
    """Código de serviço da Parametrização (9 dígitos, formato devolvido pela própria API: 07.02.01.000):
    cTribNac pontuado + cTribMun (ou 000). Confirmado em leitura oficial de 07/10/2026 (sem 9 dígitos: HTTP 400)."""
    if not c_trib_nac or not re.fullmatch(r"\d{6}", c_trib_nac):
        return None
    suf = c_trib_mun if c_trib_mun and re.fullmatch(r"\d{3}", c_trib_mun) else "000"
    return f"{c_trib_nac[:2]}.{c_trib_nac[2:4]}.{c_trib_nac[4:6]}.{suf}"


def municipio_incidencia(c_trib_nac: str | None, location_code: str | None, customer_municipality: str | None,
                         company_municipality: str | None) -> str | None:
    """Anexo I (MUN.INCID_INFO.SERV.): LP = local da prestação, ET = estabelecimento do tomador, EP = do prestador."""
    from . import dominios

    regra = dominios.incidencia(c_trib_nac)
    return {"LP": location_code, "ET": customer_municipality, "EP": company_municipality}.get(regra or "")


def convenio_ativo(http_status: int, body: dict | None) -> bool | None:
    """Parametrização GET /{municipio}/convenio: 200 com parametrosConvenio.aderenteAmbienteNacional=1 => ativo;
    200 sem adesão ou 404 "ainda não está ativo" => inativo; qualquer outra resposta => None (indeterminado)."""
    body = body or {}
    if http_status == 200 and (body.get("parametrosConvenio") or {}).get("aderenteAmbienteNacional") == 1:
        return True
    if http_status in (200, 404):
        return False
    return None


def aliquota_parametrizada(http_status: int, body: dict | None, servico: str, competencia: str) -> dict:
    """Parametrização GET /{municipio}/{servico}/{competencia}/aliquota → {status, rate, incidence, valid_from,
    valid_to, message}. status: FOUND | NOT_FOUND | UNAVAILABLE. Aliq vem como número JSON: convertido para texto
    com 2 casas sem aritmética de ponto flutuante."""
    from decimal import Decimal, InvalidOperation

    body = body or {}
    msg = body.get("mensagem")
    if http_status not in (200, 404):
        return {"status": "UNAVAILABLE", "rate": None, "message": msg or f"HTTP {http_status}"}
    inicio_comp = f"{competencia}-01"
    candidatas = []
    for codigo, itens in (body.get("aliquotas") or {}).items():
        if codigo != servico:
            continue
        for it in itens or []:
            ini, fim = str(it.get("DtIni") or "")[:10], str(it.get("DtFim") or "")[:10]
            if ini and ini > inicio_comp or (fim and fim < inicio_comp):
                continue
            candidatas.append(it)
    if not candidatas:
        return {"status": "NOT_FOUND", "rate": None, "message": msg}
    it = max(candidatas, key=lambda x: str(x.get("DtIni") or ""))
    try:
        rate = None if it.get("Aliq") is None else str(Decimal(str(it["Aliq"])).quantize(Decimal("0.01")))
    except InvalidOperation:
        rate = None
    return {"status": "FOUND" if rate is not None else "NOT_FOUND", "rate": rate, "incidence": it.get("Incidencia"),
            "valid_from": str(it.get("DtIni") or "")[:10] or None, "valid_to": str(it.get("DtFim") or "")[:10] or None,
            "message": msg}
