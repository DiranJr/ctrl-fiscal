"""Cancelamento de NFS-e EMITIDA pela empresa: elegibilidade, pedido explícito e aplicação do evento oficial.

Contrato oficial (consultado em 08/10/2026; arquivos oficiais em docs/oficial/eventos-20261008/):
- 101101 Cancelamento: pedido direto do emitente; cancela na hora se passar nas regras (inclusive parâmetros do
  município emissor: prazo ≤ 730 dias, valor, tomador identificado, tributos recolhidos — NÃO consultáveis por API;
  conhecidos só pela recusa E0822/E0823/E0824/E0827).
- 101103 Solicitação de análise fiscal: caminho quando o cancelamento direto não é aceito; fica PENDENTE até o
  município registrar 105104 (deferido = cancelada) ou 105105 (indeferido = continua válida).
- 105102 Cancelamento por substituição: NÃO é pedido por este fluxo; a Sefin o gera ao autorizar uma DPS substituta.
- Matriz do Anexo II (RN EVENTOSxEVENTOS): análise pendente bloqueia tudo; confirmação do prestador/tomador/
  intermediário bloqueia cancelamento e análise; confirmação tácita e anulação de rejeição bloqueiam só o
  cancelamento direto; bloqueio de ofício sem desbloqueio bloqueia o cancelamento direto; indeferimento bloqueia tudo.

Regras locais (segurança): só nota EMITIDA pela própria empresa (CNPJ emitente = empresa; nunca nota de terceiro),
no ambiente da nota, com XML oficial gerado pela Sefin Nacional (ambGer=2; E0831); confirmação explícita com
empresa, ambiente, chave e número; um pedido ativo por nota; situação da nota muda SOMENTE com o evento oficial.
"""
from __future__ import annotations

import gzip
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..clients import CompanyInfo
from ..models import CancellationRequest, Document, DocumentEvent, StoredBlob
from .common import DomainError, audit

KINDS = {"CANCEL": "101101", "ANALYSIS": "101103"}
KIND_LABELS = {"CANCEL": "Cancelamento (evento 101101)",
               "ANALYSIS": "Solicitação de análise fiscal para cancelamento (evento 101103)"}
MOTIVOS = {"1": "Erro na Emissão", "2": "Serviço não Prestado", "9": "Outros"}
ACTIVE = ("QUEUED", "PROCESSING", "AWAITING_CONFIRMATION")
TERMINAL = ("REGISTERED", "REJECTED", "BLOCKED", "WITHDRAWN")
NATIONAL_MAX_DAYS = 730  # Guia do Painel Municipal v1.2 §3.4.1: teto e padrão do prazo de cancelamento
_TSSTRING = re.compile(r"^(?:[!-ÿ][ -ÿ]*[!-ÿ]|[!-ÿ])$")
MUNICIPAL_REJECTIONS = {"E0822", "E0823", "E0824", "E0827"}
CONFIRMATIONS = {"e202201", "e203202", "e204203"}
NS = "{http://www.sped.fazenda.gov.br/nfse}"


def validar_justificativa(texto: str) -> list[str]:
    erros = []
    if not 15 <= len(texto or "") <= 255:
        erros.append("A justificativa deve ter de 15 a 255 caracteres.")
    if not _TSSTRING.match(texto or ""):
        erros.append("Sem espaço no início/fim, sem quebra de linha e só caracteres aceitos pelo leiaute (Latin-1).")
    return erros


def _origem(s: Session, doc: Document) -> dict | None:
    """ambGer, tpEmit e CNPJ do emitente lidos do XML oficial da nota (autoria do evento: E0812/E0813/E0831)."""
    if not doc.xml_blob_id:
        return None
    b = s.get(StoredBlob, doc.xml_blob_id)
    if b is None:
        return None
    from defusedxml import ElementTree as SafeET

    try:
        root = SafeET.fromstring(gzip.decompress(b.content_gz), forbid_dtd=True, forbid_entities=True,
                                 forbid_external=True)
    except Exception:  # noqa: BLE001
        return None

    def t(path: str) -> str | None:
        el = root.find("/".join(NS + p for p in path.split("/")))
        return el.text.strip() if el is not None and el.text else None

    return {"ambGer": t("infNFSe/ambGer"), "tpEmit": t("infNFSe/DPS/infDPS/tpEmit"),
            "emit_cnpj": t("infNFSe/emit/CNPJ"), "prest_cnpj": t("infNFSe/DPS/infDPS/prest/CNPJ")}


