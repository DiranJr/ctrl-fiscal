"""Agendamento de lotes: valida versões/empresa/ambiente/data e congela snapshot."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clients import CompanyInfo, CoreClient
from ..models import Batch, BatchItem, Draft
from .common import DomainError, audit, canonical_hash, money
from .drafts import consultar_parametrizacao_iss, customer_snapshot, draft_out, validate_draft

QUEUE_STATUSES = ("SCHEDULED", "PROCESSING", "AWAITING_CONFIRMATION", "AUTHORIZED", "REJECTED", "WITHDRAWN")
TERMINAL = ("AUTHORIZED", "REJECTED", "WITHDRAWN")


def create_batch(
    s: Session, *, company: CompanyInfo, core: CoreClient, user_id: str, drafts: list[dict], scheduled_at: datetime,
    timezone_name: str, now: datetime, max_drafts: int, past_tolerance_seconds: int, trace_id: str | None,
    production_emission_enabled: bool = False, production_confirmation: dict | None = None, mode: str = "SCHEDULED",
) -> Batch:
    if company.environment == "production" and not production_emission_enabled:
        raise DomainError(422, "PRODUCTION_EMISSION_BLOCKED",
                          "Emissão em produção desligada na configuração desta instalação "
                          "(NFSE_PRODUCTION_EMISSION_ENABLED).")
    if company.environment == "production" and not production_confirmation:
        raise DomainError(422, "PRODUCTION_CONFIRMATION_REQUIRED",
                          "Emissão em produção exige a confirmação individual do lote revisado: empresa, ambiente, "
                          "documentos (versão e valor), quantidade e valor total.")
    if company.workspace_status != "ACTIVE":
        raise DomainError(422, "WORKSPACE_NOT_ACTIVE",
                          f"Workspace em {company.workspace_status or 'situação desconhecida'}: transmissões bloqueadas "
                          "até a ativação (o rascunho pode ser preparado).")
    if company.is_demo and company.environment != "mock":
        raise DomainError(422, "DEMO_COMPANY_OFFICIAL_BLOCKED", "Empresa de demonstração não emite em ambiente oficial.")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DomainError(422, "TIMEZONE_INVALID", "Fuso horário IANA inválido.")
    if timezone_name != company.timezone:
        raise DomainError(422, "TIMEZONE_MISMATCH", f"O fuso do lote deve ser o da empresa ({company.timezone}).")
    if scheduled_at.tzinfo is None:
        raise DomainError(422, "SCHEDULE_OFFSET_REQUIRED", "Data/hora agendada exige offset.")
    # Qualquer offset explícito define um instante inequívoco (ex.: "Z" de toISOString);
    # a hora local da empresa é derivada no servidor e devolvida em scheduled_local.
    if scheduled_at < now - timedelta(seconds=past_tolerance_seconds):
        raise DomainError(422, "SCHEDULE_IN_PAST", "Data/hora agendada já passou. Escolha um horário futuro.")
    ids = [d["id"] for d in drafts]
    if not ids:
        raise DomainError(422, "BATCH_EMPTY", "Informe ao menos um rascunho.")
    if len(ids) != len(set(ids)):
        raise DomainError(422, "BATCH_DUPLICATE_DRAFT", "Rascunho repetido no lote.")
    if len(ids) > max_drafts:
        raise DomainError(422, "BATCH_TOO_LARGE", f"Máximo de {max_drafts} rascunhos por lote.")
    expected = {d["id"]: d["expected_version"] for d in drafts}
    rows = s.scalars(
        select(Draft).where(Draft.id.in_(ids), Draft.company_id == company.id).order_by(Draft.id).with_for_update()
    ).all()
    found = {d.id: d for d in rows}
    errors: list[dict] = []
    conflict = False
    for did in ids:
        d = found.get(did)
        if d is None:
            errors.append({"field": f"drafts[{did}]", "code": "DRAFT_NOT_FOUND", "message": "Rascunho não encontrado."})
            continue
        if d.deleted_at is not None:  # excluído (mesma trava de linha da exclusão): nunca entra na fila
            conflict = True
            errors.append({"field": f"drafts[{did}]", "code": "DRAFT_DELETED",
                           "message": "Rascunho excluído: não pode ser agendado nem emitido."})
            continue
        if d.environment != company.environment:
            errors.append({"field": f"drafts[{did}]", "code": "DRAFT_OTHER_ENVIRONMENT", "message": "Rascunho de outro ambiente."})
        elif d.status != "DRAFT":
            conflict = True
            # Um rascunho só entra na fila uma vez (trava por linha + troca de situação na mesma transação): emitir agora
            # e agendar o mesmo rascunho ao mesmo tempo, ou clicar duas vezes, nunca geram duas transmissões.
            errors.append({"field": f"drafts[{did}]", "code": "DRAFT_NOT_SCHEDULABLE",
                           "message": f"Rascunho em estado {d.status}: já está na fila de emissão (agendado ou em "
                                      "processamento) ou já foi emitido. Para mudar a forma de emissão, retire-o da "
                                      "fila antes." if d.status in ("SCHEDULED", "PROCESSING", "AWAITING_CONFIRMATION")
                           else f"Rascunho em estado {d.status}."})
        elif d.version != expected[did]:
            conflict = True
            errors.append({"field": f"drafts[{did}]", "code": "VERSION_CONFLICT", "message": "Versão diferente da revisada."})
    if errors:
        if conflict:
            raise DomainError(409, "BATCH_CONFLICT", "Um ou mais rascunhos mudaram ou não podem ser agendados.", errors)
        raise DomainError(422, "BATCH_INVALID", "Um ou mais rascunhos são inválidos.", errors)

    snapshots: dict[str, tuple[dict, dict]] = {}
    # E0015 contra a data EFETIVA de emissão: emitir agora = agora; agendar = horário programado (no fuso da empresa).
    emissao = max(scheduled_at, now)
    for did in ids:
        d = found[did]
        customer = core.customer(company.id, d.customer_id)
        profile = core.service_profile(company.id, d.service_profile_id) if d.service_profile_id else None
        issues = validate_draft(d, company, customer, now, profile,
                                production_emission_enabled=production_emission_enabled, emissao=emissao, modo=mode,
                                parametrizacao=consultar_parametrizacao_iss(core, company, d, customer))
        d.issues = issues
        bad = [i for i in issues if i["severity"] == "error"]
        if bad:
            errors.extend({"field": f"drafts[{did}].{i['field']}", "code": i["code"], "message": i["message"]} for i in bad)
        else:
            snapshots[did] = (customer, issues)
    if errors:
        raise DomainError(422, "DRAFT_INVALID", "Rascunhos com pendências de validação.", errors)
    if company.environment == "production":
        _conferir_confirmacao(company, [found[i] for i in ids], production_confirmation or {}, mode)

    batch = Batch(
        company_id=company.id, environment=company.environment, scheduled_at=scheduled_at, timezone=timezone_name,
        scheduled_local=scheduled_at.astimezone(zone).isoformat(), total=len(ids), mode=mode, created_by=user_id,
        created_at=now,
    )
    s.add(batch)
    s.flush()
    for did in ids:
        d = found[did]
        customer, _ = snapshots[did]
        snap = _snapshot(d, company, customer)
        item = BatchItem(
            batch_id=batch.id, company_id=company.id, environment=company.environment, draft_id=d.id,
            draft_version=d.version, snapshot=snap, snapshot_hash=canonical_hash(snap), status="SCHEDULED",
            scheduled_at=scheduled_at, next_attempt_at=scheduled_at, submission_id=str(uuid.uuid4()),
            submit_phase="NONE", attempts=0, fencing_token=0, created_at=now, updated_at=now,
        )
        s.add(item)
        s.flush()
        d.status = "SCHEDULED"
        d.version += 1
        d.current_item_id = item.id
        d.updated_at = now
        # A validação feita AGORA (mesma transação, conteúdo travado) é a que vale para o conteúdo agendado.
        d.validated_version = d.version
        d.validated_at = now
    audit(s, now, "batch.created", company_id=company.id, actor_id=user_id, target_type="batch", target_id=batch.id,
          details={"total": len(ids), "scheduled_at_utc": scheduled_at.astimezone(timezone.utc).isoformat(),
                   "scheduled_at_received": scheduled_at.isoformat(), "timezone_validated": timezone_name,
                   "scheduled_local": batch.scheduled_local, "environment": company.environment, "mode": mode,
                   "production_confirmation": production_confirmation if company.environment == "production" else None},
          trace_id=trace_id)
    return batch


def _conferir_confirmacao(company: CompanyInfo, drafts: list[Draft], conf: dict, mode: str = "SCHEDULED") -> None:
    """Produção: o que a pessoa confirmou tem de ser EXATAMENTE o lote agendado — mesma empresa e ambiente, os mesmos
    documentos nas MESMAS versões revisadas, com os mesmos valores, quantidade e total. Qualquer edição posterior
    muda a versão e exige nova revisão e confirmação."""
    from decimal import Decimal

    total = sum((d.amount for d in drafts), Decimal("0")).quantize(Decimal("0.01"))
    docs = {(d.id, d.version, f"{d.amount.quantize(Decimal('0.01'))}") for d in drafts}
    conf_docs = {(x.get("id"), x.get("version"), x.get("amount")) for x in (conf.get("documents") or [])}
    diverge = [campo for campo, ok in (
        ("company_cnpj", conf.get("company_cnpj") == company.cnpj),
        ("environment", conf.get("environment") == "production"),
        ("count", conf.get("count") == len(drafts)),
        ("total_amount", conf.get("total_amount") == f"{total}"),
        ("documents", conf_docs == docs),
        # A ação confirmada (emitir agora x agendar) precisa ser a pedida: confirmar um agendamento não autoriza
        # a emissão imediata, e vice-versa.
        ("mode", (conf.get("mode") or "SCHEDULED") == mode),
    ) if not ok]
    if diverge:
        raise DomainError(409, "PRODUCTION_CONFIRMATION_MISMATCH",
                          "A confirmação não corresponde ao lote revisado (empresa, ambiente, documentos/versões, "
                          "quantidade ou valor total). Revise e confirme novamente.",
                          [{"field": f"production_confirmation.{k}", "code": "MISMATCH", "message": "Divergente."}
                           for k in diverge])


def _snapshot(d: Draft, company: CompanyInfo, customer: dict) -> dict:
    """Snapshot imutável usado na transmissão; edições posteriores não o alteram."""
    base = draft_out(d)
    return {
        "draft_id": d.id,
        "draft_version": d.version,
        "environment": company.environment,
        "company": company.as_dict(),
        "customer": customer_snapshot(customer),
        "competence": d.competence,
        "service_date": str(d.service_date) if d.service_date else None,
        "service": base["service"],
        "amount": money(d.amount),
        "deductions": money(d.deductions),
        "unconditional_discount": money(d.discount_unconditional),
        "conditional_discount": money(d.discount_conditional),
        "tax_settings": base["tax_settings"],
        "retention_summary": base["retention_summary"],
    }


def get_batch(s: Session, company: CompanyInfo, batch_id: str) -> Batch:
    b = s.scalar(select(Batch).where(Batch.id == batch_id, Batch.company_id == company.id))
    if b is None:
        raise DomainError(404, "BATCH_NOT_FOUND", "Lote não encontrado.")
    return b


def cancel_pending(s: Session, company: CompanyInfo, batch: Batch, user_id: str, now: datetime, trace_id: str | None) -> dict:
    """Retira apenas itens ainda não iniciados. Nunca toca envio em curso/autorizado."""
    items = s.scalars(select(BatchItem).where(BatchItem.batch_id == batch.id).order_by(BatchItem.id).with_for_update()).all()
    withdrawn = 0
    kept = 0
    for it in items:
        # Não iniciado = nada pode ter sido transmitido: fase NONE (nunca chegou ao envio ou o conector
        # respondeu BLOCKED/NOT_CONFIGURED, que ele garante não ter transmitido) e sem lease ativo.
        not_started = (
            it.status == "SCHEDULED"
            and it.submit_phase == "NONE"
            and (it.lease_expires_at is None or it.lease_expires_at <= now)
        )
        if not_started:
            it.status = "WITHDRAWN"
            it.finished_at = now
            it.updated_at = now
            it.fencing_token += 1  # invalida qualquer lease residual
            d = s.scalar(select(Draft).where(Draft.id == it.draft_id).with_for_update())
            if d is not None and d.current_item_id == it.id:
                d.status = "DRAFT"
                d.version += 1
                d.current_item_id = None
                d.updated_at = now
            withdrawn += 1
        elif it.status not in TERMINAL:
            kept += 1
    audit(s, now, "batch.cancel_pending", company_id=company.id, actor_id=user_id, target_type="batch",
          target_id=batch.id, details={"withdrawn": withdrawn, "kept_in_progress": kept}, trace_id=trace_id)
    return {"batch_id": batch.id, "withdrawn": withdrawn, "not_withdrawn": kept}


def batch_counts(s: Session, batch_ids: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {b: {k: 0 for k in QUEUE_STATUSES} for b in batch_ids}
    if not batch_ids:
        return out
    for bid, status, n in s.execute(
        select(BatchItem.batch_id, BatchItem.status, func.count()).where(BatchItem.batch_id.in_(batch_ids))
        .group_by(BatchItem.batch_id, BatchItem.status)
    ).all():
        out[bid][status] = n
    return out


def derive_status(counts: dict[str, int], total: int) -> str:
    if counts.get("WITHDRAWN", 0) == total:
        return "WITHDRAWN"
    if counts.get("PROCESSING", 0) or counts.get("AWAITING_CONFIRMATION", 0):
        return "PROCESSING"
    done = sum(counts.get(k, 0) for k in TERMINAL)
    if done == total:
        return "COMPLETED_WITH_REJECTIONS" if counts.get("REJECTED", 0) else "COMPLETED"
    if done > 0:
        return "PROCESSING"
    return "SCHEDULED"


def batch_out(b: Batch, counts: dict[str, int], items: list[BatchItem] | None = None,
              drafts: dict[str, Draft] | None = None) -> dict:
    done = sum(counts.get(k, 0) for k in TERMINAL)
    out = {
        "id": b.id,
        "status": derive_status(counts, b.total),
        "mode": b.mode or "SCHEDULED",
        "scheduled_at": b.scheduled_at,
        "scheduled_local": b.scheduled_local,
        "timezone": b.timezone,
        "environment": b.environment,
        "simulated": b.environment == "mock",
        "total": b.total,
        "counts": counts,
        "progress": {"done": done, "total": b.total, "percent": round(100 * done / b.total) if b.total else 100},
        "created_at": b.created_at,
        "created_by": b.created_by,
    }
    if items is not None:
        out["items"] = [item_out(i, (drafts or {}).get(i.draft_id)) for i in items]
    return out


def item_out(i: BatchItem, draft: Draft | None = None) -> dict:
    from .itens_lote import diagnostico  # noqa: PLC0415

    return {**diagnostico(i, draft),
        "id": i.id,
        "draft_id": i.draft_id,
        "draft_version": i.draft_version,
        "status": i.status,
        "competence": (i.snapshot or {}).get("competence"),
        "customer_name": ((i.snapshot or {}).get("customer") or {}).get("name"),
        "amount": (i.snapshot or {}).get("amount"),
        "total_withheld": ((i.snapshot or {}).get("retention_summary") or {}).get("total_withheld"),
        "net_amount": ((i.snapshot or {}).get("retention_summary") or {}).get("net_amount"),
        "dps_id": i.dps_id,
        "attempts": i.attempts,
        "next_attempt_at": i.next_attempt_at if i.status not in TERMINAL else None,
        "started_at": i.started_at,
        "finished_at": i.finished_at,
        "late_seconds": i.late_seconds,
        "last_error": {"code": i.last_error_code, "message": i.last_error_message} if i.last_error_code else None,
        "result": i.result,
        "document_id": i.document_id,
        "simulated": i.environment == "mock",
    }
