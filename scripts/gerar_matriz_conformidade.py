"""Gera a matriz de conformidade da DPS (impressa no terminal): campo da interface -> campo da API -> caminho na DPS -> formato/domínio ->
obrigatoriedade/condição -> resultado (XSD / regras de negócio / aceitação oficial).

Uso: .venv\\Scripts\\python -I scripts\\gerar_matriz_conformidade.py

Fontes automáticas (nenhum status de regra é escrito à mão):
- Regras do Anexo I por elemento: docs/oficial/regras-dps-anexo-i.json (scripts/extrair_regras_anexo_i.py).
- Regras que o SISTEMA aplica: códigos E#### citados nos fontes (validação do rascunho/cadastros = bloqueia
  agendamento; provider oficial = conferidas na transmissão, sem envio).
- Presença do CAMINHO completo nas DPS reais ACEITAS da empresa (ADN produção, leitura local).
- Rótulos das telas: NovaNfsePage (rascunho), SettingsPage (perfil fiscal), CustomersPage (tomador).
"""
from __future__ import annotations

import base64
import gzip
import json
import re
from collections import Counter
import sys
from pathlib import Path

from defusedxml import ElementTree as SafeET

ROOT = Path(__file__).resolve().parents[1]
RN = json.loads((ROOT / "docs/oficial/regras-dps-anexo-i.json").read_text(encoding="utf-8"))
P = "NFSe/infNFSe/DPS/infDPS/"
N = "{http://www.sped.fazenda.gov.br/nfse}"

LOCAL_SRC = [ROOT / "services/fiscal-api/ctrl_fiscal/domain/regras_dps.py",
             ROOT / "services/fiscal-api/ctrl_fiscal/domain/drafts.py",
             ROOT / "services/nfse-api/nfse_api/dps.py",
             ROOT / "services/core-api/core_api/schemas.py",
             ROOT / "services/core-api/core_api/company_admin.py"]
TRANSM_SRC = [ROOT / "services/nfse-api/nfse_api/providers/official.py"]


def _codes(files: list[Path]) -> set[str]:
    return {m for f in files for m in re.findall(r"E\d{4}", f.read_text(encoding="utf-8"))}


LOCAL = _codes(LOCAL_SRC)
TRANSM = _codes(TRANSM_SRC)  # aplicadas pelo conector antes do envio (prioridade sobre citações em avisos)
# Garantidas pela própria montagem da DPS (o proibido nunca é gerado; o obrigatório sempre é).
POR_CONSTRUCAO = {"E0121", "E0128", "E0204", "E0910", "E0010", "E0593"}
# Não aplicáveis: emitente é sempre o prestador (tpEmit=1); sem NIF, exterior ou intermediário.
NAO_APLICAVEL = {"E0122", "E0123", "E0125", "E0129", "E0142", "E0146", "E0194", "E0212", "E0228", "E0229", "E0231",
                 "E0232", "E0233", "E0236", "E0318", "E0320", "E0382", "E0650", "E0652", "E0672", "E0673", "E0675",
                 "E1402", "E0161", "E1270", "E1272", "E1304", "E0235"}
DEPENDE = {
    "cadastros RFB/CNC/Simples": {"E0018", "E0020", "E0023", "E0025", "E0082", "E0084", "E0116", "E0119", "E0120",
                                  "E0124", "E0160", "E0190", "E0207", "E0016", "E0041", "E0132", "E0134", "E0912",
                                  "E0914", "E0921", "E0922"},
    "tabela de CEP": {"E0138", "E0240", "E0380"},
    "parametrização municipal": {"E0312", "E0314", "E0446", "E0454", "E0177", "E0178", "E0176", "E0539", "E0540",
                                 "E0612", "E0594", "E0439", "E0440", "E0667", "E0670", "E0444", "E0447", "E0037",
                                 "E0038", "E0039", "E0621", "E0625", "E0628", "E0631", "E0635", "E0640"},
    "tabela cClassTrib/calculadora": {"E0958", "E0964", "E0965", "E0969", "E0970", "E0971", "E0972"},
    "processamento Sefin": {"E0008", "E0014", "E0854", "E1294"},
}

PAGES = {"Rascunho": "NovaNfsePage", "Configurações": "SettingsPage", "Clientes": "CustomersPage"}
UI: set[str] = set()
for _tela, _arq in PAGES.items():
    for _l in re.findall(r'label="([^"]+)"', (ROOT / f"apps/painel-fiscal/src/pages/{_arq}.tsx").read_text(encoding="utf-8")):
        UI.add(f"{_tela}: {_l}")

TF = "valores/trib/tribFed"
PC = f"{TF}/piscofins"
TT = "valores/trib/totTrib"
G = "IBSCBS/valores/trib/gIBSCBS"
# (tela: rótulo | None, campo da API, caminhos DPS (relativos a infDPS; o 1º conta o uso), formato/domínio, condição)
CAMPOS: list[tuple] = [
    ("§Prestador (perfil fiscal — nunca editável no rascunho)",),
    (None, "company.cnpj (tenant)", ["prest/CNPJ"], "CNPJ 14 com DV", "1-1"),
    ("Configurações: Inscrição Municipal (prest/IM)", "fiscal_profile.municipal_registration", ["prest/IM"], "até 15", "0-1; se houver cadastro no CNC"),
    ("Configurações: Telefone Oficial do Prestador (prest/fone)", "fiscal_profile.phone", ["prest/fone"], "[0-9]{6,20}", "0-1"),
    ("Configurações: E-mail Oficial do Prestador (prest/email)", "fiscal_profile.email", ["prest/email"], "e-mail", "0-1"),
    ("Configurações: Opção pelo Simples Nacional (opSimpNac) *", "fiscal_profile.simples_option", ["prest/regTrib/opSimpNac"], "1|2|3", "1-1; igual ao cadastro Simples"),
    ("Configurações: Regime de Apuração Simples (regApTribSN)", "fiscal_profile.simples_regime", ["prest/regTrib/regApTribSN"], "1|2|3", "só opSimpNac=3"),
    ("Configurações: Regime Especial de Tributação (regEspTrib) *", "fiscal_profile.special_regime", ["prest/regTrib/regEspTrib"], "0..6, 9", "1-1"),
    ("Configurações: Município de Emissão (IBGE 7 dígitos - cLocEmi) *", "company.municipality_code", ["cLocEmi"], "IBGE (Anexo A)", "1-1; convênio ativo"),
    ("§Tomador (cadastro de clientes; o rascunho referencia e recarrega)",),
    ("Rascunho: Cliente (Tomador) *", "customer_id → customer.document", ["toma/CNPJ", "toma/CPF"], "DV; ≠ CNPJ do prestador", "0-1; obrigatório p/ certos cIndOp"),
    ("Clientes: Razão Social / Nome Completo *", "customer.name", ["toma/xNome"], "1–300", "1-1 no grupo"),
    ("Clientes: Inscrição Municipal", "customer.municipal_registration", ["toma/IM"], "até 15", "0-1"),
    ("Clientes: Código do Município (IBGE 7 dígitos)", "customer.municipality_code (+CEP, endereço)", ["toma/end/endNac/cMun", "toma/end", "toma/end/endNac/CEP"],
     "IBGE (Anexo A); CEP 8", "0-1; obrigatório c/ retenção pelo tomador, cIndOp ou incidência no tomador"),
    ("Clientes: Telefone / Celular (com DDD)", "customer.phone", ["toma/fone"], "[0-9]{6,20}", "0-1"),
    ("Clientes: E-mail de Contato / Faturamento", "customer.email", ["toma/email"], "e-mail", "0-1"),
    ("§Datas",),
    ("Rascunho: Competência Fiscal (AAAA-MM) *", "competence", [], "AAAA-MM", "controle interno; dCompet deve pertencer"),
    ("Rascunho: Data Fiscal / Fato Gerador (AAAA-MM-DD) *", "service_date", ["dCompet"], "AAAA-MM-DD", "1-1; ≤ data de emissão"),
    (None, "(instante do envio)", ["dhEmi"], "data-hora com fuso", "1-1"),
    ("§Serviço e construção civil",),
    ("Rascunho: Código Tributação Nacional (cTribNac — 6 dígitos)", "service.national_code", ["serv/cServ/cTribNac"],
     "6 dígitos **e existir** na lista nacional (Anexo B)", "1-1; administrado pelo município de incidência"),
    ("Rascunho: Código Tributação Municipal (cTribMun — Exatos 3 dígitos)", "service.municipal_code", ["serv/cServ/cTribMun"],
     "3 dígitos ≠ 000", "0-1; existir no município"),
    ("Rascunho: Discriminação dos Serviços Prestados *", "service.description", ["serv/cServ/xDescServ"], "1–2000", "1-1"),
    ("Rascunho: Código NBS (9 dígitos — Nomenclatura Brasileira de Serviços)", "service.nbs_code", ["serv/cServ/cNBS"],
     "9 dígitos **e existir** (Anexo B)", "0-1; obrigatório com IBS/CBS"),
    ("Rascunho: Município de Incidência (7 dígitos IBGE)", "service.location_code", ["serv/locPrest/cLocPrestacao"],
     "IBGE (Anexo A) ou 0000000", "1-1 — **rótulo errado: é o LOCAL DA PRESTAÇÃO**"),
    ("Rascunho: Código da Obra / CNO (cObra) *", "service.obra_code", ["serv/obra/cObra", "serv/obra"], "1–30",
     "escolha c/ endereço; obrigatório nos 13 subitens de obra, proibido nos demais"),
    ("Rascunho: CEP *", "service.obra_address", ["serv/obra/end", "serv/obra/end/CEP"], "CEP 8 + logradouro, número, bairro",
     "escolha c/ cObra; CEP do município da prestação"),
    ("§Valores, descontos e deduções",),
    ("Rascunho: Valor do Serviço (R$ pt-BR) *", "amount", ["valores/vServPrest/vServ"], "decimal 2 casas > 0", "1-1"),
    (None, "unconditional_discount", ["valores/vDescCondIncond/vDescIncond"], "0 < v < vServ", "0-1"),
    (None, "conditional_discount", ["valores/vDescCondIncond/vDescCond"], "0 < v < vServ", "0-1"),
    ("Rascunho: Deduções Legais (R$)", "deductions", ["valores/vDedRed/vDR", "valores/vDedRed"], "decimal ≤ vServ",
     "0-1; conforme tributação, perfil e parametrização municipal"),
    ("§ISSQN",),
    ("Rascunho: Tributação ISSQN (tribISSQN) *", "tax_settings.issqn_taxation", ["valores/trib/tribMun/tribISSQN"], "1..4",
     "1-1; exportação (3) não suportada"),
    (None, "tax_settings.immunity_type", ["valores/trib/tribMun/tpImunidade"], "1..5", "só com imunidade (2)"),
    ("Rascunho: Retenção na Fonte (tpRetISSQN) *", "tax_settings.iss_retention", ["valores/trib/tribMun/tpRetISSQN"], "1..3",
     "1-1; retenção pelo intermediário (3) não suportada"),
    ("Rascunho: Alíquota ISS (%)", "tax_settings.iss_rate", ["valores/trib/tribMun/pAliq"], "0–5 (TSDec1V2)",
     "depende do convênio do município de incidência no ambiente"),
    ("§PIS/COFINS e retenções federais",),
    ("Rascunho: Situação Tributária PIS/COFINS (CST)", "tax_settings.pis_cofins_cst", [f"{PC}/CST"], "lista do XSD", "1-1 no grupo"),
    ("Rascunho: Base de Cálculo PIS/COFINS (R$)", "tax_settings.pis_cofins_base", [f"{PC}/vBCPisCofins"], "≤ vServ", "0-1"),
    ("Rascunho: Alíquota PIS (%)", "tax_settings.pis_rate", [f"{PC}/pAliqPis"], "0–100", "0-1"),
    ("Rascunho: Alíquota COFINS (%)", "tax_settings.cofins_rate", [f"{PC}/pAliqCofins"], "0–100", "0-1"),
    ("Rascunho: Valor PIS (R$)", "tax_settings.pis_amount", [f"{PC}/vPis"], "= base × alíquota", "0-1"),
    ("Rascunho: Valor COFINS (R$)", "tax_settings.cofins_amount", [f"{PC}/vCofins"], "= base × alíquota", "0-1"),
    (None, "tax_settings.pis_cofins_retention", [f"{PC}/tpRetPisCofins"], "0..9", "0-1; define se vRetCSLL é proibido/obrigatório"),
    (None, "tax_settings.inss_withheld", [f"{TF}/vRetCP"], "0 < v < vServ", "0-1"),
    (None, "tax_settings.irrf_withheld", [f"{TF}/vRetIRRF"], "0 < v < vServ", "0-1"),
    (None, "tax_settings.csll_withheld", [f"{TF}/vRetCSLL"], "0 < v < vServ", "0-1; conforme tpRetPisCofins"),
    ("§Tributos aproximados (Lei 12.741/2012)",),
    ("Rascunho: Modalidade de Apuração", "tax_settings.approx_tax.mode", [f"{TT}/vTotTrib", f"{TT}/pTotTrib", f"{TT}/indTotTrib", f"{TT}/pTotTribSN"],
     "VALUES | PERCENT | SIMPLES_PERCENT | NOT_INFORMED", "1-1; não optante só VALUES/PERCENT"),
    ("Rascunho: Tributos Federais (R$)", "approx_tax.federal (VALUES)", [f"{TT}/vTotTrib/vTotTribFed"], "0 ≤ v ≤ vServ", "1-1 no modo"),
    ("Rascunho: Tributos Estaduais (R$)", "approx_tax.state (VALUES)", [f"{TT}/vTotTrib/vTotTribEst"], "0 ≤ v ≤ vServ", "1-1 no modo"),
    ("Rascunho: Tributos Municipais (R$)", "approx_tax.municipal (VALUES)", [f"{TT}/vTotTrib/vTotTribMun"], "0 ≤ v ≤ vServ", "1-1 no modo"),
    ("Rascunho: Alíquota Total Estimada (%)", "approx_tax.* (PERCENT)", [f"{TT}/pTotTrib/pTotTribFed", f"{TT}/pTotTrib/pTotTribEst", f"{TT}/pTotTrib/pTotTribMun"], "0–100", "1-1 no modo"),
    ("Rascunho: Alíquota Simples Nacional (%)", "approx_tax.simples", [f"{TT}/pTotTribSN"], "percentual", "só optante"),
    ("§IBS/CBS (Reforma Tributária)",),
    (None, "(fixo 0)", ["IBSCBS/finNFSe"], "0", "1-1 no grupo"),
    ("Rascunho: Uso Pessoal *", "ibs_cbs.personal_use", ["IBSCBS/indFinal"], "0|1", "0-1"),
    ("Rascunho: Indicador de Operação (6 dígitos) *", "ibs_cbs.operation_indicator", ["IBSCBS/cIndOp", "IBSCBS"], "**existir** no Anexo C", "1-1 no grupo"),
    (None, "(fixo 0)", ["IBSCBS/indDest", "IBSCBS/dest"], "0", "1-1; destinatário diferente não suportado"),
    ("Rascunho: CST IBS/CBS (3 dígitos) *", "ibs_cbs.cst", [f"{G}/CST"], "3 dígitos (tabela RFB não local)", "1-1"),
    ("Rascunho: Classificação Tributária (6 dígitos) *", "ibs_cbs.class_code", [f"{G}/cClassTrib", f"{G}/gTribRegular", f"{G}/gDif"],
     "6 dígitos; prefixo = CST", "1-1; atributos da tabela cClassTrib só na Sefin"),
    (None, "(não suportado)", ["IBSCBS/imovel", "IBSCBS/tpOper"], "—", "exigido em certas operações: recusado localmente"),
]