def _amb_ger(s: Session, doc: Document) -> str | None:
    o = _origem(s, doc)
    return o["ambGer"] if o else None


def _events(s: Session, doc: Document) -> list[DocumentEvent]:
    return list(s.scalars(select(DocumentEvent).where(
        DocumentEvent.company_id == doc.company_id, DocumentEvent.environment == doc.environment,
        DocumentEvent.access_key == doc.access_key).order_by(DocumentEvent.occurred_at, DocumentEvent.created_at)))


def environment_allowed(environment: str, simulated: bool, production_enabled: bool) -> tuple[bool, dict | None]:
    if environment == "mock":
        return (True, None) if simulated else (False, {"code": "MOCK_NOT_SIMULATED", "message": "Inconsistência de ambiente."})
    if environment == "restricted":
        return True, None
    if environment == "production":
        if production_enabled:
            return True, None
        return False, {"code": "PRODUCTION_CANCELLATION_BLOCKED",
                       "message": "Cancelamento em produção desligado na configuração desta instalação "
                                  "(NFSE_PRODUCTION_CANCELLATION_ENABLED). Nenhum pedido é enviado."}
    return False, {"code": "ENVIRONMENT_UNKNOWN", "message": "Ambiente desconhecido."}


def eligibility(s: Session, company: CompanyInfo, doc: Document, now: datetime, *, production_enabled: bool) -> dict:
    """Avaliação no SERVIDOR (a tela só exibe). blockers por tipo de pedido; warnings não impedem."""
    common: list[dict] = []
    warnings: list[dict] = []
    if doc.direction != "ISSUED" or doc.company_role != "PRESTADOR":
        common.append({"code": "NOT_ISSUED_BY_COMPANY",
                       "message": "Nota recebida (de terceiro): só o emitente pode pedir o cancelamento."})
    elif (doc.issuer_doc or "") != company.cnpj:
        common.append({"code": "ISSUER_MISMATCH", "message": "O emitente da nota não é o CNPJ desta empresa."})
    if not doc.access_key:
        common.append({"code": "ACCESS_KEY_MISSING", "message": "Nota sem chave de acesso."})
    if company.workspace_status not in (None, "ACTIVE"):
        common.append({"code": "WORKSPACE_NOT_ACTIVE",
                       "message": f"Workspace em {company.workspace_status}: pedidos de cancelamento bloqueados."})
    ok, why = environment_allowed(doc.environment, bool(doc.simulated), production_enabled)
    if not ok:
        common.append(why)
    if doc.status == "CANCELLED":
        common.append({"code": "ALREADY_CANCELLED",
                       "message": "Nota já cancelada" + (" por substituição." if doc.substituted_by_access_key else ".")})
    elif doc.status != "AUTHORIZED":
        common.append({"code": "STATUS_NOT_AUTHORIZED", "message": "Situação da nota não confirmada como autorizada."})
    if not doc.simulated:
        if not doc.xml_blob_id:
            common.append({"code": "XML_UNAVAILABLE", "message": "XML oficial da nota não armazenado."})
        else:
            o = _origem(s, doc) or {}
            if o.get("ambGer") != "2":
                common.append({"code": "NOT_GENERATED_BY_SEFIN",
                               "message": "Nota gerada fora da Sefin Nacional (ambGer≠2): o pedido deve ir ao sistema "
                                          "que a gerou (regra E0831)."})
            if o.get("tpEmit") != "1":
                common.append({"code": "ISSUER_NOT_PROVIDER",
                               "message": "Nota emitida pelo tomador ou intermediário (tpEmit≠1): o autor do evento "
                                          "seria outro (regra E0813); não é pedida por esta plataforma."})
            elif (o.get("emit_cnpj") or "") != company.cnpj:
                common.append({"code": "EMITTER_MISMATCH",
                               "message": "O emitente no XML oficial não é o CNPJ desta empresa (regra E0812)."})
    active = s.scalar(select(CancellationRequest).where(CancellationRequest.document_id == doc.id,
                                                        CancellationRequest.status.in_(ACTIVE)))
    if active is not None:
        common.append({"code": "CANCELLATION_IN_PROGRESS", "message": "Já existe pedido em andamento para esta nota."})

    types = [e.event_type for e in _events(s, doc)]
    has = set(types)
    cancel_only: list[dict] = []
    analysis_only: list[dict] = []
    if "e101103" in has and not has & {"e105104", "e105105"}:
        common.append({"code": "ANALYSIS_PENDING", "message": "Solicitação de análise fiscal pendente no município "
                                                              "(Anexo II: impede novo pedido até o deferimento/indeferimento)."})
    if "e105105" in has:
        common.append({"code": "ANALYSIS_DENIED", "message": "Análise fiscal indeferida pelo município: a nota continua "
                                                             "válida e não aceita novo pedido (Anexo II)."})
    if has & CONFIRMATIONS:
        common.append({"code": "CONFIRMED_BY_PARTY", "message": "Nota confirmada por prestador/tomador/intermediário: "
                                                                "não aceita cancelamento nem análise (Anexo II); só o "
                                                                "município pode cancelar de ofício."})
    if "e205204" in has:
        cancel_only.append({"code": "TACIT_CONFIRMATION", "message": "Confirmação tácita registrada: cancelamento "
                                                                    "direto não permitido; use a análise fiscal."})
    if "e205208" in has:
        cancel_only.append({"code": "REJECTION_ANNULLED", "message": "Anulação de rejeição registrada: cancelamento "
                                                                     "direto não permitido; use a análise fiscal."})
    last_block = max((i for i, t in enumerate(types) if t == "e305102"), default=-1)
    last_unblock = max((i for i, t in enumerate(types) if t == "e305103"), default=-1)
    if last_block > last_unblock:
        cancel_only.append({"code": "BLOCKED_BY_MUNICIPALITY", "message": "Bloqueio de cancelamento por ofício ativo."})
    if doc.issued_at is not None and now - doc.issued_at > timedelta(days=NATIONAL_MAX_DAYS):
        cancel_only.append({"code": "DEADLINE_EXCEEDED_730", "message": "Mais de 730 dias da emissão (limite máximo "
                                                                        "nacional): use a solicitação de análise fiscal."})
    if doc.environment != "mock":
        warnings.append({"code": "MUNICIPAL_PARAMETERS_UNKNOWN",
                         "message": "Prazo, valor máximo, exigência de tomador e tributos recolhidos são parâmetros do "
                                    "município emissor, não consultáveis por API. Se o município recusar (E0822, E0823, "
                                    "E0824, E0827), use a solicitação de análise fiscal."})
    if doc.substitutes_access_key:
        warnings.append({"code": "IS_SUBSTITUTE", "message": "Esta nota substituiu outra; cancelá-la não restaura a "
                                                             "nota substituída."})
    return {
        "eligible": {"CANCEL": not (common or cancel_only), "ANALYSIS": not (common or analysis_only)},
        "blockers": {"CANCEL": common + cancel_only, "ANALYSIS": common + analysis_only},
        "warnings": warnings,
        "events": [{"event_type": e.event_type, "description": e.description, "occurred_at": e.occurred_at,
                    "cancels_document": e.cancels_document, "simulated": e.simulated} for e in _events(s, doc)],
    }


