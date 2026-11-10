"""Impacto da troca de ambiente do workspace (somente leitura): o que existe em cada ambiente e o que acontece com
o que estiver em andamento. Nada é movido, copiado nem apagado — cada ambiente mantém documentos, rascunhos, agenda e
cursor ADN próprios.

Na troca (ambiente decidido no core; o worker confere o vigente antes de transmitir):
- item agendado e não iniciado do ambiente anterior é retirado na hora prevista (ENVIRONMENT_CHANGED, nada
  transmitido) e o rascunho volta a ser editável naquele ambiente;
- item com envio possível (em transmissão ou resultado incerto) continua só a reconciliação no ambiente ORIGINAL,
  nunca é reenviado;
- pedidos de cancelamento seguem vinculados ao ambiente da nota;
- a agenda ADN do ambiente que deixa de ser lido é suspensa com o cursor preservado; pedido manual pendente nela é
  encerrado com o motivo.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import BatchItem, CancellationRequest, Document, Draft, Job, SyncSchedule
from . import cancelamento


def _por_env(s: Session, col_env, *where) -> dict[str, int]:  # noqa: ANN001
    return {env: n for env, n in s.execute(select(col_env, func.count()).where(*where).group_by(col_env))}


def impacto(s: Session, company_id: str, atual: str, destino: str | None = None) -> dict:
    docs = _por_env(s, Document.environment, Document.company_id == company_id)
    rascunhos = _por_env(s, Draft.environment, Draft.company_id == company_id, Draft.deleted_at.is_(None))
    nao_iniciados = _por_env(s, BatchItem.environment, BatchItem.company_id == company_id,
                             BatchItem.status == "SCHEDULED", BatchItem.submit_phase == "NONE")
    em_andamento = _por_env(s, BatchItem.environment, BatchItem.company_id == company_id,
                            BatchItem.status.in_(("SCHEDULED", "PROCESSING", "AWAITING_CONFIRMATION")),
                            ~((BatchItem.status == "SCHEDULED") & (BatchItem.submit_phase == "NONE")))
    cancel = _por_env(s, CancellationRequest.environment, CancellationRequest.company_id == company_id,
                      CancellationRequest.status.in_(cancelamento.ACTIVE))
    jobs = _por_env(s, Job.environment, Job.company_id == company_id, Job.status.in_(("QUEUED", "RUNNING")))
    agendas = {a.environment: a for a in s.scalars(select(SyncSchedule).where(SyncSchedule.company_id == company_id))}
    envs = sorted({atual, *([destino] if destino else []), *docs, *rascunhos, *agendas} - {None})
    out = []
    for env in envs:
        a = agendas.get(env)
        out.append({
            "environment": env, "current": env == atual, "target": env == destino,
            "documents": docs.get(env, 0), "drafts": rascunhos.get(env, 0),
            "scheduled_not_started": nao_iniciados.get(env, 0), "in_flight": em_andamento.get(env, 0),
            "active_cancellations": cancel.get(env, 0), "active_jobs": jobs.get(env, 0),
            "sync": ({"enabled": a.enabled, "status": a.status, "cursor_nsu": a.cursor_nsu,
                      "last_success_at": a.last_success_at} if a else None),
        })
    return {"company_id": company_id, "current_environment": atual, "target_environment": destino,
            "environments": out,
            "on_switch": [
                "Documentos, rascunhos, agendas e cursores ADN de cada ambiente são preservados e continuam separados.",
                "Agendamentos não iniciados do ambiente anterior são retirados na hora prevista, sem transmitir nada; "
                "o rascunho volta a ser editável naquele ambiente.",
                "Envios em andamento ou com resultado incerto só são reconciliados no ambiente original, sem reenvio.",
                "A agenda ADN do ambiente que deixa de ser lido é suspensa (cursor guardado); voltar a habilitá-la "
                "retoma do mesmo ponto.",
                "Emissão e cancelamento não são habilitados pela troca.",
            ]}