def presenca() -> tuple[Counter, int]:
    perfil = json.loads((ROOT / "data/homologacao/empresa.json").read_text(encoding="utf-8"))
    c: Counter = Counter()
    n = 0

    def walk(el, prefix: str, acc: set) -> None:  # noqa: ANN001
        for ch in el:
            path = f"{prefix}{ch.tag.replace(N, '')}"
            acc.add(path)
            walk(ch, path + "/", acc)

    for f in sorted((ROOT / "data/oficial/producao/adn").glob("lote-*.json")):
        for it in json.loads(f.read_text(encoding="utf-8")).get("LoteDFe") or []:
            if it.get("TipoDocumento") != "NFSE":
                continue
            r = SafeET.fromstring(gzip.decompress(base64.b64decode(it["ArquivoXml"])))
            if r.findtext(f"{N}infNFSe/{N}emit/{N}CNPJ") != perfil["cnpj"]:
                continue
            n += 1
            acc: set = set()
            walk(r.find(f"{N}infNFSe/{N}DPS/{N}infDPS"), "", acc)
            c.update(acc)
    return c, n


def _norm(el: str) -> str:
    """Normaliza os caminhos do Anexo I (algumas linhas omitem a barra ou usam 'totalTrib')."""
    e = el.replace(P, "")
    e = e.replace("servobra", "serv/obra").replace("substchSubstda", "subst/chSubstda").replace("substxMotivo", "subst/xMotivo")
    e = re.sub(r"totalTrib/(vTotTrib|pTotTrib)(\w+)$", r"totTrib/\1/\2", e)
    return e