# Códigos de recusa com orientação objetiva (Anexo II e regras municipais).
ORIENTACAO = {
    "E0840": "A nota já tem evento que impede o cancelamento direto (por exemplo, já cancelada ou substituída fora da "
             "plataforma, confirmada pelo tomador ou com bloqueio de ofício). Consulte a sincronização ADN.",
    "E0848": "A nota já tem evento que impede a solicitação de análise fiscal (ex.: análise pendente ou indeferida).",
    "E0822": "Fora do prazo de cancelamento definido pelo município: use a solicitação de análise fiscal.",
    "E0823": "Valor acima do limite do município para cancelamento direto: use a solicitação de análise fiscal.",
    "E0824": "O município exige tomador identificado para o cancelamento direto: use a solicitação de análise fiscal.",
    "E0827": "Tributos já recolhidos: use a solicitação de análise fiscal.",
    "E1831": "A nota não foi encontrada no Ambiente de Dados Nacional.",
}
ANALISE_ROTULO = {"PENDENTE": "Aguardando decisão do município", "DEFERIDA": "Deferida pelo município (nota cancelada)",
                  "INDEFERIDA": "Indeferida pelo município (a nota continua válida)"}


def request_out(r: CancellationRequest) -> dict:
    analise = (r.result or {}).get("analise") if r.kind == "ANALYSIS" and r.status == "REGISTERED" else None
    codigos = [e.get("code") for e in (r.errors or [])]
    return {
        "analysis": ({**analise, "label": ANALISE_ROTULO.get(analise.get("situacao"), analise.get("situacao"))}
                     if analise else None),
        "guidance": next((ORIENTACAO[c] for c in codigos if c in ORIENTACAO), None),
        "id": r.id, "document_id": r.document_id, "environment": r.environment, "access_key": r.access_key,
        "kind": r.kind, "event_code": r.event_code, "reason_code": r.reason_code,
        "reason_label": MOTIVOS.get(r.reason_code), "reason_text": r.reason_text, "status": r.status,
        "simulated": r.simulated, "attempts": r.attempts, "requested_by": r.requested_by, "requested_at": r.requested_at,
        "finished_at": r.finished_at, "last_error_code": r.last_error_code, "last_error_message": r.last_error_message,
        "errors": r.errors or [], "result": r.result,
        "suggest_analysis": r.status == "REJECTED" and r.kind == "CANCEL" and bool(
            {e.get("code") for e in (r.errors or [])} & MUNICIPAL_REJECTIONS),
    }


