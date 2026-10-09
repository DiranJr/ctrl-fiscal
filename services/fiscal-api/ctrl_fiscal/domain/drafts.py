"""Rascunhos de NFS-e: criação, cópia por referência, edição com versão e validação.

A cópia NUNCA leva número, chave, protocolo, assinatura, Id DPS, estado fiscal,
agendamento ou aprovação anterior. Cliente/serviço/valores são referência
editável e a competência é revisável.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ctrl_common.fiscal_types import is_valid_cnpj, is_valid_cpf

from ..clients import CompanyInfo, CoreClient
from ..models import BatchItem, Document, Draft
from . import retencoes
from .common import DomainError, audit, money
from .regras_dps import dominio_issues, negocio_issues

EDITABLE_STATUSES = ("DRAFT", "REJECTED")
TAX_KEYS = (
    "issqn_taxation", "iss_retention", "iss_rate", "pis_cofins_retention",
    "irrf_withheld", "inss_withheld", "csll_withheld", "pis_cofins_cst", "approx_tax", "ibs_cbs",
    "pis_cofins_base", "pis_rate", "cofins_rate", "pis_amount", "cofins_amount", "immunity_type",
)
OFFICIAL_ENVS = ("restricted", "production")
# Anexo I (RN DPS_NFS-e, linhas 580/581): cIndOp de operações com imóveis exige o grupo "imovel" (não suportado
# nesta etapa) quando cTribNac não é um destes subitens de construção civil.
IMOVEL_CINDOP = ("020101", "020201", "020301")
CONSTRUCAO_CTRIBNAC = ("070201", "070202", "070401", "070501", "070502", "070601", "070602", "070701", "070801",
                       "071701", "071901")


def customer_snapshot(c: dict) -> dict:
    return {
        "id": c["id"], "name": c["name"], "document_type": c["document_type"], "document": c["document"],
        "email": c.get("email"), "phone": c.get("phone"), "municipal_registration": c.get("municipal_registration"),
        "municipality_code": c.get("municipality_code"), "postal_code": c.get("postal_code"),
        "address": c.get("address"), "is_active": c.get("is_active", True),
    }


def require_customer(core: CoreClient, company_id: str, customer_id: str) -> dict:
    c = core.customer(company_id, customer_id)
    if c is None:
        raise DomainError(422, "CUSTOMER_NOT_FOUND", "Cliente não encontrado nesta empresa.",
                          [{"field": "customer_id", "code": "CUSTOMER_NOT_FOUND", "message": "Cliente inexistente."}])
    return c


def create_draft(
    s: Session, *, company: CompanyInfo, user_id: str, customer: dict, data: dict, now: datetime,
    source_document_id: str | None = None, trace_id: str | None = None,
) -> Draft:
    d = Draft(
        company_id=company.id,
        environment=company.environment,
        customer_id=customer["id"],
        customer_snapshot=customer_snapshot(customer),
        source_document_id=source_document_id,
        status="DRAFT",
        version=1,
        competence=data["competence"],
        service_date=str(data["service_date"]) if data.get("service_date") else None,
        service_national_code=data.get("service", {}).get("national_code"),
        service_municipal_code=data.get("service", {}).get("municipal_code"),
        service_nbs_code=data.get("service", {}).get("nbs_code"),
        service_location_code=data.get("service", {}).get("location_code"),
        service_obra_code=data.get("service", {}).get("obra_code"),
        service_obra_address=data.get("service", {}).get("obra_address"),
        service_extra=_extra(data.get("service") or {}),
        service_profile_id=(data.get("_service_profile") or {}).get("id"),
        service_profile_version=(data.get("_service_profile") or {}).get("version"),
        service_description=data.get("service", {}).get("description") or "",
        amount=Decimal(data["amount"]),
        deductions=Decimal(data.get("deductions") or "0.00"),
        discount_unconditional=_opt_dec(data.get("unconditional_discount")),
        discount_conditional=_opt_dec(data.get("conditional_discount")),
        tax_settings=_tax(data.get("tax_settings")),
        issues=[],
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    s.add(d)
    s.flush()
    audit(s, now, "draft.created", company_id=company.id, actor_id=user_id, target_type="draft", target_id=d.id,
          details={"source_document_id": source_document_id}, trace_id=trace_id)
    return d


EXTRA_KEYS = ("obra_cib", "obra_property_registration", "additional_info")


def _extra(svc: dict, base: dict | None = None) -> dict | None:
    out = dict(base or {})
    for k in EXTRA_KEYS:
        if k in svc:
            out[k] = svc[k]
    out = {k: v for k, v in out.items() if v not in (None, "", {}, [])}
    return out or None


def _check_obra_choice(d: Draft) -> None:
    x = d.service_extra or {}
    if sum(bool(v) for v in (d.service_obra_code, x.get("obra_cib"), d.service_obra_address)) > 1:
        raise DomainError(422, "OBRA_CHOICE", "obra: informe cObra OU cCIB OU endereço, não mais de um.",
                          [{"field": "service.obra_address", "code": "OBRA_CHOICE", "message": "Escolha do XSD."}])


def _opt_dec(v) -> Decimal | None:  # noqa: ANN001
    return None if v in (None, "") else Decimal(str(v))


IBS_OPTIONAL_GROUPS = ("regular", "deferral", "presumed_credit", "real_estate", "recipient")


def _ibs(v: dict | None) -> dict | None:
    """Grupos condicionais ausentes não são guardados como null (o grupo só existe quando informado)."""
    if not v:
        return v
    return {k: x for k, x in v.items() if not (k in IBS_OPTIONAL_GROUPS and x in (None, {}, ""))}


def _tax(t: dict | None) -> dict:
    t = t or {}
    out = {k: t.get(k) for k in TAX_KEYS}
    out["ibs_cbs"] = _ibs(out["ibs_cbs"])
    return out


def default_competence(company: CompanyInfo, now: datetime) -> str:
    return now.astimezone(ZoneInfo(company.timezone)).strftime("%Y-%m")


def draft_from_document(
    s: Session, *, company: CompanyInfo, core: CoreClient, user_id: str, source_document_id: str, now: datetime,
    trace_id: str | None,
) -> Draft:
    doc = s.scalar(select(Document).where(Document.id == source_document_id, Document.company_id == company.id))
    if doc is None:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", "Documento de referência não encontrado.")
    if doc.direction != "ISSUED":
        raise DomainError(422, "SOURCE_NOT_ISSUED", "Somente notas emitidas pela empresa podem servir de referência.")
    if doc.environment != company.environment:
        raise DomainError(422, "SOURCE_OTHER_ENVIRONMENT", "Documento de outro ambiente não pode ser copiado.")
    customer = core.customer(company.id, doc.customer_id) if doc.customer_id else None
    if customer is None and doc.recipient_doc:
        customer = core.customer_by_document(company.id, doc.recipient_doc)
    if customer is None:
        # O rascunho exige um tomador CADASTRADO (o cadastro é a fonte dos dados do tomador na DPS). Nada é
        # inventado: a tela oferece cadastrá-lo com os dados lidos do XML oficial (GET /documents/{id}/recipient).
        raise DomainError(422, "CUSTOMER_NOT_LINKED",
                          "O tomador desta nota ainda não está cadastrado em Clientes. Cadastre-o (os dados podem ser "
                          "lidos do XML da nota) e tente novamente.",
                          [{"field": "customer", "code": "CUSTOMER_NOT_REGISTERED",
                            "message": "Tomador da nota sem cadastro nesta empresa."}])
    data = {
        # Competência revisável: parte do mês corrente no fuso da empresa, não da nota antiga.
        "competence": default_competence(company, now),
        "service": {
            "national_code": doc.service_national_code,
            "municipal_code": doc.service_municipal_code,
            "description": doc.description or "",
            "nbs_code": doc.nbs_code,
            "location_code": doc.service_location_code,
            "obra_code": doc.obra_code,
            # Mesmo grupo obra da nota de referência (cCIB/inscImobFisc/endereço). infoCompl (pedido, ART, docRef)
            # é próprio de cada nota e NÃO é copiado.
            **{k: v for k, v in (doc.service_extra or {}).items()
               if k in ("obra_cib", "obra_property_registration", "obra_address")},
        },
        # dCompet não é copiado: depende da nova prestação (fica para revisão).
        "service_date": None,
        "amount": money(doc.amount) or "0.00",
        "deductions": money(doc.deductions) or "0.00",
        "tax_settings": doc.tax_settings or {},
    }
    return create_draft(s, company=company, user_id=user_id, customer=customer, data=data, now=now,
                        source_document_id=doc.id, trace_id=trace_id)


def recipient_from_document(s: Session, *, company: CompanyInfo, core: CoreClient, document_id: str) -> dict:
    """Tomador de uma nota EMITIDA pela empresa, lido do XML oficial guardado (DPS/infDPS/toma), para cadastro
    assistido em Clientes. Só devolve o que está no XML; o que faltar volta vazio e é listado em `missing`."""
    from defusedxml import ElementTree as SafeET

    from ..models import StoredBlob
    from .common import read_blob

    doc = s.scalar(select(Document).where(Document.id == document_id, Document.company_id == company.id))
    if doc is None:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", "Documento não encontrado.")
    if doc.direction != "ISSUED":
        raise DomainError(422, "SOURCE_NOT_ISSUED", "Somente notas emitidas pela empresa têm tomador para cadastro.")
    out: dict = {"document_type": None, "document": None, "name": doc.recipient_name, "municipal_registration": None,
                 "email": None, "phone": None, "municipality_code": None, "postal_code": None,
                 "address": {"street": None, "number": None, "complement": None, "district": None},
                 "foreign": False, "source": "XML" if doc.xml_blob_id else "DOCUMENT"}
    if doc.recipient_doc:
        out["document_type"] = "CNPJ" if len(doc.recipient_doc) == 14 else "CPF" if len(doc.recipient_doc) == 11 else None
        out["document"] = doc.recipient_doc
    blob = s.get(StoredBlob, doc.xml_blob_id) if doc.xml_blob_id else None
    if blob is not None and blob.company_id == company.id:
        ns = "{http://www.sped.fazenda.gov.br/nfse}"
        root = SafeET.fromstring(read_blob(blob), forbid_dtd=True, forbid_entities=True, forbid_external=True)
        toma = root.find(f"{ns}infNFSe/{ns}DPS/{ns}infDPS/{ns}toma")

        def t(path: str) -> str | None:
            el = toma.find("/".join(f"{ns}{p}" for p in path.split("/"))) if toma is not None else None
            return el.text.strip() if el is not None and el.text and el.text.strip() else None

        if toma is not None:
            for kind in ("CNPJ", "CPF"):
                if t(kind):
                    out["document_type"], out["document"] = kind, t(kind)
            out["foreign"] = t("NIF") is not None or toma.find(f"{ns}end/{ns}endExt") is not None
            out.update(name=t("xNome") or out["name"], municipal_registration=t("IM"), email=t("email"), phone=t("fone"),
                       municipality_code=t("end/endNac/cMun"), postal_code=t("end/endNac/CEP"))
            out["address"] = {"street": t("end/xLgr"), "number": t("end/nro"), "complement": t("end/xCpl"),
                              "district": t("end/xBairro")}
    reg = core.customer_by_document(company.id, out["document"]) if out["document"] else None
    out["registered_customer_id"] = reg["id"] if reg else None
    out["missing"] = [k for k in ("document", "name", "municipality_code", "postal_code") if not out.get(k)] + \
        [f"address.{k}" for k in ("street", "number", "district") if not out["address"].get(k)]
    return out


def get_draft(s: Session, company: CompanyInfo, draft_id: str, lock: bool = False) -> Draft:
    q = select(Draft).where(Draft.id == draft_id, Draft.company_id == company.id)
    if lock:
        q = q.with_for_update()
    d = s.scalar(q)
    if d is None:
        raise DomainError(404, "DRAFT_NOT_FOUND", "Rascunho não encontrado.")
    return d


def patch_draft(
    s: Session, d: Draft, *, changes: dict, expected_version: int, user_id: str, core: CoreClient | None,
    now: datetime, trace_id: str | None,
) -> Draft:
    if d.status not in EDITABLE_STATUSES:
        hint = " Retire-o da fila (POST /drafts/{id}/withdraw) antes de editar." if d.status == "SCHEDULED" else ""
        raise DomainError(409, "DRAFT_LOCKED", f"Rascunho em {d.status} não pode ser editado.{hint}")
    if d.version != expected_version:
        raise DomainError(409, "VERSION_CONFLICT", "Rascunho alterado por outra pessoa. Recarregue antes de salvar.")
    if "customer_id" in changes:
        # Troca de tomador ou recarga dos dados atuais do mesmo tomador (cadastro editado no core).
        cust = require_customer(core, d.company_id, changes["customer_id"])
        d.customer_id = cust["id"]
        d.customer_snapshot = customer_snapshot(cust)
    if "service_profile_id" in changes:
        prof = changes.get("_service_profile")
        d.service_profile_id = prof["id"] if prof else None
        d.service_profile_version = prof["version"] if prof else None
    if "competence" in changes:
        d.competence = changes["competence"]
    if "service_date" in changes:
        d.service_date = str(changes["service_date"]) if changes["service_date"] else None
    if "service" in changes and changes["service"] is not None:
        svc = changes["service"]
        if "national_code" in svc:
            d.service_national_code = svc["national_code"]
        if "municipal_code" in svc:
            d.service_municipal_code = svc["municipal_code"]
        if "description" in svc and svc["description"] is not None:
            d.service_description = svc["description"]
        if "nbs_code" in svc:
            d.service_nbs_code = svc["nbs_code"]
        if "location_code" in svc:
            d.service_location_code = svc["location_code"]
        if "obra_code" in svc:
            d.service_obra_code = svc["obra_code"]
        if "obra_address" in svc:
            d.service_obra_address = svc["obra_address"]
        if any(k in svc for k in EXTRA_KEYS):
            d.service_extra = _extra(svc, d.service_extra)
        _check_obra_choice(d)
    if "amount" in changes:
        d.amount = Decimal(changes["amount"])
    if "deductions" in changes:
        d.deductions = Decimal(changes["deductions"] or "0.00")  # null = sem dedução
    if "unconditional_discount" in changes:
        d.discount_unconditional = _opt_dec(changes["unconditional_discount"])
    if "conditional_discount" in changes:
        d.discount_conditional = _opt_dec(changes["conditional_discount"])
    if "tax_settings" in changes:
        merged = dict(d.tax_settings or {})
        # campo a campo; null remove; grupos (approx_tax, ibs_cbs) substituídos inteiros
        merged.update({k: v for k, v in changes["tax_settings"].items() if k in TAX_KEYS})
        merged["ibs_cbs"] = _ibs(merged.get("ibs_cbs"))
        d.tax_settings = merged
    # Qualquer alteração invalida a validação anterior: o conteúdo atual precisa ser validado de novo.
    d.issues = []
    d.validated_version = None
    d.validated_at = None
    if d.status == "REJECTED":
        d.status = "DRAFT"  # correção após rejeição fiscal confirmada
    d.version += 1
    d.updated_at = now
    audit(s, now, "draft.updated", company_id=d.company_id, actor_id=user_id, target_type="draft", target_id=d.id,
          details={"fields": sorted(changes.keys())}, trace_id=trace_id)
    return d


def _issue(field: str, code: str, message: str, severity: str = "error") -> dict:
    return {"field": field, "code": code, "message": message, "severity": severity}


def load_profile(core: CoreClient | None, company_id: str, profile_id: str | None) -> dict | None:
    """Perfil aplicado (situação, versão e pendências), conferido no core — nunca confiado ao navegador."""
    if not profile_id or core is None:
        return None
    p = core.service_profile(company_id, profile_id)
    if p is None:
        raise DomainError(422, "SERVICE_PROFILE_NOT_FOUND", "Perfil de serviço não encontrado neste workspace.",
                          [{"field": "service_profile_id", "code": "NOT_FOUND", "message": "Perfil inexistente."}])
    return p


def profile_issues(d: Draft, profile: dict | None) -> list[dict]:
    """Perfil pendente/inativo bloqueia a transmissão; divergências entre rascunho e perfil viram aviso."""
    if not d.service_profile_id:
        return []
    if profile is None:
        return [_issue("service_profile_id", "SERVICE_PROFILE_NOT_FOUND", "Perfil de serviço aplicado não existe mais.")]
    out = []
    nome = profile.get("name")
    if profile.get("status") == "INACTIVE":
        out.append(_issue("service_profile_id", "SERVICE_PROFILE_INACTIVE",
                          f"Perfil \"{nome}\" está inativo: escolha outro perfil ou desvincule-o."))
    elif profile.get("status") != "APPROVED":
        falta = "; ".join(f"{i['label']}" for i in profile.get("blocking") or [])
        out.append(_issue("service_profile_id", "SERVICE_PROFILE_NOT_APPROVED",
                          f"Perfil \"{nome}\" pendente de revisão: aprove-o em Configurações → Perfis de serviço "
                          "antes de agendar" + (f" (falta: {falta})" if falta else "") + ". O rascunho pode ser preparado."))
    if d.service_profile_version and profile.get("version") != d.service_profile_version:
        out.append(_issue("service_profile_id", "SERVICE_PROFILE_CHANGED",
                          f"O perfil mudou desde a aplicação (versão {d.service_profile_version} → "
                          f"{profile.get('version')}): reaplique o perfil ou confira os campos.", "warning"))
    data = d.service_date
    vf, vt = profile.get("valid_from"), profile.get("valid_to")
    if data and ((vf and data < vf) or (vt and data > vt)):
        out.append(_issue("service_profile_id", "SERVICE_PROFILE_OUT_OF_VALIDITY",
                          f"Data da prestação fora da vigência do perfil ({vf or '…'} a {vt or '…'})."))
    tax = d.tax_settings or {}
    ibs = tax.get("ibs_cbs") or {}
    pibs = profile.get("ibs_cbs") or {}
    diverg = [rot for rot, a, b in (
        ("cTribNac", d.service_national_code, profile.get("national_code")),
        ("cNBS", d.service_nbs_code, profile.get("nbs_code")),
        ("tribISSQN", tax.get("issqn_taxation"), profile.get("issqn_taxation")),
        ("cIndOp", ibs.get("operation_indicator"), pibs.get("operation_indicator")),
        ("cClassTrib", ibs.get("class_code"), pibs.get("class_code")),
    ) if b and a != b]
    if diverg:
        out.append(_issue("service_profile_id", "SERVICE_PROFILE_DIVERGENT",
                          "Rascunho diverge do perfil aplicado em: " + ", ".join(diverg) + " (permitido; confira).",
                          "warning"))
    return out


def validate_draft(d: Draft, company: CompanyInfo, customer: dict | None, now: datetime,
                   profile: dict | None = None) -> list[dict]:
    """Validações locais e de formato do leiaute verificado. Não autoriza nem transmite."""
    issues: list[dict] = profile_issues(d, profile)
    if customer is None:
        issues.append(_issue("customer_id", "CUSTOMER_NOT_FOUND", "Cliente não encontrado."))
    else:
        if not customer.get("is_active", True):
            issues.append(_issue("customer_id", "CUSTOMER_INACTIVE", "Cliente inativo."))
        doc = customer.get("document", "")
        valid = is_valid_cnpj(doc) if customer.get("document_type") == "CNPJ" else is_valid_cpf(doc)
        if not valid:
            issues.append(_issue("customer_id", "CUSTOMER_DOCUMENT_INVALID", "Documento do cliente inválido (E0188/E0206)."))
    if d.amount is None or d.amount <= 0:
        issues.append(_issue("amount", "AMOUNT_NOT_POSITIVE", "Valor do serviço deve ser maior que zero."))
    if d.deductions is not None and d.deductions < 0:
        issues.append(_issue("deductions", "DEDUCTIONS_NEGATIVE", "Deduções não podem ser negativas."))
    if d.amount is not None and d.deductions is not None and d.deductions > d.amount:
        issues.append(_issue("deductions", "DEDUCTIONS_EXCEED_AMOUNT", "Deduções maiores que o valor do serviço."))
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", d.competence or ""):
        issues.append(_issue("competence", "COMPETENCE_INVALID", "Competência deve estar no formato AAAA-MM."))
    else:
        current = now.astimezone(ZoneInfo(company.timezone)).strftime("%Y-%m")
        if d.competence > current:
            issues.append(_issue("competence", "COMPETENCE_IN_FUTURE", "Competência posterior ao mês atual da empresa.", "warning"))
    desc = (d.service_description or "").strip()
    if not desc:
        issues.append(_issue("service.description", "DESCRIPTION_REQUIRED", "Descrição do serviço obrigatória."))
    elif len(desc) > 2000:  # xDescServ: TSDesc2000
        issues.append(_issue("service.description", "DESCRIPTION_TOO_LONG", "Descrição excede 2000 caracteres."))
    if not d.service_national_code:
        issues.append(_issue("service.national_code", "NATIONAL_CODE_REQUIRED", "Código de tributação nacional obrigatório."))
    elif not re.match(r"^[0-9]{6}$", d.service_national_code):  # cTribNac: TSCodTribNac [0-9]{6}
        issues.append(_issue("service.national_code", "NATIONAL_CODE_FORMAT", "Código de tributação nacional deve ter 6 dígitos."))
    tax = d.tax_settings or {}
    if tax.get("issqn_taxation") is None:  # tribISSQN [1..1]
        issues.append(_issue("tax_settings.issqn_taxation", "ISSQN_TAXATION_REQUIRED", "Informe a tributação do ISSQN."))
    if tax.get("iss_retention") is None:  # tpRetISSQN [1..1]
        issues.append(_issue("tax_settings.iss_retention", "ISS_RETENTION_REQUIRED", "Informe se o ISS é retido."))
    # Mesmas contas exibidas em retention_summary (retencoes.resumo): total retido sem vPis/vCofins; líquido ≥ 0.
    for field, code, msg, sev, rule in retencoes.issues(retention_summary(d)):
        issues.append({**_issue(field, code, msg, sev), "rule": rule})
    issues.extend(dominio_issues(d, customer))
    # IBS/CBS: exigência legal (vale em qualquer ambiente; a simulação também mostra as pendências reais).
    from . import ibscbs  # noqa: PLC0415

    issues.extend(ibscbs.validacao_issues(d, company.fiscal_profile, d.environment))
    if company.environment == "mock":
        issues.append(_issue("environment", "SIMULATION_ONLY", "Ambiente de simulação: nenhuma NFS-e real será gerada.", "info"))
    else:
        issues.extend(_official_issues(d, company))
        issues.extend(negocio_issues(d, company, customer, now))
    return issues


def record_validation(d: Draft, issues: list[dict], now: datetime) -> None:
    """Guarda o resultado para a versão ATUAL; qualquer edição posterior o invalida."""
    d.issues = issues
    d.validated_version = d.version
    d.validated_at = now


def validation_state(d: Draft) -> dict:
    if d.validated_version is None or d.validated_version != d.version:
        return {"status": "NOT_VALIDATED", "validated_version": d.validated_version, "validated_at": d.validated_at}
    bad = any(i.get("severity") == "error" for i in (d.issues or []))
    return {"status": "INVALID" if bad else "VALID", "validated_version": d.validated_version, "validated_at": d.validated_at}


def withdraw_draft(s: Session, d: Draft, *, expected_version: int, user_id: str, now: datetime,
                   trace_id: str | None) -> Draft:
    """Retira da fila o item do rascunho SOMENTE se nada pode ter sido transmitido (SCHEDULED, fase NONE e sem
    lease ativo). O item vira WITHDRAWN (fencing invalida qualquer lease residual) e o rascunho volta a DRAFT."""
    if d.version != expected_version:
        raise DomainError(409, "VERSION_CONFLICT", "Rascunho alterado por outra pessoa. Recarregue antes de retirar.")
    if d.status != "SCHEDULED" or not d.current_item_id:
        raise DomainError(409, "DRAFT_NOT_WITHDRAWABLE", f"Rascunho em {d.status}: não há agendamento retirável.")
    item = s.scalar(select(BatchItem).where(BatchItem.id == d.current_item_id).with_for_update())
    lease_active = item is not None and item.lease_expires_at is not None and item.lease_expires_at > now
    if item is None or item.status != "SCHEDULED" or item.submit_phase != "NONE" or lease_active:
        raise DomainError(409, "ITEM_IN_PROGRESS",
                          "O item já está em processamento ou com envio a confirmar; não pode ser retirado agora.")
    item.status = "WITHDRAWN"
    item.finished_at = now
    item.updated_at = now
    item.fencing_token += 1
    d.status = "DRAFT"
    d.current_item_id = None
    d.version += 1
    d.updated_at = now
    d.issues = []
    d.validated_version = None
    d.validated_at = None
    audit(s, now, "draft.withdrawn", company_id=d.company_id, actor_id=user_id, target_type="draft", target_id=d.id,
          details={"item_id": item.id, "batch_id": item.batch_id}, trace_id=trace_id)
    return d


def _official_issues(d: Draft, company: CompanyInfo) -> list[dict]:
    """Campos obrigatórios da DPS v1.01 (sem valores presumidos)."""
    out: list[dict] = []
    if company.environment == "production":
        out.append(_issue("environment", "PRODUCTION_EMISSION_BLOCKED",
                          "Transmissão em produção bloqueada (prontidão de IBS/CBS e demais requisitos pendentes); o rascunho "
                          "pode ser preparado e editado.", "error"))
    else:
        out.append(_issue("environment", "HOMOLOGATION", "Produção Restrita: NFS-e sem validade fiscal.", "info"))
    if not d.service_date:
        out.append(_issue("service_date", "SERVICE_DATE_REQUIRED", "Informe dCompet (data de início da prestação)."))
    elif d.competence and d.service_date[:7] != d.competence:
        out.append(_issue("service_date", "SERVICE_DATE_OUTSIDE_COMPETENCE", "dCompet deve pertencer à competência."))
    if not d.service_location_code:
        out.append(_issue("service.location_code", "LOCATION_REQUIRED", "Informe o município da prestação (cLocPrestacao)."))
    if d.service_nbs_code is None:
        out.append(_issue("service.nbs_code", "NBS_RECOMMENDED", "cNBS não informado (opcional no XSD; verificar regra municipal).", "warning"))
    tax = d.tax_settings or {}
    if not (tax.get("approx_tax") or {}).get("mode"):
        out.append(_issue("tax_settings.approx_tax", "APPROX_TAX_REQUIRED", "Informe totTrib (Lei 12.741/2012)."))
    ibs = tax.get("ibs_cbs") or None
    if ibs:
        if not d.service_nbs_code:  # Anexo I, E0322
            out.append(_issue("service.nbs_code", "NBS_REQUIRED_WITH_IBS_CBS",
                              "cNBS é obrigatório quando o grupo IBS/CBS é informado (regra E0322)."))
        imovel = ibs.get("real_estate")
        obra_item = d.service_national_code in CONSTRUCAO_CTRIBNAC
        if imovel and obra_item:
            out.append(_issue("tax_settings.ibs_cbs.real_estate", "REAL_ESTATE_NOT_ALLOWED_FOR_OBRA",
                              "Subitem de obra: o grupo 'imovel' do IBS/CBS não é permitido — use o grupo obra do "
                              "serviço (regra E0931)."))
        elif not obra_item and ibs.get("operation_indicator") in IMOVEL_CINDOP and not imovel:
            out.append(_issue("tax_settings.ibs_cbs.real_estate", "REAL_ESTATE_REQUIRED",
                              "Operação com bem imóvel (cIndOp 020101/020201/020301): informe o imóvel (cCIB ou "
                              "endereço) (regra E0932)."))
        elif imovel and not obra_item and ibs.get("operation_indicator") not in IMOVEL_CINDOP:
            out.append(_issue("tax_settings.ibs_cbs.real_estate", "REAL_ESTATE_NOT_ALLOWED",
                              "Grupo 'imovel' só para operações com bem imóvel (cIndOp 020101/020201/020301) "
                              "(regra E0928)."))
    pis_detail = ("pis_cofins_retention", "pis_cofins_base", "pis_rate", "cofins_rate", "pis_amount", "cofins_amount")
    if any(tax.get(k) is not None for k in pis_detail) and not tax.get("pis_cofins_cst"):
        out.append(_issue("tax_settings.pis_cofins_cst", "PIS_COFINS_CST_REQUIRED", "CST obrigatório quando informar PIS/COFINS."))
    perfil = company.fiscal_profile or {}
    out.extend(_paliq_issues(tax, perfil))
    out.extend(_paliq_convenio_warning(tax, perfil))
    if not company.municipality_code:
        out.append(_issue("company.municipality_code", "COMPANY_MUNICIPALITY_REQUIRED", "Perfil fiscal: município do emitente."))
    if not perfil.get("simples_option"):
        out.append(_issue("company.simples_option", "COMPANY_SIMPLES_REQUIRED", "Perfil fiscal: opSimpNac."))
    if perfil.get("special_regime") is None:
        out.append(_issue("company.special_regime", "COMPANY_SPECIAL_REGIME_REQUIRED", "Perfil fiscal: regEspTrib."))
    return out


def _paliq_issues(tax: dict, perfil: dict) -> list[dict]:
    """pAliq (Anexo I, RN DPS_NFS-e 501–506). A alíquota nunca é calculada; quando informada, estas regras
    oficiais de rejeição são antecipadas localmente."""
    rate = tax.get("iss_rate")
    if rate in (None, ""):
        return []
    out = []
    def add(code: str, rule: str, msg: str) -> None:  # noqa: E306
        out.append(_issue("tax_settings.iss_rate", code, f"{msg} (regra {rule})."))
    if Decimal(str(rate)) > Decimal("5"):
        add("PALIQ_ABOVE_5", "E0595", "Alíquota acima de 5% não é permitida")
    if perfil.get("simples_option") == "2":
        add("PALIQ_NOT_ALLOWED_MEI", "E0600", "Prestador MEI não informa alíquota")
    if tax.get("issqn_taxation") in ("IMMUNE", "EXPORT", "NOT_INCIDENT"):
        add("PALIQ_NOT_ALLOWED_TAXATION", "E0602", "Sem alíquota para imunidade, exportação ou não incidência")
    if perfil.get("special_regime") not in (None, "0"):
        add("PALIQ_NOT_ALLOWED_SPECIAL_REGIME", "E0604", "Sem alíquota com regime especial de tributação")
    return out


def _paliq_convenio_warning(tax: dict, perfil: dict) -> list[dict]:
    """RN 506/507 dependem do convênio do município de INCIDÊNCIA no ambiente (consultado na transmissão)."""
    if perfil.get("simples_option") != "1":
        return []
    informed = tax.get("iss_rate") not in (None, "")
    return [_issue("tax_settings.iss_rate", "PALIQ_DEPENDS_ON_INCIDENCE_CONVENIO",
                   ("Alíquota informada: será recusada (E0617) se o convênio do município de incidência estiver ativo "
                    "neste ambiente." if informed else
                    "Sem alíquota: será exigida (E0619) se o convênio do município de incidência não estiver ativo "
                    "neste ambiente.") + " Conferido na transmissão pela Parametrização oficial.", "warning")]


def retention_summary(d: Draft) -> dict:
    """Total retido e líquido do rascunho pela fórmula oficial (domain/retencoes.py)."""
    return retencoes.resumo(d.amount, d.discount_unconditional, d.discount_conditional, d.deductions, d.tax_settings)


def draft_out(d: Draft) -> dict:
    return {
        "id": d.id,
        "customer_id": d.customer_id,
        "customer": d.customer_snapshot,
        "source_document_id": d.source_document_id,
        "status": d.status,
        "version": d.version,
        "competence": d.competence,
        "service_date": d.service_date,
        "service_profile_id": d.service_profile_id,
        "service_profile_version": d.service_profile_version,
        "service": {
            "national_code": d.service_national_code,
            "municipal_code": d.service_municipal_code,
            "description": d.service_description,
            "nbs_code": d.service_nbs_code,
            "location_code": d.service_location_code,
            "obra_code": d.service_obra_code,
            "obra_address": d.service_obra_address,
            **{k: (d.service_extra or {}).get(k) for k in EXTRA_KEYS},
        },
        "amount": money(d.amount),
        "deductions": money(d.deductions),
        "unconditional_discount": money(d.discount_unconditional),
        "conditional_discount": money(d.discount_conditional),
        "tax_settings": {k: (d.tax_settings or {}).get(k) for k in TAX_KEYS},
        "retention_summary": retention_summary(d),
        "issues": (d.issues or []) if d.validated_version == d.version else [],
        "validation": validation_state(d),
        "editable": d.status in EDITABLE_STATUSES,
        "environment": d.environment,
        "current_item_id": d.current_item_id,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }
