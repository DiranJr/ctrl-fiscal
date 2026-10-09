"""Esquemas públicos da fiscal-api (contrato canônico publicado em contracts/)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Field(pattern=r"^\d{1,13}\.\d{2}$", description="Decimal em string com 2 casas, ex.: \"1000.00\".")
Competence = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Competência AAAA-MM.")
UUIDStr = Field(pattern=r"^[0-9a-fA-F-]{36}$")

Direction = Literal["ISSUED", "RECEIVED"]
DocumentStatus = Literal["AUTHORIZED", "CANCELLED", "UNKNOWN"]
ReviewStatus = Literal["PENDING", "REVIEWED", "DIVERGENT"]
RetentionClass = Literal["WITH", "WITHOUT", "UNKNOWN"]
RetentionType = Literal["ISS", "INSS", "IRRF", "CONTRIB"]
RetentionMatch = Literal["ANY", "ALL"]
QueueStatus = Literal["DRAFT", "SCHEDULED", "PROCESSING", "AUTHORIZED", "REJECTED", "AWAITING_CONFIRMATION", "WITHDRAWN"]
Environment = Literal["mock", "restricted", "production"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- rascunhos ----------
class ApproxTax(Strict):
    """totTrib (Lei 12.741/2012), uma das 4 opções do XSD (TCTribTotal)."""

    mode: Literal["VALUES", "PERCENT", "SIMPLES_PERCENT", "NOT_INFORMED"] = Field(
        description="VALUES=vTotTrib (R$), PERCENT=pTotTrib (%), SIMPLES_PERCENT=pTotTribSN, NOT_INFORMED=indTotTrib 0.")
    federal: str | None = Field(None, description="vTotTribFed (R$, 2 casas) ou pTotTribFed (%).")
    state: str | None = Field(None, description="vTotTribEst ou pTotTribEst.")
    municipal: str | None = Field(None, description="vTotTribMun ou pTotTribMun.")
    simples: str | None = Field(None, pattern=r"^(0|0\.[0-9]{2}|[1-9][0-9]?(\.[0-9]{2})?)$", description="pTotTribSN (%).")


class ObraAddress(Strict):
    """obra/end (TCEnderObraEvento), endereço nacional da obra — alternativa a obra_code."""

    postal_code: str = Field(pattern=r"^[0-9]{8}$", description="CEP")
    street: str = Field(min_length=1, max_length=255, description="xLgr")
    number: str = Field(min_length=1, max_length=60, description="nro")
    complement: str | None = Field(None, min_length=1, max_length=156, description="xCpl")
    district: str = Field(min_length=1, max_length=60, description="xBairro")


Dec3V2 = r"^(0|0\.[0-9]{2}|[1-9][0-9]{0,2}(\.[0-9]{2})?)$"


class IbsCbsRegular(Strict):
    """gTribRegular: tributação regular que se aplicaria se não houvesse o tratamento da classificação.
    Só quando a classificação exige (exigeGrupoTributacaoRegular — E0964/E0965)."""

    cst: str = Field(pattern=r"^[0-9]{3}$", description="CSTReg (3 primeiros dígitos de cClassTribReg — E0970).")
    class_code: str = Field(pattern=r"^[0-9]{6}$", description="cClassTribReg (tabela oficial para NFS-e — E0969).")


class IbsCbsDeferral(Strict):
    """gDif: percentuais de diferimento (TSDec3V2). Só quando a classificação permite diferimento (E0971/E0972)."""

    uf: str = Field(pattern=Dec3V2, description="pDifUF (%)")
    mun: str = Field(pattern=Dec3V2, description="pDifMun (%)")
    cbs: str = Field(pattern=Dec3V2, description="pDifCBS (%)")


class IbsCbsRealEstate(Strict):
    """imovel (TCRTCInfoImovel): bem imóvel objeto da operação — cIndOp 020101/020201/020301 FORA dos subitens de
    obra (E0932); proibido nos subitens de obra (E0931) e nas demais operações (E0928). Não confundir com serv/obra."""

    registration: str | None = Field(None, min_length=1, max_length=30, description="inscImobFisc (opcional).")
    cib: str | None = Field(None, pattern=r"^[0-9A-Za-z]{8}$", description="cCIB — Cadastro Imobiliário Brasileiro.")
    address: ObraAddress | None = Field(None, description="end — endereço do imóvel (alternativa ao cCIB).")

    @model_validator(mode="after")
    def _choice(self):  # noqa: ANN202
        if bool(self.cib) == bool(self.address):
            raise ValueError("imovel: informe cCIB OU endereço (escolha do XSD).")
        return self


class RecipientAddress(Strict):
    """dest/end/endNac (endereço nacional)."""

    municipality_code: str = Field(pattern=r"^[0-9]{7}$", description="cMun (IBGE)")
    postal_code: str = Field(pattern=r"^[0-9]{8}$", description="CEP")
    street: str = Field(min_length=1, max_length=255, description="xLgr")
    number: str = Field(min_length=1, max_length=60, description="nro")
    complement: str | None = Field(None, min_length=1, max_length=156, description="xCpl")
    district: str = Field(min_length=1, max_length=60, description="xBairro")


class IbsCbsRecipient(Strict):
    """dest (TCRTCInfoDest): destinatário DIFERENTE do tomador/adquirente (indDest=1). Ausente = indDest=0."""

    document_type: Literal["CNPJ", "CPF", "NIF", "NO_NIF"] = Field(description="CNPJ, CPF, NIF (exterior) ou cNaoNIF.")
    document: str | None = Field(None, min_length=1, max_length=40, description="CNPJ/CPF (com DV) ou NIF.")
    no_nif_reason: Literal["0", "1", "2"] | None = Field(
        None, description="cNaoNIF: 0 não informado na origem; 1 dispensado; 2 não exigido.")
    name: str = Field(min_length=1, max_length=150, description="xNome")
    address: RecipientAddress | None = Field(None, description="end (opcional).")
    phone: str | None = Field(None, pattern=r"^[0-9]{6,20}$", description="fone")
    email: str | None = Field(None, min_length=3, max_length=80, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
                              description="email (E0930)")

    @model_validator(mode="after")
    def _doc(self):  # noqa: ANN202
        from ctrl_common.fiscal_types import is_valid_cnpj, is_valid_cpf

        if self.document_type == "NO_NIF":
            if not self.no_nif_reason or self.document:
                raise ValueError("dest: sem NIF exige o motivo (cNaoNIF) e nenhum documento.")
        elif not self.document:
            raise ValueError("dest: informe o documento do destinatário.")
        elif self.document_type == "CNPJ" and not is_valid_cnpj(self.document):
            raise ValueError("dest: CNPJ inválido (E0911).")
        elif self.document_type == "CPF" and not is_valid_cpf(self.document):
            raise ValueError("dest: CPF inválido (E0913).")
        if self.document_type != "NO_NIF" and self.no_nif_reason:
            raise ValueError("dest: cNaoNIF só sem documento.")
        return self


class IbsCbs(Strict):
    """Grupo IBSCBS da DPS v1.01 (TCRTCInfoIBSCBS), NFS-e regular (finNFSe=0). Códigos informados pelo usuário/
    contabilidade (nada é presumido). Grupos condicionais conforme a classificação (cClassTrib) e a operação (cIndOp)."""

    operation_indicator: str = Field(pattern=r"^[0-9]{6}$", description="cIndOp: código indicador da operação (6 dígitos).")
    cst: str = Field(pattern=r"^[0-9]{3}$", description="gIBSCBS/CST (3 dígitos).")
    class_code: str = Field(pattern=r"^[0-9]{6}$", description="gIBSCBS/cClassTrib (6 dígitos).")
    personal_use: Literal["0", "1"] | None = Field(None, description="indFinal (art. 57): 0 não, 1 sim.")
    regular: IbsCbsRegular | None = Field(None, description="gTribRegular — só quando a classificação exige (E0964/E0965).")
    deferral: IbsCbsDeferral | None = Field(None, description="gDif — só quando a classificação permite diferimento (E0971/E0972).")
    presumed_credit: str | None = Field(None, pattern=r"^[0-9]{2}$",
                                        description="cCredPres — só se a classificação permitir crédito presumido.")
    real_estate: IbsCbsRealEstate | None = Field(None, description="imovel — operação com bem imóvel (E0928/E0931/E0932).")
    recipient: IbsCbsRecipient | None = Field(None, description="dest — destinatário ≠ tomador (indDest=1).")


class TaxSettings(Strict):
    """Campos tributários mapeados ao leiaute DPS v1.01 verificado (sem alíquotas inventadas)."""

    issqn_taxation: Literal["TAXABLE", "IMMUNE", "EXPORT", "NOT_INCIDENT"] | None = Field(
        None, description="tribISSQN: 1 tributável, 2 imunidade, 3 exportação, 4 não incidência.")
    iss_retention: Literal["NOT_RETAINED", "RETAINED_BY_TOMADOR", "RETAINED_BY_INTERMEDIARY"] | None = Field(
        None, description="tpRetISSQN: 1 não retido, 2 retido pelo tomador, 3 retido pelo intermediário.")
    iss_rate: str | None = Field(None, pattern=r"^(0|[0-9](\.[0-9]{2})?)$",
                                 description="pAliq (TSDec1V2), informado pelo usuário; não é calculado.")
    pis_cofins_retention: Literal["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"] | None = Field(
        None, description="tpRetPisCofins — QUAIS contribuições foram retidas (0 nenhuma; 1 PIS/COFINS; 2 PIS/COFINS não "
                          "retidos; 3 PIS/COFINS/CSLL; 4 PIS/COFINS sem CSLL; 5 só PIS; 6 só COFINS; 7 COFINS+CSLL; "
                          "8 só CSLL; 9 PIS+CSLL). Vai em piscofins: exige pis_cofins_cst.")
    irrf_withheld: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$",
                                      description="tribFed/vRetIRRF — IRRF RETIDO (>0 e < valor do serviço, E0700).")
    inss_withheld: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$",
                                      description="tribFed/vRetCP — contribuição previdenciária RETIDA (>0 e < serviço, E0699).")
    csll_withheld: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description=(
        "tribFed/vRetCSLL — TOTAL das contribuições RETIDAS indicadas por tpRetPisCofins (PIS, COFINS e/ou CSLL), "
        "não só a CSLL. Obrigatório com tpRetPisCofins fora de {0,2} (E0724); proibido com 0 (E0720); "
        ">0 e < serviço (E0701). É o único valor de PIS/COFINS/CSLL que entra no total retido."))
    pis_cofins_cst: Literal["00", "01", "02", "03", "04", "05", "06", "07", "08", "09", "49", "50", "51", "52", "53", "54", "55", "56", "60", "61", "62", "63", "64", "65", "66", "67", "70", "71", "72", "73", "74", "75", "98", "99"] | None = Field(None, description="piscofins/CST (exigido para enviar o grupo PIS/COFINS).")
    pis_cofins_base: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description="piscofins/vBCPisCofins")
    pis_rate: str | None = Field(None, pattern=r"^(0|0\.[0-9]{2}|[1-9][0-9]?(\.[0-9]{2})?)$", description="piscofins/pAliqPis (%)")
    cofins_rate: str | None = Field(None, pattern=r"^(0|0\.[0-9]{2}|[1-9][0-9]?(\.[0-9]{2})?)$", description="piscofins/pAliqCofins (%)")
    pis_amount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description=(
        "piscofins/vPis — débito de PIS de apuração própria. Fora da modalidade 1 é valor informado (não retido); na "
        "modalidade 1 a NT 008 v1.02 o apresenta como retido (cálculo do total ainda não confirmado — ver retention_summary). "
        "= base × pAliqPis quando a alíquota é informada (E0694)."))
    cofins_amount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description=(
        "piscofins/vCofins — débito de COFINS de apuração própria; mesma regra de vPis (E0696)."))
    immunity_type: Literal["1", "2", "3", "4", "5"] | None = Field(
        None, description="tpImunidade (só com issqn_taxation=IMMUNE; 0 não é aceito pelos emissores — E0593).")
    approx_tax: ApproxTax | None = Field(None, description="totTrib — obrigatório para emissão oficial.")
    ibs_cbs: IbsCbs | None = Field(None, description="IBSCBS (Reforma Tributária) — opcional no XSD v1.01.")


class TaxSettingsOut(BaseModel):
    issqn_taxation: str | None = None
    iss_retention: str | None = None
    iss_rate: str | None = None
    pis_cofins_retention: str | None = None
    irrf_withheld: str | None = None
    inss_withheld: str | None = None
    csll_withheld: str | None = None
    pis_cofins_cst: str | None = None
    immunity_type: str | None = None
    pis_cofins_base: str | None = None
    pis_rate: str | None = None
    cofins_rate: str | None = None
    pis_amount: str | None = None
    cofins_amount: str | None = None
    approx_tax: dict | None = None
    ibs_cbs: dict | None = None


class AdditionalInfo(Strict):
    """serv/infoCompl (TCInfoCompl) — todos opcionais."""

    technical_doc: str | None = Field(None, min_length=1, max_length=40, description="idDocTec: ART, RRT, DRT ou outro.")
    document_reference: str | None = Field(None, min_length=1, max_length=255, description="docRef")
    order_number: str | None = Field(None, min_length=1, max_length=60, description="xPed: pedido/ordem/projeto (B2B).")
    order_items: list[str] | None = Field(None, max_length=99, description="gItemPed/xItemPed (1 a 99, até 60 car.).")
    notes: str | None = Field(None, min_length=1, max_length=2000, description="xInfComp: informações complementares.")

    @field_validator("order_items")
    @classmethod
    def _items(cls, v):  # noqa: ANN001, ANN206
        if v is not None and (not v or any(not (1 <= len(i.strip()) <= 60) for i in v)):
            raise ValueError("Itens do pedido: 1 a 99 itens de 1 a 60 caracteres.")
        return v


def _obra_choice(obj):  # noqa: ANN001, ANN202
    if sum(bool(getattr(obj, k, None)) for k in ("obra_code", "obra_cib", "obra_address")) > 1:
        raise ValueError("obra: informe cObra OU cCIB OU endereço (escolha do XSD), não mais de um.")
    return obj


class ServiceIn(Strict):
    national_code: str | None = Field(None, pattern=r"^[0-9]{6}$", description="cTribNac (6 dígitos).")
    municipal_code: str | None = Field(None, pattern=r"^[0-9]{3}$", description="cTribMun (3 dígitos).")
    description: str = Field(min_length=1, max_length=2000, description="xDescServ.")
    nbs_code: str | None = Field(None, pattern=r"^[0-9]{9}$", description="cNBS (9 dígitos, Anexo B).")
    location_code: str | None = Field(None, pattern=r"^[0-9]{7}$", description="cLocPrestacao: município IBGE da prestação.")
    obra_code: str | None = Field(None, min_length=1, max_length=30, description="obra/cObra (CNO/CEI), quando houver.")
    obra_address: ObraAddress | None = Field(None, description="obra/end: endereço da obra (alternativa a obra_code).")
    obra_cib: str | None = Field(None, pattern=r"^[0-9A-Za-z]{8}$", description="obra/cCIB (alternativa a cObra/end).")
    obra_property_registration: str | None = Field(None, min_length=1, max_length=30,
                                                   description="obra/inscImobFisc (opcional, só com o grupo obra).")
    additional_info: AdditionalInfo | None = Field(None, description="infoCompl (opcional).")

    @field_validator("national_code", "municipal_code", "nbs_code", "location_code", "obra_code", "obra_cib",
                     "obra_property_registration", mode="before")
    @classmethod
    def _blank_is_null(cls, v):  # noqa: ANN001, ANN206
        return None if isinstance(v, str) and v.strip() == "" else v

    _choice = model_validator(mode="after")(_obra_choice)


class ServicePatch(Strict):
    national_code: str | None = Field(None, pattern=r"^[0-9]{6}$")
    municipal_code: str | None = Field(None, pattern=r"^[0-9]{3}$")
    description: str | None = Field(None, min_length=1, max_length=2000)
    nbs_code: str | None = Field(None, pattern=r"^[0-9]{9}$", description="cNBS (9 dígitos, Anexo B).")
    location_code: str | None = Field(None, pattern=r"^[0-9]{7}$", description="cLocPrestacao: município IBGE da prestação.")
    obra_code: str | None = Field(None, min_length=1, max_length=30, description="obra/cObra (CNO/CEI), quando houver.")
    obra_address: ObraAddress | None = Field(None, description="obra/end: endereço da obra (alternativa a obra_code).")
    obra_cib: str | None = Field(None, pattern=r"^[0-9A-Za-z]{8}$", description="obra/cCIB (alternativa a cObra/end).")
    obra_property_registration: str | None = Field(None, min_length=1, max_length=30,
                                                   description="obra/inscImobFisc (opcional, só com o grupo obra).")
    additional_info: AdditionalInfo | None = Field(None, description="infoCompl (opcional).")

    @field_validator("national_code", "municipal_code", "nbs_code", "location_code", "obra_code", "obra_cib",
                     "obra_property_registration", mode="before")
    @classmethod
    def _blank_is_null(cls, v):  # noqa: ANN001, ANN206
        return None if isinstance(v, str) and v.strip() == "" else v

    _choice = model_validator(mode="after")(_obra_choice)


class ServiceOut(BaseModel):
    national_code: str | None
    municipal_code: str | None
    description: str | None
    nbs_code: str | None = None
    location_code: str | None = None
    obra_code: str | None = None
    obra_address: dict | None = None
    obra_cib: str | None = None
    obra_property_registration: str | None = None
    additional_info: dict | None = None


class DraftCreate(Strict):
    customer_id: str = UUIDStr
    service_profile_id: str | None = Field(None, pattern=r"^[0-9a-fA-F-]{36}$", description=(
        "Perfil de serviço aplicado (o servidor registra a versão vigente). Perfil pendente de revisão ajuda a "
        "preparar o rascunho, mas bloqueia o agendamento."))
    competence: str = Competence
    service_date: date | None = Field(None, description="dCompet: data de início da prestação; deve estar na competência.")
    service: ServiceIn
    amount: str = Money
    deductions: str = Field("0.00", pattern=r"^\d{1,13}\.\d{2}$")
    unconditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description="vDescIncond")
    conditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description="vDescCond")
    tax_settings: TaxSettings | None = None


class DraftPatch(Strict):
    """Edição parcial com controle de versão.

    Semântica (por campo): AUSENTE = mantém; null = REMOVE (campos/grupos opcionais); valor = substitui.
    Grupos (`service.obra_address`, `tax_settings.approx_tax`, `tax_settings.ibs_cbs`) são substituídos inteiros;
    null remove o grupo. Campos obrigatórios (customer_id, competence, amount, service, tax_settings,
    service.description) não aceitam null (422). `deductions: null` = sem dedução (0.00).
    `customer_id` informado (mesmo igual) recarrega os dados atuais do tomador no rascunho.
    """

    service_profile_id: str | None = Field(None, pattern=r"^[0-9a-fA-F-]{36}$",
                                           description="Perfil aplicado; null desvincula o perfil.")

    expected_version: int = Field(ge=1)
    customer_id: str | None = Field(None, pattern=r"^[0-9a-fA-F-]{36}$")
    competence: str | None = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    service_date: date | None = Field(None, description="dCompet (AAAA-MM-DD). null remove.")
    service: ServicePatch | None = None
    amount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$")
    deductions: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$")
    unconditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description="vDescIncond. null remove.")
    conditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$", description="vDescCond. null remove.")
    tax_settings: TaxSettings | None = None

    @model_validator(mode="after")
    def _no_null_required(self):  # noqa: ANN201
        bad = [k for k in ("customer_id", "competence", "amount", "service", "tax_settings")
               if k in self.model_fields_set and getattr(self, k) is None]
        if self.service is not None and "description" in self.service.model_fields_set and self.service.description is None:
            bad.append("service.description")
        if bad:
            raise ValueError(f"Campos obrigatórios não podem ser removidos (null): {', '.join(bad)}")
        return self


class DraftWithdraw(Strict):
    expected_version: int = Field(ge=1, description="Versão do rascunho que o usuário está vendo.")


class DraftFromDocument(Strict):
    source_document_id: str = UUIDStr


class RecipientAddress(BaseModel):
    street: str | None
    number: str | None
    complement: str | None
    district: str | None


class RecipientSuggestion(BaseModel):
    """Somente o que consta no XML oficial da nota; campos ausentes ficam nulos e são listados em `missing`."""
    document_type: Literal["CNPJ", "CPF"] | None
    document: str | None
    name: str | None
    municipal_registration: str | None
    email: str | None
    phone: str | None
    municipality_code: str | None
    postal_code: str | None
    address: RecipientAddress
    foreign: bool
    source: Literal["XML", "DOCUMENT"]
    registered_customer_id: str | None
    missing: list[str]


class Issue(BaseModel):
    field: str
    code: str
    message: str
    severity: Literal["error", "warning", "info"]
    rule: str | None = Field(None, description="Código da regra oficial (Anexo I), quando houver.")


class DraftValidation(BaseModel):
    status: Literal["NOT_VALIDATED", "VALID", "INVALID"] = Field(
        description="NOT_VALIDATED quando o conteúdo mudou desde a última validação (issues anteriores descartadas).")
    validated_version: int | None
    validated_at: datetime | None


class CustomerSnapshot(BaseModel):
    id: str
    name: str
    document_type: str
    document: str
    email: str | None = None
    municipal_registration: str | None = None
    municipality_code: str | None = None
    postal_code: str | None = None
    address: dict | None = None
    is_active: bool = True


class RetentionIss(BaseModel):
    retention: str | None = Field(description="tpRetISSQN informado (iss_retention).")
    retained: bool
    base: str = Field(description="vBC = vServ − desconto incondicionado − deduções.")
    rate: str | None = Field(description="pAliq informado; null = a Sefin aplica a alíquota parametrizada.")
    amount: str | None = Field(description="ISSQN retido; null quando não retido ou quando definido pela Sefin.")
    amount_source: Literal["NOT_RETAINED", "ESTIMATED_FROM_RATE", "DETERMINED_BY_SEFIN"]


class RetentionCovered(BaseModel):
    pis: bool | None
    cofins: bool | None
    csll: bool | None = Field(description="null = o código não trata a CSLL (1 e 2).")


class RetentionFederal(BaseModel):
    inss: str | None = Field(description="vRetCP retido.")
    irrf: str | None = Field(description="vRetIRRF retido.")
    contributions: str | None = Field(description="vRetCSLL: total das contribuições retidas indicadas pelo código.")
    contributions_code: str | None = Field(description="tpRetPisCofins.")
    contributions_label: str | None
    contributions_covered: RetentionCovered | None
    total: str = Field(description="inss + irrf + contributions.")
    code1_pis_cofins: dict | None = Field(None, description=(
        "Modalidade 1: {pis, cofins, status: UNCONFIRMED_IN_TOTAL}. A NT 008 v1.02 os apresenta como retidos no DANFSe; "
        "não somados ao total desta prévia até a confirmação do cálculo da Sefin."))


class RetentionInformed(BaseModel):
    pis: str | None = Field(description="vPis (apuração própria) — NÃO retido, não reduz o líquido.")
    cofins: str | None = Field(description="vCofins (apuração própria) — NÃO retido, não reduz o líquido.")


class RetentionSummary(BaseModel):
    """Calculado no backend (domain/retencoes.py) com Decimal; a tela exibe, não recalcula."""

    service_amount: str
    unconditional_discount: str
    conditional_discount: str
    deductions: str
    iss: RetentionIss
    federal: RetentionFederal
    informed_not_withheld: RetentionInformed
    total_withheld: str = Field(description="vTotalRet = vRetCP + vRetIRRF + vRetCSLL + ISSQN (só se retido).")
    net_amount: str = Field(description="vLiq = vServ − desc. incondicionado − desc. condicionado − total retido.")
    complete: bool = Field(description="false quando o ISSQN retido depende da alíquota da Sefin (pending).")
    pending: list[str]


class RetentionPreviewIn(Strict):
    """Valores do formulário ainda não salvos: o backend calcula o resumo canônico (a tela não recalcula)."""

    amount: str = Field(pattern=r"^\d{1,13}\.\d{2}$")
    deductions: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$")
    unconditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$")
    conditional_discount: str | None = Field(None, pattern=r"^\d{1,13}\.\d{2}$")
    tax_settings: TaxSettings | None = None


class RetentionPreviewOut(BaseModel):
    retention_summary: RetentionSummary
    issues: list[Issue] = Field(description="Inconsistências de retenção (mesmas regras da validação do rascunho).")


class DraftOut(BaseModel):
    id: str
    customer_id: str
    customer: CustomerSnapshot | None
    source_document_id: str | None
    status: QueueStatus
    version: int
    competence: str
    service_date: str | None = Field(None, description="dCompet (AAAA-MM-DD); separado do agendamento.")
    service_profile_id: str | None = Field(None, description="Perfil de serviço aplicado.")
    service_profile_version: int | None = Field(None, description="Versão do perfil vigente quando foi aplicado.")
    service: ServiceOut
    amount: str
    deductions: str
    unconditional_discount: str | None = None
    conditional_discount: str | None = None
    tax_settings: TaxSettingsOut
    retention_summary: RetentionSummary
    issues: list[Issue]
    validation: DraftValidation
    editable: bool = Field(description="true em DRAFT/REJECTED; SCHEDULED exige retirada da fila (POST withdraw).")
    environment: Environment
    current_item_id: str | None
    created_at: datetime
    updated_at: datetime


class DraftPage(BaseModel):
    items: list[DraftOut]
    page: int
    page_size: int
    total: int


class ValidationOut(BaseModel):
    valid: bool
    issues: list[Issue]


# ---------- documentos ----------
class PartyOut(BaseModel):
    name: str | None
    cnpj: str | None = Field(description="CNPJ (ou CPF/NIF quando aplicável), somente caracteres alfanuméricos.")


class Retentions(BaseModel):
    """Valores retidos conhecidos (string decimal) ou null quando ausentes/não informados."""

    iss: str | None
    irrf: str | None
    inss: str | None
    pis: str | None
    cofins: str | None
    csll: str | None
    pcc_total: str | None = Field(description="Somente quando a fonte traz PCC consolidado; nunca derivado/rateado.")


class DocumentServiceOut(BaseModel):
    national_code: str | None
    municipal_code: str | None
    nbs_code: str | None


class ReviewHint(BaseModel):
    code: str
    message: str


class DocRetentionIss(BaseModel):
    retained: bool = Field(description="tpRetISSQN = 2/3 (retido pelo tomador/intermediário).")
    amount: str | None = Field(description="vISSQN quando retido.")


class DocRetentionCovered(BaseModel):
    pis: bool | None
    cofins: bool | None
    csll: bool | None


class DocRetentionContrib(BaseModel):
    amount: str | None = Field(description="Valor consolidado retido (vRetCSLL; modalidade 1: vRetCSLL+vPis+vCofins, NT 008 v1.02).")
    code: str | None = Field(description="tpRetPisCofins (modalidade).")
    label: str | None
    covered: DocRetentionCovered | None = Field(description="Quais contribuições a modalidade indica como retidas.")
    consolidated: bool = Field(description="Sempre true: o XML não separa o valor por tributo; não há rateio.")
    note: str


class DocRetentionInformed(BaseModel):
    pis: str | None = Field(description="vPis informado, não retido (null na modalidade 1, em que a NT 008 o apresenta como retido).")
    cofins: str | None = Field(description="vCofins informado, não retido (null na modalidade 1).")


class DocumentRetentionSummary(BaseModel):
    classification: RetentionClass
    iss: DocRetentionIss
    inss: str | None = Field(description="vRetCP retido.")
    irrf: str | None = Field(description="vRetIRRF retido.")
    contributions: DocRetentionContrib
    informed_not_withheld: DocRetentionInformed
    total_withheld: str | None = Field(description="vTotalRet da NFS-e (valor oficial).")
    total_withheld_source: str | None


class DocumentOut(BaseModel):
    id: str
    direction: Direction
    company_role: str
    number: str | None
    access_key: str
    dps_id: str | None
    status: DocumentStatus
    status_code: str | None
    status_code_label: str | None = Field(None, description="Rótulo do cStat original (ex.: 101 = NFS-e de Substituição Gerada).")
    cancellation_event_type: str | None = Field(None, description="Evento que cancelou (ex.: e105102 = por substituição).")
    substitution: "SubstitutionOut | None" = None
    competence: str | None
    service_date: str | None
    issued_at: datetime | None
    processed_at: datetime | None
    issuer: PartyOut
    recipient: PartyOut | None
    intermediary: PartyOut | None
    description: str | None
    service: DocumentServiceOut
    amount: str | None
    deductions: str | None
    net_amount: str | None
    iss_amount: str | None
    total_retained: str | None
    retentions: Retentions
    retention_indicators: dict[str, Any] = Field(
        description="Indicação de retenção separada do valor: {iss|pis|cofins|csll|irrf|inss: {status, source, ...}}.")
    review_hints: list[ReviewHint]
    review_status: ReviewStatus | None
    review_note: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    version: int
    xml_available: bool
    danfse_available: bool = Field(description="true quando há XML oficial e o documento não é simulação.")
    danfse_watermark: Literal["CANCELADA", "SUBSTITUÍDA"] | None = Field(
        None, description="Marca d'água do DANFSe pelos eventos oficiais (NT 008, item 2.5).")
    retention_summary: DocumentRetentionSummary
    source: Literal["ADN", "IMPORT", "EMISSION"]
    simulated: bool = Field(description="true = dado de simulação local (mock), sem validade fiscal.")
    environment: Environment
    customer_id: str | None


class SubstitutionOut(BaseModel):
    substitutes_access_key: str | None = Field(description="Chave da NFS-e substituída por esta (DPS/subst/chSubstda).")
    reason_code: str | None = Field(description="subst/cMotivo.")
    substituted_by_access_key: str | None = Field(description="Chave da NFS-e que substituiu esta.")


class EventOut(BaseModel):
    id: str
    event_id: str
    event_type: str
    description: str | None
    sequence: str | None
    occurred_at: datetime | None
    cancels_document: bool
    substitute_access_key: str | None = None
    source: str
    simulated: bool


class IbsCbsOficial(BaseModel):
    valores: dict[str, str] = Field(description="Folhas de infNFSe/IBSCBS (caminho relativo → texto exato do XML).")
    dps: dict[str, str] = Field(default_factory=dict, description="Códigos informados na DPS (cIndOp, CST, cClassTrib…).")
    fonte: str


class DocumentDetail(DocumentOut):
    events: list[EventOut]
    ibs_cbs_official: IbsCbsOficial | None = Field(
        None, description="IBS/CBS calculados e autorizados pela Sefin para esta NFS-e (não é a prévia local).")


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    page: int
    page_size: int
    total: int


class ReviewPatch(Strict):
    review_status: ReviewStatus
    note: str | None = Field(None, max_length=2000)
    expected_version: int = Field(ge=1)


# ---------- lotes ----------
class BatchDraftRef(Strict):
    id: str = UUIDStr
    expected_version: int = Field(ge=1)


class BatchCreate(Strict):
    drafts: list[BatchDraftRef] = Field(min_length=1, max_length=500)
    scheduled_at: AwareDatetime = Field(description="Instante com offset, ex.: 2026-11-01T00:01:00-03:00.")
    timezone: str = Field(min_length=3, max_length=64, description="Fuso IANA da empresa.")


class BatchCreated(BaseModel):
    batch_id: str
    status: Literal["SCHEDULED"]
    scheduled_at: datetime
    total: int


class ItemError(BaseModel):
    code: str | None
    message: str | None


class BatchItemOut(BaseModel):
    id: str
    draft_id: str
    draft_version: int
    status: QueueStatus
    competence: str | None
    customer_name: str | None
    amount: str | None
    total_withheld: str | None = Field(None, description="Do snapshot congelado (retention_summary).")
    net_amount: str | None = Field(None, description="Do snapshot congelado (retention_summary).")
    dps_id: str | None
    attempts: int
    next_attempt_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    late_seconds: int | None = Field(description="Atraso entre o horário agendado e o início real (registrado, nunca ocultado).")
    last_error: ItemError | None
    result: dict[str, Any] | None
    document_id: str | None
    simulated: bool


class Progress(BaseModel):
    done: int
    total: int
    percent: int


class BatchOut(BaseModel):
    id: str
    status: str = Field(description="SCHEDULED | PROCESSING | COMPLETED | COMPLETED_WITH_REJECTIONS | WITHDRAWN")
    scheduled_at: datetime
    scheduled_local: str
    timezone: str
    environment: Environment
    simulated: bool
    total: int
    counts: dict[str, int]
    progress: Progress
    created_at: datetime
    created_by: str


class BatchDetail(BatchOut):
    items: list[BatchItemOut]


class BatchPage(BaseModel):
    items: list[BatchOut]
    page: int
    page_size: int
    total: int


class CancelPendingOut(BaseModel):
    batch_id: str
    withdrawn: int
    not_withdrawn: int


# ---------- jobs / sync / exportação ----------
class JobQueued(BaseModel):
    job_id: str
    status: Literal["QUEUED", "RUNNING"]


class JobErrorOut(BaseModel):
    file: str | None
    code: str
    message: str


class JobOut(BaseModel):
    id: str
    type: Literal["IMPORT", "EXPORT", "SYNC", "BUNDLE"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "PARTIAL", "FAILED"]
    environment: Environment
    processed_count: int
    error_count: int
    errors: list[JobErrorOut]
    errors_page: int
    errors_page_size: int
    errors_total_stored: int
    errors_truncated: bool
    result: dict[str, Any] | None
    download_available: bool
    stale_pdf_documents: int = Field(0, description="Notas cujo PDF no ZIP ficou desatualizado (situação mudou); download bloqueado.")
    stale_documents: int = Field(0, description="Notas do ZIP/XLSX com cancelamento ou substituição registrado depois do "
                                                "início da geração; download bloqueado (409 BUNDLE_STALE / EXPORT_STALE).")
    output_expires_at: datetime | None = Field(None, description="Após esta data o arquivo é apagado (410 EXPORT_EXPIRED).")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BundleFilters(Strict):
    direction: Direction | None = None
    query: str | None = Field(None, max_length=100)
    status: DocumentStatus | None = None
    review_status: ReviewStatus | None = None
    competence: str | None = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    date_from: date | None = None
    date_to: date | None = None
    retention: RetentionClass | None = None
    retention_type: list[RetentionType] = Field(default_factory=list)
    retention_match: RetentionMatch | None = None
    withheld_min: str | None = Field(None, pattern=r"^\d{1,13}(\.\d{1,2})?$")
    withheld_max: str | None = Field(None, pattern=r"^\d{1,13}(\.\d{1,2})?$")


class BundleCreate(Strict):
    """Exatamente um entre document_ids (seleção) e filters (TODOS os resultados, além da página atual)."""

    format: Literal["PDF", "XML", "PDF_XML"]
    environment: Literal["mock", "restricted", "production"] | None = None
    document_ids: list[str] | None = Field(None, min_length=1, max_length=5000)
    filters: BundleFilters | None = None


class DownloadTicketOut(BaseModel):
    ticket: str = Field(description="Uso único, 60 s; enviar no CORPO de um POST de formulário (campo 'ticket').")
    expires_at: datetime
    method: Literal["POST"]
    action: str
    field: Literal["ticket"]


class ExportCreate(Strict):
    format: Literal["xlsx"]
    environment: Literal["mock", "restricted", "production"] | None = Field(
        None, description="Ambiente consultado (padrão: produção quando a empresa lê o ADN de produção).")
    direction: Direction | None = None
    query: str | None = Field(None, max_length=100)
    status: DocumentStatus | None = None
    review_status: ReviewStatus | None = None
    competence: str | None = Field(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    date_from: date | None = None
    date_to: date | None = None
    retention: RetentionClass | None = None
    retention_type: list[RetentionType] = Field(default_factory=list)
    retention_match: RetentionMatch | None = None
    withheld_min: str | None = Field(None, pattern=r"^\d{1,13}(\.\d{1,2})?$")
    withheld_max: str | None = Field(None, pattern=r"^\d{1,13}(\.\d{1,2})?$")
    page: int | None = Field(None, description="Ignorado: a exportação inclui todos os resultados do filtro.")
    page_size: int | None = Field(None, description="Ignorado: a exportação inclui todos os resultados do filtro.")


class SyncError(BaseModel):
    code: str | None
    message: str | None


class SyncStatus(BaseModel):
    enabled: bool
    interval_hours: int
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    next_run_at: datetime | None
    status: Literal["PENDING", "NOT_CONFIGURED", "BLOCKED", "RUNNING", "DRAINING", "OK", "ERROR"]
    environment: Environment
    simulated: bool
    processed_count: int
    received_count: int
    pending_parse_count: int
    coverage_message: str
    last_error: SyncError | None
    running: bool


class Dashboard(BaseModel):
    environment: Environment
    simulated: bool
    documents: dict[str, int]
    drafts: dict[str, int]
    queue: dict[str, int]
    next_scheduled_at: datetime | None
    sync: SyncStatus



# ---------- cancelamento (eventos do emitente) ----------
class CancellationConfirm(Strict):
    """Eco do que o usuário viu na confirmação; o servidor recusa (409 CONFIRMATION_MISMATCH) se divergir."""
    access_key: str = Field(pattern=r"^[0-9]{6}[0-9A-Z]{14}[0-9]{30}$")
    environment: Environment
    company_cnpj: str = Field(pattern=r"^[0-9A-Z]{12}[0-9]{2}$")
    document_number: str = Field(max_length=20)


class CancellationCreate(Strict):
    kind: Literal["CANCEL", "ANALYSIS"] = Field(description="CANCEL = evento 101101; ANALYSIS = evento 101103 "
                                                            "(solicitação de análise fiscal ao município).")
    reason_code: Literal["1", "2", "9"] = Field(description="cMotivo: 1 Erro na Emissão; 2 Serviço não Prestado; 9 Outros.")
    reason_text: str = Field(min_length=15, max_length=255,
                             description="xMotivo (TSMotivo): 15 a 255, sem quebra de linha nem espaço nas pontas.")
    confirm: CancellationConfirm


class CancellationIssue(BaseModel):
    code: str
    message: str


class CancellationRequestOut(BaseModel):
    id: str
    document_id: str
    environment: Environment
    access_key: str
    kind: Literal["CANCEL", "ANALYSIS"]
    event_code: str
    reason_code: str
    reason_label: str | None
    reason_text: str
    status: Literal["QUEUED", "PROCESSING", "AWAITING_CONFIRMATION", "REGISTERED", "REJECTED", "BLOCKED", "WITHDRAWN"]
    simulated: bool
    attempts: int
    requested_by: str
    requested_at: datetime
    finished_at: datetime | None
    last_error_code: str | None
    last_error_message: str | None
    errors: list[dict[str, Any]]
    result: dict[str, Any] | None
    suggest_analysis: bool = Field(description="Recusa municipal (E0822/E0823/E0824/E0827): cabe a análise fiscal.")
    analysis: dict | None = Field(None, description=(
        "Solicitação de análise fiscal registrada: situação (PENDENTE, DEFERIDA, INDEFERIDA) acompanhada pela consulta "
        "dos eventos 105104/105105 do município."))
    guidance: str | None = Field(None, description="Orientação objetiva para o código de recusa (ex.: E0840, E0822).")


class CancellationEventOut(BaseModel):
    event_type: str
    description: str | None
    occurred_at: datetime | None
    cancels_document: bool
    simulated: bool


class CancellationInfo(BaseModel):
    document: dict[str, Any]
    company: dict[str, Any]
    eligible: dict[str, bool]
    blockers: dict[str, list[CancellationIssue]]
    warnings: list[CancellationIssue]
    events: list[CancellationEventOut]
    requests: list[CancellationRequestOut]
    reasons: list[dict[str, str]]
    kinds: list[dict[str, str]]
    substitution_note: str
    can_request: bool = Field(description="O usuário tem a permissão 'cancel' nesta empresa.")


# ---------- IBS/CBS (alíquotas com vigência e prévia) ----------
class TaxRateParameterOut(BaseModel):
    tributo: Literal["IBS", "CBS"]
    esfera: Literal["UF", "MUN", "FED"]
    aliquota: str = Field(description="Percentual (ex.: '0.1000' = 0,1%).")
    vigencia_inicio: date
    vigencia_fim: date
    fonte: str
    autorizacao: str


class TaxClassificationOut(BaseModel):
    codigo: str
    cst: str
    nome: str
    cst_nome: str
    reducao_ibs: str
    reducao_cbs: str
    tributacao_integral: bool
    exige_tributacao_regular: bool


class TaxClassificationPage(BaseModel):
    fonte: str
    publicacao: str
    em: date
    items: list[TaxClassificationOut]


class IbsCbsIssue(BaseModel):
    code: str
    message: str


class IbsCbsPreview(BaseModel):
    """PRÉVIA local (Decimal, 2 casas). O valor oficial é calculado pela Sefin; a DPS não leva alíquota nem valor."""
    status: Literal["CALCULADO", "PENDENTE", "NAO_APLICAVEL_SIMPLES"]
    fato_gerador: str | None
    aliquotas: dict[str, Any] | None
    base: dict[str, Any] | None
    valores: dict[str, Any] | None
    classificacao: dict[str, Any] | None
    pendencias: list[IbsCbsIssue]
    observacoes: list[str]
    calculado_por: str
