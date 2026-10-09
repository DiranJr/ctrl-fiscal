"""Utilidades HTTP comuns: paginação, middleware de trace e limite de corpo."""
from __future__ import annotations

from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from .problems import trace_id_for

T = TypeVar("T")

MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
    items: list[T]
    page: int
    page_size: int
    total: int


class PageParams:
    def __init__(
        self,
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=MAX_PAGE_SIZE),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class TraceMiddleware:
    """Garante trace_id em toda resposta (X-Request-ID)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        request = Request(scope)
        tid = trace_id_for(request)
        scope.setdefault("state", {})["trace_id"] = tid

        async def _send(message):  # noqa: ANN001, ANN202
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(h[0].lower() == b"x-request-id" for h in headers):
                    headers.append((b"x-request-id", tid.encode()))
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)


class BodySizeLimitMiddleware:
    """Recusa corpos acima do limite (defesa em profundidade além do gateway)."""

    def __init__(self, app: ASGIApp, default_limit: int, overrides: dict[str, int] | None = None) -> None:
        self.app = app
        self.default_limit = default_limit
        self.overrides = overrides or {}

    def _limit_for(self, path: str) -> int:
        for suffix, limit in self.overrides.items():
            if path.endswith(suffix):
                return limit
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope.get("path", ""))
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _too_large(scope, receive, send)
            return
        received = 0

        async def _receive():  # noqa: ANN202
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, _receive, send)
        except _BodyTooLarge:
            await _too_large(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


async def _too_large(scope: Scope, receive: Receive, send: Send) -> None:
    import json

    body = json.dumps(
        {
            "type": "https://ctrl-fiscal.local/problems/payload-too-large",
            "title": "Conteúdo muito grande",
            "status": 413,
            "detail": "O corpo da requisição excede o limite configurado.",
            "instance": scope.get("path"),
            "code": "PAYLOAD_TOO_LARGE",
            "trace_id": scope.get("state", {}).get("trace_id", ""),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