RULES_BY_EL: dict[str, list[dict]] = {}
for _r in RN["regras"]:
    if _r.get("emissor_proprio") in ("V", ""):
        RULES_BY_EL.setdefault(_norm(_r["elemento"]), []).append(_r)


def ui(label: str | None) -> str:
    if label is None:
        return "**ausente na tela**"
    return label if label in UI else f"{label} (**não encontrado na tela**)"


def classifica(codes: list[str]) -> tuple[list[str], list[str], dict[str, list[str]]]:
    loc = sorted({c for c in codes if c in LOCAL and c not in TRANSM} | {f"{c}*" for c in codes if c in POR_CONSTRUCAO and c not in LOCAL})
    tra = sorted(c for c in codes if c in TRANSM)
    resto: dict[str, list[str]] = {}
    for c in codes:
        if c in LOCAL or c in TRANSM or c in POR_CONSTRUCAO:
            continue
        motivo = "não aplicável" if c in NAO_APLICAVEL else next((k for k, v in DEPENDE.items() if c in v), "só Sefin")
        resto.setdefault(motivo, []).append(c)
    return loc, tra, resto


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # saída no terminal (Windows usa cp1252 por padrão)
    pres, n = presenca()
    used: set[str] = set()
    lines = [
        "# Matriz de conformidade da DPS v1.01",
        "",
        "Campo da interface → campo da API → caminho na DPS → formato/domínio → obrigatoriedade/condição → resultado.",
        "",
        "Gerada por `scripts/gerar_matriz_conformidade.py`, sem status de regra escrito à mão. Fontes:",
        "- Anexo I Produção Restrita (leiaute e RN DPS_NFS-e);",
        "- domínios oficiais dos Anexos A, B e C (`ctrl_common/dominios`);",
        "- XSD PRODREST v1.01-20260727;",
        f"- {n} DPS reais **aceitas** da empresa;",
        "- telas do painel: rascunho, configurações e clientes.",
        "",
        "## Como ler o resultado (três níveis distintos)",
        "",
        "1. **Estrutura (XSD):** toda DPS é validada no XSD antes do envio. Passar no XSD **não** prova regra de negócio.",
        "2. **Regras de negócio (Anexo I):**",
        "   - **Local:** aplicada pela plataforma, que bloqueia validação e agendamento. `*` = garantida pela montagem da DPS.",
        "   - **Transmissão:** conferida pelo conector antes do envio, com consulta real à Parametrização; recusa sem enviar.",
        "   - **Depende de…:** cadastro ou tabela governamental que a plataforma não tem; só a Sefin verifica.",
        "   - **Não aplicável:** emitente sempre prestador (`tpEmit=1`), sem NIF, exterior ou intermediário.",
        f"3. **Aceitação oficial:** **nenhuma DPS foi transmitida pela plataforma até agora**, então a aceitação efetiva está **não comprovada** em todas as linhas. A última coluna mostra em quantas das {n} DPS reais aceitas (emitidas pelo Emissor Web) o caminho aparece. Isso prova que o elemento e o formato são aceitos, não que os dados enviados pela plataforma o serão.",
        "",
        "Também medido: as mesmas regras locais aplicadas às 186 DPS aceitas acusam **0 falsos positivos** (`scripts/regras_contra_notas_reais.py`), e as 186 DPS remontadas pela plataforma passam no XSD com 6.918 valores idênticos (`scripts/comparar_dps_reais.py`).",
        "",
        "| Interface | API | DPS (infDPS/…) | Formato / domínio | Obrigatoriedade / condição | Regras locais | Na transmissão | Fora do alcance local | Uso nas DPS aceitas |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in CAMPOS:
        if len(row) == 1:
            lines.append(f"| **{row[0][1:]}** | | | | | | | | |")
            continue
        label, api, paths, fmt, cond = row
        codes: list[str] = []
        for pth in paths:
            for r in RULES_BY_EL.get(pth, []):
                codes.append(r["codigo"])
            used.add(pth)
        codes = sorted(set(codes))
        loc, tra, resto = classifica(codes)
        uso = f"{pres.get(paths[0], 0)}/{n}" if paths else "—"
        fora = "; ".join(f"{k}: {', '.join(sorted(v))}" for k, v in sorted(resto.items())) or "—"
        lines.append(f"| {ui(label)} | `{api}` | {'<br>'.join(f'`{p}`' for p in paths) or '—'} | {fmt} | {cond} | "
                     f"{', '.join(loc) or '—'} | {', '.join(tra) or '—'} | {fora} | {uso} |")
    sem_campo = sorted({el for el in RULES_BY_EL if not any(el == u or el.startswith(u + "/") for u in used)})
    lines += [
        "",
        "## Elementos do leiaute com regras, mas NÃO suportados pela plataforma",
        "",
        "Quando a regra exige um destes grupos, o rascunho é recusado localmente (ex.: `atvEvento`, `imovel`, `tpOper`, exportação, intermediário). Quando o grupo é opcional, ele simplesmente não é gerado.",
        "",
        ", ".join(f"`{e}`" for e in sem_campo) + ".",
        "",
        "## Lacunas da interface (API pronta; campo ausente ou incorreto na tela)",
        "",
        "- **Rótulo errado:** `service.location_code` aparece como \"Município de Incidência\". É o **local da prestação** (`cLocPrestacao`); a incidência depende do subitem (EP/LP/ET, Anexo I).",
        "- **Campos sem tela** (linhas \"ausente na tela\" acima):",
        "  - descontos (`unconditional_discount`, `conditional_discount`);",
        "  - `immunity_type`;",
        "  - retenções federais (`inss_withheld`, `irrf_withheld`, `csll_withheld`) e `pis_cofins_retention`.",
        "- **Ações sem tela:**",
        "  - retirada da fila (`POST /drafts/{id}/withdraw`);",
        "  - recarga do tomador no rascunho (`PATCH` com `customer_id`).",
        "- **Prestador:** ajustável só em Configurações → Perfil fiscal (admin). O CNPJ do emitente é o tenant e não existe no rascunho.",
    ]
    print("\n".join(lines))
    print(f"\nmatriz: {sum(1 for r in CAMPOS if len(r) > 1)} campos; regras locais citadas={len(LOCAL)}, na transmissão={len(TRANSM)}; "
          f"elementos com regra sem campo={len(sem_campo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
