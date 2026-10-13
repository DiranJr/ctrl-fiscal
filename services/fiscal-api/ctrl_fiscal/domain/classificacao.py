"""Classificação ÚNICA de rascunhos e lotes para listas, filtros e indicadores (somente leitura; não muda estados).

Rascunhos (excluídos logicamente nunca aparecem):
- pending: tudo que ainda não foi autorizado — editável, agendado, em fila, em processamento, resultado incerto,
  rejeitado ou retirado (itens em andamento e que exigem atenção continuam visíveis);
- issued: autorizado APÓS confirmação oficial (Draft.status = AUTHORIZED é gravado só quando o conector confirma a
  autorização ou a reconciliação encontra a DPS). Vale para registros antigos sem nenhuma alteração no banco;
- all: os dois.

Lotes (pelos estados reais dos itens):
- in_progress: algum item agendado, em fila/processamento ou aguardando confirmação;
- attention: algum item rejeitado cuja pendência não foi resolvida (o rascunho ainda aponta para esse item e não foi
  excluído) ou bloqueado antes do envio (agendado com motivo registrado);
- history: nenhum item ativo (concluídos, inclusive com rejeições, e retirados);
- all: todos.
Um lote encerrado com rejeição pendente está em history E em attention; cada contagem é da sua própria lista.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, select, true

from ..models import Batch, BatchItem, Document, Draft

DRAFT_VIEWS = ("pending", "issued", "all")
BATCH_VIEWS = ("in_progress", "attention", "history", "all")
ACTIVE = ("SCHEDULED", "PROCESSING", "AWAITING_CONFIRMATION")


def draft_view(view: str):  # noqa: ANN201
    if view == "issued":
        return Draft.status == "AUTHORIZED"
    if view == "pending":
        return Draft.status != "AUTHORIZED"
    return true()


def item_attention():  # noqa: ANN201
    """Item que exige revisão: rejeição ainda não resolvida ou bloqueio antes do envio."""
    rejeicao_pendente = and_(BatchItem.status == "REJECTED", exists(select(1).where(
        Draft.id == BatchItem.draft_id, Draft.current_item_id == BatchItem.id, Draft.deleted_at.is_(None))))
    bloqueado = and_(BatchItem.status == "SCHEDULED", BatchItem.last_error_code.is_not(None))
    return or_(rejeicao_pendente, bloqueado)


def batch_view(view: str):  # noqa: ANN201
    ativo = exists(select(1).where(BatchItem.batch_id == Batch.id, BatchItem.status.in_(ACTIVE)))
    if view == "in_progress":
        return ativo
    if view == "attention":
        return exists(select(1).where(BatchItem.batch_id == Batch.id, item_attention()))
    if view == "history":
        return ~ativo
    return true()


def draft_counts(s, company_id: str, environment: str) -> dict[str, int]:  # noqa: ANN001
    base = (Draft.company_id == company_id, Draft.environment == environment, Draft.deleted_at.is_(None))
    return {v: s.scalar(select(func.count()).select_from(Draft).where(*base, draft_view(v))) or 0 for v in DRAFT_VIEWS}


def batch_view_counts(s, company_id: str, environment: str) -> dict[str, int]:  # noqa: ANN001
    base = (Batch.company_id == company_id, Batch.environment == environment)
    return {v: s.scalar(select(func.count()).select_from(Batch).where(*base, batch_view(v))) or 0 for v in BATCH_VIEWS}


def item_counts(s, company_id: str, environment: str) -> dict[str, int]:  # noqa: ANN001
    base = (BatchItem.company_id == company_id, BatchItem.environment == environment)
    return {
        "active": s.scalar(select(func.count()).select_from(BatchItem).where(*base, BatchItem.status.in_(ACTIVE))) or 0,
        "attention": s.scalar(select(func.count()).select_from(BatchItem).where(*base, item_attention())) or 0,
    }


def attention_batches(s, batch_ids: list[str]) -> set[str]:  # noqa: ANN001
    if not batch_ids:
        return set()
    return set(s.scalars(select(BatchItem.batch_id).where(BatchItem.batch_id.in_(batch_ids), item_attention())
                         .distinct()))


def issued_documents(s, drafts: list[Draft]) -> dict[str, dict]:  # noqa: ANN001
    """Nota de cada rascunho emitido, com a situação ATUAL (autorizada, cancelada ou substituída)."""
    ids = [d.current_item_id for d in drafts if d.status == "AUTHORIZED" and d.current_item_id]
    if not ids:
        return {}
    out = {}
    for it, doc in s.execute(select(BatchItem, Document).outerjoin(Document, Document.id == BatchItem.document_id)
                             .where(BatchItem.id.in_(ids))).all():
        if doc is None:
            situacao = "AUTHORIZED"
        elif doc.status == "CANCELLED":
            situacao = "CANCELLED"
        elif doc.substituted_by_access_key:
            situacao = "SUBSTITUTED"
        else:
            situacao = "AUTHORIZED" if doc.status == "AUTHORIZED" else doc.status
        out[it.draft_id] = {"document_id": it.document_id, "status": situacao,
                            "number": doc.number if doc else (it.result or {}).get("number"),
                            "access_key": doc.access_key if doc else (it.result or {}).get("access_key"),
                            "authorized_at": (it.result or {}).get("authorized_at")}
    return out
