from __future__ import annotations

import os
from datetime import date, timezone

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ctrl_common.problems import ProblemError, problem_responses, trace_id_for
from ctrl_common.web import PageParams

from ..clients import UpstreamError
from ..domain import batches as batches_d
from ..domain import bundles as bundles_d
from ..domain import cancelamento as cancel_d
from ..domain import drafts as drafts_d
from ..domain import jobs as jobs_d
from ..domain import sync as sync_d
from ..domain.common import DomainError, read_blob
from ..domain.documents import (
    DocumentFilters,
    danfse_available,
    danfse_marca,
    document_out,
    event_out,
    events_for,
    get_document,
    list_documents,
    review_document,
)
from ..models import Batch, BatchItem, Document, Draft, Job, StoredBlob
from . import schemas as sc
from .app import CompanyCtx, company_ctx, ctx, db_session, require_idempotency_key, run_idempotent

router = APIRouter(prefix="/companies/{company_id}")
E = problem_responses(401, 403, 404, 409, 422, 503)


def _env(cc: CompanyCtx, environment: str | None) -> str:
    """Ambiente consultado: padrão = ambiente de emissão; outros só se a empresa os lê (ADN)."""
    env = environment or cc.company.environment
    if env not in cc.company.readable_environments():
        raise ProblemError(403, "ENVIRONMENT_NOT_ENABLED", "Ambiente não habilitado para esta empresa.")
    return env


def _query_env(cc: CompanyCtx, environment: str | None) -> str:
    """Consulta OPERACIONAL de documentos: padrão = produção quando a empresa lê o ADN de produção. Documentos de
    Produção Restrita (evidência de homologação) ficam preservados e só aparecem com environment=restricted."""
    if environment:
        return _env(cc, environment)
    if "production" in cc.company.readable_environments():
        return "production"
    return _env(cc, None)


EnvQuery = Query(None, pattern="^(mock|restricted|production)$",
                 description="Ambiente a consultar (padrão: ambiente de emissão). 'production' = leitura ADN.")


def _core_guard(fn):  # noqa: ANN001, ANN202
    try:
        return fn()
    except UpstreamError:
        raise ProblemError(503, "CORE_UNAVAILABLE", "Cadastro indisponível no momento.")


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", response_model=sc.Dashboard, responses=E, tags=["dashboard"])
def dashboard(request: Request, environment: str | None = EnvQuery, cc: CompanyCtx = Depends(company_ctx("view")),
              s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    now = c.clock.now()
    env = _env(cc, environment)
    base = (Document.company_id == cc.company.id, Document.environment == env)
    docs = {
        "issued_total": s.scalar(select(func.count()).select_from(Document).where(*base, Document.direction == "ISSUED")),
        "received_total": s.scalar(select(func.count()).select_from(Document).where(*base, Document.direction == "RECEIVED")),
        "received_pending_review": s.scalar(select(func.count()).select_from(Document).where(
            *base, Document.direction == "RECEIVED", Document.review_status == "PENDING")),
        "received_divergent": s.scalar(select(func.count()).select_from(Document).where(
            *base, Document.direction == "RECEIVED", Document.review_status == "DIVERGENT")),
        "cancelled": s.scalar(select(func.count()).select_from(Document).where(*base, Document.status == "CANCELLED")),
    }
    drafts = {st: n for st, n in s.execute(
        select(Draft.status, func.count()).where(Draft.company_id == cc.company.id, Draft.environment == env,
                                                 Draft.deleted_at.is_(None)).group_by(Draft.status)
    ).all()}
    queue = {st: n for st, n in s.execute(
        select(BatchItem.status, func.count()).where(BatchItem.company_id == cc.company.id, BatchItem.environment == env)
        .group_by(BatchItem.status)
    ).all()}
    next_at = s.scalar(select(func.min(BatchItem.next_attempt_at)).where(
        BatchItem.company_id == cc.company.id, BatchItem.environment == env, BatchItem.status == "SCHEDULED"))
    try:
        sched = sync_d.ensure_schedule(s, cc.company, now, c.settings.sync_interval_hours, env)
    except DomainError:
        raise
    s.commit()
    return {
        "environment": env, "simulated": env == "mock", "documents": docs, "drafts": drafts, "queue": queue,
        "next_scheduled_at": next_at, "sync": sync_d.status_out(s, sched),
    }


# ---------------------------------------------------------------- documentos
def _filters(
    direction: sc.Direction | None = Query(None),
    query: str | None = Query(None, max_length=100),
    status: sc.DocumentStatus | None = Query(None),
    review_status: sc.ReviewStatus | None = Query(None),
    competence: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    retention: sc.RetentionClass | None = Query(
        None, description="WITH = com retenção efetiva; WITHOUT = sem retenção; UNKNOWN = informação desconhecida."),
    retention_type: list[sc.RetentionType] = Query(
        [], description="Tipos de retenção efetiva: ISS, INSS (CP), IRRF, CONTRIB (PIS/COFINS/CSLL, valor consolidado)."),
    retention_match: sc.RetentionMatch | None = Query(
        None, description="Regra para combinar retention_type: ANY = qualquer um; ALL = todos. Obrigatória com 2+ tipos."),
    withheld_min: str | None = Query(None, pattern=r"^\d{1,13}(\.\d{1,2})?$", description="Total retido mínimo (R$)."),
    withheld_max: str | None = Query(None, pattern=r"^\d{1,13}(\.\d{1,2})?$", description="Total retido máximo (R$)."),
) -> DocumentFilters:
    if date_from and date_to and date_from > date_to:
        raise ProblemError(422, "DATE_RANGE_INVALID", "date_from posterior a date_to.")
    return _build_filters(direction, query, status, review_status, competence, date_from, date_to, retention,
                          retention_type, retention_match, withheld_min, withheld_max)


def _build_filters(direction, query, status, review_status, competence, date_from, date_to, retention,  # noqa: ANN001, ANN202
                   retention_type, retention_match, withheld_min, withheld_max):
    types = list(dict.fromkeys(retention_type or []))
    if len(types) > 1 and not retention_match:
        raise ProblemError(422, "RETENTION_MATCH_REQUIRED",
                           "Com mais de um tipo de retenção, informe retention_match: ANY (qualquer um) ou ALL (todos).")
    lo = Decimal(withheld_min) if withheld_min else None
    hi = Decimal(withheld_max) if withheld_max else None
    if lo is not None and hi is not None and lo > hi:
        raise ProblemError(422, "WITHHELD_RANGE_INVALID", "withheld_min maior que withheld_max.")
    return DocumentFilters(direction, query, status, review_status, competence, date_from, date_to, retention, types,
                           retention_match or "ANY", lo, hi)


@router.get("/documents", response_model=sc.DocumentPage, responses=E, tags=["documents"])
def documents(
    f: DocumentFilters = Depends(_filters), pp: PageParams = Depends(), environment: str | None = EnvQuery,
    cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session),
) -> dict:
    rows, total = list_documents(s, cc.company.id, _query_env(cc, environment), f, cc.company.timezone, pp.offset, pp.page_size)
    return {"items": [document_out(d) for d in rows], "page": pp.page, "page_size": pp.page_size, "total": total}