def history(s: Session, doc: Document) -> list[dict]:
    return [request_out(r) for r in s.scalars(select(CancellationRequest).where(
        CancellationRequest.document_id == doc.id).order_by(CancellationRequest.requested_at.desc()))]


def create_request(s: Session, company: CompanyInfo, document_id: str, *, kind: str, reason_code: str,
                   reason_text: str, confirm: dict, user_id: str, now: datetime, production_enabled: bool,
                   trace_id: str | None) -> CancellationRequest:
    # Trava a nota: dois pedidos simultâneos para a mesma nota serializam aqui.
    doc = s.scalar(select(Document).where(Document.id == document_id, Document.company_id == company.id)
                   .with_for_update())
    if doc is None:
        raise DomainError(404, "DOCUMENT_NOT_FOUND", "Documento não encontrado.")
    if kind not in KINDS:
        raise DomainError(422, "CANCELLATION_KIND_INVALID", "Tipo de pedido inválido.")
    if reason_code not in MOTIVOS:
        raise DomainError(422, "REASON_CODE_INVALID", "Motivo inválido para o leiaute (1, 2 ou 9).")
    erros = validar_justificativa(reason_text)
    if erros:
        raise DomainError(422, "REASON_TEXT_INVALID", erros[0], errors=[{"field": "reason_text", "code": "INVALID",
                                                                          "message": m} for m in erros])
    # Confirmação explícita: o que o usuário viu tem de ser exatamente o que será pedido.
    expected = {"access_key": doc.access_key, "environment": doc.environment, "company_cnpj": company.cnpj,
                "document_number": doc.number or ""}
    diverge = [k for k, v in expected.items() if (confirm.get(k) or "") != v]
    if diverge:
        raise DomainError(409, "CONFIRMATION_MISMATCH", "A confirmação não corresponde à nota/empresa/ambiente.",
                          errors=[{"field": f"confirm.{k}", "code": "MISMATCH", "message": "Divergente."} for k in diverge])
    el = eligibility(s, company, doc, now, production_enabled=production_enabled)
    if not el["eligible"][kind]:
        raise DomainError(409, "CANCELLATION_NOT_ALLOWED", el["blockers"][kind][0]["message"],
                          errors=[{"field": "document", "code": b["code"], "message": b["message"]}
                                  for b in el["blockers"][kind]])
    r = CancellationRequest(
        company_id=company.id, environment=doc.environment, document_id=doc.id, access_key=doc.access_key,
        company_cnpj=company.cnpj, kind=kind, event_code=KINDS[kind], reason_code=reason_code, reason_text=reason_text,
        status="QUEUED", submit_phase="NONE", attempts=0, fencing_token=0, next_attempt_at=now,
        simulated=bool(doc.simulated), active_document_id=doc.id, requested_by=user_id, requested_at=now,
        trace_id=trace_id, created_at=now, updated_at=now,
    )
    try:
        with s.begin_nested():
            s.add(r)
            s.flush()
    except IntegrityError:
        raise DomainError(409, "CANCELLATION_IN_PROGRESS", "Já existe pedido em andamento para esta nota.")
    audit(s, now, "cancellation.requested", company_id=company.id, actor_id=user_id, target_type="document",
          target_id=doc.id, trace_id=trace_id,
          details={"request_id": r.id, "environment": doc.environment, "kind": kind, "event_code": KINDS[kind],
                   "reason_code": reason_code, "outcome": "QUEUED"})
    return r


def get_request(s: Session, company: CompanyInfo, request_id: str) -> CancellationRequest:
    r = s.scalar(select(CancellationRequest).where(CancellationRequest.id == request_id,
                                                   CancellationRequest.company_id == company.id))
    if r is None:
        raise DomainError(404, "CANCELLATION_NOT_FOUND", "Pedido não encontrado.")
    return r
