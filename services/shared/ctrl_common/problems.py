"""Respostas de erro no formato application/problem+json (RFC 9457).

Nenhuma resposta contém stacktrace, SQL ou dados de terceiros.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"
TYPE_BASE = "https://ctrl-fiscal.local/problems/"

log = logging.getLogger("ctrl.problems")

_TITLES = {
    400: "Requisição inválida",
    401: "Não autenticado",
    403: "Acesso negado",
    404: "Não encontrado",
    405: "Método não permitido",
    409: "Conflito",
    413: "Conteúdo muito grande",
    415: "Tipo de mídia não suportado",
    422: "Dados inválidos",
    428: "Pré-condição obrigatória",
    429: "Muitas requisições",
    500: "Erro interno",
    503: "Serviço indisponível",
}

_DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    428: "PRECONDITION_REQUIRED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


class ProblemFieldError(BaseModel):
    field: str | None = None
    code: str
    message: str


class Problem(BaseModel):
    """Esquema publicado no OpenAPI para todas as respostas de erro."""

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    code: str
    trace_id: str
    errors: list[ProblemFieldError] | None = None


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str | None = None,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        title: str | None = None,
    ) -> None:
        super().__init__(detail or code or str(status))
        self.status = status
        self.code = code or _DEFAULT_CODES.get(status, "ERROR")
        self.detail = detail
        self.errors = errors
        self.headers = headers or {}
        self.title = title


def trace_id_for(request: Request) -> str:
    existing = getattr(request.state, "trace_id", None)
    if existing:
        return existing
    incoming = request.headers.get("x-request-id", "")
    # Aceita somente identificadores curtos e seguros vindos do gateway.
    if incoming and len(incoming) <= 64 and all(c.isalnum() or c in "-_" for c in incoming):
        tid = incoming
    else:
        tid = uuid.uuid4().hex
    request.state.trace_id = tid
    return tid


def problem_response(
    request: Request,
    status: int,
    code: str | None = None,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    title: str | None = None,
) -> JSONResponse:
    code = code or _DEFAULT_CODES.get(status, "ERROR")
    body: dict[str, Any] = {
        "type": TYPE_BASE + code.lower().replace("_", "-"),
        "title": title or _TITLES.get(status, "Erro"),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "trace_id": trace_id_for(request),
    }
    if errors:
        body["errors"] = errors
    hdrs = {"X-Request-ID": body["trace_id"], **(headers or {})}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=hdrs)


def _loc_to_field(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts) if parts else ".".join(str(p) for p in loc)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError):  # noqa: ANN202
        return problem_response(
            request, exc.status, exc.code, exc.detail, exc.errors, exc.headers, exc.title
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ANN202
        errors = []
        for err in exc.errors()[:50]:
            errors.append(
                {
                    "field": _loc_to_field(tuple(err.get("loc", ()))),
                    "code": str(err.get("type", "invalid")).upper(),
                    # A mensagem do Pydantic não ecoa o valor recebido.
                    "message": str(err.get("msg", "Valor inválido"))[:300],
                }
            )
        return problem_response(
            request, 422, "VALIDATION_ERROR", "Um ou mais campos são inválidos.", errors
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # noqa: ANN202
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(request, exc.status_code, None, detail, None, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        tid = trace_id_for(request)
        # Registra apenas o tipo da exceção; mensagens podem conter dados sensíveis.
        log.error("erro não tratado trace_id=%s tipo=%s", tid, type(exc).__name__)
        return problem_response(request, 500, "INTERNAL_ERROR", "Erro interno. Informe o trace_id ao suporte.")


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Declaração OpenAPI das respostas de erro (application/problem+json)."""
    return {
        s: {
            "model": Problem,
            "description": _TITLES.get(s, "Erro"),
            "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
        }
        for s in statuses
    }