@router.get("/documents/{document_id}", response_model=sc.DocumentDetail, responses=E, tags=["documents"])
def document_detail(document_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    d = get_document(s, cc.company.id, document_id)
    return {**document_out(d), "events": [event_out(e) for e in events_for(s, d)], "ibs_cbs_official": d.ibs_cbs_official}


def _cancel_info(s: Session, cc: CompanyCtx, d: Document, now, production_enabled: bool) -> dict:  # noqa: ANN001
    el = cancel_d.eligibility(s, cc.company, d, now, production_enabled=production_enabled)
    return {
        "document": {"id": d.id, "number": d.number, "access_key": d.access_key, "environment": d.environment,
                     "status": d.status, "issued_at": d.issued_at, "amount": d.amount, "recipient_doc": d.recipient_doc,
                     "recipient_name": d.recipient_name, "issuer_doc": d.issuer_doc, "direction": d.direction,
                     "simulated": d.simulated},
        "company": {"id": cc.company.id, "name": cc.company.name, "cnpj": cc.company.cnpj},
        **el,
        "requests": cancel_d.history(s, d),
        "reasons": [{"code": k, "label": v} for k, v in cancel_d.MOTIVOS.items()],
        "kinds": [{"code": k, "label": v} for k, v in cancel_d.KIND_LABELS.items()],
        "substitution_note": "Cancelamento por substituição (evento 105102) não é pedido aqui: a Sefin o registra ao "
                             "autorizar uma DPS substituta que informa esta chave.",
        "can_request": "cancel" in cc.permissions,
    }


@router.get("/documents/{document_id}/cancellation", response_model=sc.CancellationInfo, responses=E,
            tags=["cancellation"], summary="Elegibilidade (avaliada no servidor), eventos e histórico de pedidos.")
def cancellation_info(document_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                      s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    return _cancel_info(s, cc, get_document(s, cc.company.id, document_id), c.clock.now(),
                        c.settings.production_cancellation_enabled)


@router.post("/documents/{document_id}/cancellation", status_code=202, response_model=sc.CancellationRequestOut,
             responses=E, tags=["cancellation"],
             summary="Pede o cancelamento (101101) ou a análise fiscal (101103). Exige permissão 'cancel', "
                     "Idempotency-Key e a confirmação de empresa, ambiente, chave e número.")
def request_cancellation(document_id: str, body: sc.CancellationCreate, request: Request,
                         cc: CompanyCtx = Depends(company_ctx("cancel")), s: Session = Depends(db_session),
                         key: str = Depends(require_idempotency_key)) -> JSONResponse:
    c = ctx(request)
    now = c.clock.now()

    def action() -> tuple[int, dict]:
        r = cancel_d.create_request(
            s, cc.company, document_id, kind=body.kind, reason_code=body.reason_code, reason_text=body.reason_text,
            confirm=body.confirm.model_dump(), user_id=cc.user_id, now=now,
            production_enabled=c.settings.production_cancellation_enabled, trace_id=trace_id_for(request))
        s.flush()
        from fastapi.encoders import jsonable_encoder

        return 202, jsonable_encoder(cancel_d.request_out(r))

    status, payload = run_idempotent(s, cc, "cancellation.create", key,
                                     {"document_id": document_id, **body.model_dump(mode="json")}, now, action)
    return JSONResponse(payload, status_code=status)


@router.get("/cancellations/{request_id}", response_model=sc.CancellationRequestOut, responses=E, tags=["cancellation"])
def get_cancellation(request_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    return cancel_d.request_out(cancel_d.get_request(s, cc.company, request_id))


@router.get(
    "/documents/{document_id}/xml", responses={**E, 200: {"content": {"application/xml": {}}, "description": "XML original"}},
    response_class=Response, tags=["documents"],
)
def document_xml(document_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> Response:
    d = get_document(s, cc.company.id, document_id)
    if d.xml_blob_id is None:
        raise ProblemError(409, "XML_UNAVAILABLE", "XML original indisponível para este documento.")
    blob = s.get(StoredBlob, d.xml_blob_id)
    if blob is None or blob.company_id != cc.company.id:
        raise ProblemError(409, "XML_UNAVAILABLE", "XML original indisponível para este documento.")
    return Response(
        read_blob(blob), media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="NFSe-{d.access_key}.xml"',
                 "X-Content-Type-Options": "nosniff"},
    )


@router.post("/documents/import", status_code=202, response_model=sc.JobQueued, responses=problem_responses(401, 403, 413, 422, 503),
             tags=["documents"])
async def import_documents(
    request: Request, files: list[UploadFile] = File(..., description="Arquivos .xml ou .zip"),
    cc: CompanyCtx = Depends(company_ctx("import")), s: Session = Depends(db_session),
) -> dict:
    c = ctx(request)
    if len(files) > c.settings.upload_max_files:
        raise ProblemError(422, "TOO_MANY_FILES", f"Máximo de {c.settings.upload_max_files} arquivos por envio.")
    total = 0
    payload: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read(c.settings.upload_max_bytes + 1)
        total += len(data)
        if total > c.settings.upload_max_bytes:
            raise ProblemError(413, "PAYLOAD_TOO_LARGE", "Envio excede o limite configurado.")
        is_zip = data[:4] == b"PK\x03\x04"
        head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n")
        if not is_zip and not head.startswith(b"<"):
            raise ProblemError(422, "UNSUPPORTED_FILE", "Envie somente arquivos XML ou ZIP.",
                               errors=[{"field": "files", "code": "UNSUPPORTED_FILE", "message": "Arquivo não é XML nem ZIP."}])
        payload.append((f.filename or "arquivo", data))
    job = jobs_d.create_import_job(s, company=cc.company, user_id=cc.user_id, files=payload, now=c.clock.now(),
                                   trace_id=trace_id_for(request))
    s.commit()
    return {"job_id": job.id, "status": "QUEUED"}


@router.patch("/documents/{document_id}/review", response_model=sc.DocumentOut, responses=E, tags=["documents"])
def review(
    document_id: str, body: sc.ReviewPatch, request: Request,
    cc: CompanyCtx = Depends(company_ctx("review")), s: Session = Depends(db_session),
) -> dict:
    d = get_document(s, cc.company.id, document_id, lock=True)
    review_document(s, d, body.review_status, body.note, body.expected_version, cc.user_id, ctx(request).clock.now(),
                    trace_id_for(request))
    s.commit()
    return document_out(d)


@router.get("/customers/{customer_id}/last-issued-document", response_model=sc.DocumentOut, responses=E, tags=["documents"])
def last_issued(customer_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                s: Session = Depends(db_session)) -> dict:
    customer = _core_guard(lambda: ctx(request).core.customer(cc.company.id, customer_id))
    if customer is None:
        raise ProblemError(404, "CUSTOMER_NOT_FOUND", "Cliente não encontrado.")
    d = s.scalar(
        select(Document).where(
            Document.company_id == cc.company.id, Document.environment == cc.company.environment,
            Document.direction == "ISSUED", Document.status == "AUTHORIZED",
            or_(Document.customer_id == customer_id, Document.recipient_doc == customer["document"]),
        ).order_by(Document.issued_at.desc(), Document.id.desc()).limit(1)
    )
    if d is None:
        raise ProblemError(404, "NO_ISSUED_DOCUMENT", "Nenhuma nota autorizada para este cliente neste ambiente.")
    return document_out(d)


# ---------------------------------------------------------------- rascunhos
@router.post("/drafts/from-document", status_code=201, response_model=sc.DraftOut, responses=E, tags=["drafts"])
def draft_from_document(body: sc.DraftFromDocument, request: Request, cc: CompanyCtx = Depends(company_ctx("prepare")),
                        s: Session = Depends(db_session), key: str = Depends(require_idempotency_key)) -> JSONResponse:
    """Rascunho NOVO a partir de nota emitida (sem número, chave, protocolo, assinatura, eventos ou situação).
    Idempotency-Key: clique repetido ou nova tentativa após timeout devolvem o MESMO rascunho."""
    c = ctx(request)
    now = c.clock.now()

    def action() -> tuple[int, dict]:
        from fastapi.encoders import jsonable_encoder

        d = _core_guard(lambda: drafts_d.draft_from_document(
            s, company=cc.company, core=c.core, user_id=cc.user_id, source_document_id=body.source_document_id,
            now=now, trace_id=trace_id_for(request)))
        s.flush()
        return 201, jsonable_encoder(sc.DraftOut.model_validate(drafts_d.draft_out(d)).model_dump(mode="json"))

    status, payload = run_idempotent(s, cc, "drafts.from_document", key, body.model_dump(mode="json"), now, action)
    return JSONResponse(payload, status_code=status)


@router.get("/documents/{document_id}/recipient", response_model=sc.RecipientSuggestion, responses=E, tags=["drafts"],
            summary="Tomador da nota emitida lido do XML oficial (para cadastro assistido em Clientes).")
def document_recipient(document_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                       s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    return _core_guard(lambda: drafts_d.recipient_from_document(s, company=cc.company, core=c.core,
                                                                document_id=document_id))


@router.get("/drafts/{draft_id}/ibs-cbs-preview", response_model=sc.IbsCbsPreview, responses=E, tags=["drafts"],
            summary="Prévia LOCAL do IBS/CBS do rascunho pela vigência do fato gerador (o valor oficial é o da Sefin).")
def ibs_cbs_preview(draft_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    from ..domain import ibscbs

    d = drafts_d.get_draft(s, cc.company, draft_id)
    return ibscbs.previa(s, draft=d, fiscal_profile=cc.company.fiscal_profile,
                         retention_summary=drafts_d.retention_summary(d))


@router.get("/tax-parameters", response_model=list[sc.TaxRateParameterOut], responses=E, tags=["drafts"],
            summary="Alíquotas-padrão de IBS/CBS com vigência, fonte oficial e autorização (somente leitura).")
def tax_parameters(cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> list[dict]:
    from ..models import TaxRateParameter

    return [{"tributo": r.tributo, "esfera": r.esfera, "aliquota": str(r.aliquota), "vigencia_inicio": r.vigencia_inicio,
             "vigencia_fim": r.vigencia_fim, "fonte": r.fonte, "autorizacao": r.autorizacao}
            for r in s.scalars(select(TaxRateParameter).order_by(TaxRateParameter.vigencia_inicio,
                                                                 TaxRateParameter.tributo, TaxRateParameter.esfera))]


@router.get("/tax-classifications", response_model=sc.TaxClassificationPage, responses=E, tags=["drafts"],
            summary="Classificações tributárias (cClassTrib) da tabela OFICIAL válidas para NFS-e na data informada.")
def tax_classifications(on: date = Query(..., description="Data do fato gerador (dCompet)."),
                        cc: CompanyCtx = Depends(company_ctx("view"))) -> dict:
    from ..domain import ibscbs

    meta = ibscbs.tabela()["meta"]
    return {"fonte": meta["fonte"], "publicacao": meta["publicacao_mais_recente"], "em": on,
            "items": [{"codigo": c["codigo"], "cst": c["cst"], "nome": c["nome"], "cst_nome": c["cst_nome"],
                       "reducao_ibs": c["perc_red_ibs"], "reducao_cbs": c["perc_red_cbs"],
                       "tributacao_integral": c["cst"] == "000" and float(c["perc_red_ibs"]) == 0
                       and float(c["perc_red_cbs"]) == 0, "exige_tributacao_regular": c["exige_tributacao_regular"],
                       "permite_diferimento": c["cst_diferimento"],
                       "permite_credito_presumido": c["permite_credito_presumido"]}
                      for c in sorted(ibscbs.classificacoes_vigentes(on), key=lambda x: x["codigo"])]}


@router.get("/drafts", response_model=sc.DraftPage, responses=E, tags=["drafts"])
def list_drafts(
    status: sc.QueueStatus | None = Query(None), customer_id: str | None = Query(None, max_length=36),
    pp: PageParams = Depends(), cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session),
) -> dict:
    q = select(Draft).where(Draft.company_id == cc.company.id, Draft.environment == cc.company.environment,
                            Draft.deleted_at.is_(None))
    if status:
        q = q.where(Draft.status == status)
    if customer_id:
        q = q.where(Draft.customer_id == customer_id)
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    rows = s.scalars(q.order_by(Draft.updated_at.desc(), Draft.id).offset(pp.offset).limit(pp.page_size)).all()
    return {"items": [drafts_d.draft_out(d) for d in rows], "page": pp.page, "page_size": pp.page_size, "total": total}


@router.post("/drafts", status_code=201, response_model=sc.DraftOut, responses=E, tags=["drafts"])
def create_draft(body: sc.DraftCreate, request: Request, cc: CompanyCtx = Depends(company_ctx("prepare")),
                 s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    customer = _core_guard(lambda: drafts_d.require_customer(c.core, cc.company.id, body.customer_id))
    data = body.model_dump()
    data["_service_profile"] = _core_guard(lambda: drafts_d.load_profile(c.core, cc.company.id, body.service_profile_id))
    d = drafts_d.create_draft(s, company=cc.company, user_id=cc.user_id, customer=customer,
                              data=data, now=c.clock.now(), trace_id=trace_id_for(request))
    s.commit()
    return drafts_d.draft_out(d)


@router.get("/drafts/{draft_id}", response_model=sc.DraftOut, responses=E, tags=["drafts"])
def get_draft(draft_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    return drafts_d.draft_out(drafts_d.get_draft(s, cc.company, draft_id))


@router.patch("/drafts/{draft_id}", response_model=sc.DraftOut, responses=E, tags=["drafts"])
def patch_draft(draft_id: str, body: sc.DraftPatch, request: Request, cc: CompanyCtx = Depends(company_ctx("prepare")),
                s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    d = drafts_d.get_draft(s, cc.company, draft_id, lock=True)
    changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "service_profile_id" in changes:
        changes["_service_profile"] = _core_guard(
            lambda: drafts_d.load_profile(c.core, cc.company.id, changes["service_profile_id"]))
    _core_guard(lambda: drafts_d.patch_draft(s, d, changes=changes, expected_version=body.expected_version,
                                             user_id=cc.user_id, core=c.core, now=c.clock.now(),
                                             trace_id=trace_id_for(request)))
    s.commit()
    return drafts_d.draft_out(d)


@router.post("/drafts/{draft_id}/operation-config", response_model=sc.DraftOut, responses=E, tags=["drafts"],
             summary="Configuração tributária da operação do rascunho: reutiliza a aprovada equivalente ou registra "
                     "a variante para revisão. Não aprova, não agenda e não emite.")
def register_operation(draft_id: str, body: sc.OperationRegister, request: Request,
                       cc: CompanyCtx = Depends(company_ctx("prepare")), s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    d = drafts_d.get_draft(s, cc.company, draft_id, lock=True)
    _core_guard(lambda: drafts_d.registrar_operacao(s, d, expected_version=body.expected_version, core=c.core,
                                                    user_id=cc.user_id, now=c.clock.now(),
                                                    trace_id=trace_id_for(request)))
    s.commit()
    return drafts_d.draft_out(d)


@router.post("/drafts/{draft_id}/validate", response_model=sc.ValidationOut, responses=E, tags=["drafts"])
def validate(draft_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("prepare")),
             s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    d = drafts_d.get_draft(s, cc.company, draft_id, lock=True)
    customer = _core_guard(lambda: c.core.customer(cc.company.id, d.customer_id))
    profile = _core_guard(lambda: c.core.service_profile(cc.company.id, d.service_profile_id)
                          if d.service_profile_id else None)
    issues = drafts_d.validate_draft(d, cc.company, customer, c.clock.now(), profile,
                                     production_emission_enabled=c.settings.production_emission_enabled)
    drafts_d.record_validation(d, issues, c.clock.now())  # vale só para a versão atual; não autoriza nada
    s.commit()
    from ..domain import pendencias  # noqa: PLC0415

    return {"valid": not any(i["severity"] == "error" for i in issues), "issues": pendencias.detalhar_todas(issues, d),
            "validated_version": d.version}


@router.post("/retention-preview", response_model=sc.RetentionPreviewOut, responses=E, tags=["drafts"],
             summary="Resumo canônico de retenções para valores ainda não salvos (mesma função do rascunho).")
def retention_preview(body: sc.RetentionPreviewIn, cc: CompanyCtx = Depends(company_ctx("view"))) -> dict:
    from ..domain import retencoes

    tax = body.tax_settings.model_dump(exclude_none=True, mode="json") if body.tax_settings else {}
    summary = retencoes.resumo(body.amount, body.unconditional_discount, body.conditional_discount, body.deductions, tax)
    issues = [{"field": f, "code": c, "message": m, "severity": sev, "rule": r}
              for f, c, m, sev, r in retencoes.issues(summary)]
    return {"retention_summary": summary, "issues": issues}


@router.post("/drafts/{draft_id}/withdraw", response_model=sc.DraftOut, responses=E, tags=["drafts"],
             summary="Retira da fila o agendamento do rascunho, se ainda não iniciado; o rascunho volta a DRAFT.")
def withdraw_draft(draft_id: str, body: sc.DraftWithdraw, request: Request,
                   cc: CompanyCtx = Depends(company_ctx("schedule")), s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    d = drafts_d.get_draft(s, cc.company, draft_id, lock=True)
    drafts_d.withdraw_draft(s, d, expected_version=body.expected_version, user_id=cc.user_id, now=c.clock.now(),
                            trace_id=trace_id_for(request))
    s.commit()
    return drafts_d.draft_out(d)


@router.delete("/drafts/{draft_id}", response_model=sc.DraftDeleted, responses=E, tags=["drafts"],
               summary="Exclui (logicamente) um rascunho sem transmissão nem vínculo com processamento fiscal. "
                       "Agendado exige cancelar o agendamento antes. Repetir devolve o mesmo resultado.")
def delete_draft(draft_id: str, request: Request,
                 expected_version: int = Query(ge=1, description="Versão do rascunho que o usuário confirmou."),
                 cc: CompanyCtx = Depends(company_ctx("prepare")), s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    d, agora = drafts_d.delete_draft(s, cc.company, draft_id, expected_version=expected_version, user_id=cc.user_id,
                                     now=c.clock.now(), trace_id=trace_id_for(request))
    s.commit()
    return {"id": d.id, "deleted": True, "deleted_at": d.deleted_at, "version": d.version, "already_deleted": not agora}


# ---------------------------------------------------------------- lotes
@router.post("/batches", status_code=202, response_model=sc.BatchCreated, responses=E, tags=["batches"])
def create_batch(body: sc.BatchCreate, request: Request, cc: CompanyCtx = Depends(company_ctx("schedule")),
                 s: Session = Depends(db_session), key: str = Depends(require_idempotency_key)) -> JSONResponse:
    c = ctx(request)
    now = c.clock.now()
    if "emit" not in cc.permissions:  # agendar um lote = pedir transmissão: exige também "emit"
        raise ProblemError(403, "PERMISSION_DENIED", "Permissão necessária: emit.")

    if body.mode == "SCHEDULED" and body.scheduled_at is None:
        raise ProblemError(422, "SCHEDULE_REQUIRED", "Informe a data/hora do agendamento (ou use emitir agora).")

    def action() -> tuple[int, dict]:
        # Emitir agora: o instante é o do SERVIDOR (o do navegador é ignorado); o worker o pega no próximo ciclo.
        quando = now if body.mode == "NOW" else body.scheduled_at
        b = _core_guard(lambda: batches_d.create_batch(
            s, company=cc.company, core=c.core, user_id=cc.user_id,
            drafts=[d.model_dump() for d in body.drafts], scheduled_at=quando, timezone_name=body.timezone,
            now=now, max_drafts=c.settings.max_batch_drafts,
            past_tolerance_seconds=c.settings.schedule_past_tolerance_seconds, trace_id=trace_id_for(request),
            production_emission_enabled=c.settings.production_emission_enabled,
            production_confirmation=body.production_confirmation.model_dump() if body.production_confirmation else None,
            mode=body.mode))
        return 202, {"batch_id": b.id, "status": "SCHEDULED", "mode": b.mode,
                     "scheduled_at": b.scheduled_at.astimezone(timezone.utc).isoformat(), "total": b.total}

    status, payload = run_idempotent(s, cc, "batches.create", key, body.model_dump(mode="json"), now, action)
    return JSONResponse(payload, status_code=status)


@router.get("/batches", response_model=sc.BatchPage, responses=E, tags=["batches"])
def list_batches(pp: PageParams = Depends(), cc: CompanyCtx = Depends(company_ctx("view")),
                 s: Session = Depends(db_session)) -> dict:
    q = select(Batch).where(Batch.company_id == cc.company.id, Batch.environment == cc.company.environment)
    total = s.scalar(select(func.count()).select_from(q.subquery()))
    rows = s.scalars(q.order_by(Batch.scheduled_at.desc(), Batch.id).offset(pp.offset).limit(pp.page_size)).all()
    counts = batches_d.batch_counts(s, [b.id for b in rows])
    return {"items": [batches_d.batch_out(b, counts[b.id]) for b in rows], "page": pp.page, "page_size": pp.page_size,
            "total": total}


@router.get("/batches/{batch_id}", response_model=sc.BatchDetail, responses=E, tags=["batches"])
def get_batch(batch_id: str, cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    b = batches_d.get_batch(s, cc.company, batch_id)
    # Todos os itens do lote, em qualquer situação (rejeitados, retirados, pendentes, incertos e autorizados).
    items = s.scalars(select(BatchItem).where(BatchItem.batch_id == b.id, BatchItem.company_id == cc.company.id)
                      .order_by(BatchItem.created_at, BatchItem.id)).all()
    ids = {i.draft_id for i in items}
    drafts = {d.id: d for d in s.scalars(select(Draft).where(Draft.id.in_(ids), Draft.company_id == cc.company.id))}         if ids else {}
    return batches_d.batch_out(b, batches_d.batch_counts(s, [b.id])[b.id], items, drafts)


@router.post("/batches/{batch_id}/cancel-pending", response_model=sc.CancelPendingOut, responses=E, tags=["batches"])
def cancel_pending(batch_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("schedule")),
                   s: Session = Depends(db_session)) -> dict:
    b = batches_d.get_batch(s, cc.company, batch_id)
    out = batches_d.cancel_pending(s, cc.company, b, cc.user_id, ctx(request).clock.now(), trace_id_for(request))
    s.commit()
    return out


# ---------------------------------------------------------------- sincronização
@router.get("/sync/status", response_model=sc.SyncStatus, responses=E, tags=["sync"])
def sync_status(request: Request, environment: str | None = EnvQuery, cc: CompanyCtx = Depends(company_ctx("view")),
                s: Session = Depends(db_session)) -> dict:
    c = ctx(request)
    sched = sync_d.ensure_schedule(s, cc.company, c.clock.now(), c.settings.sync_interval_hours, _env(cc, environment))
    s.commit()
    return sync_d.status_out(s, sched)


@router.post("/sync/run", status_code=202, response_model=sc.JobQueued, responses=E, tags=["sync"])
def sync_run(request: Request, environment: str | None = EnvQuery, cc: CompanyCtx = Depends(company_ctx("sync")),
             s: Session = Depends(db_session), key: str = Depends(require_idempotency_key)) -> JSONResponse:
    c = ctx(request)
    now = c.clock.now()
    env = _env(cc, environment)

    def action() -> tuple[int, dict]:
        sched = sync_d.ensure_schedule(s, cc.company, now, c.settings.sync_interval_hours, env)
        s.flush()
        sched = s.scalar(select(type(sched)).where(type(sched).id == sched.id).with_for_update())
        job = sync_d.request_run(s, sched, cc.company, cc.user_id, now, trace_id_for(request))
        return 202, {"job_id": job.id, "status": job.status}

    status, payload = run_idempotent(s, cc, "sync.run", key, {"environment": env}, now, action)
    return JSONResponse(payload, status_code=status)


# ---------------------------------------------------------------- jobs / exportação
@router.get(
    "/documents/{document_id}/danfse",
    responses={**E, 200: {"content": {"application/pdf": {}},
                          "description": "DANFSe gerado pela plataforma a partir do XML oficial (NT 008 v1.02)"}},
    response_class=Response, tags=["documents"],
    summary="DANFSe (PDF) gerado pela plataforma a partir do XML oficial armazenado; não é arquivo do governo.",
)
def document_danfse(document_id: str, request: Request,
                    disposition: str = Query("inline", pattern="^(inline|attachment)$"),
                    cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> Response:
    from ..danfse import NT_VERSAO, XmlNaoNfse, gerar, resolve_fontes

    d = get_document(s, cc.company.id, document_id)
    if d.simulated:
        raise ProblemError(409, "DANFSE_SIMULATED", "Documento de simulação não gera DANFSe.")
    if not danfse_available(d):
        raise ProblemError(409, "XML_UNAVAILABLE", "XML original indisponível: o DANFSe só é gerado a partir dele.")
    blob = s.get(StoredBlob, d.xml_blob_id)
    if blob is None or blob.company_id != cc.company.id:
        raise ProblemError(409, "XML_UNAVAILABLE", "XML original indisponível: o DANFSe só é gerado a partir dele.")
    try:
        fontes = resolve_fontes()
        pdf = gerar(read_blob(blob), danfse_marca(d), fontes)
    except XmlNaoNfse:
        raise ProblemError(409, "DANFSE_NOT_NFSE", "O XML armazenado não é uma NFS-e.")
    name = f"DANFSe-{d.access_key}.pdf"
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{name}"', "X-Content-Type-Options": "nosniff",
        "X-Danfse-Origem": "gerado-pela-plataforma", "X-Danfse-Especificacao": NT_VERSAO.split(" (")[0],
        "X-Danfse-Fontes": fontes.origem,
    })


@router.post("/documents/bundles", status_code=202, response_model=sc.JobQueued, responses=E, tags=["documents"],
             summary="Download em lote (ZIP) de PDF/XML por job assíncrono; conjunto fixado na criação.")
def create_bundle(body: sc.BundleCreate, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                  s: Session = Depends(db_session)) -> dict:
    env = _query_env(cc, body.environment)
    f = None
    if body.filters is not None:
        x = body.filters
        if x.date_from and x.date_to and x.date_from > x.date_to:
            raise ProblemError(422, "DATE_RANGE_INVALID", "date_from posterior a date_to.")
        f = _build_filters(x.direction, x.query, x.status, x.review_status, x.competence, x.date_from, x.date_to,
                           x.retention, x.retention_type, x.retention_match, x.withheld_min, x.withheld_max)
    st = ctx(request).settings
    job = bundles_d.create_bundle_job(s, company=cc.company, environment=env, user_id=cc.user_id, fmt=body.format,
                                      document_ids=body.document_ids, filters=f, now=ctx(request).clock.now(),
                                      max_docs_pdf=st.bundle_max_documents_pdf, max_docs_xml=st.bundle_max_documents_xml,
                                      trace_id=trace_id_for(request))
    s.commit()
    return {"job_id": job.id, "status": "QUEUED"}


@router.get("/jobs/{job_id}", response_model=sc.JobOut, responses=E, tags=["jobs"])
def get_job(job_id: str, errors_page: int = Query(1, ge=1), errors_page_size: int = Query(50, ge=1, le=100),
            cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    return jobs_d.job_out(s, jobs_d.get_job(s, cc.company, job_id), errors_page, errors_page_size)


@router.get("/jobs/{job_id}/download", response_class=FileResponse, tags=["jobs"],
            responses={**E, 200: {"content": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {}},
                                  "description": "Arquivo XLSX"}})
def download(job_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
             s: Session = Depends(db_session)) -> FileResponse:
    return _job_file(jobs_d.get_job(s, cc.company, job_id), request)


def _job_file(j: Job, request: Request) -> FileResponse:
    if j.type not in ("EXPORT", "BUNDLE"):
        raise ProblemError(409, "JOB_HAS_NO_DOWNLOAD", "Este job não gera arquivo.")
    now = ctx(request).clock.now()
    if j.output_expires_at is not None and j.output_expires_at <= now or (j.result or {}).get("expired"):
        raise ProblemError(410, "EXPORT_EXPIRED", "Arquivo expirado e removido; gere novamente.")
    if not jobs_d.download_available(j):
        raise ProblemError(409, "JOB_NOT_READY", "Exportação ainda não concluída.")
    real = os.path.realpath(j.output_path)
    base = os.path.realpath(ctx(request).settings.export_dir)
    if not real.startswith(base + os.sep) or not os.path.isfile(real):
        raise ProblemError(409, "EXPORT_FILE_MISSING", "Arquivo de exportação indisponível; gere novamente.")
    with ctx(request).sessions() as s2:
        stale = bundles_d.stale_documents(s2, j)
    if stale and j.type == "BUNDLE":
        raise ProblemError(409, "BUNDLE_STALE", f"A situação de {stale} nota(s) mudou depois da geração (ex.: "
                           "cancelamento/substituição). Gere o lote novamente para obter arquivos atualizados.")
    if stale:
        raise ProblemError(409, "EXPORT_STALE", f"A situação de {stale} nota(s) mudou depois da geração (ex.: "
                           "cancelamento/substituição). Gere a exportação novamente.")
    if j.type == "BUNDLE":
        return FileResponse(real, media_type="application/zip", filename=j.output_name or "nfse.zip")
    return FileResponse(real, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=j.output_name or "exportacao.xlsx")


@router.post("/jobs/{job_id}/download-ticket", response_model=sc.DownloadTicketOut, responses=E, tags=["jobs"],
             summary="Ticket de uso único (60 s) para baixar o arquivo do job direto para o disco (POST de formulário).")
def download_ticket(job_id: str, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                    s: Session = Depends(db_session)) -> dict:
    j = jobs_d.get_job(s, cc.company, job_id)
    _job_file(j, request)  # mesmas verificações (pronto, não expirado, arquivo presente)
    ticket, exp = bundles_d.issue_ticket(s, j, cc.user_id, cc.session_id or "", ctx(request).clock.now())
    s.commit()
    return {"ticket": ticket, "expires_at": exp, "method": "POST",
            "action": f"/api/fiscal/v1/companies/{cc.company.id}/jobs/{job_id}/download-file", "field": "ticket"}


@router.post("/jobs/{job_id}/download-file", response_class=FileResponse, tags=["jobs"],
             responses={**problem_responses(403, 404, 409, 410),
                        200: {"content": {"application/zip": {}}, "description": "Arquivo do job"}},
             summary="Baixa o arquivo com ticket de uso único enviado no CORPO (form); sem token na URL.")
def download_file(company_id: str, job_id: str, request: Request, ticket: str = Form(..., min_length=20, max_length=100),
                  s: Session = Depends(db_session)) -> FileResponse:
    c = ctx(request)
    now = c.clock.now()
    t = bundles_d.redeem_ticket(s, ticket, company_id, job_id, now)
    try:  # a sessão e o acesso à empresa continuam válidos?
        authz = c.core.authorize(t.user_id, t.session_id, company_id)
    except UpstreamError as exc:
        s.commit()
        if exc.status in (401, 403):
            raise ProblemError(403, "DOWNLOAD_TICKET_INVALID", "Sessão encerrada ou acesso revogado.")
        raise ProblemError(503, "AUTHZ_UNAVAILABLE", "Serviço de autorização indisponível.")
    if authz.company.id != company_id or "view" not in authz.permissions:
        s.commit()
        raise ProblemError(403, "DOWNLOAD_TICKET_INVALID", "Sessão encerrada ou acesso revogado.")
    j = s.scalar(select(Job).where(Job.id == job_id, Job.company_id == company_id))
    if j is None:
        s.commit()
        raise ProblemError(404, "JOB_NOT_FOUND", "Job não encontrado.")
    s.commit()
    return _job_file(j, request)


@router.post("/exports", status_code=202, response_model=sc.JobQueued, responses=E, tags=["jobs"])
def create_export(body: sc.ExportCreate, request: Request, cc: CompanyCtx = Depends(company_ctx("view")),
                  s: Session = Depends(db_session)) -> dict:
    f = _build_filters(body.direction, body.query, body.status, body.review_status, body.competence, body.date_from,
                       body.date_to, body.retention, body.retention_type, body.retention_match, body.withheld_min,
                       body.withheld_max)
    job = jobs_d.create_export_job(s, company=cc.company, user_id=cc.user_id, filters=f, fmt=body.format,
                                   now=ctx(request).clock.now(), trace_id=trace_id_for(request),
                                   environment=_query_env(cc, body.environment))
    s.commit()
    return {"job_id": job.id, "status": "QUEUED"}



# ---------------------------------------------------------------- inventário de serviços e perfis
@router.get("/service-inventory", responses=E, tags=["service-profiles"],
            summary="Serviços prestados pelo workspace, lidos dos XMLs oficiais das notas EMITIDAS (recebidas não "
                    "entram), agrupados por cTribNac + cNBS com as variações de tratamento tributário.")
def service_inventory(cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    from ..domain import inventario_servicos as inv

    return inv.inventario(s, cc.company)


@router.post("/service-profiles/prepare", responses=E, tags=["service-profiles"],
             summary="Cria perfis PENDENTES DE REVISÃO a partir do inventário (idempotente; nunca sobrescreve perfil "
                     "existente; nada é aprovado automaticamente).")
def prepare_service_profiles(request: Request, cc: CompanyCtx = Depends(company_ctx("admin")),
                             s: Session = Depends(db_session)) -> dict:
    from ..domain import inventario_servicos as inv

    data = inv.inventario(s, cc.company)
    props = [inv.proposta(g) for g in data["grupos"]]
    res = _core_guard(lambda: ctx(request).core.prepare_service_profiles(cc.company.id, props, cc.user_id))
    return {"groups": len(props), **res}


@router.get("/provider-data-from-documents", responses=E, tags=["service-profiles"],
            summary="Dados do prestador no grupo emit das notas emitidas, com consistência entre as notas.")
def provider_data(cc: CompanyCtx = Depends(company_ctx("view")), s: Session = Depends(db_session)) -> dict:
    from ..domain import inventario_servicos as inv

    return inv.dados_prestador(s, cc.company)


@router.post("/provider-data-from-documents/apply", responses=E, tags=["service-profiles"],
             summary="Completa SOMENTE campos vazios do cadastro com valores consistentes em todas as notas; "
                     "divergências voltam para decisão (nunca sobrescreve).")
def apply_provider_data(request: Request, cc: CompanyCtx = Depends(company_ctx("admin")),
                        s: Session = Depends(db_session)) -> dict:
    from ..domain import inventario_servicos as inv

    values, sources = inv.valores_seguros(inv.dados_prestador(s, cc.company))
    return _core_guard(lambda: ctx(request).core.complete_provider(cc.company.id, values, sources, cc.user_id))


__all__ = ["router", "Job"]
