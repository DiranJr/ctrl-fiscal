"""Diagnóstico de apresentação de cada item de lote (somente leitura; não muda situação, histórico nem auditoria).

A etapa é derivada do que o fluxo de emissão já grava:
- REJECTED com todos os códigos LOCAL_* = validação local do conector ANTES do envio (nada foi transmitido);
- REJECTED com outro código = rejeição oficial da Sefin — o conector só a registra depois de consultar a DPS e
  confirmar que ela não gerou NFS-e (resultado com consulta falha vira incerto, nunca rejeitado);
- AWAITING_CONFIRMATION = resultado incerto: a reconciliação oficial (consulta da DPS) decide; nada é reenviado antes;
- SCHEDULED com último erro = bloqueado antes do envio (o conector garante que nada foi transmitido);
- WITHDRAWN = retirado da fila antes do envio.
"""
from __future__ import annotations

from ..models import BatchItem, Draft

ETAPAS = {
    "QUEUED": "Na fila, aguardando o horário de emissão",
    "BLOCKED_BEFORE_SEND": "Bloqueado antes do envio (nada transmitido)",
    "SENDING": "Em transmissão",
    "UNCERTAIN": "Resultado incerto — aguardando reconciliação oficial",
    "LOCAL_VALIDATION": "Validação local antes do envio (nada transmitido)",
    "OFFICIAL_REJECTION": "Rejeição oficial da Sefin",
    "AUTHORIZED": "Autorizada",
    "WITHDRAWN": "Retirado da fila antes do envio",
}
ORIENTACAO = {
    "QUEUED": "Nenhuma ação necessária. Para alterar o rascunho, retire o item da fila antes do envio.",
    "BLOCKED_BEFORE_SEND": "Resolva o motivo indicado (ex.: certificado, permissão ou cadastro). O item será tentado de "
                           "novo automaticamente; se preferir, retire-o da fila.",
    "SENDING": "Aguarde a conclusão. Não reenvie nem emita de novo o mesmo serviço.",
    "UNCERTAIN": "Não reenvie nem emita de novo: a reconciliação oficial consulta a DPS na Sefin. Se a DPS existir, a "
                 "NFS-e é registrada como autorizada; se não existir, o envio é retomado com a MESMA DPS.",
    "LOCAL_VALIDATION": "Corrija o rascunho conforme as mensagens, valide e emita ou agende de novo. Nenhuma DPS foi "
                        "transmitida para este item.",
    "OFFICIAL_REJECTION": "Corrija o rascunho conforme a rejeição oficial, valide e emita ou agende de novo. A Sefin "
                          "recusou esta DPS e a consulta confirmou que ela não gerou NFS-e.",
    "AUTHORIZED": "NFS-e autorizada: consulte-a em Notas emitidas. Cancelamento é um fluxo próprio.",
    "WITHDRAWN": "O rascunho voltou a ser editável; agende ou emita de novo se necessário.",
}


def etapa(i: BatchItem) -> str:
    if i.status == "AUTHORIZED":
        return "AUTHORIZED"
    if i.status == "WITHDRAWN":
        return "WITHDRAWN"
    if i.status == "AWAITING_CONFIRMATION":
        return "UNCERTAIN"
    if i.status == "PROCESSING":
        return "SENDING" if i.submit_phase == "SUBMITTING" else "QUEUED"
    if i.status == "REJECTED":
        codigos = [str(e.get("code") or "") for e in ((i.result or {}).get("errors") or [])]
        return "LOCAL_VALIDATION" if codigos and all(c.startswith("LOCAL_") for c in codigos) else "OFFICIAL_REJECTION"
    return "BLOCKED_BEFORE_SEND" if i.last_error_code else "QUEUED"


def erros(i: BatchItem, e: str) -> list[dict]:
    """Todos os códigos e mensagens registrados (rejeição: lista completa; demais: o último motivo)."""
    out = []
    if i.status == "REJECTED":
        for x in (i.result or {}).get("errors") or []:
            code = str(x.get("code") or "")
            out.append({"code": code, "message": x.get("message"),
                        "source": "LOCAL" if code.startswith("LOCAL_") else "OFFICIAL"})
    if i.last_error_code and not (i.status == "REJECTED" and out):
        out.append({"code": i.last_error_code, "message": i.last_error_message,
                    "source": "LOCAL" if e in ("BLOCKED_BEFORE_SEND", "WITHDRAWN") else "PROCESSING"})
    return out


def diagnostico(i: BatchItem, draft: Draft | None) -> dict:
    e = etapa(i)
    snap = i.snapshot or {}
    cust = snap.get("customer") or {}
    return {
        "stage": e,
        # Mesma regra de classificacao.item_attention: rejeição ainda vinculada ao rascunho ou bloqueio antes do envio.
        "needs_attention": (i.status == "REJECTED" and draft is not None and draft.current_item_id == i.id
                            and draft.deleted_at is None) or (i.status == "SCHEDULED" and bool(i.last_error_code)),
        "stage_label": ETAPAS[e],
        "guidance": ORIENTACAO[e],
        # True/False quando o fluxo garante; None = só a reconciliação (consulta oficial da DPS) pode dizer.
        "transmitted": {"LOCAL_VALIDATION": False, "BLOCKED_BEFORE_SEND": False, "WITHDRAWN": False, "QUEUED": False,
                        "OFFICIAL_REJECTION": True, "AUTHORIZED": True}.get(e),
        "errors": erros(i, e),
        "customer_document": cust.get("document"),
        "scheduled_at": i.scheduled_at,
        "updated_at": i.updated_at,
        "submission_id": i.submission_id if i.dps_id else None,  # só quando a DPS chegou a ser reservada
        "dps_series": i.dps_series,
        "dps_number": i.dps_number,
        "draft": ({"id": draft.id, "current_version": draft.version, "status": draft.status,
                   "deleted": draft.deleted_at is not None, "linked": draft.current_item_id == i.id}
                  if draft is not None else None),
    }
